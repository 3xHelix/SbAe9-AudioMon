# SPDX-License-Identifier: GPL-2.0-only
"""Fake AE-9 test rig: a fake PipeWire graph with a fake Sound Blaster AE-9,
fake meters and a fake pw-link, so SbAe9-AudioMon can be tested (or watched)
without touching the real card, the real PipeWire or the user's files.

Built the same way as SbAe9-SBC's test harness, starting from its snapshot of
the real machine (fixtures/pw_ae9_stereo.json: the AE-9 sink and Mic / Line In
on driver 0.6.8), with What U Hear and Optical In added the way SBC's
add_what_u_hear / add_optical_in do.

install() must run before anything else touches the app:
  - points XDG_CONFIG_HOME / XDG_CACHE_HOME / XDG_STATE_HOME at a temp folder
    (so settings and the debug log never touch the real ones),
  - loads sbae9-audiomon.py as module `am`,
  - swaps am.Graph / am.Linker / am.Capture for the fakes below,
  - makes any real subprocess start fail.

The model, as close to the real thing as the app can see:
  - The AE-9 sink runs while an app stream plays to it, or while a meter that
    isn't passive is wired to its monitor. When nothing keeps it running it
    goes idle, then suspended after SUSPEND_AFTER seconds (5 s in real life).
  - A capture source (What U Hear, Mic, Optical) runs while a non-passive meter
    is wired to it.
  - A meter only gets data from a source that's running (passive or not).
  - What U Hear plays back the output monitor 3.1 dB lower while the sink runs,
    unless `silent_restart` is set: then it runs but delivers silence (the bug).
  - Optical In delivers nothing unless `optical_db` is set (no light on the jack).
"""

import atexit
import importlib.util
import json
import math
import os
import queue
import random
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FIXTURES = os.path.join(HERE, "fixtures")

SINK = "alsa_output.pci-0000_06_00.0.analog-stereo"
MIC = "alsa_input.pci-0000_06_00.0.analog-stereo"
WUH = "alsa_input.ae9_what_u_hear"
OPTICAL = "alsa_input.ae9_optical"
WUH_OFFSET_DB = -3.1          # measured on the real card: -20 dBFS sine reads -23.1
FAKE_DRIVER = "snd_hda_codec_creative_ae9 0.9.2 srcversion FAKE0000000000000000000"

am = None   # the app module, after install()
PW = None   # the FakePipeWire in use


def fixture(name):
    with open(os.path.join(FIXTURES, name)) as f:
        return json.load(f)


class FakePipeWire:
    """Stands in for the app's Graph (pw-dump --monitor)."""

    SUSPEND_AFTER = 0.3       # idle -> suspended (seconds); real PipeWire: 5 s

    def __init__(self, card=True, what_u_hear=True, optical=True):
        self.objects = {}
        self._version = 0
        self._next_id = 2000
        self.calls = []               # ("link", out_port, in_port) / ("unlink", link_id)
        self.streams = {}             # node id -> level dBFS
        self.silent_restart = False   # What U Hear runs but delivers silence
        self.mic_db = None            # level on Mic / Line In (None = no data)
        self.optical_db = None        # level on Optical In (None = no light, no data)
        self.jitter = 0.0             # +/- dB of random wobble on every level (demo)
        self._idle_since = {}
        self._card_nodes = []
        if card:
            self.card_present(True, what_u_hear, optical)

    # -- building the graph ----------------------------------------------

    def _id(self):
        self._next_id += 1
        return self._next_id

    def _bump(self):
        self._version += 1

    def _node(self, props, state="suspended", nid=None):
        nid = nid or self._id()
        self.objects[nid] = {"id": nid, "type": "PipeWire:Interface:Node",
                             "info": {"state": state, "error": None, "props": dict(props), "params": {}}}
        return nid

    def _port(self, node_id, direction, name, channel, monitor=False):
        pid = self._id()
        self.objects[pid] = {"id": pid, "type": "PipeWire:Interface:Port",
                             "info": {"direction": "output" if direction == "out" else "input",
                                      "props": {"node.id": node_id, "port.name": name,
                                                "port.direction": direction, "audio.channel": channel,
                                                "port.monitor": monitor}}}
        return pid

    def card_present(self, present, what_u_hear=True, optical=True):
        """Card on (nodes from SBC's snapshot + the driver's extra sources) or
        off (PSU cycle, driver reload): every AE-9 node and its links go."""
        if not present:
            for nid in self._card_nodes:
                self._remove_node(nid)
            self._card_nodes = []
            self._bump()
            return
        for o in fixture("pw_ae9_stereo.json"):
            if o.get("type") != "PipeWire:Interface:Node":
                continue
            props = o["info"]["props"]
            nid = self._node(props)
            self._card_nodes.append(nid)
            if props["media.class"] == "Audio/Sink":
                for ch in ("FL", "FR"):
                    self._port(nid, "in", f"playback_{ch}", ch)
                for ch in ("FL", "FR"):
                    self._port(nid, "out", f"monitor_{ch}", ch, monitor=True)
            else:
                for ch in ("FL", "FR"):
                    self._port(nid, "out", f"capture_{ch}", ch)
        # the driver's extra capture devices (SBC: add_what_u_hear / add_optical_in)
        for want, name, desc, dev in ((what_u_hear, WUH, "AE-9 What U Hear", 2),
                                      (optical, OPTICAL, "AE-9 Optical In", 1)):
            if want:
                nid = self._node({"node.name": name, "node.description": desc,
                                  "media.class": "Audio/Source", "api.alsa.path": f"hw:AE9,{dev}",
                                  "priority.session": 100})
                self._card_nodes.append(nid)
                for ch in ("FL", "FR"):
                    self._port(nid, "out", f"capture_{ch}", ch)
        self._bump()

    def add_stream(self, app, media, level_db=-14.0):
        """An app starts playing to the AE-9 (WirePlumber links it to the sink)."""
        sink = self.node(SINK)
        nid = self._node({"node.name": app.lower().replace(" ", "-"), "media.class": "Stream/Output/Audio",
                          "application.name": app, "media.name": media}, state="running")
        outs = [self._port(nid, "out", f"output_{ch}", ch) for ch in ("FL", "FR")]
        if sink:
            ins = [p["id"] for p in self.ports(sink["id"]) if p["info"]["direction"] == "input"]
            for o, i in zip(outs, ins):
                self._link(o, i)
        self.streams[nid] = level_db
        self._bump()
        return nid

    def stop_stream(self, nid):
        self.streams.pop(nid, None)
        self._remove_node(nid)
        self._bump()

    def link_external(self, out_port, in_port):
        """A link made by someone else (e.g. by hand in Helvum)."""
        lid = self._link(out_port, in_port)
        self._bump()
        return lid

    def _link(self, out_port, in_port):
        po, pi = self.objects[out_port], self.objects[in_port]
        lid = self._id()
        self.objects[lid] = {"id": lid, "type": "PipeWire:Interface:Link",
                             "info": {"output-node-id": po["info"]["props"]["node.id"], "output-port-id": out_port,
                                      "input-node-id": pi["info"]["props"]["node.id"], "input-port-id": in_port,
                                      "state": "active"}}
        return lid

    def _remove_node(self, nid):
        gone = {nid} | {pid for pid, o in self.objects.items()
                        if o["type"].endswith("Port") and o["info"]["props"]["node.id"] == nid}
        for oid in [oid for oid, o in self.objects.items() if o["type"].endswith("Link")
                    and (o["info"]["output-port-id"] in gone or o["info"]["input-port-id"] in gone)]:
            del self.objects[oid]
        for oid in gone:
            self.objects.pop(oid, None)

    # -- lookups -----------------------------------------------------------

    def node(self, name):
        for o in self.objects.values():
            if o["type"].endswith("Node") and o["info"]["props"].get("node.name") == name:
                return o
        return None

    def state(self, name):
        self._recompute()
        n = self.node(name)
        return n["info"]["state"] if n else None

    def ports(self, nid):
        return [o for o in self.objects.values()
                if o["type"].endswith("Port") and o["info"]["props"]["node.id"] == nid]

    def links(self):
        return [o for o in self.objects.values() if o["type"].endswith("Link")]

    def links_from(self, name):
        n = self.node(name)
        return [l for l in self.links() if n and l["info"]["output-node-id"] == n["id"]]

    def is_ours(self, nid):
        o = self.objects.get(nid)
        return bool(o) and o["info"]["props"].get("application.name") == "SbAe9-AudioMon"

    def _passive(self, nid):
        return str(self.objects[nid]["info"]["props"].get("node.passive", "")).lower() == "true"

    # -- states ---------------------------------------------------------------

    def _recompute(self):
        """Who's running: the rules in the module docstring."""
        now = time.monotonic()
        held = {}   # node id -> kept running by a non-passive meter
        for l in self.links():
            i = l["info"]
            if self.is_ours(i["input-node-id"]) and not self._passive(i["input-node-id"]):
                held[i["output-node-id"]] = True
        changed = False
        for nid in self._card_nodes:
            o = self.objects.get(nid)
            if not o:
                continue
            info = o["info"]
            is_sink = info["props"]["media.class"] == "Audio/Sink"
            run = held.get(nid, False) or (is_sink and bool(self.streams))
            if run:
                new = "running"
                self._idle_since.pop(nid, None)
            elif info["state"] == "running":
                new = "idle"
                self._idle_since[nid] = now
            elif info["state"] == "idle":
                new = "suspended" if now - self._idle_since.get(nid, now) >= self.SUSPEND_AFTER else "idle"
            else:
                new = "suspended"
            if new != info["state"]:
                info["state"] = new
                changed = True
        if changed:
            self._bump()

    # -- what a meter hears ---------------------------------------------------

    def _wobble(self, db):
        return db + random.uniform(-self.jitter, self.jitter) if self.jitter and db is not None else db

    def level_of(self, src_nid):
        """dBFS a source node delivers right now; None = no data, -inf = silence."""
        o = self.objects.get(src_nid)
        if not o:
            return None
        info = o["info"]
        name = info["props"].get("node.name")
        if src_nid in self.streams:
            return self._wobble(self.streams[src_nid])
        if info["state"] != "running":
            return None
        if name == SINK:
            return self._wobble(max(self.streams.values())) if self.streams else -math.inf
        if name == WUH:
            sink = self.node(SINK)
            if self.silent_restart or not sink or sink["info"]["state"] != "running" or not self.streams:
                return -math.inf
            return self._wobble(max(self.streams.values()) + WUH_OFFSET_DB)
        if name == MIC:
            return self._wobble(self.mic_db) if self.mic_db is not None else -math.inf
        if name == OPTICAL:
            return self._wobble(self.optical_db)
        return None

    def levels_into(self, meter_nid, names):
        """Per input channel of our meter: dBFS or None (nothing / no data)."""
        self._recompute()
        port_of = {o["info"]["props"]["port.name"]: o["id"] for o in self.ports(meter_nid)}
        out = []
        for ch in names:
            best = None
            for l in self.links():
                if l["info"]["input-port-id"] == port_of.get(f"input_{ch}"):
                    db = self.level_of(l["info"]["output-node-id"])
                    if db is not None:
                        best = db if best is None else max(best, db)
            out.append(best)
        return out

    # -- the Graph interface the app uses ---------------------------------------

    @property
    def version(self):
        self._recompute()
        return self._version

    def snapshot(self):
        self._recompute()
        return self._version, json.loads(json.dumps(list(self.objects.values())))

    def stop(self):
        pass

    # -- what pw-link / pw-record do ------------------------------------------------

    def pw_link(self, out_port, in_port):
        self.calls.append(("link", out_port, in_port))
        if out_port in self.objects and in_port in self.objects and not any(
                l["info"]["output-port-id"] == out_port and l["info"]["input-port-id"] == in_port
                for l in self.links()):
            self._link(out_port, in_port)
            self._bump()
            return True
        return False

    def pw_unlink(self, link_id):
        self.calls.append(("unlink", link_id))
        if self.objects.pop(link_id, None):
            self._bump()

    def meter_node(self, node_name, label, channels, passive):
        props = {"node.name": node_name, "node.description": label, "media.class": "Stream/Input/Audio",
                 "application.name": "SbAe9-AudioMon"}
        if passive:
            props["node.passive"] = "true"
        nid = self._node(props, state="idle")
        for ch in channels:
            self._port(nid, "in", f"input_{ch}", ch)
        self._bump()
        return nid

    def drop_meter(self, nid):
        self._remove_node(nid)
        self._bump()


class FakeLinker:
    """Stands in for am.Linker: links show up in the graph straight away."""

    def __init__(self):
        self.failures = queue.Queue()

    def link(self, out_port, in_port):
        if not PW.pw_link(out_port, in_port):
            self.failures.put(f"pw-link --wait {out_port} {in_port}: no such port")

    def unlink(self, link_id):
        PW.pw_unlink(link_id)


def make_fake_capture(np):
    class FakeCapture:
        """Stands in for am.Capture (pw-record): a meter node in the fake graph
        whose levels come from whatever is wired into it."""

        def __init__(self, node_name, label, channels, passive=False):
            self.node_name, self.label, self.channels, self.passive = node_name, label, channels, passive
            self.error = None
            self.last_data = 0.0
            self._last = time.monotonic()
            self._names = am.CHANNEL_MAPS[channels]
            self.nid = PW.meter_node(node_name, label, self._names, passive)

        def take(self):
            now = time.monotonic()
            frames = int((now - self._last) * am.RATE)
            self._last = now
            levels = PW.levels_into(self.nid, self._names)
            if frames <= 0 or all(db is None for db in levels):
                z = np.zeros(self.channels)
                return z, z.copy(), 0, self.last_data
            amp = np.array([0.0 if db is None or db == -math.inf else 10 ** (db / 20) for db in levels])
            self.last_data = now
            return amp, (amp ** 2) / 2 * frames, frames, self.last_data   # a sine at that peak

        def stop(self):
            PW.drop_meter(self.nid)

    return FakeCapture


def _no_subprocess(*a, **k):
    raise AssertionError(f"tried to run a real command under the fake AE-9: {a}")


def install(offscreen=True):
    """Set up the temp folders, load the app and swap in the fakes."""
    global am, PW
    if am is not None:
        return am
    tmp = tempfile.mkdtemp(prefix="sbae9-audiomon-fake-")
    atexit.register(shutil.rmtree, tmp, True)
    for var in ("XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME"):
        os.environ[var] = os.path.join(tmp, var.split("_")[1].lower())
    if offscreen:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    spec = importlib.util.spec_from_file_location("audiomon", os.path.join(ROOT, "sbae9-audiomon.py"))
    am = importlib.util.module_from_spec(spec)
    sys.modules["audiomon"] = am
    spec.loader.exec_module(am)

    import numpy as np
    import subprocess
    subprocess.Popen = _no_subprocess
    subprocess.run = _no_subprocess

    PW = FakePipeWire()
    am.Graph = lambda: PW
    am.Linker = FakeLinker
    am.Capture = make_fake_capture(np)
    am.driver_info = lambda: FAKE_DRIVER
    return am


def reset(**kw):
    """A fresh fake card for the next test or scenario."""
    global PW
    PW = FakePipeWire(**kw)
    return PW
