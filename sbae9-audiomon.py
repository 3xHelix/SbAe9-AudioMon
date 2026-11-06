#!/usr/bin/env python3
"""SbAe9-AudioMon - lightweight PipeWire VU meters (Qt / PySide6).

Two modes:

* AE-9 mode (default): finds the Sound Blaster AE-9 nodes and wires meters to
  its output monitor, What U Hear loopback, Mic/Line In, Optical In and every
  app stream playing to it. Flags "SILENT" dropouts and keeps a timestamped
  debug log. Listen-only: never changes defaults, never moves app streams,
  never touches ALSA mixer controls.
* Manual mode: two meters (In / Out) with 2/4/6/8 unlinked input ports, to
  wire up yourself with Helvum, qpwgraph, pw-link...

Each meter is a PipeWire capture node created via `pw-record`.
"""

import argparse
import codecs
import json
import math
import os
import queue
import shutil
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime
from functools import lru_cache
from types import SimpleNamespace

import numpy as np
from PySide6.QtCore import QByteArray, QPointF, QRectF, QSignalBlocker, QSize, Qt, QTimer, QUrl
from PySide6.QtGui import (
    QColor, QDesktopServices, QFont, QFontDatabase, QIcon, QPainter, QPen, QPixmap,
)
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFrame, QHBoxLayout, QLabel, QPlainTextEdit,
    QPushButton, QRadioButton, QScrollArea, QSizePolicy, QStackedWidget, QToolButton,
    QVBoxLayout, QWidget,
)

RATE = 48000
BLOCK = 512  # frames per read / node latency
CHANNEL_CHOICES = [2, 4, 6, 8]
CHANNEL_MAPS = {
    2: ["FL", "FR"],
    4: ["FL", "FR", "RL", "RR"],
    6: ["FL", "FR", "FC", "LFE", "RL", "RR"],
    8: ["FL", "FR", "FC", "LFE", "RL", "RR", "SL", "SR"],
}
CHANNEL_ORDER = ["MONO", "FL", "FR", "FC", "LFE", "RL", "RR", "SL", "SR"]
FLOOR_DB = -70.0
VU_TAU = 0.3          # seconds, classic VU integration time
PEAK_FALL = 20.0      # dB per second
PEAK_HOLD = 2.0       # seconds
GREEN_MAX = -18.0     # colour zones (dBFS)
YELLOW_MAX = -6.0

# SILENT detection (AE-9 mode): the output monitor has signal but What U Hear
# stays below WUH_SILENT_DB for longer than SILENT_AFTER seconds.
MON_SIGNAL_DB = -50.0   # monitor counts as "has signal" above this (peak)
WUH_SILENT_DB = -60.0   # What U Hear counts as silent below this (peak)
SILENT_AFTER = 0.5      # seconds
WIRE_GRACE = 1.0        # ignore What U Hear for this long after (re)wiring it

DRIVER_MODULE = "snd_hda_codec_creative_ae9"

CONFIG_DIR = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
CONFIG_PATH = os.path.join(CONFIG_DIR, "sbae9-audiomon", "config.json")
STATE_DIR = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
LOG_PATH = os.path.join(STATE_DIR, "sbae9-audiomon", "ae9-debug.log")
DEFAULT_CONFIG = {
    "mode": "ae9",        # "ae9" or "manual"
    "let_idle": True,     # AE-9 mode: don't keep the card awake
    "always_capture": False,  # AE-9 mode + let_idle: keep What U Hear/Mic/Optical wired anyway
    "frameless": True,    # draw our own title bar, like Sound Blaster Command
    "page": 0,            # last page shown
    "meters": [
        {"name": "sbae9-audiomon.in", "label": "AudioMon In", "channels": 2},
        {"name": "sbae9-audiomon.out", "label": "AudioMon Out", "channels": 2},
    ],
}


def amp_db(a):
    return np.where(a > 1e-9, 20.0 * np.log10(np.maximum(a, 1e-9)), FLOOR_DB).clip(FLOOR_DB)


def pow_db(p):
    return np.where(p > 1e-18, 10.0 * np.log10(np.maximum(p, 1e-18)), FLOOR_DB).clip(FLOOR_DB)


def fmt_db(v):
    return "-inf" if v <= FLOOR_DB + 0.05 else f"{v:+.1f}" if v > 0 else f"{v:.1f}"


def html_escape(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def elide(s, n=30):
    return s if len(s) <= n else s[: n - 1] + "…"


def spa_str(s):
    """Make a string safe inside a quoted SPA-JSON property value."""
    return s.replace("\\", "/").replace('"', "'")


def child_cmd(cmd):
    """Make sure helper processes die with us even if we crash."""
    if shutil.which("setpriv"):
        return ["setpriv", "--pdeathsig", "TERM", "--"] + cmd
    return cmd


def choose_channels(n):
    for c in CHANNEL_CHOICES:
        if n <= c:
            return c
    return CHANNEL_CHOICES[-1]


def driver_info():
    base = f"/sys/module/{DRIVER_MODULE}"
    try:
        with open(f"{base}/version") as f:
            ver = f.read().strip()
    except OSError:
        if not os.path.isdir(base):
            return f"{DRIVER_MODULE} not loaded"
        ver = "?"
    try:
        with open(f"{base}/srcversion") as f:
            src = f.read().strip()
    except OSError:
        src = "?"
    return f"{DRIVER_MODULE} {ver} srcversion {src}"


OLD_CONFIG_PATH = os.path.join(CONFIG_DIR, "audioview", "config.json")  # pre-rename


def load_config():
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    try:
        if os.path.exists(CONFIG_PATH) or not os.path.exists(OLD_CONFIG_PATH):
            with open(CONFIG_PATH) as f:
                user = json.load(f)
        else:
            # First run after the AudioView -> SbAe9-AudioMon rename: keep the
            # settings but not the old node names/labels.
            with open(OLD_CONFIG_PATH) as f:
                user = json.load(f)
            for m in user.get("meters", []):
                m.pop("name", None)
                m.pop("label", None)
        for i, m in enumerate(user.get("meters", [])[: len(cfg["meters"])]):
            cfg["meters"][i].update({k: v for k, v in m.items() if k in ("name", "label", "channels")})
        if user.get("mode") in ("ae9", "manual"):
            cfg["mode"] = user["mode"]
        for k in ("let_idle", "always_capture", "frameless"):
            if isinstance(user.get(k), bool):
                cfg[k] = user[k]
        if isinstance(user.get("page"), int):
            cfg["page"] = user["page"]
    except (OSError, ValueError):
        pass
    for m in cfg["meters"]:
        if m["channels"] not in CHANNEL_CHOICES:
            m["channels"] = 2
    return cfg


def save_config(cfg):
    try:
        os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
        with open(CONFIG_PATH, "w") as f:
            json.dump(cfg, f, indent=2)
    except OSError as e:
        print(f"sbae9-audiomon: could not save config: {e}", file=sys.stderr)


# --------------------------------------------------------------------------
# Capture: one pw-record process = one PipeWire node
# --------------------------------------------------------------------------

class Capture:
    """Runs pw-record as an unlinked PipeWire node and accumulates levels.

    The reader thread adds up per-channel peak and sum-of-squares; the UI
    calls take() each frame to collect and reset them.

    passive=True sets node.passive so the meter never keeps whatever it is
    linked to awake: it only receives data while the peer runs anyway.
    """

    def __init__(self, node_name, label, channels, passive=False):
        self.node_name = node_name
        self.label = label
        self.channels = channels
        self.passive = passive
        self.lock = threading.Lock()
        self.error = None
        self.last_data = 0.0
        self._stopping = False
        self._proc = None
        self._clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _clear(self):
        self._peak = np.zeros(self.channels)
        self._sumsq = np.zeros(self.channels)
        self._frames = 0

    def take(self):
        with self.lock:
            out = (self._peak, self._sumsq, self._frames, self.last_data)
            self._clear()
        return out

    def _command(self):
        label = spa_str(self.label)
        props = (
            "{ "
            f'node.name = "{spa_str(self.node_name)}" '
            f'node.description = "{label}" '
            f'node.nick = "{label}" '
            f'media.name = "{label}" '
            'application.name = "SbAe9-AudioMon" '
            "stream.dont-remix = true "
            "state.restore-props = false "
            "state.restore-target = false "
            + ("node.passive = true " if self.passive else "")
            + "}"
        )
        return child_cmd([
            "pw-record", "--raw", "--format", "f32",
            "--rate", str(RATE),
            "--channels", str(self.channels),
            "--channel-map", ",".join(CHANNEL_MAPS[self.channels]),
            "--target", "0",
            "--latency", str(BLOCK),
            "-P", props,
            "-",
        ])

    def _run(self):
        n = self.channels
        chunk = BLOCK * n * 4
        while not self._stopping:
            try:
                proc = subprocess.Popen(self._command(), stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL)
            except FileNotFoundError:
                self.error = "pw-record not found (install pipewire-utils)"
                return
            self._proc = proc
            if self._stopping:
                proc.terminate()
            while True:
                data = proc.stdout.read(chunk)
                if not data:
                    break
                usable = len(data) - len(data) % (n * 4)
                if not usable:
                    continue
                a = np.frombuffer(data[:usable], dtype=np.float32).reshape(-1, n)
                pk = np.abs(a).max(axis=0)
                ss = np.square(a, dtype=np.float64).sum(axis=0)
                with self.lock:
                    np.maximum(self._peak, pk, out=self._peak)
                    self._sumsq += ss
                    self._frames += a.shape[0]
                    self.last_data = time.monotonic()
            proc.wait()
            if not self._stopping:
                self.error = "pw-record exited, restarting..."
                time.sleep(1.0)
                self.error = None

    def stop(self):
        self._stopping = True
        proc = self._proc
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()


# --------------------------------------------------------------------------
# PipeWire graph: watched with `pw-dump -m`, changed only with `pw-link`
# --------------------------------------------------------------------------

class Graph:
    """Keeps a live copy of the PipeWire object list from `pw-dump --monitor`."""

    def __init__(self):
        self.lock = threading.Lock()
        self.objects = {}
        self.version = 0
        self.error = None
        self._stopping = False
        self._proc = None
        threading.Thread(target=self._run, daemon=True).start()

    def snapshot(self):
        with self.lock:
            return self.version, list(self.objects.values())

    def _apply(self, arr):
        with self.lock:
            for o in arr if isinstance(arr, list) else [arr]:
                if not isinstance(o, dict) or "id" not in o:
                    continue
                if o.get("info") is None:
                    self.objects.pop(o["id"], None)
                else:
                    self.objects[o["id"]] = o
            self.version += 1

    def _run(self):
        dec = json.JSONDecoder()
        while not self._stopping:
            try:
                proc = subprocess.Popen(child_cmd(["pw-dump", "--monitor", "--no-colors"]),
                                        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            except FileNotFoundError:
                self.error = "pw-dump not found (install pipewire-utils)"
                return
            self._proc = proc
            with self.lock:
                self.objects = {}
                self.version += 1
            utf8 = codecs.getincrementaldecoder("utf-8")("replace")
            buf = ""
            fd = proc.stdout.fileno()
            while True:
                chunk = os.read(fd, 1 << 16)
                if not chunk:
                    break
                buf += utf8.decode(chunk)
                pos = 0
                while True:
                    while pos < len(buf) and buf[pos].isspace():
                        pos += 1
                    if pos >= len(buf):
                        break
                    try:
                        arr, pos = dec.raw_decode(buf, pos)
                    except json.JSONDecodeError:
                        break  # incomplete; wait for more
                    self._apply(arr)
                buf = buf[pos:]
                if len(buf) > 64 << 20:  # garbage we can't parse; start over
                    buf = ""
            proc.wait()
            if not self._stopping:
                time.sleep(1.0)

    def stop(self):
        self._stopping = True
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()


class Snapshot:
    """Nodes, ports and links parsed from a Graph snapshot."""

    def __init__(self, objects):
        self.nodes, self.ports, self.links = {}, {}, {}
        for o in objects:
            t = o.get("type", "")
            info = o.get("info") or {}
            props = info.get("props") or {}
            if t.endswith(":Node"):
                name = props.get("node.name", "")
                self.nodes[o["id"]] = SimpleNamespace(
                    id=o["id"], name=name, props=props,
                    desc=props.get("node.description") or props.get("node.nick") or name,
                    media_class=props.get("media.class", ""),
                    state=info.get("state", ""), error=info.get("error"))
            elif t.endswith(":Port"):
                direction = info.get("direction") or props.get("port.direction") or ""
                self.ports[o["id"]] = SimpleNamespace(
                    id=o["id"], node=props.get("node.id"), name=props.get("port.name", ""),
                    out=direction.startswith("out"), channel=props.get("audio.channel", ""),
                    monitor=bool(props.get("port.monitor")))
            elif t.endswith(":Link"):
                self.links[o["id"]] = SimpleNamespace(
                    id=o["id"], out_node=info.get("output-node-id"), out_port=info.get("output-port-id"),
                    in_node=info.get("input-node-id"), in_port=info.get("input-port-id"))
        self.node_ports = {}
        for p in self.ports.values():
            self.node_ports.setdefault(p.node, []).append(p)

    def by_name(self, name):
        for n in self.nodes.values():
            if n.name == name:
                return n
        return None

    def out_ports(self, node, monitor=False):
        ps = [p for p in self.node_ports.get(node.id, []) if p.out and p.monitor == monitor]
        return sorted(ps, key=lambda p: (CHANNEL_ORDER.index(p.channel)
                                         if p.channel in CHANNEL_ORDER else 99, p.id))


def is_ours(n):
    return n.props.get("application.name") == "SbAe9-AudioMon" or n.name.startswith("sbae9-audiomon.")


def is_ae9(n):
    blob = " ".join(str(n.props.get(k, "")) for k in (
        "node.name", "api.alsa.path", "object.path", "alsa.card_name",
        "api.alsa.card.name", "device.name")).lower()
    return "ae9" in blob or "ae-9" in blob


def find_ae9(snap):
    """Locate the AE-9 nodes. Returns dict key -> node (or None)."""
    found = {"monitor": None, "wuh": None, "mic": None, "optical": None}
    for n in sorted(snap.nodes.values(), key=lambda n: n.id):
        if is_ours(n) or not is_ae9(n):
            continue
        name, desc = n.name.lower(), n.desc.lower()
        if n.media_class == "Audio/Sink":
            if found["monitor"] is None or ("analog" in name and "analog" not in found["monitor"].name):
                found["monitor"] = n
        elif n.media_class == "Audio/Source":
            if "what_u_hear" in name or "what u hear" in desc:
                found["wuh"] = found["wuh"] or n
            elif "optical" in name or "optical" in desc or "iec958" in name:
                found["optical"] = found["optical"] or n
            elif found["mic"] is None or ("analog" in name and "analog" not in found["mic"].name):
                found["mic"] = n
    return found


class Linker:
    """Runs pw-link commands on a worker thread so the UI never blocks."""

    def __init__(self):
        self.q = queue.Queue()
        self.failures = queue.Queue()
        threading.Thread(target=self._run, daemon=True).start()

    def link(self, out_port, in_port):
        self.q.put(["pw-link", "--wait", str(out_port), str(in_port)])

    def unlink(self, link_id):
        self.q.put(["pw-link", "--disconnect", str(link_id)])

    def _run(self):
        while True:
            cmd = self.q.get()
            try:
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
                if r.returncode:
                    self.failures.put(f"{' '.join(cmd)}: {r.stderr.strip() or r.returncode}")
            except (OSError, subprocess.TimeoutExpired) as e:
                self.failures.put(f"{' '.join(cmd)}: {e}")


class EventLog:
    """Timestamped (local time, to the second) log: file + on-screen view."""

    def __init__(self, path):
        self.path = path
        self.views = []
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            self.file = open(path, "a", buffering=1)
        except OSError as e:
            print(f"sbae9-audiomon: cannot open log {path}: {e}", file=sys.stderr)
            self.file = None

    def __call__(self, msg):
        line = f"{datetime.now():%Y-%m-%d %H:%M:%S}  {msg}"
        if self.file:
            self.file.write(line + "\n")
        print(line, flush=True)
        for v in self.views:
            bar = v.verticalScrollBar()
            follow = bar.value() >= bar.maximum() - 4   # keep following the newest line
            v.appendPlainText(line)
            if follow:
                bar.setValue(bar.maximum())

    def add_view(self, view, backfill=False):
        """Show new lines in `view`; backfill=True first loads the file's tail."""
        if backfill:
            try:
                with open(self.path, errors="replace") as f:
                    tail = f.readlines()[-500:]
                view.setPlainText("".join(tail).rstrip("\n"))
                view.moveCursor(view.textCursor().MoveOperation.End)
            except OSError:
                pass
        self.views.append(view)

    def remove_view(self, view):
        if view in self.views:
            self.views.remove(view)


# --------------------------------------------------------------------------
# Meter state (ballistics + stats)
# --------------------------------------------------------------------------

class MeterState:
    def __init__(self, channels):
        self.channels = channels
        self.names = CHANNEL_MAPS[channels]
        self.rms_pow = np.zeros(channels)          # VU-smoothed power
        self.peak_db = np.full(channels, FLOOR_DB)  # falling peak bar
        self.hold_db = np.full(channels, FLOOR_DB)  # peak hold marker
        self.hold_t = np.zeros(channels)
        self.reset_stats()

    def reset_stats(self):
        self.max_amp = np.zeros(self.channels)
        self.tot_ss = np.zeros(self.channels)
        self.tot_frames = 0
        self.clipped = np.zeros(self.channels, dtype=bool)

    def update(self, peak, sumsq, frames, last_data, now, dt):
        if frames == 0 and now - last_data < 0.2:
            return  # stream is live, block just hasn't arrived yet
        inst_pow = sumsq / frames if frames else np.zeros(self.channels)
        self.rms_pow += (inst_pow - self.rms_pow) * (1.0 - math.exp(-dt / VU_TAU))

        pk_db = amp_db(peak)
        self.peak_db = np.maximum(pk_db, self.peak_db - PEAK_FALL * dt)
        new_hold = pk_db >= self.hold_db
        self.hold_db = np.where(new_hold, pk_db, self.hold_db)
        self.hold_t = np.where(new_hold, now, self.hold_t)
        expired = (now - self.hold_t) > PEAK_HOLD
        self.hold_db = np.where(expired, np.maximum(self.hold_db - PEAK_FALL * dt, self.peak_db),
                                self.hold_db)

        if frames:
            self.max_amp = np.maximum(self.max_amp, peak)
            self.clipped |= peak >= 1.0
            self.tot_ss += sumsq
            self.tot_frames += frames

    @property
    def rms_db(self):
        return pow_db(self.rms_pow)

    @property
    def avg_db(self):
        if not self.tot_frames:
            return np.full(self.channels, FLOOR_DB)
        return pow_db(self.tot_ss / self.tot_frames)


# --------------------------------------------------------------------------
# Theme: the "Helix" palette and styling of SbAe9-SBC (Sound Blaster Command
# for Linux), so the two apps look like a set.
# --------------------------------------------------------------------------

class T:
    ACCENT, ACCENT_HI, ACCENT_TEXT = "#f57a45", "#ffb63a", "#1b1230"
    TEXT, MUTED, DIM = "#f2edff", "#ab9fcb", "#6d6190"
    TRACK, PANEL, PANEL_HI, PANEL_LO = "#3a2f5a", "#3b2e5e", "#4b3b74", "#2e2449"
    TITLE_BG, BOTTOM_BG = "#120f21", "#110e1f"
    SIDEBAR_TOP, SIDEBAR_BOTTOM = "#2a1850", "#3a1452"
    MAIN_TOP, MAIN_BOTTOM = "#1d1832", "#26183f"
    POPUP, POPUP_BORDER, LIST_BG, TOOLTIP = "#16122a", "#3b2f5c", "#211b38", "#2a2145"
    IDLE = "#7a6c9e"
    GRAPH_BG, GRAPH_TEXT = "#241d3d", "#d9d0f0"
    OK, WARN, DANGER = "#5dcaa8", "#ffb63a", "#e2505c"
    METER = ("#5dcaa8", "#ffb63a", "#e31b49")   # green / yellow / red zones
    OVERLAY, SHADE, HANDLE = "#ffffff", "#000000", "#f2f2f6"


FONT_FAMILIES = ["Roboto Condensed", "Barlow Semi Condensed", "Noto Sans", "DejaVu Sans Condensed"]


def rgba(hex_color, alpha):
    h = hex_color.lstrip("#")
    return f"rgba({int(h[0:2], 16)},{int(h[2:4], 16)},{int(h[4:6], 16)},{alpha})"


def qcolor(hex_color, alpha=1.0):
    c = QColor(hex_color)
    c.setAlphaF(alpha)
    return c


def mono_font(px):
    f = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
    f.setPixelSize(px)
    return f


def ui_font(px, bold=False):
    f = QFont()
    f.setFamilies(FONT_FAMILIES)
    f.setPixelSize(px)
    f.setBold(bold)
    return f


_S = 'fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"'
ICONS = {
    "wave": f'<path {_S} d="M2 12h2l2-6 3 12 3-15 3 15 3-12 2 6h2"/>',
    "log": f'<path {_S} d="M8 6h13M8 12h13M8 18h13"/><circle cx="3.5" cy="6" r="1.2" fill="currentColor"/>'
           f'<circle cx="3.5" cy="12" r="1.2" fill="currentColor"/><circle cx="3.5" cy="18" r="1.2" fill="currentColor"/>',
    "mixer": f'<path {_S} d="M3 6h18M3 12h18M3 18h18"/><circle {_S} cx="15" cy="12" r="2"/>'
             f'<circle {_S} cx="10" cy="18" r="2"/><circle {_S} cx="8" cy="6" r="2"/>',
    "settings": f'<circle {_S} cx="12" cy="12" r="3"/><path {_S} d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>',
    "refresh": f'<path {_S} d="M20 11a8 8 0 1 0-2.3 5.7"/><path {_S} d="M20 4v7h-7"/>',
    "folder": f'<path {_S} d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
    "minimize": f'<path {_S} d="M6 12.5h12"/>',
    "maximize": f'<rect {_S} x="6" y="6" width="12" height="12" rx="1.5"/>',
    "close": f'<path {_S} d="M6.5 6.5l11 11M17.5 6.5l-11 11"/>',
    "chevron-down": f'<path {_S} d="M5 9l7 7 7-7"/>',
    "check": f'<path {_S} d="M5 12.5l4.5 4.5L19 7.5"/>',
}


@lru_cache(maxsize=256)
def icon_pixmap(name, color, size=22, ratio=2.0):
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
           + ICONS[name].replace("currentColor", color) + "</svg>")
    r = QSvgRenderer(QByteArray(svg.encode()))
    px = QPixmap(int(size * ratio), int(size * ratio))
    px.fill(Qt.GlobalColor.transparent)
    p = QPainter(px)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    r.render(p, QRectF(0, 0, px.width(), px.height()))
    p.end()
    px.setDevicePixelRatio(ratio)
    return px


def make_icon(name, color=None, active=None, size=22):
    color = color or T.TEXT
    ic = QIcon()
    ic.addPixmap(icon_pixmap(name, color, size), QIcon.Mode.Normal, QIcon.State.Off)
    ic.addPixmap(icon_pixmap(name, active or color, size), QIcon.Mode.Normal, QIcon.State.On)
    ic.addPixmap(icon_pixmap(name, active or color, size), QIcon.Mode.Active, QIcon.State.Off)
    ic.addPixmap(icon_pixmap(name, T.DIM, size), QIcon.Mode.Disabled, QIcon.State.Off)
    return ic


def app_icon():
    """The app icon: from the icon theme (install.sh puts it there), else the
    PNGs installed or next to this file, else a drawn one."""
    ic = QIcon.fromTheme("sbae9-audiomon")
    if not ic.isNull():
        return ic
    ic = QIcon()
    here = os.path.dirname(os.path.abspath(__file__))
    hicolor = os.path.join(os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"),
                           "icons", "hicolor")
    for size in (16, 24, 32, 48, 64, 128, 256, 512):
        for path in (os.path.join(hicolor, f"{size}x{size}", "apps", "sbae9-audiomon.png"),
                     os.path.join(here, "appicon", f"sbae9-audiomon-{size}.png")):
            if os.path.exists(path):
                ic.addFile(path, QSize(size, size))
                break
    return ic if not ic.isNull() else QIcon(icon_pixmap("wave", T.ACCENT_HI, 64))


def stylesheet():
    cache = os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"),
                         "sbae9-audiomon")
    os.makedirs(cache, exist_ok=True)
    img = {}
    for name, color in (("chevron-down", T.TEXT), ("check", T.ACCENT_TEXT)):
        path = os.path.join(cache, f"qss-{name}-{color.lstrip('#')}.png")
        if not os.path.exists(path):
            icon_pixmap(name, color, 16, 2.0).save(path)
        img[name] = path
    fams = ", ".join(f'"{f}"' for f in FONT_FAMILIES)
    return f"""
* {{ font-family: {fams}, sans-serif; color: {T.TEXT}; font-size: 14px; }}
#root {{ background: {T.MAIN_TOP}; }}
#titleBar {{ background: {T.TITLE_BG}; }}
#winButton, #winClose {{ background: transparent; border: none; border-radius: 3px; }}
#winButton:hover {{ background: {rgba(T.OVERLAY, 0.10)}; }}
#winClose:hover {{ background: {T.DANGER}; }}
#appTitle {{ font-size: 16px; color: {T.TEXT}; }}
#deviceName {{ font-size: 17px; font-weight: 700; color: {T.ACCENT}; }}
#sidebar {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {T.SIDEBAR_TOP}, stop:1 {T.SIDEBAR_BOTTOM});
}}
#sidebar QPushButton {{
    background: transparent; border: none; border-left: 3px solid transparent; border-radius: 0;
    text-align: left; padding: 11px 16px; font-size: 16px;
}}
#sidebar QPushButton:hover {{ background: {rgba(T.OVERLAY, 0.06)}; }}
#sidebar QPushButton:checked {{
    color: {T.ACCENT_HI}; border-left: 3px solid {T.ACCENT}; background: {rgba(T.OVERLAY, 0.06)};
}}
#sideCaption {{ color: {T.MUTED}; font-size: 13px; padding-left: 18px; }}
#sep {{ background: {rgba(T.OVERLAY, 0.13)}; max-height: 1px; min-height: 1px; margin: 4px 10px; }}
#content {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {T.MAIN_TOP}, stop:1 {T.MAIN_BOTTOM});
}}
#bottomBar {{ background: {T.BOTTOM_BG}; }}
#pageTitle {{ font-size: 23px; font-weight: 700; }}
#sectionTitle {{ font-size: 15px; font-weight: 700; }}
#cardTitle {{ font-size: 15px; font-weight: 700; }}
#muted, #hint {{ color: {T.MUTED}; font-size: 13px; }}
#bigValue {{ font-size: 15px; font-weight: 700; }}
#profileLabel {{ font-size: 20px; font-weight: 600; }}
#logo {{ font-size: 26px; font-weight: 900; font-style: italic; color: {T.TEXT}; letter-spacing: 1px; }}
#silentBadge {{
    background: {T.DANGER}; color: #ffffff; font-size: 15px; font-weight: 800; letter-spacing: 1px;
    padding: 3px 12px; border-radius: 4px;
}}
#banner {{ background: {rgba(T.ACCENT, 0.16)}; border: 1px solid {rgba(T.ACCENT, 0.55)}; border-radius: 4px; }}
#card {{ background: {rgba(T.SHADE, 0.10)}; border-radius: 6px; }}
QToolTip {{ background: {T.TOOLTIP}; color: {T.TEXT}; border: 1px solid {T.POPUP_BORDER}; padding: 4px; }}
QPushButton {{ background: {T.PANEL}; border: none; padding: 7px 18px; border-radius: 2px; }}
QPushButton:hover {{ background: {T.PANEL_HI}; }}
QPushButton:pressed {{ background: {T.PANEL_LO}; }}
QToolButton {{ background: transparent; border: none; padding: 4px; border-radius: 3px; }}
QToolButton:hover {{ background: {rgba(T.OVERLAY, 0.08)}; }}
QComboBox {{
    background: {T.PANEL}; border: none; padding: 5px 10px; border-radius: 2px; min-height: 20px;
    font-size: 14px; font-weight: 600;
}}
QComboBox:hover {{ background: {T.PANEL_HI}; }}
QComboBox::drop-down {{ border: none; width: 28px; }}
QComboBox::down-arrow {{ image: url({img["chevron-down"]}); width: 14px; height: 14px; }}
QComboBox QAbstractItemView {{
    background: {T.LIST_BG}; border: 1px solid {T.POPUP_BORDER}; selection-background-color: {T.ACCENT};
    selection-color: {T.ACCENT_TEXT}; outline: none; padding: 4px;
}}
QCheckBox {{ spacing: 10px; font-size: 15px; }}
QCheckBox:disabled {{ color: {T.DIM}; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border: 2px solid {T.TEXT}; border-radius: 2px; }}
QCheckBox::indicator:disabled {{ border-color: {T.DIM}; }}
QCheckBox::indicator:checked {{ background: {T.ACCENT}; border-color: {T.ACCENT}; image: url({img["check"]}); }}
#quickToggle {{ spacing: 7px; font-size: 13px; color: {T.MUTED}; }}
#quickToggle:checked {{ color: {T.TEXT}; }}
#quickToggle:disabled {{ color: {T.DIM}; }}
#quickToggle::indicator {{
    width: 12px; height: 12px; border-radius: 8px; border: 2px solid {T.MUTED}; background: transparent;
}}
#quickToggle::indicator:checked {{
    border: 2px solid {T.ACCENT}; background: qradialgradient(cx:0.5, cy:0.5, radius:0.5, fx:0.5, fy:0.5,
        stop:0 {T.ACCENT}, stop:0.55 {T.ACCENT}, stop:0.6 transparent);
}}
#quickToggle::indicator:disabled {{ border-color: {T.DIM}; }}
QPlainTextEdit {{
    background: {rgba(T.SHADE, 0.18)}; border: none; border-radius: 6px; padding: 6px;
    color: {T.GRAPH_TEXT}; selection-background-color: {T.ACCENT}; selection-color: {T.ACCENT_TEXT};
}}
QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; border: none; }}
QScrollBar:vertical, QScrollBar:horizontal {{ background: transparent; width: 10px; height: 10px; }}
QScrollBar::handle {{ background: {rgba(T.OVERLAY, 0.18)}; border-radius: 4px; min-height: 30px; min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
"""


def label(text, name=None, wrap=False):
    lbl = QLabel(text)
    if name:
        lbl.setObjectName(name)
    lbl.setWordWrap(wrap)
    return lbl


def hline():
    sep = QFrame()
    sep.setObjectName("sep")
    return sep


# --------------------------------------------------------------------------
# Meter geometry + drawing
# --------------------------------------------------------------------------

SCALE_W = 40
COL_W = 50
PAIR_GAP = 14
PAD = 8
TOP = 32
ROW_H = 15
STAT_ROWS = ("Peak", "RMS", "Avg", "Max")
STATS_H = len(STAT_ROWS) * ROW_H + 10
TICKS = (0, -3, -6, -10, -18, -30, -40, -50, -60)
COL_MAX = 120   # widest a column gets when the window is stretched
BAR_MAX = 60    # widest a bar gets
ZONES = (
    (FLOOR_DB, GREEN_MAX, 0),
    (GREEN_MAX, YELLOW_MAX, 1),
    (YELLOW_MAX, 0.0, 2),
)


def iec_scale(db):
    """IEC 60268-18 style deflection, 0..1 (more resolution near 0 dB)."""
    if db < -70:
        d = 0.0
    elif db < -60:
        d = (db + 70) * 0.25
    elif db < -50:
        d = (db + 60) * 0.5 + 2.5
    elif db < -40:
        d = (db + 50) * 0.75 + 7.5
    elif db < -30:
        d = (db + 40) * 1.5 + 15
    elif db < -20:
        d = (db + 30) * 2.0 + 30
    elif db < 0:
        d = (db + 20) * 2.5 + 50
    else:
        d = 100.0
    return d / 100.0


def panel_width(n):
    return SCALE_W + PAD + n * COL_W + (n // 2 - 1) * PAIR_GAP + PAD


def col_width(n, w):
    """Column width that fills the available width (between COL_W and COL_MAX)."""
    extra = max(0, w - panel_width(n))
    return min(COL_W + extra / n, COL_MAX)


def col_x(i, cw=COL_W):
    return SCALE_W + PAD + i * cw + (i // 2) * PAIR_GAP


class MeterWidget(QWidget):
    """Draws one meter: scale, bars, clip LEDs and the stats rows."""

    def __init__(self, panel):
        super().__init__()
        self.panel = panel
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setToolTip("Click to reset stats")
        self.f_tick = ui_font(10)
        self.f_name = ui_font(12, bold=True)
        self.f_val = ui_font(12)
        self.f_row = ui_font(11)

    def sizeHint(self):
        return QSize(panel_width(self.panel.state.channels), 330)

    def minimumSizeHint(self):
        return QSize(panel_width(self.panel.state.channels), 220)

    def mousePressEvent(self, _event):
        self.panel.reset()

    def paintEvent(self, _event):
        st = self.panel.state
        w, h = self.width(), self.height()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(qcolor(T.GRAPH_BG))
        p.drawRoundedRect(QRectF(0, 0, w, h), 6, 6)
        if self.panel.alert:
            p.setBrush(qcolor(T.DANGER, 0.22))
            p.drawRoundedRect(QRectF(0, 0, w, h), 6, 6)

        bar_top = TOP
        bar_bot = h - STATS_H - 4
        bar_h = max(bar_bot - bar_top, 10)

        def y_of(db):
            return bar_bot - iec_scale(db) * bar_h

        def text(s, x, y, align="left", font=None, color=T.GRAPH_TEXT):
            # (x, y) is the baseline anchor
            p.setFont(font or self.f_val)
            p.setPen(QColor(color))
            box = QRectF(x - 200, y - 16, 400, 20)
            if align == "left":
                box.moveLeft(x)
                flags = Qt.AlignmentFlag.AlignLeft
            elif align == "right":
                box.moveRight(x)
                flags = Qt.AlignmentFlag.AlignRight
            else:
                flags = Qt.AlignmentFlag.AlignHCenter
            p.drawText(box, flags | Qt.AlignmentFlag.AlignBottom, s)

        # Scale + grid; centre the whole meter when the card is wider than it
        cw = col_width(st.channels, w)
        right = col_x(st.channels - 1, cw) + cw
        p.translate(max(0.0, (w - right - PAD) / 2), 0)
        last_label_y = -100
        for t in TICKS:
            y = round(y_of(t)) + 0.5
            if y - last_label_y >= 12:   # skip labels that would overlap
                text(f"{t}", SCALE_W - 4, y + 5, "right", self.f_tick, T.MUTED)
                last_label_y = y
            p.setPen(QPen(qcolor(T.OVERLAY, 0.16 if t in (0, -18) else 0.07), 1))
            p.drawLine(QPointF(SCALE_W, y), QPointF(right, y))

        rms_db = st.rms_db
        avg_db = st.avg_db
        max_db = amp_db(st.max_amp)
        for i in range(st.channels):
            x0 = col_x(i, cw)
            cx = x0 + cw / 2
            bw = min(cw - 16, BAR_MAX)
            bx = cx - bw / 2

            text(st.names[i], cx, 15, "center", self.f_name, T.TEXT)

            p.setPen(Qt.PenStyle.NoPen)
            # Clip LED (latched until reset)
            p.setBrush(qcolor(T.DANGER) if st.clipped[i] else qcolor(T.DANGER, 0.16))
            p.drawRoundedRect(QRectF(bx, 19, bw, 6), 2, 2)

            # Bar background
            p.setBrush(qcolor(T.TRACK))
            p.drawRoundedRect(QRectF(bx, bar_top, bw, bar_h), 2, 2)

            def fill_level(level, alpha):
                for lo, hi, zone in ZONES:
                    if level <= lo:
                        break
                    top = y_of(min(level, hi))
                    p.fillRect(QRectF(bx, top, bw, y_of(lo) - top), qcolor(T.METER[zone], alpha))

            fill_level(st.peak_db[i], 0.35)  # peak: translucent
            fill_level(rms_db[i], 1.0)       # VU/RMS: solid

            if st.hold_db[i] > FLOOR_DB + 0.5:
                y = round(y_of(st.hold_db[i]))
                p.fillRect(QRectF(bx, y - 1, bw, 2), qcolor(T.HANDLE))

            # Stats
            vals = (st.hold_db[i], rms_db[i], avg_db[i], max_db[i])
            for r, v in enumerate(vals):
                y = bar_bot + 17 + r * ROW_H
                color = T.DANGER if (r == 3 and st.clipped[i]) else T.GRAPH_TEXT
                text(fmt_db(v), cx, y, "center", self.f_val, color)

        for r, name in enumerate(STAT_ROWS):
            text(name, 6, bar_bot + 17 + r * ROW_H, "left", self.f_row, T.MUTED)
        p.end()


# --------------------------------------------------------------------------
# Meter panels and views
# --------------------------------------------------------------------------

class MeterPanel(QFrame):
    """A card: header (title, status, optional channel picker, reset) + meter."""

    def __init__(self, title, node_name, channels, subtitle=None, passive=False,
                 on_channels=None, fit=None, tooltip=None):
        super().__init__()
        self.setObjectName("card")
        self.title_text = title
        self.node_name = node_name
        self.passive = passive
        self.on_channels = on_channels
        self.fit = fit
        self.capture = None
        self.live = False
        self.alert = False
        self.note = None            # status text override (set by the AE-9 view)
        self.tick_peak_db = None    # loudest sample this tick, None if no data
        self._status_html = None

        sub = subtitle if subtitle is not None else node_name
        titles = QVBoxLayout()
        titles.setSpacing(0)
        titles.addWidget(label(elide(title), "cardTitle"))
        titles.addWidget(label(elide(sub, 34), "muted"))
        tip = tooltip or f"{title}\n{sub}\nmeter node: {node_name}"
        for i in range(titles.count()):
            titles.itemAt(i).widget().setToolTip(tip)

        self.status = QLabel()
        self.status.setTextFormat(Qt.TextFormat.RichText)

        header = QHBoxLayout()
        header.setSpacing(8)
        header.addLayout(titles, 1)
        header.addWidget(self.status)

        if on_channels:
            self.combo = QComboBox()
            self.combo.addItems([f"{c} ch" for c in CHANNEL_CHOICES])
            self.combo.setToolTip("Channel count (recreates the PipeWire node; links are dropped)")
            self.combo.setCurrentIndex(CHANNEL_CHOICES.index(channels))
            self.combo.currentIndexChanged.connect(self._on_combo)
            header.addWidget(self.combo)

        reset = QToolButton()
        reset.setIcon(make_icon("refresh", T.MUTED, active=T.TEXT, size=18))
        reset.setIconSize(QSize(18, 18))
        reset.setToolTip("Reset stats and clip")
        reset.clicked.connect(self.reset)
        header.addWidget(reset, 0, Qt.AlignmentFlag.AlignTop)

        self.state = MeterState(channels)
        self.meter = MeterWidget(self)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setSpacing(8)
        layout.addLayout(header)
        layout.addWidget(self.meter, 1)

        self._started = False
        self.start(channels)
        self._started = True

    @property
    def channels(self):
        return self.state.channels

    def start(self, channels):
        if self.capture:
            self.capture.stop()
        self.capture = Capture(self.node_name, self.title_text, channels, self.passive)
        self.state = MeterState(channels)
        self.meter.updateGeometry()
        if self.fit and self._started:
            self.fit()

    def stop(self):
        if self.capture:
            self.capture.stop()

    def reset(self):
        self.state.reset_stats()
        self.meter.update()

    def _on_combo(self, idx):
        ch = CHANNEL_CHOICES[idx]
        if ch != self.state.channels:
            self.start(ch)
            self.on_channels(ch)

    def tick(self, now, dt):
        peak, sumsq, frames, last = self.capture.take()
        self.state.update(peak, sumsq, frames, last, now, dt)
        self.tick_peak_db = float(amp_db(peak).max()) if frames else None
        self.live = now - last < 0.5
        if self.capture.error:
            html = f'<span style="color:{T.DANGER}">{html_escape(self.capture.error)}</span>'
        elif self.note:
            html = f'<span style="color:{T.MUTED}">{html_escape(self.note)}</span>'
        elif self.live:
            html = f'<span style="color:{T.OK}">● live</span>'
        else:
            html = f'<span style="color:{T.IDLE}">○ idle</span>'
        html = f'<span style="font-size:13px">{html}</span>'
        if html != self._status_html:
            self.status.setText(html)
            self._status_html = html
        self.meter.update()


class ManualView(QWidget):
    """The original two meters with unlinked ports, wired by hand."""

    def __init__(self, win):
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)
        self.panels = []
        for m in win.cfg["meters"]:
            def on_channels(ch, m=m):
                m["channels"] = ch
                save_config(win.cfg)
            p = MeterPanel(m["label"], m["name"], m["channels"],
                           on_channels=on_channels, fit=win.fit_window)
            self.panels.append(p)
            layout.addWidget(p)

    def tick(self, now, dt):
        for p in self.panels:
            p.tick(now, dt)

    def stop(self):
        for p in self.panels:
            p.stop()


FIXED_TAPS = (
    # key, title, meter node name, gated (only wired while the AE-9 sink runs
    # when "let the AE-9 idle" is on, so we never hold its capture PCMs open)
    ("monitor", "AE-9 Output (monitor)", "sbae9-audiomon.ae9.monitor", False),
    ("wuh", "AE-9 What U Hear", "sbae9-audiomon.ae9.wuh", True),
    ("mic", "AE-9 Mic / Line In", "sbae9-audiomon.ae9.mic", True),
    ("optical", "AE-9 Optical In", "sbae9-audiomon.ae9.optical", True),
)


class SilenceDetector:
    """SILENT = output monitor has signal but What U Hear stays quiet."""

    def __init__(self, log, on_change):
        self.log = log
        self.on_change = on_change
        self.active = False
        self.count = 0
        self.mon_last_loud = -1e9
        self.mon_loud_since = 0.0
        self.wuh_last_loud = -1e9
        self.wuh_last_data = -1e9
        self.started = 0.0
        self.worst_wuh = None

    def update(self, now, mon_db, wuh_db, wuh_ready_since, sink_state):
        if mon_db is not None and mon_db > MON_SIGNAL_DB:
            if now - self.mon_last_loud > 0.25:
                self.mon_loud_since = now
            self.mon_last_loud = now
        if wuh_db is not None:
            self.wuh_last_data = now
            if wuh_db > WUH_SILENT_DB:
                self.wuh_last_loud = now
        mon_on = now - self.mon_last_loud < 0.25

        silent = False
        if mon_on and wuh_ready_since is not None:
            onset = max(self.mon_loud_since, self.wuh_last_loud, wuh_ready_since + WIRE_GRACE)
            silent = now - onset > SILENT_AFTER

        if silent and not self.active:
            self.active = True
            self.count += 1
            self.started = onset
            no_data = now - self.wuh_last_data > SILENT_AFTER
            wuh = "no data" if no_data else f"{fmt_db(wuh_db if wuh_db is not None else FLOOR_DB)} dBFS"
            self.log(f"SILENT #{self.count} start: output monitor {fmt_db(mon_db or FLOOR_DB)} dBFS, "
                     f"What U Hear {wuh} for {now - onset:.1f}s | sink {sink_state} | {driver_info()}")
            self.on_change()
        elif not silent and self.active:
            self.active = False
            if sink_state != "running":
                why = f"playback stopped, AE-9 sink {sink_state}"
            elif not mon_on:
                why = "output monitor went quiet"
            elif wuh_ready_since is None:
                why = "What U Hear unwired/absent"
            else:
                why = f"What U Hear back at {fmt_db(wuh_db if wuh_db is not None else FLOOR_DB)} dBFS"
            self.log(f"SILENT #{self.count} end after {now - self.started:.1f}s ({why})")
            self.on_change()


class AE9View(QWidget):
    """Auto-wires meters to the AE-9 and watches for dropouts."""

    def __init__(self, win):
        super().__init__()
        self.win = win
        self.log = win.log
        self.let_idle = win.cfg["let_idle"]
        self.always_capture = win.cfg["always_capture"]
        self.graph = Graph()
        self.linker = Linker()
        self.seen_version = -1
        self.last_reconcile = 0.0
        self.pending = {}           # (out_port, in_port) or ("rm", link_id) -> time queued
        self.present = {}           # tap key -> node id
        self.wired = {}             # tap key -> bool
        self.sink_state = None
        self.wuh_ready_since = None
        self.reported_failures = set()
        self.silence = SilenceDetector(self.log, self._silence_changed)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        row = QHBoxLayout()
        row.setSpacing(14)
        self.fixed = {}
        for key, title, node_name, _gated in FIXED_TAPS:
            passive = self.let_idle and key == "monitor"
            p = MeterPanel(title, node_name, 2, passive=passive, fit=win.fit_window)
            p.note = "searching…"
            self.fixed[key] = p
            row.addWidget(p)
        layout.addLayout(row, 5)

        layout.addWidget(label("App streams → AE-9", "sectionTitle"))
        self.streams = {}           # stream node id -> (panel, started_at, title)
        self.streams_box = QHBoxLayout()
        self.streams_box.setContentsMargins(0, 0, 0, 0)
        self.streams_box.setSpacing(14)
        self.no_streams = label("No apps are playing to the AE-9.", "hint")
        self.streams_box.addWidget(self.no_streams)
        self.streams_box.addStretch(1)
        holder = QWidget()
        holder.setLayout(self.streams_box)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(holder)
        self.streams_scroll = scroll
        layout.addWidget(scroll, 4)
        self._size_streams()

        layout.addWidget(label("Recent events", "sectionTitle"))
        self.logview = log_view(80)
        self.logview.setMaximumHeight(120)
        layout.addWidget(self.logview)
        self.log.add_view(self.logview)

        self.log(f"SbAe9-AudioMon AE-9 mode started (let the AE-9 idle: "
                 f"{'on' if self.let_idle else 'off'}, always capture inputs: "
                 f"{'on' if self.always_capture else 'off'}) | {driver_info()}")

    # -- per-frame -------------------------------------------------------

    def tick(self, now, dt):
        for p in self.fixed.values():
            p.tick(now, dt)
        for p, _, _ in self.streams.values():
            p.tick(now, dt)

        version, _ = self.graph.version, None
        if (version != self.seen_version and now - self.last_reconcile > 0.15) or \
                now - self.last_reconcile > 1.0:
            self.seen_version = version
            self.last_reconcile = now
            _, objs = self.graph.snapshot()
            if objs:
                self.reconcile(Snapshot(objs), now)

        while not self.linker.failures.empty():
            msg = self.linker.failures.get()
            if msg not in self.reported_failures:
                self.reported_failures.add(msg)
                self.log(f"pw-link failed: {msg}")

        self.silence.update(now, self.fixed["monitor"].tick_peak_db, self.fixed["wuh"].tick_peak_db,
                            self.wuh_ready_since, self.sink_state or "absent")

    def _silence_changed(self):
        self.fixed["wuh"].alert = self.silence.active
        self.win.set_silent(self.silence.active, self.silence.count)

    # -- graph reconciliation ------------------------------------------------

    def reconcile(self, snap, now):
        found = find_ae9(snap)
        sink = found["monitor"]

        # Card nodes coming and going
        for key, title, _, _ in FIXED_TAPS:
            node = found[key]
            cur = node.id if node else None
            if key not in self.present:
                self.log(f"{title}: " + (f"found {node.name} (id {node.id})" if node else "not found"))
            elif cur != self.present[key]:
                if node:
                    extra = f" | {driver_info()}" if key == "monitor" else ""
                    self.log(f"{title}: appeared {node.name} (id {node.id}){extra}")
                else:
                    self.log(f"{title}: disappeared")
            self.present[key] = cur

        state = sink.state if sink else None
        if state != self.sink_state:
            if sink:
                self.log(f"AE-9 sink state: {self.sink_state or '-'} → {state}"
                         + (f" (error: {sink.error})" if sink.error else ""))
            self.sink_state = state
            self.win.set_sink_state(state)
        sink_running = state == "running"

        # Fixed taps
        for key, title, _, gated in FIXED_TAPS:
            panel = self.fixed[key]
            node = found[key]
            if node is None:
                panel.note = "no signal" if key == "optical" else "not found"
                self._set_wired(key, title, False, "absent")
                continue
            src = snap.out_ports(node, monitor=(key == "monitor"))
            want = choose_channels(len(src)) if src else panel.channels
            if want != panel.channels:
                panel.start(want)
            hold_off = self.let_idle and gated and not sink_running and not self.always_capture
            wired = self._wire(snap, panel, src, [] if hold_off else None, now)
            if key == "optical" and (node.state == "error" or (wired and not panel.live)):
                panel.note = "no signal"
            elif hold_off:
                panel.note = "unwired (AE-9 idle)"
            else:
                panel.note = None if wired else "wiring…"
            self._set_wired(key, title, wired, f"AE-9 sink {state}" if hold_off else "")

        wuh_ok = self.wired.get("wuh") and found["wuh"] is not None
        if wuh_ok and self.wuh_ready_since is None:
            self.wuh_ready_since = now
        elif not wuh_ok:
            self.wuh_ready_since = None

        # App streams playing to the AE-9 sink
        current = {}
        if sink:
            for link in snap.links.values():
                if link.in_node != sink.id:
                    continue
                n = snap.nodes.get(link.out_node)
                if n and n.media_class.startswith("Stream/Output/Audio") and not is_ours(n):
                    current[n.id] = n
        for sid in list(self.streams):
            if sid not in current:
                panel, started, title = self.streams.pop(sid)
                panel.stop()
                panel.setParent(None)
                panel.deleteLater()
                self.log(f"App stream ended: {title} (node {sid}, played {now - started:.0f}s)")
        for sid, n in current.items():
            src = snap.out_ports(n)
            if sid not in self.streams:
                app = n.props.get("application.name") or n.desc or n.name
                media = n.props.get("media.name") or n.name
                title = f"{app} — {media}" if media and media != app else app
                panel = MeterPanel(app, f"sbae9-audiomon.stream.{sid}", choose_channels(max(len(src), 1)),
                                   subtitle=media, passive=self.let_idle,
                                   tooltip=f"{title}\nsource node: {n.name} (id {sid})\n"
                                           f"meter node: sbae9-audiomon.stream.{sid}")
                self.streams[sid] = (panel, now, title)
                self.streams_box.insertWidget(self.streams_box.count() - 1, panel)
                self.log(f"App stream started: {title} (node {sid}, {len(src)} ch)")
            self._wire(snap, self.streams[sid][0], src, None, now)
        if self.no_streams.isHidden() != bool(self.streams):
            self.no_streams.setVisible(not self.streams)
            self._size_streams()

    def _size_streams(self):
        """Give the app-stream row room only while there are streams."""
        if self.streams:
            self.streams_scroll.setMinimumHeight(310)
            self.streams_scroll.setMaximumHeight(16777215)
        else:
            self.streams_scroll.setMinimumHeight(28)
            self.streams_scroll.setMaximumHeight(28)

    def _set_wired(self, key, title, wired, why):
        if self.wired.get(key, False) != wired:
            if key in self.wired or wired:
                self.log(f"{title}: {'wired' if wired else 'unwired'}" + (f" ({why})" if why else ""))
            self.wired[key] = wired

    def _wire(self, snap, panel, src_ports, override, now):
        """Link src_ports into the panel's meter node. override=[] unwires.

        Only links from src_ports into our own meter are ever created or
        removed; anything else (e.g. links made by hand in Helvum) is left alone.
        Returns True when every wanted link exists.
        """
        meter = snap.by_name(panel.node_name)
        if meter is None:
            return False
        meter_in = {p.name: p for p in snap.node_ports.get(meter.id, []) if not p.out}
        names = CHANNEL_MAPS[panel.channels]
        wanted = set()
        if override is None:
            if len(src_ports) == 1 and src_ports[0].channel not in names:
                targets = [meter_in.get(f"input_{c}") for c in names[:2]]
                wanted = {(src_ports[0].id, t.id) for t in targets if t}
            else:
                for i, sp in enumerate(src_ports):
                    ch = sp.channel if sp.channel in names else (names[i] if i < len(names) else None)
                    t = meter_in.get(f"input_{ch}") if ch else None
                    if t:
                        wanted.add((sp.id, t.id))
        src_ids = {p.id for p in src_ports}
        in_ids = {p.id for p in meter_in.values()}
        existing = {(l.out_port, l.in_port): l.id for l in snap.links.values()
                    if l.in_port in in_ids and l.out_port in src_ids}
        for pair in wanted - existing.keys():
            if now - self.pending.get(pair, -10) > 3.0:
                self.pending[pair] = now
                self.linker.link(*pair)
        for pair, lid in existing.items():
            if pair not in wanted and now - self.pending.get(("rm", lid), -10) > 3.0:
                self.pending[("rm", lid)] = now
                self.linker.unlink(lid)
        return bool(wanted) and wanted <= existing.keys()

    def stop(self):
        if self.silence.active:
            self.log(f"SILENT #{self.silence.count} still active at exit")
        self.log("SbAe9-AudioMon AE-9 mode stopped")
        self.log.remove_view(self.logview)
        self.graph.stop()
        for p in self.fixed.values():
            p.stop()
        for p, _, _ in self.streams.values():
            p.stop()


def log_view(min_height=90):
    v = QPlainTextEdit()
    v.setReadOnly(True)
    v.setMaximumBlockCount(5000)
    v.setFont(mono_font(12))
    v.setMinimumHeight(min_height)
    return v


class Page(QWidget):
    """Content page: big title (with optional widgets on its right) + body."""

    def __init__(self, title):
        super().__init__()
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(26, 18, 26, 18)
        self.body.setSpacing(12)
        self.head = QHBoxLayout()
        self.head.setSpacing(12)
        self.head.addWidget(label(title, "pageTitle"))
        self.head.addStretch(1)
        self.body.addLayout(self.head)


class MonitorPage(Page):
    """AE-9 meters, app streams and recent events. Hosts the AE9View."""

    def __init__(self, win):
        super().__init__("Monitor")
        self.win = win
        self.view = None
        self.off_banner = QFrame()
        self.off_banner.setObjectName("banner")
        bl = QHBoxLayout(self.off_banner)
        bl.setContentsMargins(14, 10, 14, 10)
        bl.addWidget(label("Auto-wire AE-9 is off, so nothing is being monitored. "
                           "Turn it on in Settings, or use Manual Meters.", wrap=True), 1)
        self.body.addWidget(self.off_banner)
        self.holder = QVBoxLayout()
        self.body.addLayout(self.holder, 1)

    def rebuild(self):
        if self.view:
            self.view.stop()
            self.view.setParent(None)
            self.view.deleteLater()
            self.view = None
        on = self.win.cfg["mode"] == "ae9"
        self.off_banner.setVisible(not on)
        if on:
            self.view = AE9View(self.win)
            self.holder.addWidget(self.view)

    def tick(self, now, dt):
        if self.view:
            self.view.tick(now, dt)

    def stop(self):
        if self.view:
            self.view.stop()


class LogPage(Page):
    def __init__(self, win):
        super().__init__("Event Log")
        open_btn = QPushButton(" Open log file")
        open_btn.setIcon(make_icon("folder", T.TEXT, size=18))
        open_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(win.log.path)))
        self.head.addWidget(open_btn)
        self.body.addWidget(label(
            f"Saved to {win.log.path}. Local time, to the second, so it lines up with "
            "journalctl -k (and the driver's ae9-pause-test: lines).", "hint", wrap=True))
        self.view = log_view(200)
        self.body.addWidget(self.view, 1)
        win.log.add_view(self.view, backfill=True)


class ManualPage(Page):
    """Two hand-wired meters, created the first time the page is opened."""

    def __init__(self, win):
        super().__init__("Manual Meters")
        self.win = win
        self.view = None
        self.body.addWidget(label(
            "Two meters with unlinked input ports. Wire anything into them with Helvum, "
            "qpwgraph or pw-link. They run alongside the AE-9 monitor.", "hint", wrap=True))
        self.holder = QVBoxLayout()
        self.body.addLayout(self.holder, 1)

    def showEvent(self, e):
        if self.view is None:
            self.view = ManualView(self.win)
            self.holder.addWidget(self.view)
        super().showEvent(e)

    def tick(self, now, dt):
        if self.view:
            self.view.tick(now, dt)

    def stop(self):
        if self.view:
            self.view.stop()


class SettingsPage(Page):
    def __init__(self, win):
        super().__init__("Settings")
        self.win = win
        cfg = win.cfg

        def check(text, hint, checked, slot):
            cb = QCheckBox(text)
            cb.setChecked(checked)
            cb.toggled.connect(slot)
            self.body.addWidget(cb)
            h = label(hint, "hint", wrap=True)
            h.setContentsMargins(30, 0, 0, 6)
            self.body.addWidget(h)
            return cb

        self.body.addWidget(label("Monitoring", "sectionTitle"))
        self.auto_cb = check(
            "Auto-wire AE-9",
            "Meter the AE-9 output monitor, What U Hear, Mic / Line In, Optical In and every "
            "app stream playing to the AE-9. Listen-only: never changes defaults, moves "
            "streams or touches mixer controls.",
            cfg["mode"] == "ae9", win.set_auto)
        self.idle_cb = check(
            "Let the AE-9 idle",
            "Meters never keep the card awake. The output and app meters are passive, and "
            "What U Hear / Mic / Optical are only wired while the AE-9 sink is running. "
            "Off: everything stays wired and the AE-9 never suspends.",
            cfg["let_idle"], win.set_let_idle)
        self.capture_cb = check(
            "Always capture inputs",
            "Comparison mode: keep What U Hear / Mic / Optical wired while the card idles "
            "(their capture converters and DMA stay running). The sink can still suspend.",
            cfg["always_capture"], win.set_always_capture)

        self.body.addSpacing(6)
        self.body.addWidget(label("Window", "sectionTitle"))
        self.frame_cb = check(
            "Use the desktop's title bar",
            "Off: the app draws its own title bar, like Sound Blaster Command.",
            not cfg["frameless"], lambda on: win.set_frameless(not on))

        self.body.addSpacing(6)
        self.body.addWidget(label("Device", "sectionTitle"))
        self.info = label("", "hint", wrap=True)
        self.info.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.body.addWidget(self.info)
        self.body.addStretch(1)
        self.sync()

    def sync(self):
        on = self.win.cfg["mode"] == "ae9"
        self.idle_cb.setEnabled(on)
        self.capture_cb.setEnabled(on and self.win.cfg["let_idle"])

    def showEvent(self, e):
        self.info.setText(
            f"Driver: {driver_info()}<br>"
            f"SILENT: output monitor above {MON_SIGNAL_DB:.0f} dBFS while What U Hear stays below "
            f"{WUH_SILENT_DB:.0f} dBFS for over {SILENT_AFTER} s ({WIRE_GRACE:.0f} s grace after "
            f"wiring What U Hear)<br>"
            f"Log: {self.win.log.path}<br>Config: {CONFIG_PATH}")
        super().showEvent(e)


class TitleBar(QFrame):
    """The window's header. Without the desktop's frame it is the window's
    handle: drag to move, double-click to maximize."""

    def __init__(self, win):
        super().__init__()
        self.win = win
        self.setObjectName("titleBar")
        h = QHBoxLayout(self)
        h.setContentsMargins(18, 8, 10, 6)
        h.setSpacing(4)
        names = QVBoxLayout()
        names.setSpacing(0)
        names.addWidget(label("SbAe9-AudioMon", "appTitle"))
        names.addWidget(label("AE-9", "deviceName"))
        h.addLayout(names)
        h.addStretch(1)
        self.buttons = []
        for name, tip, slot in (("minimize", "Minimize", win.showMinimized),
                                ("maximize", "Maximize", self._toggle_max),
                                ("close", "Close", win.close)):
            b = QToolButton()
            b.setObjectName("winClose" if name == "close" else "winButton")
            b.setIcon(make_icon(name, T.MUTED, active=T.TEXT, size=18))
            b.setIconSize(QSize(18, 18))
            b.setFixedSize(34, 28)
            b.setToolTip(tip)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.clicked.connect(slot)
            h.addWidget(b, 0, Qt.AlignmentFlag.AlignTop)
            self.buttons.append(b)

    def _toggle_max(self):
        self.win.showNormal() if self.win.isMaximized() else self.win.showMaximized()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self.win.frameless and self.win.windowHandle():
            self.win.windowHandle().startSystemMove()   # the desktop moves it (works on Wayland)
            e.accept()
            return
        super().mousePressEvent(e)

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self.win.frameless:
            self._toggle_max()
            e.accept()


class EdgeGrip(QWidget):
    """Invisible strip along a window edge: drag to resize a frameless window."""
    E = Qt.Edge
    CURSORS = {
        (E.LeftEdge,): Qt.CursorShape.SizeHorCursor, (E.RightEdge,): Qt.CursorShape.SizeHorCursor,
        (E.TopEdge,): Qt.CursorShape.SizeVerCursor, (E.BottomEdge,): Qt.CursorShape.SizeVerCursor,
        (E.TopEdge, E.LeftEdge): Qt.CursorShape.SizeFDiagCursor,
        (E.BottomEdge, E.RightEdge): Qt.CursorShape.SizeFDiagCursor,
        (E.TopEdge, E.RightEdge): Qt.CursorShape.SizeBDiagCursor,
        (E.BottomEdge, E.LeftEdge): Qt.CursorShape.SizeBDiagCursor,
    }
    SIZE = 6

    def __init__(self, edges, win):
        super().__init__(win)
        self.edges = edges
        self.win = win
        self.setCursor(self.CURSORS[edges])

    def place(self):
        w, h, s = self.win.width(), self.win.height(), self.SIZE
        E = self.E
        x = 0 if E.LeftEdge in self.edges else (w - s if E.RightEdge in self.edges else s)
        y = 0 if E.TopEdge in self.edges else (h - s if E.BottomEdge in self.edges else s)
        gw = s if (E.LeftEdge in self.edges or E.RightEdge in self.edges) else w - 2 * s
        gh = s if (E.TopEdge in self.edges or E.BottomEdge in self.edges) else h - 2 * s
        self.setGeometry(x, y, gw, gh)
        self.raise_()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self.win.windowHandle():
            edges = self.edges[0]
            for extra in self.edges[1:]:
                edges |= extra
            self.win.windowHandle().startSystemResize(edges)
            e.accept()


class StatusDot(QWidget):
    def __init__(self):
        super().__init__()
        self.color = T.IDLE
        self.setFixedSize(16, 16)

    def set_color(self, color):
        if color != self.color:
            self.color = color
            self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(self.color))
        p.drawEllipse(QRectF(2, 2, 12, 12))


NAV = (("Monitor", "wave"), ("Event Log", "log"), None, ("Manual Meters", "mixer"))
NAV_BOTTOM = (("Settings", "settings"),)


class MainWindow(QWidget):
    def __init__(self, cfg, log):
        super().__init__()
        self.cfg = cfg
        self.log = log
        self.setObjectName("root")
        self.setWindowTitle("SbAe9-AudioMon — AE-9")
        self.setWindowIcon(app_icon())
        self.resize(1320, 860)

        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        self.title_bar = TitleBar(self)
        v.addWidget(self.title_bar)

        mid = QHBoxLayout()
        mid.setSpacing(0)
        v.addLayout(mid, 1)

        side = QFrame()
        side.setObjectName("sidebar")
        side.setFixedWidth(210)
        sl = QVBoxLayout(side)
        sl.setContentsMargins(0, 10, 0, 12)
        sl.setSpacing(2)
        sl.addWidget(label("AE-9 Monitoring", "sideCaption"))
        sl.addSpacing(4)
        self.stack = QStackedWidget()
        self.stack.setObjectName("content")
        self.nav_buttons = []

        self.monitor = MonitorPage(self)
        self.log_page = LogPage(self)
        self.manual = ManualPage(self)
        self.settings = SettingsPage(self)
        pages = {"Monitor": self.monitor, "Event Log": self.log_page,
                 "Manual Meters": self.manual, "Settings": self.settings}

        def add(entry):
            name, ic = entry
            idx = self.stack.addWidget(pages[name])
            b = QPushButton(" " + name)
            b.setCheckable(True)
            b.setIcon(make_icon(ic, T.TEXT, active=T.ACCENT_HI, size=22))
            b.setIconSize(QSize(22, 22))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _=False, i=idx: self.go(i))
            self.nav_buttons.append(b)
            sl.addWidget(b)

        for entry in NAV:
            if entry is None:
                sl.addWidget(hline())
            else:
                add(entry)
        sl.addStretch(1)
        sl.addWidget(hline())
        for entry in NAV_BOTTOM:
            add(entry)
        mid.addWidget(side)
        mid.addWidget(self.stack, 1)

        # bottom status bar
        bottom = QFrame()
        bottom.setObjectName("bottomBar")
        bottom.setFixedHeight(56)
        bl = QHBoxLayout(bottom)
        bl.setContentsMargins(20, 0, 22, 0)
        bl.setSpacing(16)
        self.dot = StatusDot()
        bl.addWidget(self.dot)
        bl.addWidget(label("AE-9", "logo"))
        sep = QFrame()
        sep.setFixedSize(2, 26)
        sep.setStyleSheet(f"background:{T.TEXT};")
        bl.addWidget(sep)
        self.state_lbl = label("", "profileLabel")
        bl.addWidget(self.state_lbl)

        # Quick access to Settings -> Monitoring (shown on the Monitor page)
        self.quick = QWidget()
        ql = QHBoxLayout(self.quick)
        ql.setContentsMargins(10, 0, 0, 0)
        ql.setSpacing(16)
        qsep = QFrame()
        qsep.setFixedSize(1, 22)
        qsep.setStyleSheet(f"background:{rgba(T.OVERLAY, 0.25)};")
        ql.addWidget(qsep)

        def quick(text, tip, slot):
            rb = QRadioButton(text)
            rb.setObjectName("quickToggle")
            rb.setAutoExclusive(False)      # independent on/off switches
            rb.setToolTip(tip)
            rb.setCursor(Qt.CursorShape.PointingHandCursor)
            rb.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            rb.toggled.connect(slot)
            ql.addWidget(rb)
            return rb

        self.q_auto = quick("Auto-wire", "Auto-wire AE-9 (Settings → Monitoring)", self.set_auto)
        self.q_idle = quick("Let idle", "Let the AE-9 idle (Settings → Monitoring)", self.set_let_idle)
        self.q_capture = quick("Always capture", "Always capture inputs (Settings → Monitoring)",
                               self.set_always_capture)
        bl.addWidget(self.quick)
        bl.addStretch(1)
        self.silent_lbl = label("SILENT", "silentBadge")
        self.silent_lbl.setToolTip(f"Output monitor has signal but What U Hear is below "
                                   f"{WUH_SILENT_DB:.0f} dBFS for more than {SILENT_AFTER}s")
        self.silent_lbl.hide()
        bl.addWidget(self.silent_lbl, 0, Qt.AlignmentFlag.AlignVCenter)
        self.dropouts_lbl = label("", "bigValue")
        bl.addWidget(self.dropouts_lbl)
        v.addWidget(bottom)

        self.grips = [EdgeGrip(edges, self) for edges in EdgeGrip.CURSORS]
        self.frameless = False
        self.set_frameless(cfg["frameless"], save=False)

        self._silent = False
        self._sink_state = None
        self.sync_controls()
        self.monitor.rebuild()
        self._refresh_status()
        page = cfg.get("page", 0)
        self.go(page if isinstance(page, int) and 0 <= page < self.stack.count() else 0)

        self.last_tick = time.monotonic()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(33)

    # -- navigation / settings -------------------------------------------

    def go(self, idx):
        self.stack.setCurrentIndex(idx)
        self.quick.setVisible(self.stack.currentWidget() is self.monitor)
        for i, b in enumerate(self.nav_buttons):
            b.setChecked(i == idx)
        if self.cfg.get("page") != idx:
            self.cfg["page"] = idx
            save_config(self.cfg)

    def sync_controls(self):
        """Keep the Settings checkboxes and the bottom-bar toggles in step."""
        cfg = self.cfg
        auto = cfg["mode"] == "ae9"
        for widget, value in ((self.settings.auto_cb, auto), (self.q_auto, auto),
                              (self.settings.idle_cb, cfg["let_idle"]), (self.q_idle, cfg["let_idle"]),
                              (self.settings.capture_cb, cfg["always_capture"]),
                              (self.q_capture, cfg["always_capture"])):
            blocker = QSignalBlocker(widget)
            widget.setChecked(value)
            del blocker
        self.settings.sync()
        self.q_idle.setEnabled(auto)
        self.q_capture.setEnabled(auto and cfg["let_idle"])

    def set_auto(self, on):
        if (self.cfg["mode"] == "ae9") == on:
            return
        self.cfg["mode"] = "ae9" if on else "manual"
        save_config(self.cfg)
        self.sync_controls()
        self.monitor.rebuild()
        self._sink_state = None
        self._silent = False
        self._refresh_status()

    def set_let_idle(self, on):
        if self.cfg["let_idle"] == on:
            return
        self.cfg["let_idle"] = on
        save_config(self.cfg)
        self.sync_controls()
        self.log(f"'Let the AE-9 idle' turned {'on' if on else 'off'}; rebuilding meters")
        self.monitor.rebuild()

    def set_always_capture(self, on):
        if self.cfg["always_capture"] == on:
            return
        self.cfg["always_capture"] = on
        save_config(self.cfg)
        self.sync_controls()
        self.log(f"'Always capture inputs' turned {'on' if on else 'off'}; rebuilding meters")
        self.monitor.rebuild()

    def set_frameless(self, on, save=True):
        was_visible = self.isVisible()
        self.frameless = on
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, on)  # hides the window
        for b in self.title_bar.buttons:
            b.setVisible(on)
        for g in self.grips:
            g.setVisible(on)
            g.place()
        if save:
            self.cfg["frameless"] = on
            save_config(self.cfg)
        if was_visible:
            self.show()

    def resizeEvent(self, e):
        for g in self.grips:
            g.place()
        super().resizeEvent(e)

    # -- status bar ------------------------------------------------------

    def set_sink_state(self, state):
        self._sink_state = state
        self._refresh_status()

    def set_silent(self, active, count):
        self._silent = active
        self.dropouts_lbl.setText(f"Dropouts: {count}")
        self._refresh_status()

    def _refresh_status(self):
        if self.cfg["mode"] != "ae9":
            self.dot.set_color(T.DIM)
            self.state_lbl.setText("Auto-wire off")
            self.dropouts_lbl.setText("")
            self.silent_lbl.hide()
            return
        s = self._sink_state
        colors = {"running": T.OK, "idle": T.WARN, "suspended": T.IDLE}
        self.dot.set_color(T.DANGER if self._silent else colors.get(s, T.DANGER))
        self.state_lbl.setText((s or "not found").capitalize())
        self.state_lbl.setToolTip("AE-9 sink state")
        self.silent_lbl.setVisible(self._silent)
        if not self.dropouts_lbl.text():
            self.dropouts_lbl.setText("Dropouts: 0")

    def fit_window(self):
        """Shrink the window back to its natural width (e.g. after fewer channels)."""
        def shrink():
            if self.isMaximized():
                return
            if self.layout():
                self.layout().activate()
            # Asking for width 1 gets clamped to the minimum the layout allows.
            self.resize(1, self.height())
        QTimer.singleShot(0, shrink)

    def _tick(self):
        now = time.monotonic()
        dt = min(now - self.last_tick, 0.25)
        self.last_tick = now
        self.monitor.tick(now, dt)
        self.manual.tick(now, dt)

    def stop(self):
        self.monitor.stop()
        self.manual.stop()


def main():
    ap = argparse.ArgumentParser(description="PipeWire VU meters, with an AE-9 debug mode.")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--ae9", action="store_true", help="AE-9 auto-wire mode (default)")
    mode.add_argument("--manual", action="store_true", help="two meters you wire by hand")
    idle = ap.add_mutually_exclusive_group()
    idle.add_argument("--keep-awake", action="store_true",
                      help="AE-9 mode: keep everything wired (the card never suspends)")
    idle.add_argument("--let-idle", action="store_true",
                      help="AE-9 mode: never keep the card awake (default)")
    ap.add_argument("--always-capture", action=argparse.BooleanOptionalAction, default=None,
                    help="AE-9 mode: keep What U Hear/Mic/Optical wired while the card idles")
    ap.add_argument("-c", "--channels", type=int, choices=CHANNEL_CHOICES,
                    help="manual mode: channel count for both meters")
    ap.add_argument("--frame", action=argparse.BooleanOptionalAction, default=None,
                    help="use the desktop's title bar instead of our own")
    ap.add_argument("--log", default=LOG_PATH, help=f"debug log file (default {LOG_PATH})")
    args, qt_args = ap.parse_known_args()

    cfg = load_config()
    if args.ae9 or args.manual:
        cfg["mode"] = "ae9" if args.ae9 else "manual"
    if args.keep_awake or args.let_idle:
        cfg["let_idle"] = args.let_idle
    if args.frame is not None:
        cfg["frameless"] = not args.frame
    if args.manual:
        cfg["page"] = 2   # open on Manual Meters
    if args.always_capture is not None:
        cfg["always_capture"] = args.always_capture
    if args.channels:
        for m in cfg["meters"]:
            m["channels"] = args.channels

    app = QApplication([sys.argv[0]] + qt_args)
    app.setApplicationName("SbAe9-AudioMon")
    app.setDesktopFileName("sbae9-audiomon")
    app.setStyle("Fusion")
    app.setStyleSheet(stylesheet())

    win = MainWindow(cfg, EventLog(args.log))
    app.aboutToQuit.connect(win.stop)
    # The 33 ms meter timer hands control back to Python regularly, so
    # these handlers run promptly even while Qt's event loop is in charge.
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: app.quit())

    win.show()
    ret = app.exec()
    # Tear down the window before the QApplication; letting Python pick the
    # order at exit can segfault.
    win.timer.stop()
    win.deleteLater()
    del win
    app.processEvents()
    return ret


if __name__ == "__main__":
    sys.exit(main())
