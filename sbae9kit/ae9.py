# SPDX-License-Identifier: GPL-2.0-only
"""Read-only facts about the Sound Blaster AE-9 (and, through sbae9kit.boards,
the AE-7): which cards and PipeWire nodes are ours, what the driver is and
offers, and what its PCMs are open at.

Card detection (from sbae9kit.boards, re-exported here): find_cards(),
find_card(), current(), refresh(), card_id(), Card, Model, MODELS. Functions
taking `card_id` default to the detected card (card_id(): "AE9" when none
is found, so an AE-9 machine behaves as before).

Nothing here writes to the card or to PipeWire.
"""

import os
import re
import time

from . import boards
from .boards import (  # noqa: F401
    Card, Model, MODELS, card_id, current, find_card, find_cards, identify, refresh,
)

DRIVER = "snd_hda_codec_creative_ae9"   # the module, for every board
CARD_ID = "AE9"     # the AE-9's ALSA id; for the card that's here, use card_id()
MODULE = f"/sys/module/{DRIVER}"
MODULE_PARAMS = f"{MODULE}/parameters"

# Props that name the card. Matched by name, never by PCI address or card
# number (those change between machines and boots).
AE9_KEYS = ("node.name", "api.alsa.path", "object.path", "alsa.id", "alsa.card_name",
            "api.alsa.card.name", "alsa.driver_name", "device.name")

# Nodes the AE-9 apps make themselves (e.g. sbae9-audiomon.ae9.monitor):
# they mention the AE-9 but aren't the card.
APP_NODE_PREFIXES = ("sbae9-",)

# The AE-9's extra capture devices. Each is its own PipeWire source, made
# by an optional drop-in from the driver (config/ae9-optical-in.conf,
# config/ae9-what-u-hear.conf, config/ae9-rear-rca-in.conf).
EXTRA_INPUTS = {
    "optical_in": ("alsa_input.ae9_optical", 1),       # optical (S/PDIF) in, hw:AE9,1
    "what_u_hear": ("alsa_input.ae9_what_u_hear", 2),  # what the card plays, hw:AE9,2
    "rear_rca": ("alsa_input.ae9_rear_rca", 3),        # head unit rear RCA, hw:AE9,3 (0.10.1)
}


# The AE-9's separate optical output (driver 0.12.0, hw:AE9,4): the card
# profile's alsa_output.pci-<addr>.optical_out, or the old drop-in's name.
# Never the main (analog) output.
OPTICAL_OUT = ("alsa_output.ae9_optical", 4)


def is_app_node(props):
    return str(props.get("node.name", "")).startswith(APP_NODE_PREFIXES)


def _true(v):
    return v is True or str(v).lower() in ("true", "1")


def is_our_card(props, card=None):
    """A PipeWire node (or device) of one of our cards (or of `card`, a Card),
    from its props. A node from another driver never counts, even one named
    after the board (an AE-7 on the in-tree snd_hda_intel)."""
    if is_app_node(props) or _true(props.get("node.virtual")):
        return False   # virtual nodes (echo-cancel, filter-chain, loopback) only borrow the name
    drv = str(props.get("alsa.driver_name", "")).replace("-", "_")
    if drv and drv != DRIVER:
        return False
    if card is None:
        return bool(drv) or boards.model_for_props(props) is not None
    if str(props.get("alsa.id", "")).upper() == card.id.upper():
        return True
    path = str(props.get("api.alsa.path", "")).upper()
    if path.startswith(f"HW:{card.id.upper()},"):
        return True
    if drv and path.startswith(f"HW:{card.index},"):   # PipeWire's usual numeric path
        return True
    if f".{card.model.node_prefix}_" in str(props.get("node.name", "")).lower():
        return True
    return bool(drv) and str(props.get("alsa.card", "")) == str(card.index)


def is_ae9(props):
    """Old name of is_our_card() (any of our cards)."""
    return is_our_card(props)


_OUR_HW = re.compile(r"^hw:(%s),(\d+)$" % "|".join(m.card_id for m in MODELS.values()), re.I)
_NUM_HW = re.compile(r"^hw:\d+,(\d+)$")


def _our_dev(props):
    """The PCM device number of one of our cards' nodes: from hw:<our id>,N, or
    from PipeWire's numeric hw:<index>,N when alsa.driver_name is ours. None."""
    path = str(props.get("api.alsa.path", ""))
    m = _OUR_HW.match(path)
    if m:
        return int(m.group(2))
    m = _NUM_HW.match(path)
    if m and str(props.get("alsa.driver_name", "")).replace("-", "_") == DRIVER:
        return int(m.group(1))
    return None


def input_kind(props):
    """For an AE-9 Audio/Source: "optical_in", "what_u_hear", "rear_rca" or "mic" (the
    XLR / line-in, hw:AE9,0)."""
    name = str(props.get("node.name", "")).lower()
    desc = str(props.get("node.description", "")).lower()
    dev_here = _our_dev(props)
    for key, (node_name, dev) in EXTRA_INPUTS.items():
        if name == node_name or dev_here == dev:
            return key
    if "what_u_hear" in name or "what u hear" in desc:
        return "what_u_hear"
    if "rear_rca" in name or "rear rca" in desc:
        return "rear_rca"
    if "optical" in name or "optical" in desc or "iec958" in name:
        return "optical_in"
    return "mic"


def output_kind(props):
    """For an AE-9 Audio/Sink: "optical_out" (the separate optical output,
    hw:AE9,4) or "main" (speakers / headphones, hw:AE9,0)."""
    name = str(props.get("node.name", "")).lower()
    desc = str(props.get("node.description", "")).lower()
    if name == OPTICAL_OUT[0] or _our_dev(props) == OPTICAL_OUT[1]:
        return "optical_out"
    if "optical" in name or "iec958" in name or "optical out" in desc:
        return "optical_out"
    return "main"


# --- the driver ------------------------------------------------------------------
def _read(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


def module_param(name):
    """A driver option (module parameter) as text, or None."""
    return _read(os.path.join(MODULE_PARAMS, name))


def module_version():
    return _read(f"{MODULE}/version")


def module_srcversion():
    return _read(f"{MODULE}/srcversion")


def version_tuple(version):
    try:
        return tuple(int(x) for x in (version or "").split("-")[0].split("."))
    except ValueError:
        return ()


def driver_at_least(*want, version=None):
    """True if the driver (or `version`) is `want` (e.g. 0, 7, 0) or newer."""
    have = version_tuple(module_version() if version is None else version)
    return bool(have) and have >= want


def has_capture_pcm(device, card_id=None):
    """The driver offers capture device `device` (1 = optical in, 2 = What U Hear,
    3 = head unit rear RCA). `card_id`: the detected card by default."""
    return os.path.exists(boards.proc_path(card_id or boards.card_id(), f"pcm{device}c"))


def parse_hw_params(text):
    """(rate, channels) from a /proc/asound hw_params file, or None (closed)."""
    rate = re.search(r"^rate:\s*(\d+)", text or "", re.M)
    if not rate:
        return None
    ch = re.search(r"^channels:\s*(\d+)", text, re.M)
    return int(rate.group(1)), int(ch.group(1)) if ch else 2


def pcm_open_at(pcm="pcm0p", card_id=None):
    """(rate, channels) a PCM is open at: pcm0p playback, pcm0c mic / line-in,
    pcm1c optical in, pcm2c What U Hear. None when it's closed. `card_id`:
    the detected card by default."""
    return parse_hw_params(_read(boards.proc_path(card_id or boards.card_id(), pcm, "sub0", "hw_params")))


# --- ae9-reload's notice (driver 0.12.13+) ---------------------------------------
# While ae9-reload reloads the driver it writes its own PID ($$, as the desktop
# user) to /run/user/<uid>/ae9-reload, so every app holding the card lets go of
# it, and removes it on any exit (trap EXIT; a kill -9 leaves a stale PID). Run
# as plain root (no SUDO_USER), or with no /run/user/<uid>, it writes none: the
# apps then let go only once the driver says the card stopped answering.
RELOAD_NOTICE = "ae9-reload"
RELOAD_NOTICE_MAX_S = 120     # an empty / unreadable notice counts this long (ae9-reload waits 90)


def reload_notice_path():
    """Where ae9-reload writes its notice: $XDG_RUNTIME_DIR/ae9-reload, or
    /run/user/<uid>/ae9-reload."""
    run = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    return os.path.join(run, RELOAD_NOTICE)


def reload_notice_active(path=None, now=None):
    """True while ae9-reload is reloading the driver: its notice is there and
    the PID in it is alive (/proc/<pid>). A notice whose PID is gone is stale
    and doesn't count; an empty or unreadable one counts for
    RELOAD_NOTICE_MAX_S after it was written (by mtime), then is ignored.
    Read-only, no state: poll it as often as you like."""
    path = path or reload_notice_path()
    try:
        mtime = os.stat(path).st_mtime
    except OSError:
        return False
    try:
        with open(path) as f:
            pid = int(f.read().split()[0])
    except (OSError, ValueError, IndexError):
        return (time.time() if now is None else now) - mtime < RELOAD_NOTICE_MAX_S
    return pid > 0 and os.path.exists(f"/proc/{pid}")
