# SPDX-License-Identifier: GPL-2.0-only
"""A fake PipeWire graph with a fake Sound Blaster AE-9 in it.

The model, as close to the real thing as the apps can see:

- Nodes, ports and links in pw-dump's own format. The snapshot has no ports,
  so they're generated per node: the sink gets playback_* in and monitor_* out,
  sources get capture_* out, app streams output_* out, meters input_* in.
- The AE-9 sink runs while an app stream plays to it, or while a capture
  that isn't passive (node.passive) is linked to its monitor. When nothing
  keeps it running it goes idle, then suspended after suspend_after seconds
  (5 s in real life, times TIME_SCALE).
- A capture source (What U Hear, Mic, Optical) runs while a non-passive
  capture is linked to it.
- A capture only gets data from a source that's running.
- What U Hear plays back the output monitor at the same level (it's
  level-exact: 0 dB against the source, confirmed on the real card) while
  the sink runs, unless `silent_restart` is set: then it runs but delivers
  silence, which is the bug the apps watch for.
- Optical In with no light on the jack: the driver refuses to open the
  capture ("no signal on the optical input"), and PipeWire's node carries on
  delivering digital silence, which is what the apps see. `optical_db` sets a
  lit jack's level.
- The card can drop off and come back (PSU cycle, driver reload).

Apps read it like pw-dump (`objects`, `snapshot()`, `version`, or
`run("pw-dump")`) and change it like pw-link (`run("pw-link", ...)`,
FakeLinker or pw_link / pw_unlink). Other commands go through run() too: an
app's harness registers handlers for them with `handle(prog, fn)`; anything
unhandled is recorded in `calls` and answered with "".
"""

import json
import math
import queue
import random
import time

from . import fixture_path, scaled

SINK = "alsa_output.pci-0000_06_00.0.analog-stereo"
MIC = "alsa_input.pci-0000_06_00.0.analog-stereo"
WHAT_U_HEAR = "alsa_input.ae9_what_u_hear"
# The driver's desktop name for the head unit's input: the XLR/TRS jack and the
# 3.5 mm mic jack share it (config/61-ae9-names.conf). The snapshot predates it.
MIC_DESC = "AE-9 Head Unit Mic / Aux"
OPTICAL = "alsa_input.ae9_optical"
WUH_OFFSET_DB = 0.0           # What U Hear is level-exact against the source (a -20 dBFS
                              # peak sine reads -20 peak / -23 RMS, as at the output)
REAL_SUSPEND_AFTER = 5.0      # PipeWire's default idle -> suspend wait


class FakePipeWire:
    """The PipeWire graph with the AE-9 in it, plus what it would sound like."""

    def __init__(self, card=True, what_u_hear=True, optical=True, fixture="pw_ae9_stereo.json"):
        self.fixture = fixture
        self.objects = {}             # id -> pw-dump object
        self._version = 0
        self._next_id = 2000
        self.calls = []               # every command / change: ("link", out, in), ("unlink", id), (prog, *args)
        self.handlers = {}            # prog -> fn(*args) -> str, for commands an app adds
        self.streams = {}             # app stream node id -> level dBFS
        self.silent_restart = False   # What U Hear runs but delivers silence (the bug)
        self.mic_db = None            # level on the head unit's Mic / Aux input (None = silence)
        self.optical_db = None        # level on a lit Optical In (None = no light: silence)
        self.jitter = 0.0             # +/- dB of random wobble on every level (demos)
        self.suspend_after = scaled(REAL_SUSPEND_AFTER)
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

    def _node(self, props, state="suspended"):
        nid = self._id()
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
        """Card on (the snapshot's nodes + the driver's extra sources) or off:
        every AE-9 node and its links go, as on a PSU cycle or driver reload."""
        if not present:
            for nid in self._card_nodes:
                self._remove_node(nid)
            self._card_nodes = []
            self._bump()
            return
        with open(fixture_path(self.fixture)) as f:
            snapshot = json.load(f)
        for o in snapshot:
            if o.get("type") != "PipeWire:Interface:Node":
                continue
            props = dict(o["info"]["props"])
            if props.get("node.name") == MIC:
                props["node.description"] = MIC_DESC   # as the driver's names config sets it
            nid = self._node(props)
            self._card_nodes.append(nid)
            if props.get("media.class") == "Audio/Sink":
                for ch in ("FL", "FR"):
                    self._port(nid, "in", f"playback_{ch}", ch)
                for ch in ("FL", "FR"):
                    self._port(nid, "out", f"monitor_{ch}", ch, monitor=True)
            else:
                for ch in ("FL", "FR"):
                    self._port(nid, "out", f"capture_{ch}", ch)
        # the driver's extra capture devices (hw:AE9,1 and hw:AE9,2)
        for want, name, desc, dev in ((what_u_hear, WHAT_U_HEAR, "AE-9 What U Hear", 2),
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

    def props(self, nid):
        o = self.objects.get(nid)
        return o["info"]["props"] if o else {}

    def is_capture(self, nid):
        return self.props(nid).get("media.class") == "Stream/Input/Audio"

    def _passive(self, nid):
        return str(self.props(nid).get("node.passive", "")).lower() == "true"

    # -- states ---------------------------------------------------------------

    def _recompute(self):
        """Who's running: the rules in the module docstring."""
        now = time.monotonic()
        held = set()      # nodes kept running by a capture that isn't passive
        for l in self.links():
            i = l["info"]
            if self.is_capture(i["input-node-id"]) and not self._passive(i["input-node-id"]):
                held.add(i["output-node-id"])
        changed = False
        for nid in self._card_nodes:
            o = self.objects.get(nid)
            if not o:
                continue
            info = o["info"]
            is_sink = info["props"].get("media.class") == "Audio/Sink"
            if nid in held or (is_sink and self.streams):
                new = "running"
                self._idle_since.pop(nid, None)
            elif info["state"] == "running":
                new = "idle"
                self._idle_since[nid] = now
            elif info["state"] == "idle":
                new = "suspended" if now - self._idle_since.get(nid, now) >= self.suspend_after else "idle"
            else:
                new = "suspended"
            if new != info["state"]:
                info["state"] = new
                changed = True
        if changed:
            self._bump()

    # -- what a capture hears ---------------------------------------------------

    def _wobble(self, db):
        return db + random.uniform(-self.jitter, self.jitter) if self.jitter and db is not None else db

    def level_of(self, src_nid):
        """dBFS a node delivers right now; None = no data, -inf = silence."""
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
        if name == WHAT_U_HEAR:
            sink = self.node(SINK)
            if self.silent_restart or not sink or sink["info"]["state"] != "running" or not self.streams:
                return -math.inf
            return self._wobble(max(self.streams.values()) + WUH_OFFSET_DB)
        if name == MIC:
            return self._wobble(self.mic_db) if self.mic_db is not None else -math.inf
        if name == OPTICAL:
            return self._wobble(self.optical_db) if self.optical_db is not None else -math.inf
        return None

    def levels_into(self, capture_nid, channels):
        """Per input channel of a capture: dBFS, -inf (silence) or None (no data)."""
        self._recompute()
        port_of = {o["info"]["props"]["port.name"]: o["id"] for o in self.ports(capture_nid)}
        out = []
        for ch in channels:
            best = None
            for l in self.links():
                if l["info"]["input-port-id"] == port_of.get(f"input_{ch}"):
                    db = self.level_of(l["info"]["output-node-id"])
                    if db is not None:
                        best = db if best is None else max(best, db)
            out.append(best)
        return out

    # -- reading it like pw-dump ------------------------------------------------

    @property
    def version(self):
        """Goes up on every change (what `pw-dump --monitor` would report)."""
        self._recompute()
        return self._version

    def snapshot(self):
        """(version, a pw-dump copy of every object)."""
        self._recompute()
        return self._version, json.loads(json.dumps(list(self.objects.values())))

    def stop(self):
        pass

    # -- changing it like pw-link / pw-record ---------------------------------------

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
            return True
        return False

    def capture_node(self, node_name, label, channels, passive=False, app_name=None):
        """What pw-record makes: a capture stream node with input_<ch> ports."""
        props = {"node.name": node_name, "node.description": label, "media.class": "Stream/Input/Audio"}
        if app_name:
            props["application.name"] = app_name
        if passive:
            props["node.passive"] = "true"
        nid = self._node(props, state="idle")
        for ch in channels:
            self._port(nid, "in", f"input_{ch}", ch)
        self._bump()
        return nid

    def drop_node(self, nid):
        self._remove_node(nid)
        self._bump()

    # -- commands -------------------------------------------------------------------

    def handle(self, prog, fn):
        """Answer `prog` with fn(*args) -> str (an app's harness adds these,
        e.g. pw-metadata, pw-cli, wpctl)."""
        self.handlers[prog] = fn

    def run(self, prog, *args, **_):
        """Answer a PipeWire command the way the real tool would."""
        if prog in self.handlers:
            return self.handlers[prog](*args)
        if prog == "pw-dump":
            return json.dumps(self.snapshot()[1])
        if prog == "pw-link":
            flags = [a for a in args if a.startswith("-")]
            rest = [a for a in args if not a.startswith("-")]
            if "-d" in flags or "--disconnect" in flags:
                if len(rest) == 1:
                    self.pw_unlink(int(rest[0]))
                else:
                    out, inp = (self._port_id(r) for r in rest[:2])
                    for l in self.links():
                        if (l["info"]["output-port-id"], l["info"]["input-port-id"]) == (out, inp):
                            self.pw_unlink(l["id"])
                return ""
            out, inp = (self._port_id(r) for r in rest[:2])
            self.pw_link(out, inp)
            return ""
        self.calls.append((prog,) + tuple(args))
        return ""

    def _port_id(self, ref):
        """A port by id ("73") or by "node.name:port.name"."""
        if str(ref).isdigit():
            return int(ref)
        node_name, _, port_name = str(ref).rpartition(":")
        n = self.node(node_name)
        for p in self.ports(n["id"]) if n else []:
            if p["info"]["props"]["port.name"] == port_name:
                return p["id"]
        return None


class FakeLinker:
    """pw-link on a worker thread, faked: links show up in the graph straight
    away, failures go to .failures. `pw` is a FakePipeWire or a function that
    returns the current one (so a harness can swap it per test)."""

    def __init__(self, pw):
        self._pw = pw
        self.failures = queue.Queue()

    @property
    def pw(self):
        return self._pw() if callable(self._pw) else self._pw

    def link(self, out_port, in_port):
        if not self.pw.pw_link(out_port, in_port):
            self.failures.put(f"pw-link --wait {out_port} {in_port}: no such port")

    def unlink(self, link_id):
        self.pw.pw_unlink(link_id)


def make_fake_capture(pw, channel_maps, rate=48000, app_name=None):
    """A stand-in for a pw-record capture class: Capture(node_name, label,
    channels, passive=False) with take() -> (peak, sumsq, frames, last_data)
    per channel and stop(). Levels come from whatever is linked into it (a
    sine at that peak). `pw` is a FakePipeWire or a function returning one;
    `channel_maps` maps a channel count to its channel names."""
    import numpy as np

    def graph():
        return pw() if callable(pw) else pw

    class FakeCapture:
        def __init__(self, node_name, label, channels, passive=False):
            self.node_name, self.label, self.channels, self.passive = node_name, label, channels, passive
            self.error = None
            self.last_data = 0.0
            self._last = time.monotonic()
            self._names = list(channel_maps[channels])
            self._pw = graph()
            self.nid = self._pw.capture_node(node_name, label, self._names, passive, app_name)

        def take(self):
            now = time.monotonic()
            frames = int((now - self._last) * rate)
            self._last = now
            levels = self._pw.levels_into(self.nid, self._names)
            if frames <= 0 or all(db is None for db in levels):
                z = np.zeros(self.channels)
                return z, z.copy(), 0, self.last_data
            amp = np.array([0.0 if db is None or db == -math.inf else 10 ** (db / 20) for db in levels])
            self.last_data = now
            return amp, (amp ** 2) / 2 * frames, frames, self.last_data

        def stop(self):
            self._pw.drop_node(self.nid)

    return FakeCapture
