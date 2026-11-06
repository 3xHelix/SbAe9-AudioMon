# SPDX-License-Identifier: GPL-2.0-only
"""ALSA mixer controls (snd_ctl_*) via ctypes: read AND write.

Talks to the card's mixer controls directly, the same way alsamixer does,
including controls the kernel marks inactive (which `amixer contents` hides).
Also exposes the control device's poll fd so the UI can react to changes
made elsewhere (head-unit knob, alsamixer, the desktop volume slider).

This is the only kit module that can change the card. Listen-only apps
(SbAe9-AudioMon) never import it; read-only facts about the card are in
sbae9kit.ae9.
"""

import ctypes
import ctypes.util
from dataclasses import dataclass, field

_lib = ctypes.CDLL(ctypes.util.find_library("asound") or "libasound.so.2")

_vp = ctypes.c_void_p
_P = ctypes.POINTER(ctypes.c_void_p)


def _sig(name, restype, *args):
    fn = getattr(_lib, name)
    fn.restype = restype
    fn.argtypes = list(args)
    return fn


snd_card_get_index = _sig("snd_card_get_index", ctypes.c_int, ctypes.c_char_p)
snd_card_next = _sig("snd_card_next", ctypes.c_int, ctypes.POINTER(ctypes.c_int))
snd_card_get_name = _sig("snd_card_get_name", ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_char_p))
snd_card_get_longname = _sig("snd_card_get_longname", ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_char_p))
snd_ctl_open = _sig("snd_ctl_open", ctypes.c_int, _P, ctypes.c_char_p, ctypes.c_int)
snd_ctl_close = _sig("snd_ctl_close", ctypes.c_int, _vp)
snd_ctl_nonblock = _sig("snd_ctl_nonblock", ctypes.c_int, _vp, ctypes.c_int)
snd_ctl_subscribe_events = _sig("snd_ctl_subscribe_events", ctypes.c_int, _vp, ctypes.c_int)
snd_ctl_poll_descriptors_count = _sig("snd_ctl_poll_descriptors_count", ctypes.c_int, _vp)

snd_ctl_elem_list_malloc = _sig("snd_ctl_elem_list_malloc", ctypes.c_int, _P)
snd_ctl_elem_list_free = _sig("snd_ctl_elem_list_free", None, _vp)
snd_ctl_elem_list = _sig("snd_ctl_elem_list", ctypes.c_int, _vp, _vp)
snd_ctl_elem_list_get_count = _sig("snd_ctl_elem_list_get_count", ctypes.c_uint, _vp)
snd_ctl_elem_list_get_used = _sig("snd_ctl_elem_list_get_used", ctypes.c_uint, _vp)
snd_ctl_elem_list_alloc_space = _sig("snd_ctl_elem_list_alloc_space", ctypes.c_int, _vp, ctypes.c_uint)
snd_ctl_elem_list_free_space = _sig("snd_ctl_elem_list_free_space", None, _vp)
snd_ctl_elem_list_get_numid = _sig("snd_ctl_elem_list_get_numid", ctypes.c_uint, _vp, ctypes.c_uint)
snd_ctl_elem_list_get_name = _sig("snd_ctl_elem_list_get_name", ctypes.c_char_p, _vp, ctypes.c_uint)

snd_ctl_elem_id_malloc = _sig("snd_ctl_elem_id_malloc", ctypes.c_int, _P)
snd_ctl_elem_id_set_numid = _sig("snd_ctl_elem_id_set_numid", None, _vp, ctypes.c_uint)

snd_ctl_elem_info_malloc = _sig("snd_ctl_elem_info_malloc", ctypes.c_int, _P)
snd_ctl_elem_info_set_id = _sig("snd_ctl_elem_info_set_id", None, _vp, _vp)
snd_ctl_elem_info = _sig("snd_ctl_elem_info", ctypes.c_int, _vp, _vp)
snd_ctl_elem_info_get_type = _sig("snd_ctl_elem_info_get_type", ctypes.c_int, _vp)
snd_ctl_elem_info_get_count = _sig("snd_ctl_elem_info_get_count", ctypes.c_uint, _vp)
snd_ctl_elem_info_get_min = _sig("snd_ctl_elem_info_get_min", ctypes.c_long, _vp)
snd_ctl_elem_info_get_max = _sig("snd_ctl_elem_info_get_max", ctypes.c_long, _vp)
snd_ctl_elem_info_get_items = _sig("snd_ctl_elem_info_get_items", ctypes.c_uint, _vp)
snd_ctl_elem_info_set_item = _sig("snd_ctl_elem_info_set_item", None, _vp, ctypes.c_uint)
snd_ctl_elem_info_get_item_name = _sig("snd_ctl_elem_info_get_item_name", ctypes.c_char_p, _vp)
snd_ctl_elem_info_is_inactive = _sig("snd_ctl_elem_info_is_inactive", ctypes.c_int, _vp)
snd_ctl_elem_info_is_writable = _sig("snd_ctl_elem_info_is_writable", ctypes.c_int, _vp)

snd_ctl_elem_value_malloc = _sig("snd_ctl_elem_value_malloc", ctypes.c_int, _P)
snd_ctl_elem_value_set_id = _sig("snd_ctl_elem_value_set_id", None, _vp, _vp)
snd_ctl_elem_read = _sig("snd_ctl_elem_read", ctypes.c_int, _vp, _vp)
snd_ctl_elem_write = _sig("snd_ctl_elem_write", ctypes.c_int, _vp, _vp)
snd_ctl_elem_value_get_boolean = _sig("snd_ctl_elem_value_get_boolean", ctypes.c_int, _vp, ctypes.c_uint)
snd_ctl_elem_value_get_integer = _sig("snd_ctl_elem_value_get_integer", ctypes.c_long, _vp, ctypes.c_uint)
snd_ctl_elem_value_get_enumerated = _sig("snd_ctl_elem_value_get_enumerated", ctypes.c_uint, _vp, ctypes.c_uint)
snd_ctl_elem_value_set_boolean = _sig("snd_ctl_elem_value_set_boolean", None, _vp, ctypes.c_uint, ctypes.c_long)
snd_ctl_elem_value_set_integer = _sig("snd_ctl_elem_value_set_integer", None, _vp, ctypes.c_uint, ctypes.c_long)
snd_ctl_elem_value_set_enumerated = _sig("snd_ctl_elem_value_set_enumerated", None, _vp, ctypes.c_uint, ctypes.c_uint)

snd_ctl_event_malloc = _sig("snd_ctl_event_malloc", ctypes.c_int, _P)
snd_ctl_read = _sig("snd_ctl_read", ctypes.c_int, _vp, _vp)
snd_ctl_event_get_type = _sig("snd_ctl_event_get_type", ctypes.c_int, _vp)
snd_ctl_event_elem_get_numid = _sig("snd_ctl_event_elem_get_numid", ctypes.c_uint, _vp)
snd_ctl_event_elem_get_mask = _sig("snd_ctl_event_elem_get_mask", ctypes.c_uint, _vp)


class _PollFd(ctypes.Structure):
    _fields_ = [("fd", ctypes.c_int), ("events", ctypes.c_short), ("revents", ctypes.c_short)]


snd_ctl_poll_descriptors = _sig("snd_ctl_poll_descriptors", ctypes.c_int, _vp, ctypes.POINTER(_PollFd), ctypes.c_uint)

TYPE_BOOLEAN, TYPE_INTEGER, TYPE_ENUMERATED = 1, 2, 3
EVENT_MASK_VALUE, EVENT_MASK_INFO, EVENT_MASK_ADD = 1, 2, 4
EVENT_MASK_REMOVE = 0xFFFFFFFF


class AlsaError(OSError):
    pass


def _alloc(fn):
    p = ctypes.c_void_p()
    if fn(ctypes.byref(p)) < 0:
        raise MemoryError
    return p


@dataclass
class Control:
    numid: int
    name: str
    type: int = 0
    count: int = 1
    min: int = 0
    max: int = 1
    items: list = field(default_factory=list)
    inactive: bool = False
    writable: bool = True


def find_card(*hints):
    """Return the card index whose id/name matches one of the hints."""
    for h in hints:
        idx = snd_card_get_index(h.encode())
        if idx >= 0:
            return idx
    card = ctypes.c_int(-1)
    while snd_card_next(ctypes.byref(card)) == 0 and card.value >= 0:
        name = ctypes.c_char_p()
        if snd_card_get_longname(card.value, ctypes.byref(name)) == 0 and name.value:
            text = name.value.decode(errors="replace")
            if any(h.lower() in text.lower() for h in hints):
                return card.value
    return -1


def card_longname(index):
    name = ctypes.c_char_p()
    if snd_card_get_longname(index, ctypes.byref(name)) == 0 and name.value:
        return name.value.decode(errors="replace")
    return ""


class Mixer:
    """An open control device for one card."""

    def __init__(self, card_index):
        self.card_index = card_index
        self._h = ctypes.c_void_p()
        err = snd_ctl_open(ctypes.byref(self._h), f"hw:{card_index}".encode(), 0)
        if err < 0:
            raise AlsaError(-err, f"cannot open hw:{card_index}")
        self._id = _alloc(snd_ctl_elem_id_malloc)
        self._info = _alloc(snd_ctl_elem_info_malloc)
        self._val = _alloc(snd_ctl_elem_value_malloc)
        self._ev = _alloc(snd_ctl_event_malloc)
        self.controls = {}   # name -> Control
        self.by_numid = {}   # numid -> Control
        self.rescan()

    def close(self):
        if self._h:
            snd_ctl_close(self._h)
            self._h = ctypes.c_void_p()

    # --- enumeration -----------------------------------------------------
    def rescan(self):
        lst = _alloc(snd_ctl_elem_list_malloc)
        try:
            snd_ctl_elem_list(self._h, lst)
            n = snd_ctl_elem_list_get_count(lst)
            snd_ctl_elem_list_alloc_space(lst, n)
            snd_ctl_elem_list(self._h, lst)
            self.controls.clear()
            self.by_numid.clear()
            for i in range(snd_ctl_elem_list_get_used(lst)):
                numid = snd_ctl_elem_list_get_numid(lst, i)
                name = snd_ctl_elem_list_get_name(lst, i).decode(errors="replace")
                c = Control(numid, name)
                self._load_info(c)
                self.controls[name] = c
                self.by_numid[numid] = c
            snd_ctl_elem_list_free_space(lst)
        finally:
            snd_ctl_elem_list_free(lst)

    def _load_info(self, c):
        snd_ctl_elem_id_set_numid(self._id, c.numid)
        snd_ctl_elem_info_set_id(self._info, self._id)
        if snd_ctl_elem_info(self._h, self._info) < 0:
            return
        c.type = snd_ctl_elem_info_get_type(self._info)
        c.count = snd_ctl_elem_info_get_count(self._info)
        c.inactive = bool(snd_ctl_elem_info_is_inactive(self._info))
        c.writable = bool(snd_ctl_elem_info_is_writable(self._info))
        if c.type == TYPE_INTEGER:
            c.min = snd_ctl_elem_info_get_min(self._info)
            c.max = snd_ctl_elem_info_get_max(self._info)
        elif c.type == TYPE_BOOLEAN:
            c.min, c.max = 0, 1
        elif c.type == TYPE_ENUMERATED:
            items = []
            for k in range(snd_ctl_elem_info_get_items(self._info)):
                snd_ctl_elem_info_set_item(self._info, k)
                snd_ctl_elem_info(self._h, self._info)
                items.append(snd_ctl_elem_info_get_item_name(self._info).decode(errors="replace"))
            c.items = items
            c.min, c.max = 0, max(0, len(items) - 1)

    def refresh_info(self, numid):
        c = self.by_numid.get(numid)
        if c:
            self._load_info(c)
        return c

    def has(self, name):
        return name in self.controls

    # --- values ------------------------------------------------------------
    def _prep(self, name):
        c = self.controls[name]
        snd_ctl_elem_id_set_numid(self._id, c.numid)
        snd_ctl_elem_value_set_id(self._val, self._id)
        return c

    def get(self, name):
        """Value of channel 0: bool, int or enum index."""
        c = self._prep(name)
        err = snd_ctl_elem_read(self._h, self._val)
        if err < 0:
            raise AlsaError(-err, f"read {name}")
        if c.type == TYPE_BOOLEAN:
            return bool(snd_ctl_elem_value_get_boolean(self._val, 0))
        if c.type == TYPE_ENUMERATED:
            return int(snd_ctl_elem_value_get_enumerated(self._val, 0))
        return int(snd_ctl_elem_value_get_integer(self._val, 0))

    def set(self, name, value):
        """Set every channel of a control. Enums accept index or item name."""
        c = self._prep(name)
        snd_ctl_elem_read(self._h, self._val)
        if c.type == TYPE_ENUMERATED and isinstance(value, str):
            value = c.items.index(value)
        for ch in range(max(1, c.count)):
            if c.type == TYPE_BOOLEAN:
                snd_ctl_elem_value_set_boolean(self._val, ch, 1 if value else 0)
            elif c.type == TYPE_ENUMERATED:
                snd_ctl_elem_value_set_enumerated(self._val, ch, int(value))
            else:
                v = max(c.min, min(c.max, int(round(value))))
                snd_ctl_elem_value_set_integer(self._val, ch, v)
        err = snd_ctl_elem_write(self._h, self._val)
        if err < 0:
            raise AlsaError(-err, f"write {name}")

    # --- events --------------------------------------------------------------
    def subscribe(self):
        """Enable change events; returns the fd to watch for reads."""
        snd_ctl_nonblock(self._h, 1)
        snd_ctl_subscribe_events(self._h, 1)
        n = snd_ctl_poll_descriptors_count(self._h)
        pfds = (_PollFd * max(1, n))()
        snd_ctl_poll_descriptors(self._h, pfds, n)
        return pfds[0].fd

    def read_events(self):
        """Drain pending events: list of (numid, mask)."""
        out = []
        while True:
            r = snd_ctl_read(self._h, self._ev)
            if r <= 0:
                break
            if snd_ctl_event_get_type(self._ev) == 0:  # SND_CTL_EVENT_ELEM
                out.append((snd_ctl_event_elem_get_numid(self._ev),
                            snd_ctl_event_elem_get_mask(self._ev)))
        return out
