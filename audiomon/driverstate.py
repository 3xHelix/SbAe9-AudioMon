# SPDX-License-Identifier: GPL-2.0-only
"""The driver's state, read-only: a few of the card's ALSA controls and the
playback stream's format, so the debug log shows what changed just before
a dropout.

Controls are followed with `amixer -c AE9 events` and read with
`amixer cget`. Nothing here ever writes a control (AudioMon is listen-only).
"""

import os
import queue
import re
import subprocess
import threading
import time

from sbae9kit import ae9

from .core import child_cmd

# What a dropout is most likely to follow, then the speaker setup that can make
# What U Hear's two sides differ on purpose in DSP mode (names as driver 0.10.0
# has them).
WATCH = ("Direct Mode Switch", "SBX Switch", "PCM Bit Depth", "Speaker Config",
         "Headphone Profile", "Head Unit Output",
         "FX Surround Switch", "Bass Redirect Switch",
         "Speaker Level Front L Volume", "Speaker Level Front R Volume",
         "Speaker Distance Front L", "Speaker Distance Front R",
         "Optical Out Mirror Switch",      # driver 0.12.1+: the optical jack copies the main output
         # driver 0.12.2+: Voice Clarity on the mic, mic monitoring, headphone mode
         "Voice Clarity Capture Switch", "Acoustic Echo Cancellation Capture Switch",
         "Noise Reduction Capture Switch", "Mic Smart Volume Capture Switch",
         "Mic EQ Capture Enum", "Voice Morphing Capture Enum",
         "Mic Monitor Playback Switch",
         "Headphone Virtualization Switch", "Line Out Virtualization Switch")
SHORT = {"Direct Mode Switch": "Direct Mode", "SBX Switch": "SBX", "FX Surround Switch": "Surround",
         "Bass Redirect Switch": "Bass Redirect",
         "Speaker Level Front L Volume": "Front L level", "Speaker Level Front R Volume": "Front R level",
         "Speaker Distance Front L": "Front L distance", "Speaker Distance Front R": "Front R distance",
         "Optical Out Mirror Switch": "Optical mirror",
         "Voice Clarity Capture Switch": "Voice Clarity",
         "Acoustic Echo Cancellation Capture Switch": "Echo cancellation",
         "Noise Reduction Capture Switch": "Noise reduction",
         "Mic Smart Volume Capture Switch": "Mic Smart Volume",
         "Mic EQ Capture Enum": "Mic EQ", "Voice Morphing Capture Enum": "Voice morphing",
         "Mic Monitor Playback Switch": "Mic Monitor",
         "Headphone Virtualization Switch": "Headphone virtualization",
         "Line Out Virtualization Switch": "Line Out virtualization"}

HW_PARAMS = f"/proc/asound/{ae9.CARD_ID}/pcm0p/sub0/hw_params"
OPTICAL_HW_PARAMS = f"/proc/asound/{ae9.CARD_ID}/pcm{ae9.OPTICAL_OUT[1]}p/sub0/hw_params"
MIC_HW_PARAMS = f"/proc/asound/{ae9.CARD_ID}/pcm0c/sub0/hw_params"

# Bits on the optical wire per sample format: S/PDIF carries 24 at most, so the
# optical PCM's S32_LE is 24-bit there; passthrough (AC-3 / DTS) runs as S16_LE.
WIRE_BITS = {"S16LE": 16, "S24LE": 24, "S243LE": 24, "S32LE": 24}

_EVENT = re.compile(r"event (\w+): numid=(\d+),iface=\w+,name='(.*)'")
_ITEM = re.compile(r"; Item #(\d+) '(.*)'")
_VALUES = re.compile(r"^\s*: values=(.*)$", re.M)
_INT = re.compile(r"type=INTEGER,.*?min=(-?\d+)")
_DB = re.compile(r"dBscale-min=(-?[\d.]+)dB,step=([\d.]+)dB")
_NUM = re.compile(r"-?\d+(?:\.\d+)?")
_ENV = dict(os.environ, LC_ALL="C")


def short(name):
    return SHORT.get(name, name)


def parse_event(line):
    """`amixer events` line -> (kind, numid, name), or None."""
    m = _EVENT.search(line)
    return (m.group(1), int(m.group(2)), m.group(3)) if m else None


def parse_cget(text):
    """`amixer cget` output -> the value as text: an enum's item name,
    on/off, a level in dB, a distance in cm, or the number(s). None if
    there's no value line."""
    m = _VALUES.search(text)
    if not m:
        return None
    items = dict(_ITEM.findall(text))
    vals = [items.get(v, v) for v in m.group(1).split(",")]
    i, db = _INT.search(text), _DB.search(text)
    if i and db:
        lo, start, step = int(i.group(1)), float(db.group(1)), float(db.group(2))
        vals = [f"{start + (int(v) - lo) * step:+g} dB" for v in vals]
    elif i and "Distance" in text:
        vals = [f"{v} cm" for v in vals]
    return ", ".join(vals)


def number(value):
    """The number in a control's value text ("+3 dB" -> 3.0), or None."""
    m = _NUM.search(value or "")
    return float(m.group()) if m else None


def channels(stream):
    """The channel count in a read_hw_params() summary, or None."""
    m = re.search(r"(\d+) ch", stream or "")
    return int(m.group(1)) if m else None


def voice_clarity_wanted(ds):
    """The driver's rule for routing the voice processor into the mic
    recording (driver 0.12.2): the master on and at least one effect on."""
    if ds.get("Voice Clarity Capture Switch") != "on":
        return False
    return (any(ds.get(n) == "on" for n in ("Acoustic Echo Cancellation Capture Switch",
                                            "Noise Reduction Capture Switch",
                                            "Mic Smart Volume Capture Switch"))
            or ds.get("Mic EQ Capture Enum", "Off") != "Off"
            or ds.get("Voice Morphing Capture Enum", "Neutral") != "Neutral")


def headphone_mode(ds):
    """The DSP's headphone mode (driver 0.12.2): Headphones, Direct Mode off
    and Headphone Virtualization on. A 5.1 source is folded into L/R there,
    and SBX Surround virtualizes."""
    return (ds.get("Head Unit Output") == "Headphones" and ds.get("Direct Mode Switch") == "off"
            and ds.get("Headphone Virtualization Switch") == "on")


def output_jack(ds):
    """Where the main output plays, as text, or None if unknown."""
    hu = ds.get("Head Unit Output")
    if hu == "Headphones":
        if ds.get("Line Out Virtualization Switch") == "on" and ds.get("Direct Mode Switch") == "off":
            return "RCA out (headphone mix)"
        return "headphones"
    if hu == "Speakers":
        return "RCA out"
    return hu


def capture_bits():
    """The bit depth the card records (the driver's cap_bits), or None."""
    try:
        return int(ae9.module_param("cap_bits"))
    except (TypeError, ValueError):
        return None


def wire_bits(fmt):
    """Bits on the optical wire for a sample format ("S32_LE" or "S32LE"), or None."""
    return WIRE_BITS.get(str(fmt or "").upper().replace("_", ""))


def read_format(path):
    """The sample format of an open PCM ("S32_LE"), or None while it's closed."""
    return read_field(path, "format")


def read_field(path, field):
    """A field of an open PCM's hw_params ("format", "rate"), or None while it's closed."""
    try:
        with open(path) as f:
            m = re.search(rf"^{field}: (\S+)", f.read(), re.M)
    except OSError:
        return None
    return m.group(1) if m else None


def read_hw_params(path=None):
    """The open playback stream as 'S32_LE, 2 ch, 48000 Hz, period 1024,
    buffer 4096', or None while it's closed (or the card is gone)."""
    try:
        with open(path or HW_PARAMS) as f:
            text = f.read()
    except OSError:
        return None
    fields = dict(re.findall(r"^(\w+): (\S+)", text, re.M))
    if "format" not in fields:
        return None
    parts = [fields["format"]]
    if "channels" in fields:
        parts.append(f"{fields['channels']} ch")
    if "rate" in fields:
        parts.append(f"{fields['rate']} Hz")
    for key, word in (("period_size", "period"), ("buffer_size", "buffer")):
        if key in fields:
            parts.append(f"{word} {fields[key]}")
    return ", ".join(parts)


class ControlWatcher:
    """Follows the watched controls. poll() returns the (name, value) pairs
    seen since the last call: every watched control once at the start (and
    whenever the card comes back), then each change."""

    def __init__(self, card=ae9.CARD_ID, watch=WATCH):
        self.card = card
        self.watch = set(watch)
        self.error = None
        self._q = queue.Queue()
        self._stopping = False
        self._proc = None
        threading.Thread(target=self._run, daemon=True).start()

    def poll(self):
        out = []
        while not self._q.empty():
            out.append(self._q.get())
        return out

    def _cget(self, numid):
        try:
            r = subprocess.run(child_cmd(["amixer", "-c", self.card, "cget", f"numid={numid}"]),
                               capture_output=True, text=True, timeout=3, env=_ENV)
        except (OSError, subprocess.TimeoutExpired):
            return None
        return parse_cget(r.stdout) if r.returncode == 0 else None

    def _run(self):
        while not self._stopping:
            try:
                proc = subprocess.Popen(child_cmd(["amixer", "-c", self.card, "events"]),
                                        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                        text=True, env=_ENV)
            except FileNotFoundError:
                self.error = "amixer not found (install alsa-utils)"
                return
            self._proc = proc
            for line in proc.stdout:
                ev = parse_event(line)
                if not ev or ev[2] not in self.watch or ev[0] not in ("add", "value"):
                    continue
                value = self._cget(ev[1])
                if value is not None:
                    self._q.put((ev[2], value))
            proc.wait()
            if not self._stopping:
                time.sleep(3.0)     # no card (or it went away): try again

    def stop(self):
        self._stopping = True
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
