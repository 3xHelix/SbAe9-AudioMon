# SPDX-License-Identifier: GPL-2.0-only
"""Card detection (sbae9kit's, shared with SbAe9-SBC): AudioMon names and
meters the card it finds, from fake /sys/class/sound trees. An AE-9 looks
exactly as before; an AE-7 gets only the meters its PCMs allow."""

import unittest

import support
from sbae9kit import testing
from audiomon import core, driverstate, monitor
from test_ae9_monitor import MonitorTest, process

ALL = ["monitor", "optical_out", "wuh", "mic", "optical", "rear_rca"]


class Detection(unittest.TestCase):
    def tearDown(self):
        support.sound_tree(testing.AE9_CARD)

    def taps(self):
        return {key: title for key, title, _, _ in monitor.card_taps(core.card())}

    def test_ae9(self):
        support.sound_tree(testing.HDMI_CARD, testing.AE9_CARD, testing.USB_CARD)
        self.assertEqual(core.card().id, "AE9")
        self.assertEqual(core.card_short(), "AE-9")
        self.assertEqual(monitor.card_taps(core.card()), monitor.FIXED_TAPS)   # as before
        self.assertEqual(driverstate.card_proc(), "/proc/asound/AE9")

    def test_ae7(self):
        support.sound_tree(testing.AE7_CARD)
        self.assertEqual(core.card_short(), "AE-7")
        self.assertEqual(core.card_name(), "Sound Blaster AE-7")
        taps = self.taps()
        self.assertEqual(list(taps), ["monitor", "wuh", "mic"])   # no optical out, optical in or rear RCA in
        self.assertEqual(taps["monitor"], "AE-7 Output (monitor)")
        self.assertEqual(taps["mic"], "AE-7 Mic / Aux")                          # no head unit
        self.assertEqual(driverstate.card_proc(), "/proc/asound/AE7")
        self.assertEqual(driverstate.reload_paths()[1], "/proc/asound/AE7")

    def test_ae7_on_the_in_tree_driver_is_not_ours(self):
        support.sound_tree(testing.AE7_INTREE, testing.HDMI_CARD)
        self.assertIsNone(core.card())
        self.assertEqual(core.card_short(), "AE-9")                 # as before detection
        self.assertEqual(list(self.taps()), ALL)
        self.assertEqual(driverstate.card_proc(), "/proc/asound/AE9")

    def test_no_card(self):
        support.sound_tree()
        self.assertIsNone(core.card())
        self.assertEqual(list(self.taps()), ALL)
        self.assertEqual(core.card_id(), "AE9")

    def test_two_cards_first_by_number(self):
        support.sound_tree(testing.AE7_CARD, testing.AE9_CARD)
        self.assertEqual(core.card().short, "AE-7")
        self.assertEqual([c.id for c in core.cards()], ["AE7", "AE9"])

    def test_hw_params_follow_the_card(self):
        support.sound_tree(testing.AE7_CARD)
        saved = driverstate.HW_PARAMS
        driverstate.HW_PARAMS = None
        try:
            self.assertEqual(driverstate.hw_params_path(), "/proc/asound/AE7/pcm0p/sub0/hw_params")
        finally:
            driverstate.HW_PARAMS = saved


class OnAnAE9(MonitorTest):
    def test_window_unchanged(self):
        self.assertEqual(self.win.windowTitle(), "SbAe9-AudioMon — AE-9")
        self.assertEqual(list(self.view.fixed), ALL)
        self.assertIsNone(self.view.match_card)
        self.assertIn("AE-9 Rear RCA In: found", self.log_text())


class OnAnAE7(MonitorTest):
    cards = (testing.AE7_CARD,)

    def tearDown(self):
        super().tearDown()
        support.sound_tree(testing.AE9_CARD)

    def test_named_and_metered_as_an_ae7(self):
        process(300)
        self.assertEqual(self.win.windowTitle(), "SbAe9-AudioMon — AE-7")
        self.assertEqual(list(self.view.fixed), ["monitor", "wuh", "mic"])
        self.assertIn("AE-7 Output (monitor): found", self.log_text())
        self.assertNotIn("Rear RCA", self.log_text())


class TwoCards(MonitorTest):
    cards = (testing.AE9_CARD, testing.AE7_CARD)

    def tearDown(self):
        super().tearDown()
        support.sound_tree(testing.AE9_CARD)

    def test_meters_the_first(self):
        self.assertEqual(self.view.card.id, "AE9")
        self.assertEqual(self.view.match_card.id, "AE9")
        self.assertEqual(list(self.view.fixed), ALL)
        process(300)
        self.assertIn("AE-9 Output (monitor): found", self.log_text())


if __name__ == "__main__":
    unittest.main()
