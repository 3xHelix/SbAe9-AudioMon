# SPDX-License-Identifier: GPL-2.0-only
"""Read-only facts about the Sound Blaster AE-9: which PipeWire nodes are
its, what the driver is and offers, and what its PCMs are open at.

Nothing here writes to the card or to PipeWire.
"""

import os
import re

DRIVER = "snd_hda_codec_creative_ae9"
CARD_ID = "AE9"
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


def is_app_node(props):
    return str(props.get("node.name", "")).startswith(APP_NODE_PREFIXES)


def _true(v):
    return v is True or str(v).lower() in ("true", "1")


def is_ae9(props):
    """A PipeWire node (or device) of the AE-9 itself, from its props."""
    if is_app_node(props) or _true(props.get("node.virtual")):
        return False   # virtual nodes (echo-cancel, filter-chain, loopback) only borrow the name
    blob = " ".join(str(props.get(k, "")) for k in AE9_KEYS).lower()
    return "ae9" in blob or "ae-9" in blob


def input_kind(props):
    """For an AE-9 Audio/Source: "optical_in", "what_u_hear", "rear_rca" or "mic" (the
    XLR / line-in, hw:AE9,0)."""
    name = str(props.get("node.name", "")).lower()
    desc = str(props.get("node.description", "")).lower()
    path = str(props.get("api.alsa.path", ""))
    for key, (node_name, dev) in EXTRA_INPUTS.items():
        if name == node_name or (path.startswith(f"hw:{CARD_ID},") and path.endswith(f",{dev}")):
            return key
    if "what_u_hear" in name or "what u hear" in desc:
        return "what_u_hear"
    if "rear_rca" in name or "rear rca" in desc:
        return "rear_rca"
    if "optical" in name or "optical" in desc or "iec958" in name:
        return "optical_in"
    return "mic"


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


def has_capture_pcm(device, card_id=CARD_ID):
    """The driver offers capture device `device` (1 = optical in, 2 = What U Hear,
    3 = head unit rear RCA)."""
    return os.path.exists(f"/proc/asound/{card_id}/pcm{device}c")


def parse_hw_params(text):
    """(rate, channels) from a /proc/asound hw_params file, or None (closed)."""
    rate = re.search(r"^rate:\s*(\d+)", text or "", re.M)
    if not rate:
        return None
    ch = re.search(r"^channels:\s*(\d+)", text, re.M)
    return int(rate.group(1)), int(ch.group(1)) if ch else 2


def pcm_open_at(pcm="pcm0p", card_id=CARD_ID):
    """(rate, channels) a PCM is open at: pcm0p playback, pcm0c mic / line-in,
    pcm1c optical in, pcm2c What U Hear. None when it's closed."""
    return parse_hw_params(_read(f"/proc/asound/{card_id}/{pcm}/sub0/hw_params"))
