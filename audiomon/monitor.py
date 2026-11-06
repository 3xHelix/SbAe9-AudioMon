# SPDX-License-Identifier: GPL-2.0-only
"""The AE-9 monitor (auto-wiring, SILENT detection) and the manual meters."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QScrollArea, QVBoxLayout, QWidget

from . import __version__, core, pipewire
from .core import CHANNEL_MAPS, FLOOR_DB, choose_channels, fmt_db, save_config
from .pipewire import Snapshot, find_ae9, is_ours
from .widgets import MeterPanel, label, log_view

# SILENT detection (AE-9 mode): the output monitor has signal but What U Hear
# stays below WUH_SILENT_DB for longer than SILENT_AFTER seconds.
MON_SIGNAL_DB = -50.0   # monitor counts as "has signal" above this (peak)
WUH_SILENT_DB = -60.0   # What U Hear counts as silent below this (peak)
SILENT_AFTER = 0.5      # seconds
WIRE_GRACE = 1.0        # ignore What U Hear for this long after (re)wiring it


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
                     f"What U Hear {wuh} for {now - onset:.1f}s | sink {sink_state} | {core.driver_info()}")
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
        self.graph = pipewire.Graph()
        self.linker = pipewire.Linker()
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

        self.log(f"SbAe9-AudioMon {__version__} AE-9 mode started (let the AE-9 idle: "
                 f"{'on' if self.let_idle else 'off'}, always capture inputs: "
                 f"{'on' if self.always_capture else 'off'}) | {core.driver_info()}")

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
            # An unlit optical jack still runs and sends digital silence (or, on
            # some drivers, nothing at all): either way that's "no signal".
            if key == "optical" and (node.state == "error" or (wired and now - panel.last_sound > 0.5)):
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
