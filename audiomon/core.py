# SPDX-License-Identifier: GPL-2.0-only
"""Constants, dB helpers, config, driver info. No Qt."""

import functools
import json
import os
import shutil
import subprocess
import sys

import numpy as np

from sbae9kit import ae9

APP_ID = "sbae9-audiomon"
APP_NAME = "SbAe9-AudioMon"
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

CONFIG_DIR = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
CONFIG_PATH = os.path.join(CONFIG_DIR, "sbae9-audiomon", "config.json")
STATE_DIR = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
LOG_PATH = os.path.join(STATE_DIR, "sbae9-audiomon", "ae9-debug.log")
DEFAULT_CONFIG = {
    "mode": "ae9",        # "ae9" or "manual"
    "let_idle": True,     # AE-9 mode: don't keep the card awake
    "always_capture": False,  # AE-9 mode + let_idle: keep What U Hear/Mic/Optical wired anyway
    "frameless": True,    # draw our own title bar, like Sound Blaster Command
    "glass": False,       # see-through, frosted backgrounds (SBC's Glass)
    "glass_opacity": 70,  # percent, 30-95
    "palette": "helix",   # sbae9kit.palettes: command, helix, albino, wololo
    "ui_scale": 100,      # percent, 70-150
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


@functools.lru_cache(maxsize=None)
def has_option(tool, option):
    """Whether a PipeWire tool knows a command-line option, from its --help.
    PipeWire 1.0.x (Ubuntu 24.04, Mint 22) has no `pw-record --raw` and no
    `pw-link --wait` (1.2+); 1.4+ needs --raw to write raw samples to stdout."""
    try:
        r = subprocess.run([tool, "--help"], capture_output=True, text=True, timeout=5,
                           env=dict(os.environ, LC_ALL="C"))
    except (OSError, subprocess.TimeoutExpired):
        return False
    return option in r.stdout + r.stderr


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
    """The driver's version and srcversion, for the debug log (sbae9kit.ae9)."""
    if not os.path.isdir(ae9.MODULE):
        return f"{ae9.DRIVER} not loaded"
    return f"{ae9.DRIVER} {ae9.module_version() or '?'} srcversion {ae9.module_srcversion() or '?'}"


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
        for k in ("let_idle", "always_capture", "frameless", "glass"):
            if isinstance(user.get(k), bool):
                cfg[k] = user[k]
        if isinstance(user.get("page"), int):
            cfg["page"] = user["page"]
        if isinstance(user.get("glass_opacity"), int):
            cfg["glass_opacity"] = user["glass_opacity"]
        if isinstance(user.get("ui_scale"), int):
            cfg["ui_scale"] = user["ui_scale"]
        if isinstance(user.get("palette"), str):
            cfg["palette"] = user["palette"]
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
