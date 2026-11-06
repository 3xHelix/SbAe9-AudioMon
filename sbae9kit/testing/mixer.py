# SPDX-License-Identifier: GPL-2.0-only
"""FakeMixer: the AE-9's ALSA mixer controls, for control apps' tests.

Stands in for sbae9kit.mixer.Mixer. It starts from a snapshot of the real
card's controls (fixtures/ae9_controls.json: driver 0.6.8, names, types,
ranges, enum items, values, inactive flags) and behaves like the real thing
as far as an app can see: writes are logged and come back as change events
through a pipe, changes made "elsewhere" (the head-unit knob, alsamixer, the
driver) arrive the same way, and inactive controls still read and write.

Listen-only apps (SbAe9-AudioMon) don't use this: it imports sbae9kit.mixer.
"""

import json
import os

from .. import mixer as alsa
from . import fixture_path


def load_controls(name="ae9_controls.json"):
    with open(fixture_path(name)) as f:
        return json.load(f)


class FakeMixer:
    """Same API as sbae9kit.mixer.Mixer, plus test helpers. Writes are in
    .writes; FakeMixer.last is the newest one (apps make their own)."""
    last = None

    def __init__(self, idx=0, spec=None):
        self.controls, self.by_numid, self.values = {}, {}, {}
        for i, s in enumerate(spec or load_controls()):
            c = alsa.Control(numid=i + 1, name=s["name"], type=s["type"], count=s["count"],
                             min=s["min"], max=s["max"], items=list(s["items"]),
                             inactive=bool(s.get("inactive", False)),
                             writable=s.get("writable", True))
            self.controls[c.name] = c
            self.by_numid[c.numid] = c
            self.values[c.name] = s["value"]
        self.writes = []
        self._events = []
        self._r, self._w = os.pipe()
        os.set_blocking(self._r, False)
        FakeMixer.last = self

    # sbae9kit.mixer.Mixer API
    def has(self, name):
        return name in self.controls

    def get(self, name):
        return self.values[name]

    def set(self, name, value):
        c = self.controls[name]
        if c.type == alsa.TYPE_ENUMERATED and isinstance(value, str):
            value = c.items.index(value)
        if c.type == alsa.TYPE_BOOLEAN:
            value = bool(value)
        self.writes.append((name, value))
        self._store(name, value)

    def subscribe(self):
        return self._r

    def read_events(self):
        try:
            while os.read(self._r, 4096):
                pass
        except BlockingIOError:
            pass
        ev, self._events = self._events, []
        return ev

    def rescan(self):
        pass

    def refresh_info(self, numid):
        return self.by_numid.get(numid)

    def close(self):
        for fd in (self._r, self._w):
            try:
                os.close(fd)
            except OSError:
                pass

    # test helpers
    def _event(self, numid, mask):
        self._events.append((numid, mask))
        os.write(self._w, b"x")

    def _store(self, name, value):
        self.values[name] = value
        self._event(self.controls[name].numid, alsa.EVENT_MASK_VALUE)

    def external(self, name, value):
        """A change made outside the app (head-unit knob, alsamixer, the driver)."""
        c = self.controls[name]
        if c.type == alsa.TYPE_ENUMERATED and isinstance(value, str):
            value = c.items.index(value)
        self._store(name, value)

    def set_inactive(self, name, inactive=True):
        """The driver greys a control out (or back in), e.g. the effects with
        SBX off. It keeps its value; apps get an INFO event."""
        c = self.controls[name]
        c.inactive = bool(inactive)
        self._event(c.numid, alsa.EVENT_MASK_INFO)

    def set_items(self, name, items):
        """Swap an enum's items (e.g. the old driver's EQ preset order)."""
        cur = self.controls[name].items[self.values[name]]
        self.controls[name].items = list(items)
        self.values[name] = items.index(cur) if cur in items else 0

    def _add(self, name, value, **kw):
        numid = max(self.by_numid, default=0) + 1
        c = alsa.Control(numid=numid, name=name, count=1, writable=kw.pop("writable", True), **kw)
        self.controls[name] = c
        self.by_numid[numid] = c
        self.values[name] = value
        return c

    def add_bool(self, name, value=True, writable=True):
        """A switch the snapshot doesn't have (e.g. Optical Out, or a read-only
        "Head Unit 3.5mm Phones Plugged")."""
        return self._add(name, value, type=alsa.TYPE_BOOLEAN, min=0, max=1, items=[],
                         writable=writable)

    def add_enum(self, name, items, value=0):
        """An enum the snapshot doesn't have (e.g. Capture Bit Depth)."""
        return self._add(name, value, type=alsa.TYPE_ENUMERATED, min=0, max=len(items) - 1,
                         items=list(items))

    def remove(self, name):
        c = self.controls.pop(name)
        self.by_numid.pop(c.numid, None)
        self.values.pop(name, None)
