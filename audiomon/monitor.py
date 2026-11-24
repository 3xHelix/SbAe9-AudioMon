# SPDX-License-Identifier: GPL-2.0-only
"""The AE-9 monitor (auto-wiring, SILENT detection) and the manual meters."""

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QScrollArea, QSizePolicy, QVBoxLayout, QWidget

import os
import shutil

from sbae9kit import ae9

from . import __version__, core, driverstate, gated, onesided, pipewire
from .core import CHANNEL_MAPS, FLOOR_DB, choose_channels, fmt_db, save_config
from .pipewire import Snapshot, find_ae9, is_ours
from .widgets import MeterPanel, label, log_view

# SILENT detection (AE-9 mode): the output monitor has signal but What U Hear
# stays below WUH_SILENT_DB for longer than SILENT_AFTER seconds.
MON_SIGNAL_DB = -50.0   # monitor counts as "has signal" above this (peak)
WUH_SILENT_DB = -60.0   # What U Hear counts as silent below this (peak)
SILENT_AFTER = 0.5      # seconds
WIRE_GRACE = 1.0        # ignore What U Hear for this long after (re)wiring it
PW_RESTART_GRACE = 3.0  # no SILENT for this long after PipeWire comes back
CHANGE_NOTE_FOR = 30.0  # a dropout line names a driver change this recent
HW_PARAMS_EVERY = 0.5   # seconds between looks at the stream's format while it runs
SETTLE = 3.0            # after a stream starts or a driver setting changes: no ONE-SIDED
                        # (and no SILENT after a Direct Mode switch, or while a new stream
                        # moves into the DSP), since the driver restarts or reroutes it


class MeterRowScroll(QScrollArea):
    """A frameless scroll area around a row of meters. Sideways, it scrolls
    when the row needs more room (more channels), instead of making the
    window wider, so the window keeps the size the user gave it. In height it
    always shows the meters whole (room for the scroll bar included, so the
    bar coming and going doesn't change the window either)."""

    def __init__(self, widget):
        super().__init__()
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setWidget(widget)

    def _bar(self):
        return self.horizontalScrollBar().sizeHint().height()

    def sizeHint(self):
        hint = self.widget().sizeHint()
        return QSize(hint.width(), hint.height() + self._bar())

    def minimumSizeHint(self):
        # the meters' full height (scale and stats rows), never squeezed
        return QSize(0, self.widget().sizeHint().height() + self._bar())


def fixed_size_scroll(widget):
    return MeterRowScroll(widget)


class ManualView(QWidget):
    """The original two meters with unlinked ports, wired by hand."""

    def __init__(self, win):
        super().__init__()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)
        outer.addWidget(fixed_size_scroll(row))
        self.panels = []
        for m in win.cfg["meters"]:
            def on_channels(ch, m=m):
                m["channels"] = ch
                save_config(win.cfg)
            p = MeterPanel(m["label"], m["name"], m["channels"], on_channels=on_channels)
            self.panels.append(p)
            layout.addWidget(p)

    def tick(self, now, dt):
        for p in self.panels:
            p.tick(now, dt)

    def stop(self):
        for p in self.panels:
            p.stop()


FIXED_TAPS = (
    # key, full title (log lines, the meter node's name), meter node name,
    # gated (only wired while the AE-9 sink runs when "let the AE-9 idle" is on,
    # so we never hold its capture PCMs open)
    ("monitor", "AE-9 Output (monitor)", "sbae9-audiomon.ae9.monitor", False),
    ("optical_out", "AE-9 Optical Out (monitor)", "sbae9-audiomon.ae9.optical_out", False),
    ("wuh", "AE-9 What U Hear", "sbae9-audiomon.ae9.wuh", True),
    ("mic", "AE-9 Head Unit Mic / Aux", "sbae9-audiomon.ae9.mic", True),
    ("optical", "AE-9 Optical In", "sbae9-audiomon.ae9.optical", True),
    ("rear_rca", "AE-9 Rear RCA In", "sbae9-audiomon.ae9.rear_rca", True),
)

# The card PCM behind each meter. Capability first: on a detected card, the
# optional meters are shown only when the driver offers their PCM (an AE-7
# has no optical in, rear RCA in or head unit). The output and What U Hear
# meters are always there: dropout detection needs both.
TAP_PCMS = {"optical_out": f"{ae9.OPTICAL_OUT[1]}p", "mic": "0c", "optical": "1c", "rear_rca": "3c"}


def card_taps(card=None):
    """FIXED_TAPS for `card` (an sbae9kit Card, or None when none is detected:
    all of them, as before detection), titled with its model ("AE-7 Output")."""
    out = []
    for key, title, node_name, gated in FIXED_TAPS:
        if card is not None:
            if key in TAP_PCMS and not card.has_pcm(TAP_PCMS[key]):
                continue
            title = title.replace("AE-9", card.short, 1)
            if key == "mic" and not card.has("head_unit"):
                title = title.replace("Head Unit ", "", 1)
        out.append((key, title, node_name, gated))
    return tuple(out)


# On screen each meter shows a short name (and its state); where it is, jack by
# jack, is on hover.
SHORT_NAMES = {"monitor": "Output", "optical_out": "Optical Out", "wuh": "What U Hear",
               "mic": "Mic / Aux", "optical": "Optical In", "rear_rca": "Rear RCA In"}
DETAILS = {
    "monitor": "What PipeWire sends to the AE-9: the card's RCA out in speaker mode, or the "
               "head unit's headphone jacks (3.5 mm and 1/4\") in headphone mode.",
    "optical_out": "What PipeWire sends to the separate optical output (hw:AE9,4: "
                   "alsa_output.pci-….optical_out, \"AE-9 Optical Out\", driver 0.12.0+). Independent of the main output: stereo only, no SBX or EQ, "
                   "and the card's Master volume doesn't change it. While nothing plays to it, the "
                   "optical jack carries the main output instead (with Optical Out Mirror on, driver "
                   "0.12.1+; always on older drivers) or stays silent (mirror off), and this meter "
                   "reads silence.",
    "wuh": "What the card actually plays, after its DSP (the card's What U Hear loopback, "
           "hw:AE9,2: alsa_input.pci-….what_u_hear from driver 0.11.0's card profile, or "
           "alsa_input.ae9_what_u_hear from the older hand-installed conf).",
    "mic": "The head unit's input: the XLR/TRS jack and the 3.5 mm mic jack, which share it "
           "(alsa_input.pci-…analog-stereo). With Voice Clarity on (driver 0.12.2+), the "
           "recording runs through the 16 kHz voice processor: 48 kHz, band-limited to 8 kHz.",
    "optical": "The card's optical in jack (alsa_input.pci-….optical_in on driver 0.11.0+, or "
               "alsa_input.ae9_optical from the older conf). With no light on the jack it reads "
               "\"no signal\" on older drivers, silence on 0.11.0+.",
    "rear_rca": "The head unit's rear RCA L/R input jacks, recorded alongside the mic "
                "(hw:AE9,3: alsa_input.pci-….rear_rca on driver 0.11.0+, or alsa_input.ae9_rear_rca "
                "on 0.10.1 with config/ae9-rear-rca-in.conf).",
}


# PipeWire's sample format -> the bit depth it carries.
FORMAT_BITS = {"S16": 16, "S24": 24, "S32": 32, "F32": 32, "F64": 64}


def node_format(node):
    """A node's current PipeWire Format param (a dict), or None when idle."""
    for f in (getattr(node, "params", None) or {}).get("Format") or []:
        return f
    return None


def format_text(node, bits=None):
    """'48 kHz · 24-bit' for a node's current PipeWire format, '' when idle.

    The rate is the node's own; `bits` overrides the sample format's depth
    where the card uses fewer bits than the container (the card plays
    24-bit from an S32LE stream by default). An IEC958 (passthrough) format
    reads '48 kHz · AC3 passthrough'."""
    f = node_format(node)
    if f is None:
        return ""
    parts = []
    rate = f.get("rate")
    if rate:
        parts.append(f"{rate / 1000:g} kHz")
    if str(f.get("mediaSubtype") or "").lower() == "iec958":
        codec = str(f.get("iec958Codec") or f.get("codec") or "").upper()
        parts.append(f"{codec} passthrough".strip())
    else:
        depth = bits or FORMAT_BITS.get(str(f.get("format") or "").upper()[:3])
        if depth:
            parts.append(f"{depth}-bit")
    return " · ".join(parts)


class SilenceDetector:
    """SILENT = output monitor has signal but What U Hear stays quiet."""

    def __init__(self, log, on_change, note=lambda now: ""):
        self.log = log
        self.on_change = on_change
        self.note = note
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
                     f"What U Hear {wuh} for {now - onset:.1f}s | sink {sink_state} | {core.driver_info()}"
                     + self.note(now))
            self.on_change()
        elif not silent and self.active:
            if sink_state != "running":
                why = f"playback stopped, AE-9 sink {sink_state}"
            elif not mon_on:
                why = "output monitor went quiet"
            elif wuh_ready_since is None:
                why = "What U Hear unwired/absent"
            else:
                why = f"What U Hear back at {fmt_db(wuh_db if wuh_db is not None else FLOOR_DB)} dBFS"
            self.end(now, why)

    def end(self, now, why):
        if self.active:
            self.active = False
            self.log(f"SILENT #{self.count} end after {now - self.started:.1f}s ({why})")
            self.on_change()


def stopped_notice():
    """What to do once the card stopped answering: driver 0.12.9's ae9-reload
    when it's installed, else the steps SbAe9-SBC shows."""
    if shutil.which("ae9-reload"):
        return (f"The {core.card_short()} stopped answering. AudioMon has let go of the card. Reload the "
                "driver with: ae9-reload (in a terminal), or power the PC off.")
    return (f"The {core.card_short()} stopped answering. AudioMon has let go of the card so the driver can be "
            "reloaded: reload the driver or power the PC off (SbAe9-SBC shows the steps).")


def ae7_notice(waiting):
    """What to do about an AE-7 the driver doesn't run (core.ae7_waiting())."""
    if waiting == "kernel":
        return ("A Sound Blaster AE-7 is here, but the driver's AE-7 support (experimental) is off. "
                "Turn it on with: sudo ae9-ae7-enable (in a terminal), then reboot.")
    if waiting == "parked":
        return ("A Sound Blaster AE-7 is here: its experimental support is off, and it's parked (no driver), "
                "because the kernel's own driver can hang the PC on AE-7s with an ASMedia bridge. "
                "Turn the support on with: sudo ae9-ae7-enable (in a terminal), then reboot.")
    return ("A Sound Blaster AE-7 is here, but no driver runs it: enabling it isn't finished. "
            "Check with: ae9-ae7-enable --status (in a terminal).")


class AE9View(QWidget):
    """Auto-wires meters to the AE-9 and watches for dropouts."""

    def __init__(self, win):
        super().__init__()
        self.win = win
        self.log = win.log
        self.let_idle = win.cfg["let_idle"]
        self.always_capture = win.cfg["always_capture"]
        self.graph = pipewire.Graph()
        self.linker = pipewire.Linker()
        self.seen_version = -1
        self.last_reconcile = 0.0
        self.pending = {}           # (out_port, in_port) or ("rm", link_id) -> time queued
        self.present = {}           # tap key -> node id
        self.wired = {}             # tap key -> bool
        self.sink_state = None
        self.wuh_ready_since = None
        self.pw_down = False        # PipeWire lost (restarting or stopped)
        self.pw_restarts = 0        # the graph's restart count we've handled
        self.pw_back_at = -1e9      # when PipeWire last came back
        self.reported_failures = set()
        self.silence = SilenceDetector(self.log, self._silence_changed, note=self._change_note)
        self.onesided = onesided.OneSidedDetector(
            self.log, self._silence_changed, note=self._change_note,
            save_dir=os.path.join(os.path.dirname(core.LOG_PATH), "captures"))
        self.gated = gated.GatedDetector(
            self.log, self._silence_changed, note=self._change_note,
            save_dir=os.path.join(os.path.dirname(core.LOG_PATH), "captures"))
        self.controls = driverstate.ControlWatcher()
        self.driver_state = {}      # watched control -> value
        self.last_change = None     # (text, time) of the latest driver change
        self.first_state = {}       # first values, logged together as one line
        self.first_since = None
        self.stopped = False
        self.stream_format = None   # the open playback stream, from hw_params
        self.settle = {}            # "both" / "onesided" -> (until, why)
        self.next_hw_check = 0.0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)


        row_holder = QWidget()
        row = QHBoxLayout(row_holder)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(14)
        self.fixed = {}
        self.card = core.card()
        self.taps = card_taps(self.card)
        # with two of our cards, meter only the detected one's nodes
        self.match_card = self.card if len(core.cards()) > 1 else None
        for key, title, node_name, _gated in self.taps:
            passive = self.let_idle and key in ("monitor", "optical_out")
            p = MeterPanel(SHORT_NAMES[key], node_name, 2, passive=passive,
                           node_label=title, keep=onesided.KEEP if key in ("monitor", "wuh") else 0,
                           tooltip=f"{title}\n{DETAILS[key]}\nmeter node: {node_name}")
            p.note = "searching…"
            self.fixed[key] = p
            row.addWidget(p)
        layout.addWidget(fixed_size_scroll(row_holder), 0)      # its full height, no more needed

        streams_row = QHBoxLayout()
        streams_row.addWidget(label(f"App streams → {core.card_short()}", "sectionTitle"))
        # the card-stopped notice: one line beside the title (full text on
        # hover), so showing it never changes the window's size
        self.card_notice = label("", "cardNotice")
        self.card_notice.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.card_notice.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.card_notice.hide()
        streams_row.addWidget(self.card_notice, 1)
        layout.addLayout(streams_row)
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
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setWidget(holder)
        self.streams_scroll = scroll
        layout.addWidget(scroll, 1)          # the spare height goes to the app streams
        self._size_streams()

        events_row = QHBoxLayout()
        events_row.addWidget(label("Recent events", "sectionTitle"))
        self.health_lbl = label("", "hint")
        self.health_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        # a long line (several problems) is cut off rather than widening the window
        self.health_lbl.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        events_row.addWidget(self.health_lbl, 1)
        layout.addLayout(events_row)
        self.health = None          # the Driver Health counters, once seen
        self._show_health()
        self.logview = log_view(80)
        self.logview.setMaximumHeight(120)
        layout.addWidget(self.logview)
        self.log.add_view(self.logview)

        self.log(f"SbAe9-AudioMon {__version__} AE-9 mode started (let the AE-9 idle: "
                 f"{'on' if self.let_idle else 'off'}, always capture inputs: "
                 f"{'on' if self.always_capture else 'off'}) | {core.driver_info()}")
        waiting = core.ae7_waiting() if self.card is None else None
        if waiting:
            text = ae7_notice(waiting)
            self.card_notice.setText(text)
            self.card_notice.setToolTip(text)
            self.card_notice.show()
            self.log(text)

    # -- per-frame -------------------------------------------------------

    def tick(self, now, dt):
        if self.stopped:
            return
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

        self._watch_driver(now)
        self._watch_pipewire(now)
        pw_held = self.pw_down or now - self.pw_back_at < PW_RESTART_GRACE
        both_why = self._settling(now, "both")
        if pw_held or both_why:
            self.silence.end(now, "PipeWire restarted" if pw_held else both_why)
        else:
            self.silence.update(now, self.fixed["monitor"].tick_peak_db, self.fixed["wuh"].tick_peak_db,
                                self.wuh_ready_since, self.sink_state or "absent")

        # GATED: like ONE-SIDED below, but not held after a driver change (the
        # fault starts right after a speakers <-> headphones switch)
        if pw_held:
            gate_held = "PipeWire restarted"
        elif both_why:
            gate_held = both_why
        elif self.sink_state != "running":
            gate_held = f"playback stopped, AE-9 sink {self.sink_state or 'absent'}"
        elif self.wuh_ready_since is None or now - self.wuh_ready_since < WIRE_GRACE:
            gate_held = "What U Hear unwired/absent"
        elif self.silence.active:
            gate_held = "SILENT"
        else:
            gate_held = ""
        self.gated.update(now, self.fixed["monitor"].capture, self.fixed["wuh"].capture,
                          not gate_held, gate_held)

        # ONE-SIDED: only while What U Hear is wired, settled and not SILENT
        ds = self.driver_state
        dsp = ds.get("Direct Mode Switch") == "off"
        stream_ch = driverstate.channels(self.stream_format)
        if pw_held:
            held = "PipeWire restarted"
        elif both_why or self._settling(now, "onesided"):
            held = both_why or self._settling(now, "onesided")
        elif (stream_ch or 2) > 2 or self.fixed["monitor"].channels > 2:
            held = "multichannel stream (What U Hear carries only the front pair)"
        elif self.sink_state != "running":
            held = f"playback stopped, AE-9 sink {self.sink_state or 'absent'}"
        elif self.wuh_ready_since is None or now - self.wuh_ready_since < WIRE_GRACE:
            held = "What U Hear unwired/absent"
        elif self.silence.active:
            held = "SILENT"
        elif self.gated.active:
            held = "GATED"
        elif ds.get("Mic Monitor Playback Switch") == "on":
            held = "Mic Monitor on (What U Hear carries the mic)"
        else:
            held = ""
        num = driverstate.number
        setup = {}
        if dsp and driverstate.headphone_mode(ds):
            # headphone mode folds 5.1 into L/R and virtualizes with SBX Surround:
            # the looser SBX rules; the speaker setup doesn't apply
            setup["sbx"] = True
        elif dsp:   # the speaker setup only applies in DSP mode
            setup["sbx"] = ds.get("SBX Switch") == "on"
            lvl = num(ds.get("Speaker Level Front L Volume")), num(ds.get("Speaker Level Front R Volume"))
            if None not in lvl:
                setup["level_offset_db"] = lvl[1] - lvl[0]
            dist = num(ds.get("Speaker Distance Front L")), num(ds.get("Speaker Distance Front R"))
            if None not in dist:
                setup["side_delay"] = onesided.delay_frames(*dist)
            setup["no_lows"] = (ds.get("Bass Redirect Switch") == "on"
                                and ds.get("Speaker Config") in ("2.1", "4.1", "5.1"))
        self.onesided.update(now, self.fixed["monitor"].capture, self.fixed["wuh"].capture,
                             not held, held, **setup)

    def _card_bits(self, key, node):
        """The bits the card itself uses on a fixed tap, or None to go by the
        stream's sample format."""
        if key == "monitor":        # the card's PCM Bit Depth, as it is now
            bits = driverstate.number(self.driver_state.get("PCM Bit Depth"))
            return int(bits) if bits else None
        if key == "optical_out":    # what's on the wire: the open PCM, else PipeWire's format
            fmt = driverstate.read_format(driverstate.hw_params_path(f"pcm{ae9.OPTICAL_OUT[1]}p"))
            return driverstate.wire_bits(fmt or (node_format(node) or {}).get("format"))
        return driverstate.capture_bits()

    def _format_notes(self, key):
        """What the driver does to a tap right now, for its format line."""
        ds = self.driver_state
        if key == "monitor":
            jack = driverstate.output_jack(ds)
            return [f"→ {jack}"] if jack else []
        if key == "mic":
            notes = []
            # the voice processor goes in at a recording's start (48 kHz only) and out at its stop
            if (driverstate.voice_clarity_wanted(ds)
                    and driverstate.read_field(driverstate.hw_params_path("pcm0c"), "rate") == "48000"):
                notes.append("Voice Clarity (8 kHz)")
                # drivers 0.12.3-0.12.6: once the hardware mic monitor has been on, Voice
                # Clarity records left ~20 dB low and right empty until a power-off
                # (0.12.7 fixes it: the control always reads off)
                if ds.get("Mic Monitor Used Since Power-up") == "on":
                    notes.append("⚠ broken: Mic Monitor used since power-up (power off to clear)")
            if ds.get("Mic Monitor Playback Switch") == "on":
                notes.append("Mic Monitor on")
            return notes
        return []

    def _settle_for(self, now, which, why):
        until, _ = self.settle.get(which, (0.0, ""))
        if now + SETTLE > until:
            self.settle[which] = (now + SETTLE, why)

    def _settling(self, now, which):
        """Why `which` is held right now, or ""."""
        until, why = self.settle.get(which, (0.0, ""))
        return why if now < until else ""

    # -- driver state ----------------------------------------------------

    def _watch_driver(self, now):
        """Log the watched controls (once, then each change) and the playback
        stream's format whenever it opens or changes."""
        for name, value in self.controls.poll():
            if name == driverstate.RELOAD:
                self.log("ae9-reload: card released (reload notice)" if value == "released"
                         else "ae9-reload done: card reopened")
                continue
            if name == driverstate.HEALTH:
                self._health_changed(driverstate.parse_health(value))
                continue
            old = self.driver_state.get(name)
            self.driver_state[name] = value
            if old is None:
                self.first_state[name] = value
                self.first_since = self.first_since or now
            elif old != value:
                text = f"{driverstate.short(name)} {old} → {value}"
                self.last_change = (text, now)
                self.log(f"Driver: {text}")
                self._settle_for(now, "onesided", f"settling after {text}")
                if name == "Direct Mode Switch":    # the driver restarts the running stream
                    self._settle_for(now, "both", f"Direct Mode switched {value}")
            if name == driverstate.CARD_ANSWERING:
                self._card_answering(value == "on", old is None)
        if self.first_state and (len(self.first_state) >= len(driverstate.WATCH)
                                 or now - self.first_since > 1.0):
            self.log("Driver state: " + ", ".join(f"{driverstate.short(n)} {v}"
                                                  for n, v in self.first_state.items()))
            self.first_state, self.first_since = {}, None

        if self.sink_state != "running":
            self.stream_format = None
        elif now >= self.next_hw_check:
            self.next_hw_check = now + HW_PARAMS_EVERY
            fmt = driverstate.read_hw_params()
            if fmt and fmt != self.stream_format:
                if self.stream_format:
                    self.last_change = (f"stream {self.stream_format} → {fmt}", now)
                    self.log(f"AE-9 stream changed: {fmt}")
                    self._settle_for(now, "onesided", "stream restarted")
                else:
                    self.log(f"AE-9 stream: {fmt}")
                self.stream_format = fmt

    def _health_changed(self, vals):
        """Log the Driver Health counters (driver 0.12.11+) at the start, then
        each problem counter that rises and the free slots running low."""
        if vals is None:
            return
        old, self.health = self.health, vals
        if old is None:
            self.log("Driver health: " + ", ".join(
                f"{f[0]} {v}" for f, v in zip(driverstate.HEALTH_FIELDS, vals)))
        else:
            was = list(old) + [0] * (len(vals) - len(old))     # a counter new after a driver update
            for f, a, b in zip(driverstate.HEALTH_FIELDS, was, vals):
                if f[2] and b > a:
                    self.log(f"Driver health: {f[0]} {a} → {b} (should stay 0) | {core.driver_info()}")
            low = driverstate.SLOTS_LOW
            if 0 <= vals[7] < low <= old[7] or (0 <= vals[7] < low and old[7] < 0):
                self.log(f"Driver health: free router slots down to {vals[7]} "
                         f"(DSP mode goes silent at 0)")
        self._show_health()

    def _show_health(self):
        text, problem = driverstate.health_text(self.health)
        self.health_lbl.setText(text)
        self.health_lbl.setObjectName("healthProblem" if problem else "hint")
        self.health_lbl.style().unpolish(self.health_lbl)
        self.health_lbl.style().polish(self.health_lbl)
        if self.health is None:
            tip = "The driver's health counters (\"Driver Health\" control, driver 0.12.11+)."
        else:
            vals = list(self.health) + ["n/a"] * (len(driverstate.HEALTH_FIELDS) - len(self.health))
            tip = "\n".join(f"{f[1]}: {v}" for f, v in zip(driverstate.HEALTH_FIELDS, vals))
            tip += ("\n\nThe first five (and the DAC I2C NACKs) should stay 0. "
                    f"Read with amixer -c {core.card_id()}, never written.")
        self.health_lbl.setToolTip(tip)

    def _card_answering(self, answering, first):
        """Show (or clear) the notice that the card stopped answering."""
        if not answering:
            text = stopped_notice()
            self.card_notice.setText(text)
            self.card_notice.setToolTip(text)
            short = core.card_short()
            self.log(text.replace(f"The {short} stopped answering.", f"{short} stopped answering:", 1))
        elif not first:
            self.log("AE-9 answering again (driver reloaded): following its controls again")
        self.card_notice.setVisible(not answering)

    def _change_note(self, now):
        """' | after: <change>, 1.2 s before' for a dropout line, when a driver
        change came shortly before it."""
        if not self.last_change or now - self.last_change[1] > CHANGE_NOTE_FOR:
            return ""
        text, at = self.last_change
        return f" | after: {text}, {now - at:.1f} s before"

    def _watch_pipewire(self, now):
        """A PipeWire restart (Sound Blaster Command's What U Hear rate picker
        does one) takes every meter and link with it. Name it in the log and
        hold SILENT off until the meters are rewired, so the gap isn't taken
        for the silent-restart bug."""
        down = getattr(self.graph, "down_since", None) is not None
        restarts = getattr(self.graph, "restarts", 0)
        if down and not self.pw_down:
            self.pw_down = True
            self.log("PipeWire went away (restarting?): meters paused, SILENT held off")
            self._pipewire_reset(now)
        if restarts != self.pw_restarts:
            self.pw_restarts = restarts
            self.pw_down = False
            self.pw_back_at = now
            self.log(f"PipeWire restarted: rewiring the meters | {core.driver_info()}")
            self._pipewire_reset(now)

    def _pipewire_reset(self, now):
        self.silence.end(now, "PipeWire restarted")
        self.pending.clear()            # old link requests died with PipeWire
        self.reported_failures.clear()
        self.wuh_ready_since = None

    def _silence_changed(self):
        self.fixed["wuh"].alert = self.silence.active or self.onesided.active or self.gated.active
        self.win.set_silent(self.silence.active, self.silence.count)
        self.win.set_one_sided(self.onesided.side if self.onesided.active else None, self.onesided.count)
        self.win.set_gated(self.gated.active, self.gated.count)

    # -- graph reconciliation ------------------------------------------------

    def reconcile(self, snap, now):
        found = find_ae9(snap, self.match_card)
        sink = found["monitor"]

        # Card nodes coming and going
        for key, title, _, _ in self.taps:
            node = found[key]
            cur = node.id if node else None
            if key not in self.present:
                self.log(f"{title}: " + (f"found {node.name} (id {node.id})" if node else "not found"))
            elif cur != self.present[key]:
                if node:
                    extra = f" | {core.driver_info()}" if key == "monitor" else ""
                    self.log(f"{title}: appeared {node.name} (id {node.id}){extra}")
                else:
                    self.log(f"{title}: disappeared")
            self.present[key] = cur

        state = sink.state if sink else None
        if state != self.sink_state:
            if sink:
                self.log(f"AE-9 sink state: {self.sink_state or '-'} → {state}"
                         + (f" (error: {sink.error})" if sink.error else ""))
            if state == "running":
                self._settle_for(now, "onesided", "stream starting")
                if self.driver_state.get("Direct Mode Switch") == "off":
                    # the driver starts a new stream direct, then moves it into the DSP
                    self._settle_for(now, "both", "stream moving into the DSP")
            self.sink_state = state
            self.win.set_sink_state(state)
        sink_running = state == "running"

        # Fixed taps
        for key, title, _, gated in self.taps:
            panel = self.fixed[key]
            node = found[key]
            if node is None:
                panel.note = "no signal" if key == "optical" else "not found"
                panel.set_format("")
                self._set_wired(key, title, False, "absent")
                continue
            panel.set_format(" · ".join(t for t in [format_text(node, self._card_bits(key, node))]
                                        + self._format_notes(key) if t))
            src = snap.out_ports(node, monitor=(key in ("monitor", "optical_out")))
            want = choose_channels(len(src)) if src else panel.channels
            if want != panel.channels:
                panel.start(want)
            hold_off = self.let_idle and gated and not sink_running and not self.always_capture
            wired = self._wire(snap, panel, src, [] if hold_off else None, now)
            # An unlit optical jack still runs and sends digital silence (or, on
            # some drivers, nothing at all): either way that's "no signal".
            if key == "optical" and (node.state == "error" or (wired and now - panel.last_sound > 0.5)):
                panel.note = "no signal"
            elif key == "optical_out" and node.state != "running":
                # driver 0.12.1+ has a mirror switch (off by default); older ones always mirror
                mirror = self.driver_state.get("Optical Out Mirror Switch", "on")
                panel.note = "idle: jack carries Output" if mirror == "on" else "idle: silent"
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
            self.streams[sid][0].set_format(format_text(n))
            self._wire(snap, self.streams[sid][0], src, None, now)
        if self.no_streams.isHidden() != bool(self.streams):
            self.no_streams.setVisible(not self.streams)
            self._size_streams()

    def _size_streams(self):
        """Give the app-stream row room only while there are streams."""
        if self.streams:            # takes its share of the height; scrolls if short
            self.streams_scroll.setMinimumHeight(28)
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
        self.stopped = True
        if self.silence.active:
            self.log(f"SILENT #{self.silence.count} still active at exit")
        if self.onesided.active:
            self.log(f"ONE-SIDED {self.onesided.side} #{self.onesided.count} still active at exit")
        if self.gated.active:
            self.log(f"GATED #{self.gated.count} still active at exit")
        self.controls.stop()
        self.log("SbAe9-AudioMon AE-9 mode stopped")
        self.log.remove_view(self.logview)
        self.graph.stop()
        for p in self.fixed.values():
            p.stop()
        for p, _, _ in self.streams.values():
            p.stop()
