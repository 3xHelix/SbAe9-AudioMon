# SPDX-License-Identifier: GPL-2.0-only
"""SbAe9-AudioMon against the fake AE-9 (tests/fakeae9.py).

Run: python3 -m unittest discover -s tests
"""

import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import support  # noqa: E402

support.setup()

from sbae9kit import desktop, testing  # noqa: E402
from sbae9kit.testing import MIC, OPTICAL, SINK, WHAT_U_HEAR  # noqa: E402

from audiomon import core, eventlog  # noqa: E402
from audiomon import theme as T  # noqa: E402
from audiomon.window import MainWindow  # noqa: E402

from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication([])



def process(ms):
    end = time.monotonic() + ms / 1000
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.005)


def wait_until(pred, timeout=4.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.processEvents()
        if pred():
            return True
        time.sleep(0.005)
    return pred()


class MonitorTest(unittest.TestCase):
    let_idle = True
    always_capture = False
    card = dict()

    def setUp(self):
        self.pw = support.reset(**self.card)
        self.cfg = core.load_config()
        self.cfg.update(mode="ae9", let_idle=self.let_idle, always_capture=self.always_capture,
                        page=0, frameless=False, glass=False, glass_opacity=70)
        self.log_path = os.path.join(os.environ["XDG_STATE_HOME"], f"{self.id()}.log")
        self.win = MainWindow(self.cfg, eventlog.EventLog(self.log_path))
        self.win.show()
        process(300)

    def tearDown(self):
        self.win.stop()
        self.win.timer.stop()
        self.win.close()
        self.win.deleteLater()
        process(20)

    # helpers
    @property
    def view(self):
        return self.win.monitor.view

    def wired(self, key):
        return self.view.wired.get(key, False)

    def log_text(self):
        with open(self.log_path) as f:
            return f.read()

    def assert_listen_only(self):
        """Every link the app made goes from AE-9 / stream ports into our own meters."""
        for call in self.pw.calls:
            if call[0] == "link":
                in_port = call[2]
                port = self.pw.objects.get(in_port)
                if port:
                    self.assertEqual(self.pw.props(port["info"]["props"]["node.id"]).get("application.name"),
                                     core.APP_NAME,
                                    f"linked into a node that isn't ours: {call}")


class IdleCard(MonitorTest):
    def test_finds_the_card(self):
        log = self.log_text()
        for name in (SINK, WHAT_U_HEAR, MIC, OPTICAL):
            self.assertIn(f"found {name}", log)
        self.assertIn(support.FAKE_DRIVER, log)

    def test_nothing_playing_lets_the_card_suspend(self):
        process(600)
        self.assertEqual(self.pw.state(SINK), "suspended")
        for name in (WHAT_U_HEAR, MIC, OPTICAL):
            self.assertEqual(self.pw.state(name), "suspended", name)
        self.assertTrue(self.wired("monitor"))
        for key in ("wuh", "mic", "optical"):
            self.assertFalse(self.wired(key), key)
        self.assertEqual(self.view.fixed["wuh"].note, "unwired (AE-9 idle)")
        self.assertEqual(self.win.state_lbl.text(), "Suspended")
        self.assert_listen_only()

    def test_monitor_and_stream_meters_are_passive(self):
        self.assertTrue(self.view.fixed["monitor"].capture.passive)
        for key in ("wuh", "mic", "optical"):
            self.assertFalse(self.view.fixed[key].capture.passive, key)


class Captions(MonitorTest):
    def test_short_names_on_screen_jacks_on_hover(self):
        mic = self.view.fixed["mic"]
        self.assertEqual(mic.title_lbl.text(), "Mic / Aux")
        tip = mic.title_lbl.toolTip()
        for detail in ("AE-9 Head Unit Mic / Aux", "XLR/TRS jack", "3.5 mm mic jack", "sbae9-audiomon.ae9.mic"):
            self.assertIn(detail, tip)
        self.assertIn("RCA out", self.view.fixed["monitor"].title_lbl.toolTip())
        self.assertIn("optical in jack", self.view.fixed["optical"].title_lbl.toolTip())
        self.assertEqual(mic.capture.label, "AE-9 Head Unit Mic / Aux")   # the node keeps the full name
        # log lines keep the full names (they're matched against the kernel log)
        self.assertIn("AE-9 Head Unit Mic / Aux: found", self.log_text())


class Playback(MonitorTest):
    def test_stream_gets_a_meter_and_inputs_wire_while_running(self):
        sid = self.pw.add_stream("Firefox", "YouTube", -14)
        self.assertTrue(wait_until(lambda: sid in self.view.streams))
        panel = self.view.streams[sid][0]
        self.assertTrue(panel.capture.passive)
        self.assertTrue(wait_until(lambda: self.wired("wuh") and self.wired("mic")))
        self.assertEqual(self.pw.state(SINK), "running")
        process(400)
        self.assertTrue(panel.live)
        self.assertAlmostEqual(panel.tick_peak_db, -14, delta=0.5)
        self.assertAlmostEqual(self.view.fixed["wuh"].tick_peak_db, -14, delta=0.5)   # level-exact
        self.assertEqual(self.view.silence.count, 0)

        self.pw.stop_stream(sid)
        self.assertTrue(wait_until(lambda: sid not in self.view.streams))
        self.assertTrue(wait_until(lambda: not self.wired("wuh")))
        self.assertTrue(wait_until(lambda: "idle → suspended" in self.log_text()))
        log = self.log_text()
        self.assertIn("App stream started: Firefox — YouTube", log)
        self.assertIn("App stream ended: Firefox — YouTube", log)
        self.assertIn("AE-9 sink state: running → idle", log)
        self.assertIn("AE-9 sink state: idle → suspended", log)
        self.assert_listen_only()

    def test_silent_restart_is_flagged_and_logged(self):
        self.pw.silent_restart = True
        self.pw.add_stream("mpv", "tone.flac", -12)
        self.assertTrue(wait_until(lambda: self.view.silence.active, 5))
        self.assertTrue(self.win.silent_lbl.isVisibleTo(self.win))
        self.assertEqual(self.win.dropouts_lbl.text(), "Dropouts: 1")
        self.assertTrue(self.view.fixed["wuh"].alert)
        log = self.log_text()
        self.assertIn("SILENT #1 start: output monitor -12.0 dBFS, What U Hear -inf dBFS", log)
        self.assertIn(support.FAKE_DRIVER, log.split("SILENT #1 start")[1])

        self.pw.silent_restart = False        # the card comes back
        self.assertTrue(wait_until(lambda: not self.view.silence.active))
        self.assertIn("SILENT #1 end", self.log_text())
        self.assertIn("What U Hear back at", self.log_text())
        self.assertFalse(self.win.silent_lbl.isVisibleTo(self.win))

    def test_no_silent_while_nothing_plays(self):
        self.pw.silent_restart = True
        process(1200)
        self.assertEqual(self.view.silence.count, 0)

    def test_hand_made_links_are_left_alone(self):
        """A link into our meter made in Helvum isn't removed."""
        meter = self.pw.node("sbae9-audiomon.ae9.optical")
        sid = self.pw.add_stream("Helvum test", "x", -20)
        out = [p for p in self.pw.ports(sid) if p["info"]["direction"] == "output"][0]["id"]
        inp = [p for p in self.pw.ports(meter["id"]) if p["info"]["direction"] == "input"][0]["id"]
        lid = self.pw.link_external(out, inp)
        process(800)
        self.assertIn(lid, self.pw.objects)
        self.assertNotIn(("unlink", lid), self.pw.calls)


class CardComesAndGoes(MonitorTest):
    def test_card_drop_and_return(self):
        self.pw.card_present(False)
        self.assertTrue(wait_until(lambda: self.view.fixed["wuh"].note == "not found"))
        log = self.log_text()
        for title in ("AE-9 Output (monitor)", "AE-9 What U Hear", "AE-9 Head Unit Mic / Aux"):
            self.assertIn(f"{title}: disappeared", log)
        self.assertEqual(self.win.state_lbl.text(), "Not found")

        self.pw.card_present(True)
        self.assertTrue(wait_until(lambda: self.wired("monitor")))
        log = self.log_text()
        self.assertIn(f"AE-9 Output (monitor): appeared {SINK}", log)
        self.assertIn(support.FAKE_DRIVER, log.split("appeared " + SINK)[1].splitlines()[0])

        sid = self.pw.add_stream("Firefox", "after reload", -14)   # rewires after the return
        self.assertTrue(wait_until(lambda: self.wired("wuh") and sid in self.view.streams))


class OpticalWithoutLight(MonitorTest):
    let_idle = False

    def test_optical_reads_no_signal(self):
        self.assertTrue(wait_until(lambda: self.wired("optical")))
        process(800)
        optical = self.view.fixed["optical"]
        self.assertTrue(optical.live)                 # the dark jack still sends (silent) data...
        self.assertEqual(optical.note, "no signal")  # ...which reads as no signal
        self.pw.optical_db = -20                      # light on the jack
        self.assertTrue(wait_until(lambda: optical.note is None))
        self.assertAlmostEqual(optical.tick_peak_db, -20, delta=0.5)
        self.pw.optical_db = None                     # and dark again
        self.assertTrue(wait_until(lambda: optical.note == "no signal"))


class KeepAwake(MonitorTest):
    let_idle = False

    def test_everything_wired_and_card_kept_running(self):
        self.assertTrue(wait_until(lambda: all(self.wired(k) for k in ("monitor", "wuh", "mic", "optical"))))
        process(600)
        self.assertEqual(self.pw.state(SINK), "running")
        self.assertEqual(self.pw.state(WHAT_U_HEAR), "running")
        self.assertFalse(self.view.fixed["monitor"].capture.passive)


class AlwaysCapture(MonitorTest):
    always_capture = True

    def test_inputs_wired_but_sink_can_suspend(self):
        self.assertTrue(wait_until(lambda: self.wired("wuh") and self.wired("mic")))
        process(600)
        self.assertEqual(self.pw.state(SINK), "suspended")
        self.assertEqual(self.pw.state(WHAT_U_HEAR), "running")


class NoExtraSources(MonitorTest):
    card = dict(what_u_hear=False, optical=False)

    def test_older_driver_without_what_u_hear(self):
        process(400)
        self.assertEqual(self.view.fixed["wuh"].note, "not found")
        self.assertEqual(self.view.fixed["optical"].note, "no signal")
        self.assertIn("AE-9 What U Hear: not found", self.log_text())
        self.pw.add_stream("Firefox", "YouTube", -14)
        process(1200)
        self.assertEqual(self.view.silence.count, 0)   # can't judge without What U Hear


class QuickToggles(MonitorTest):
    def test_bottom_bar_and_settings_stay_in_step(self):
        self.win.q_auto.click()
        self.assertFalse(self.win.settings.auto_cb.isChecked())
        self.assertIsNone(self.view)
        self.assertFalse(self.win.q_idle.isEnabled())
        self.win.settings.auto_cb.click()
        self.assertTrue(self.win.q_auto.isChecked())
        self.assertIsNotNone(self.view)
        self.win.q_idle.click()
        self.assertFalse(self.win.settings.idle_cb.isChecked())
        self.assertFalse(self.win.q_capture.isEnabled())
        self.win.go(3)
        self.assertFalse(self.win.quick.isVisibleTo(self.win))


class Glass(MonitorTest):
    def tearDown(self):
        super().tearDown()
        T.GLASS = False
        T.GLASS_OPACITY = 0.70
        app.setStyleSheet("")

    def test_xwayland_only_for_glass_on_wayland(self):
        env = {"WAYLAND_DISPLAY": "wayland-1", "DISPLAY": ":0"}
        desktop.use_xwayland(core.APP_ID, False, env)
        self.assertNotIn("QT_QPA_PLATFORM", env)
        desktop.use_xwayland(core.APP_ID, True, env)
        self.assertEqual(env.get("QT_QPA_PLATFORM"), "xcb" if desktop.xcb_available() else None)
        desktop.use_xwayland(core.APP_ID, False, env)            # the next launch, Glass off: back to Wayland
        self.assertNotIn("QT_QPA_PLATFORM", env)
        mine = {"WAYLAND_DISPLAY": "wayland-1", "DISPLAY": ":0", "QT_QPA_PLATFORM": "wayland"}
        desktop.use_xwayland(core.APP_ID, True, mine)            # one the user set is left alone
        self.assertEqual(mine["QT_QPA_PLATFORM"], "wayland")
        x11 = {"DISPLAY": ":0"}
        desktop.use_xwayland(core.APP_ID, True, x11)             # plain X11 session: nothing to do
        self.assertNotIn("QT_QPA_PLATFORM", x11)

    def test_surfaces_go_see_through_and_opacity_is_live(self):
        self.assertNotIn("rgba(18,15,33", T.stylesheet())   # title bar solid without Glass
        T.GLASS = True
        T.GLASS_OPACITY = 0.7
        self.assertIn("#titleBar { background: rgba(18,15,33,0.7);", T.stylesheet())
        self.assertIn("#root { background: transparent; }", T.stylesheet())
        self.assertAlmostEqual(T.surface_q(T.GRAPH_BG, 0.85).alphaF(), 0.595, places=2)
        self.cfg["glass"] = True
        self.win.settings.sync()
        self.win.settings.opacity.setValue(50)       # keyboard-style change applies at once
        self.assertEqual(self.cfg["glass_opacity"], 50)
        self.assertEqual(T.GLASS_OPACITY, 0.5)
        self.assertIn("rgba(18,15,33,0.5)", app.styleSheet())
        self.assertEqual(self.win.settings.opacity_lbl.text(), "50%")

    def test_toggling_glass_reopens_the_app(self):
        calls = []
        self.win.restart = lambda drop=(): calls.append(drop)
        self.assertFalse(self.win.settings.opacity.isEnabled())
        self.win.settings.glass_cb.click()
        self.assertEqual(calls, [("--glass", "--no-glass")])
        self.assertTrue(self.cfg["glass"])
        self.assertTrue(self.win.settings.opacity.isEnabled())
        self.assertIn("Glass turned on; reopening the window", self.log_text())


if __name__ == "__main__":
    unittest.main()
