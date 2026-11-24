# SPDX-License-Identifier: GPL-2.0-only
"""Which Creative cards are here: the AE-9 now, the AE-7 later, the AE-5
reserved. Read-only, no Qt: it reads sysfs and /proc/asound files only.
(Its names are also in sbae9kit.ae9, which is where apps use them from.)

Only cards bound to the AE driver count: snd-hda-codec-creative-ae9 drives the
PCI device itself, so /sys/class/sound/cardN/device/driver names it. The same
boards under the in-tree snd_hda_intel are never ours. Each of our cards is
identified, first match wins, by:

    0. the driver's "Card Model" control, once it has one (pass `card_model`)
    1. its ALSA card id: AE9, AE7 (AE5 reserved)
    2. its card longname / shortname: "Sound Blaster AE-9" / "... AE-7" / "... AE-5"
    3. its PCI subsystem device: 0x0071 AE-9, 0x0081 AE-7, 0x0051 / 0x0191 AE-5

Capability first: whether a card has a feature is decided by the driver's
controls and PCMs (mixer.has(), card.has_pcm()). A model's features only
cover what controls can't say (wording, pictures, which pages to offer), and
are never derived from which PipeWire nodes exist.

Naming, in both apps:
    model.short    "AE-9" / "AE-7": title bar, labels ("AE-7 Optical Out")
    model.name     "Sound Blaster AE-9": full sentences, About, notices
    model.key      "ae9" / "ae7": settings keys, file and asset names, logs
    model.node_prefix  "ae9" / "ae7": PipeWire drop-in node names
                   (alsa_input.<prefix>_optical)
    card.id        the ALSA card id ("AE9"): card.hw(4) = "hw:AE9,4",
                   card.proc = "/proc/asound/AE9"
    card.index     the ALSA card number: changes between boots, never store it
The driver module (snd-hda-codec-creative-ae9) and its debugfs folder
(creative_ae9) keep the AE-9's name on every board.
"""

import os
import re
from dataclasses import dataclass, field

DRIVER = "snd_hda_codec_creative_ae9"
CREATIVE = 0x1102
DEFAULT_ID = "AE9"     # what card_id() says when no card is found (as before detection)
ROOT = "/"             # tests point this at a fake tree (testing.fake_sound_tree)


@dataclass(frozen=True)
class Model:
    key: str                   # "ae9"
    short: str                 # "AE-9"
    name: str                  # "Sound Blaster AE-9"
    card_id: str               # the driver's ALSA card id, "AE9"
    node_prefix: str           # "ae9": alsa_input.ae9_optical
    subsys: tuple              # PCI subsystem device ids
    supported: bool            # the driver drives it (the AE-5 is reserved: no)
    provisional: bool = False  # features not yet confirmed on the card
    features: dict = field(default_factory=dict, compare=False)
    pci_devices: tuple = (0x0010, 0x0012)   # Creative PCI device ids its INF lists
                                            # (0x0010 on every AE-9 / AE-7 seen; facts
                                            # only: the driver binding decides)

    def __str__(self):
        return self.short

    def has(self, feature):
        """A wording-level feature (False when unknown)."""
        return bool(self.features.get(feature, False))


# Wording-level features (controls and PCMs decide; these only word it):
#   head_unit        the AE-9's external head unit (XLR / 3.5 mm, knob)
#   optical_in       an optical input
#   direct_51        Direct Mode in 5.1 (the AE-7's is stereo only: Windows
#                    switches 5.1 speakers to Direct Stereo)
#   dsd              DSD64
#   hp_gain          "switch" (on the head unit) or "software" (a Headphone Gain control)
#   hp_gain_names    the software Headphone Gain's levels, as Windows words them
#   dsp_51_analog    analog 5.1 in DSP mode
#   optical_out_max  the optical output's top rate, Hz (None: not known yet)
#   dac_filters      DAC filter choices (ES9038Q2M: 7; ES9018K2M: 3)
#   dac_filter_names the DAC filters as Windows words them, for a card whose
#                    driver has no DAC Filter control yet (the control's own
#                    item names always win: the AE-9's order differs)
#   lighting         a Lighting page (only the AE-5 / AE-5 Plus: RGB + LED header)
#   encoder          an Encoder page (Dolby Digital Live / DTS Connect); the
#                    plain AE-5 has none, the AE-5 Plus has one
#   aux_in           an Aux (line) input (the AE-9's)
#   line_in          a Line-In input
#   input_select     a Microphone / Line-In combo jack ("Multipurpose Input Select")
#   front_mic        a Front Microphone (front-panel audio header)
#   speaker_setup    Speaker Setup: Bass Redirect, speaker levels and distances
# Rates and formats (None: the app keeps its own driver-version logic, as on
# the AE-9; a version check can't tell the cards apart, both report the same):
#   dsp_rates        playback rates in DSP mode
#   direct_rates     playback rates in Direct Mode
#   direct_channels  Direct Mode's channel count
#   mic_rates        mic capture rates
#   whu_rates        What U Hear capture rates
#   formats          playback sample formats
# Sources: the driver for the AE-9; Creative's Sound Blaster Command support
# notes (AE-9 200213, AE-7 200221, AE-5 / AE-5 Plus 200282) for the others.
_SW_GAIN = ("Low (16 - 31 ohm)", "Normal (32 - 149 ohm)", "High (150 - 600 ohm)")
MODELS = {
    "ae9": Model("ae9", "AE-9", "Sound Blaster AE-9", "AE9", "ae9", (0x0071,), True, features={
        "head_unit": True, "optical_in": True, "direct_51": True, "dsd": True,
        "hp_gain": "switch", "dsp_51_analog": True, "optical_out_max": 192000,
        "dac_filters": 7, "lighting": False, "encoder": True,
        "aux_in": True, "line_in": False, "input_select": False, "front_mic": False,
        "speaker_setup": True, "dsp_rates": None, "direct_rates": None,
        "direct_channels": None, "mic_rates": None, "whu_rates": None, "formats": None}),
    "ae7": Model("ae7", "AE-7", "Sound Blaster AE-7", "AE7", "ae7", (0x0081,), True,
                 provisional=True, features={     # driver kit 11 (ae7-k11); confirm on 0.13.0
        "head_unit": False, "optical_in": False, "direct_51": False,
        "dsd": False,                     # the driver has no DSD
        "hp_gain": "software", "hp_gain_names": _SW_GAIN, "dsp_51_analog": True,
        "optical_out_max": None,          # the driver has no optical out (the board does)
        "dac_filters": 3,
        "dac_filter_names": ("Fast Roll Off", "Slow Roll Off", "Minimum Phase"),
        "lighting": False,
        "encoder": False,                 # no encoder PCM (it rides the optical)
        "aux_in": False, "line_in": True, "input_select": True, "front_mic": False,
        "speaker_setup": False,
        "dsp_rates": (48000,),            # stereo and 5.1
        "direct_rates": (44100, 48000, 88200, 96000, 192000, 384000),   # no 176.4
        "direct_channels": 2,
        "mic_rates": (44100, 48000, 96000),
        "whu_rates": (44100, 48000),
        "formats": ("S32_LE",)}),
    "ae5": Model("ae5", "AE-5", "Sound Blaster AE-5", "AE5", "ae5", (0x0051, 0x0191), False,
                 provisional=True, pci_devices=(0x0012,), features={
        "head_unit": False, "optical_in": False, "direct_51": False, "dsd": False,
        "hp_gain": "software", "hp_gain_names": _SW_GAIN, "dsp_51_analog": True,
        "optical_out_max": 96000, "dac_filters": 3, "lighting": True,
        "encoder": False,     # the AE-5 Plus has one: told apart once the AE-5 is supported
        "aux_in": False, "line_in": True, "input_select": True, "front_mic": True}),
}


@dataclass(frozen=True)
class Card:
    """One card bound to the AE driver."""
    index: int                 # ALSA card number (cardN)
    id: str                    # ALSA card id, "AE9"
    model: Model
    longname: str = ""         # "Creative Labs Sound Blaster AE-9 at 0x... irq 44"
    subsystem: int | None = None   # PCI subsystem device, 0x0071
    source: str = ""           # what identified it: card_model / card_id / longname / subsys
    root: str = field(default="/", compare=False, repr=False)

    @property
    def short(self):
        return self.model.short

    @property
    def name(self):
        return self.model.name

    @property
    def key(self):
        return self.model.key

    @property
    def features(self):
        return self.model.features

    def has(self, feature):
        return self.model.has(feature)

    def hw(self, dev=0):
        """The ALSA device name: "hw:AE9,4"."""
        return f"hw:{self.id},{dev}"

    @property
    def proc(self):
        """/proc/asound/<id> (under the test root, if any)."""
        return os.path.join(self.root, "proc", "asound", self.id)

    def has_pcm(self, pcm):
        """The driver offers PCM `pcm`: "4p" (optical out), "1c" (optical in), ..."""
        return os.path.exists(os.path.join(self.proc, f"pcm{pcm}"))


def _read(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


def _hex(text):
    try:
        return int(text, 16)
    except (TypeError, ValueError):
        return None


def model_by_card_id(card_id):
    for m in MODELS.values():
        if card_id and card_id.upper() == m.card_id:
            return m
    return None


def model_by_name(text):
    """A model from a longname, a "Card Model" value or any text naming it
    ("Sound Blaster AE-7", "AE-9", "AE9"); None if it names none or several."""
    t = (text or "").upper()
    found = [m for m in MODELS.values() if re.search(rf"\bAE-?{m.key[2:]}\b", t)]
    return found[0] if len(found) == 1 else None


def model_by_subsys(subsys):
    for m in MODELS.values():
        if subsys in m.subsys:
            return m
    return None


def identify(card_id=None, longname=None, subsys=None, card_model=None):
    """(model, source) from one card's facts, in the detection order; (None, None)."""
    for source, model in (("card_model", model_by_name(card_model) if card_model else None),
                          ("card_id", model_by_card_id(card_id)),
                          ("longname", model_by_name(longname)),
                          ("subsys", model_by_subsys(subsys))):
        if model:
            return model, source
    return None, None


def _names(root):
    """{index: (shortname, longname)} from /proc/asound/cards."""
    out = {}
    lines = (_read(os.path.join(root, "proc", "asound", "cards")) or "").splitlines()
    for i, line in enumerate(lines):
        m = re.match(r"\s*(\d+)\s+\[[^\]]*\]:\s*(.*)", line)
        if m:
            short = m.group(2).split(" - ", 1)[-1].strip()
            out[int(m.group(1))] = (short, lines[i + 1].strip() if i + 1 < len(lines) else "")
    return out


def _driver(card_dir):
    try:
        return os.path.basename(os.readlink(os.path.join(card_dir, "device", "driver"))).replace("-", "_")
    except OSError:
        return None


def find_cards(root=None, supported_only=True, card_model=None):
    """Every card bound to the AE driver, by ALSA card number. `card_model`:
    {index: the driver's "Card Model" value}, once the driver has that control.
    `supported_only=False` also returns reserved models (an AE-5)."""
    root = ROOT if root is None else root
    sys_sound = os.path.join(root, "sys", "class", "sound")
    try:
        entries = os.listdir(sys_sound)
    except OSError:
        return []
    names = _names(root)
    cards = []
    for entry in entries:
        m = re.fullmatch(r"card(\d+)", entry)
        if not m:
            continue
        index, card_dir = int(m.group(1)), os.path.join(sys_sound, entry)
        if _driver(card_dir) != DRIVER:
            continue
        card_id = _read(os.path.join(card_dir, "id")) or ""
        dev = os.path.join(card_dir, "device")
        subsys = _hex(_read(os.path.join(dev, "subsystem_device")))
        if _hex(_read(os.path.join(dev, "subsystem_vendor"))) not in (None, CREATIVE):
            subsys = None
        short, longname = names.get(index, ("", ""))
        model, source = identify(card_id, f"{short}\n{longname}", subsys,
                                 (card_model or {}).get(index))
        if model and (model.supported or not supported_only):
            cards.append(Card(index, card_id or model.card_id, model, longname, subsys, source, root))
    return sorted(cards, key=lambda c: c.index)


def find_card(prefer=None, **kw):
    """The card to use: the one whose key or id is `prefer` (a saved pick),
    else the first by ALSA card number. None when there's none."""
    cards = find_cards(**kw)
    for c in cards:
        if prefer and prefer.lower() in (c.key, c.id.lower()):
            return c
    return cards[0] if cards else None


_current = []    # [] = not looked yet, [None] = looked, none found


def current():
    """The detected card (find_card()), cached until refresh(). None if none."""
    if not _current:
        _current.append(find_card())
    return _current[0]


def refresh():
    """Look again (after a driver reload or card drop-out). Returns current()."""
    _current.clear()
    return current()


def proc_path(card_id_, *parts):
    """/proc/asound/<card id>/... under ROOT (a fake tree in tests)."""
    return os.path.join(ROOT, "proc", "asound", card_id_, *parts)


def card_id():
    """The detected card's ALSA id, or "AE9" when none is found, so an AE-9
    machine behaves as before detection."""
    c = current()
    return c.id if c else DEFAULT_ID


def model_for_props(props):
    """The model a PipeWire node / device belongs to, from its props, or None."""
    blob = " ".join(str(props.get(k, "")) for k in
                    ("node.name", "api.alsa.path", "object.path", "alsa.id", "alsa.card_name",
                     "api.alsa.card.name", "device.name")).lower()
    for m in MODELS.values():
        if m.supported and (m.key in blob or m.short.lower() in blob):
            return m
    return None
