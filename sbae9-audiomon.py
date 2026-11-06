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
from types import SimpleNamespace

import numpy as np
from PySide6.QtCore import QRectF, QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QFontDatabase, QIcon, QPainter, QPen
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFrame, QHBoxLayout, QLabel, QPlainTextEdit,
    QPushButton, QScrollArea, QSizePolicy, QToolButton, QVBoxLayout, QWidget,
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
        for k in ("let_idle", "always_capture"):
            if isinstance(user.get(k), bool):
                cfg[k] = user[k]
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
        self.view = None
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
        if self.view is not None:
            self.view.appendPlainText(line)


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
# Meter geometry + drawing
# --------------------------------------------------------------------------

SCALE_W = 40
COL_W = 50
PAIR_GAP = 14
PAD = 8
TOP = 32
ROW_H = 14
STAT_ROWS = ("Peak", "RMS", "Avg", "Max")
STATS_H = len(STAT_ROWS) * ROW_H + 10
TICKS = (0, -3, -6, -10, -18, -30, -40, -50, -60)
COL_MAX = 120   # widest a column gets when the window is stretched
BAR_MAX = 64    # widest a bar gets

BG = QColor.fromRgbF(0.08, 0.085, 0.10)
BG_ALERT = QColor.fromRgbF(0.22, 0.06, 0.07)
BAR_BG = QColor.fromRgbF(0.13, 0.14, 0.16)
TEXT = QColor.fromRgbF(0.85, 0.87, 0.90)
TEXT_DIM = QColor.fromRgbF(0.55, 0.57, 0.60)
CLIP_ON = QColor.fromRgbF(0.95, 0.15, 0.15)
CLIP_OFF = QColor.fromRgbF(0.22, 0.10, 0.10)
CLIP_TEXT = QColor.fromRgbF(0.95, 0.35, 0.30)
HOLD = QColor.fromRgbF(0.95, 0.95, 0.95)
ZONES = (
    (FLOOR_DB, GREEN_MAX, QColor.fromRgbF(0.18, 0.80, 0.35)),
    (GREEN_MAX, YELLOW_MAX, QColor.fromRgbF(0.95, 0.80, 0.15)),
    (YELLOW_MAX, 0.0, QColor.fromRgbF(0.95, 0.25, 0.20)),
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


def mono_font(px):
    f = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
    f.setPixelSize(px)
    return f


class MeterWidget(QWidget):
    """Draws one meter: scale, bars, clip LEDs and the stats rows."""

    def __init__(self, panel):
        super().__init__()
        self.panel = panel
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setToolTip("Click to reset stats")
        self.fonts = {px: mono_font(px) for px in (9, 10, 11)}

    def sizeHint(self):
        return QSize(panel_width(self.panel.state.channels), 340)

    def minimumSizeHint(self):
        return QSize(panel_width(self.panel.state.channels), 220)

    def mousePressEvent(self, _event):
        self.panel.reset()

    def paintEvent(self, _event):
        st = self.panel.state
        w, h = self.width(), self.height()
        p = QPainter(self)
        p.fillRect(0, 0, w, h, BG_ALERT if self.panel.alert else BG)

        bar_top = TOP
        bar_bot = h - STATS_H - 4
        bar_h = max(bar_bot - bar_top, 10)

        def y_of(db):
            return bar_bot - iec_scale(db) * bar_h

        def text(s, x, y, align="left", px=10, color=TEXT):
            # (x, y) is the baseline anchor
            p.setFont(self.fonts[px])
            p.setPen(color)
            box = QRectF(x - 200, y - px - 2, 400, px + 6)
            if align == "left":
                box.moveLeft(x)
                flags = Qt.AlignmentFlag.AlignLeft
            elif align == "right":
                box.moveRight(x)
                flags = Qt.AlignmentFlag.AlignRight
            else:
                flags = Qt.AlignmentFlag.AlignHCenter
            p.drawText(box, flags | Qt.AlignmentFlag.AlignBottom, s)

        # Scale + grid
        cw = col_width(st.channels, w)
        right = col_x(st.channels - 1, cw) + cw
        for t in TICKS:
            y = round(y_of(t)) + 0.5
            text(f"{t}", SCALE_W - 4, y + 4, "right", 9, TEXT_DIM)
            grid = QColor(255, 255, 255, 41 if t in (0, -18) else 18)
            p.setPen(QPen(grid, 1))
            p.drawLine(SCALE_W, int(y), int(right), int(y))

        rms_db = st.rms_db
        avg_db = st.avg_db
        max_db = amp_db(st.max_amp)
        p.setPen(Qt.PenStyle.NoPen)
        for i in range(st.channels):
            x0 = col_x(i, cw)
            cx = x0 + cw / 2
            bw = min(cw - 16, BAR_MAX)
            bx = cx - bw / 2

            text(st.names[i], cx, 14, "center", 11, TEXT)

            # Clip LED (latched until reset)
            p.fillRect(QRectF(bx, 19, bw, 7), CLIP_ON if st.clipped[i] else CLIP_OFF)

            # Bar background
            p.fillRect(QRectF(bx, bar_top, bw, bar_h), BAR_BG)

            def fill_level(level, alpha):
                for lo, hi, color in ZONES:
                    if level <= lo:
                        break
                    top = y_of(min(level, hi))
                    c = QColor(color)
                    c.setAlphaF(alpha)
                    p.fillRect(QRectF(bx, top, bw, y_of(lo) - top), c)

            fill_level(st.peak_db[i], 0.35)  # peak: translucent
            fill_level(rms_db[i], 1.0)       # VU/RMS: solid

            if st.hold_db[i] > FLOOR_DB + 0.5:
                y = round(y_of(st.hold_db[i]))
                p.fillRect(QRectF(bx, y - 1, bw, 2), HOLD)

            # Stats
            vals = (st.hold_db[i], rms_db[i], avg_db[i], max_db[i])
            for r, v in enumerate(vals):
                y = bar_bot + 16 + r * ROW_H
                color = CLIP_TEXT if (r == 3 and st.clipped[i]) else TEXT
                text(fmt_db(v), cx, y, "center", 10, color)

        for r, name in enumerate(STAT_ROWS):
            text(name, 4, bar_bot + 16 + r * ROW_H, "left", 9, TEXT_DIM)

        p.end()


# --------------------------------------------------------------------------
# UI
# --------------------------------------------------------------------------

class MeterPanel(QWidget):
    """Header (title, status, optional channel picker, reset) + meter."""

    def __init__(self, title, node_name, channels, subtitle=None, passive=False,
                 on_channels=None, fit=None, tooltip=None):
        super().__init__()
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
        title_lbl = QLabel(f"<b>{html_escape(elide(title))}</b><br>"
                           f"<small><tt>{html_escape(elide(sub, 34))}</tt></small>")
        title_lbl.setTextFormat(Qt.TextFormat.RichText)
        title_lbl.setToolTip(tooltip or f"{title}\n{sub}\nmeter node: {node_name}")

        self.status = QLabel()
        self.status.setTextFormat(Qt.TextFormat.RichText)

        header = QHBoxLayout()
        header.setSpacing(8)
        header.addWidget(title_lbl, 1)
        header.addWidget(self.status)

        if on_channels:
            self.combo = QComboBox()
            self.combo.addItems([f"{c} ch" for c in CHANNEL_CHOICES])
            self.combo.setToolTip("Channel count (recreates the PipeWire node; links are dropped)")
            self.combo.setCurrentIndex(CHANNEL_CHOICES.index(channels))
            self.combo.currentIndexChanged.connect(self._on_combo)
            header.addWidget(self.combo)

        reset = QToolButton()
        icon = QIcon.fromTheme("view-refresh")
        if icon.isNull():
            reset.setText("⟳")
        else:
            reset.setIcon(icon)
        reset.setToolTip("Reset stats and clip")
        reset.clicked.connect(self.reset)
        header.addWidget(reset)

        self.state = MeterState(channels)
        self.meter = MeterWidget(self)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addLayout(header)
        layout.addWidget(self.meter, 1)

        self.start(channels)

    @property
    def channels(self):
        return self.state.channels

    def start(self, channels):
        if self.capture:
            self.capture.stop()
        self.capture = Capture(self.node_name, self.title_text, channels, self.passive)
        self.state = MeterState(channels)
        self.meter.updateGeometry()
        if self.fit:
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
            html = f"<small>{html_escape(self.capture.error)}</small>"
        elif self.note:
            html = f'<small><span style="color:#999">{html_escape(self.note)}</span></small>'
        elif self.live:
            html = '<small><span style="color:#3c3">● live</span></small>'
        else:
            html = '<small><span style="color:#888">○ idle</span></small>'
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
        layout.setSpacing(12)
        self.panels = []
        for i, m in enumerate(win.cfg["meters"]):
            if i:
                layout.addWidget(vline())
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
            if not mon_on:
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
        layout.setSpacing(8)

        row = QHBoxLayout()
        row.setSpacing(12)
        self.fixed = {}
        for i, (key, title, node_name, _gated) in enumerate(FIXED_TAPS):
            if i:
                row.addWidget(vline())
            passive = self.let_idle and key == "monitor"
            p = MeterPanel(title, node_name, 2, passive=passive, fit=win.fit_window)
            p.note = "searching…"
            self.fixed[key] = p
            row.addWidget(p)
        layout.addLayout(row, 3)

        streams_title = QLabel("<b>App streams → AE-9</b>")
        layout.addWidget(streams_title)
        self.streams = {}           # stream node id -> (panel, started_at, title)
        self.streams_box = QHBoxLayout()
        self.streams_box.setSpacing(12)
        self.no_streams = QLabel("No apps are playing to the AE-9.")
        self.no_streams.setStyleSheet("color: #888")
        self.streams_box.addWidget(self.no_streams)
        self.streams_box.addStretch(1)
        holder = QWidget()
        holder.setLayout(self.streams_box)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(holder)
        scroll.setMinimumHeight(290)
        layout.addWidget(scroll, 3)

        self.logview = QPlainTextEdit()
        self.logview.setReadOnly(True)
        self.logview.setMaximumBlockCount(5000)
        self.logview.setFont(mono_font(11))
        self.logview.setMinimumHeight(110)
        layout.addWidget(self.logview, 1)
        self.log.view = self.logview

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
        self.no_streams.setVisible(not self.streams)

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
        self.log.view = None
        self.graph.stop()
        for p in self.fixed.values():
            p.stop()
        for p, _, _ in self.streams.values():
            p.stop()


def vline():
    sep = QFrame()
    sep.setFrameShape(QFrame.Shape.VLine)
    sep.setFrameShadow(QFrame.Shadow.Sunken)
    return sep


class MainWindow(QWidget):
    def __init__(self, cfg, log):
        super().__init__()
        self.cfg = cfg
        self.log = log
        self.body = None
        self.setWindowTitle("SbAe9-AudioMon")
        self.setWindowIcon(QIcon.fromTheme("audio-volume-high"))

        bar = QHBoxLayout()
        bar.setSpacing(14)
        self.auto_cb = QCheckBox("Auto-wire AE-9")
        self.auto_cb.setToolTip("Meter the AE-9 output monitor, What U Hear, inputs and every app\n"
                                "stream playing to it. Off = two meters you wire by hand.")
        self.auto_cb.setChecked(cfg["mode"] == "ae9")
        self.auto_cb.toggled.connect(self._on_mode)
        bar.addWidget(self.auto_cb)

        self.idle_cb = QCheckBox("Let the AE-9 idle")
        self.idle_cb.setToolTip(
            "On: meters never keep the AE-9 awake. The output monitor and app-stream\n"
            "meters are passive, and What U Hear / Mic / Optical are only wired while\n"
            "the AE-9 sink is running, so the card can idle and suspend normally.\n"
            "Off: everything stays wired, which keeps the AE-9 running all the time.")
        self.idle_cb.setChecked(cfg["let_idle"])
        self.idle_cb.toggled.connect(self._on_idle)
        bar.addWidget(self.idle_cb)

        self.capture_cb = QCheckBox("Always capture inputs")
        self.capture_cb.setToolTip(
            "Keep What U Hear / Mic / Optical wired even while the AE-9 sink is idle.\n"
            "The output and app-stream meters stay passive, so the sink can still\n"
            "suspend, but the card's capture PCMs stay open. Used to compare\n"
            "against the default, where the card is fully idle between plays.")
        self.capture_cb.setChecked(cfg["always_capture"])
        self.capture_cb.toggled.connect(self._on_capture)
        bar.addWidget(self.capture_cb)

        self.sink_lbl = QLabel()
        bar.addWidget(self.sink_lbl)
        self.silent_lbl = QLabel(" SILENT ")
        self.silent_lbl.setStyleSheet("background:#d22; color:white; font-weight:bold;"
                                      "padding:2px 8px; border-radius:4px")
        self.silent_lbl.setToolTip("Output monitor has signal but What U Hear is below "
                                   f"{WUH_SILENT_DB:.0f} dBFS for more than {SILENT_AFTER}s")
        self.silent_lbl.hide()
        bar.addWidget(self.silent_lbl)
        self.dropouts_lbl = QLabel()
        bar.addWidget(self.dropouts_lbl)
        bar.addStretch(1)
        self.log_btn = QPushButton("Open log")
        self.log_btn.setToolTip(LOG_PATH)
        self.log_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(LOG_PATH)))
        bar.addWidget(self.log_btn)

        self.layout_ = QVBoxLayout(self)
        self.layout_.setContentsMargins(10, 10, 10, 10)
        self.layout_.setSpacing(10)
        self.layout_.addLayout(bar)
        self._build()

        self.last_tick = time.monotonic()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(33)

    def _build(self):
        if self.body:
            self.body.stop()
            self.body.setParent(None)
            self.body.deleteLater()
        ae9 = self.cfg["mode"] == "ae9"
        for w in (self.idle_cb, self.capture_cb, self.sink_lbl, self.dropouts_lbl, self.log_btn):
            w.setVisible(ae9)
        # Without "let idle" everything is always wired anyway.
        self.capture_cb.setEnabled(self.cfg["let_idle"])
        self.silent_lbl.hide()
        self.set_sink_state(None)
        self.dropouts_lbl.setText("Dropouts: 0")
        self.body = AE9View(self) if ae9 else ManualView(self)
        self.layout_.addWidget(self.body, 1)
        self.fit_window()

    def _on_mode(self, on):
        self.cfg["mode"] = "ae9" if on else "manual"
        save_config(self.cfg)
        self._build()

    def _on_idle(self, on):
        self.cfg["let_idle"] = on
        save_config(self.cfg)
        self.log(f"'Let the AE-9 idle' turned {'on' if on else 'off'}; rebuilding meters")
        self._build()

    def _on_capture(self, on):
        self.cfg["always_capture"] = on
        save_config(self.cfg)
        self.log(f"'Always capture inputs' turned {'on' if on else 'off'}; rebuilding meters")
        self._build()

    def set_sink_state(self, state):
        colors = {"running": "#3c3", "idle": "#cc3", "suspended": "#888", "error": "#e44"}
        s = state or "absent"
        self.sink_lbl.setText(f'AE-9 sink: <span style="color:{colors.get(s, "#e44")}">{s}</span>')

    def set_silent(self, active, count):
        self.silent_lbl.setVisible(active)
        self.dropouts_lbl.setText(f"Dropouts: {count}")

    def fit_window(self):
        """Shrink the window back to its natural width (e.g. after fewer channels)."""
        def shrink():
            if self.layout():
                self.layout().activate()
            # Asking for width 1 gets clamped to the minimum the layout allows.
            self.resize(1, self.height())
        QTimer.singleShot(0, shrink)

    def _tick(self):
        now = time.monotonic()
        dt = min(now - self.last_tick, 0.25)
        self.last_tick = now
        self.body.tick(now, dt)

    def stop(self):
        if self.body:
            self.body.stop()


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
    ap.add_argument("--log", default=LOG_PATH, help=f"debug log file (default {LOG_PATH})")
    args, qt_args = ap.parse_known_args()

    cfg = load_config()
    if args.ae9 or args.manual:
        cfg["mode"] = "ae9" if args.ae9 else "manual"
    if args.keep_awake or args.let_idle:
        cfg["let_idle"] = args.let_idle
    if args.always_capture is not None:
        cfg["always_capture"] = args.always_capture
    if args.channels:
        for m in cfg["meters"]:
            m["channels"] = args.channels

    app = QApplication([sys.argv[0]] + qt_args)
    app.setApplicationName("SbAe9-AudioMon")
    app.setDesktopFileName("sbae9-audiomon")

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
