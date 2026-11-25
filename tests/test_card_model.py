# SPDX-License-Identifier: GPL-2.0-only
"""Driver 0.13.0's "Card Model" control, and an AE-7 the driver doesn't run.

"Card Model" (iface CARD, read-only, one item) names each card on the AE
driver; AudioMon hands it to the kit's detection, with the ALSA id, names and
PCI subsystem as the fallback for older drivers. An AE-7 left on the kernel's
own driver, or on none, gets a notice with the command to run."""

import subprocess
import unittest
from types import SimpleNamespace
from unittest import mock

import support  # noqa: E402

support.setup()

from sbae9kit import ae9, testing  # noqa: E402
from audiomon import core, monitor  # noqa: E402
from test_ae9_monitor import MonitorTest, process  # noqa: E402

CGET = """numid=76,iface=CARD,name='Card Model'
  ; type=ENUMERATED,access=r-------,values=1,items=1
  ; Item #0 'Sound Blaster AE-9'
  : values=0
"""
# A card the driver runs whose id and names say nothing (a renamed id, say):
# only "Card Model" can name it.
RENAMED = ("Studio", "HD-Audio", "HD-Audio at 0xfc004000 irq 45", None,
           testing.AE9_CARD[4], testing.AE7_CARD[5])


class ReadCardModel(unittest.TestCase):
    read = staticmethod(support.REAL["read_card_model"])

    def run_amixer(self, stdout, returncode=0):
        result = SimpleNamespace(stdout=stdout, returncode=returncode)
        with mock.patch.object(core.subprocess, "run", return_value=result) as run:
            value = self.read(2)
        return value, run.call_args[0][0]

    def test_reads_the_one_item(self):
        value, cmd = self.run_amixer(CGET)
        self.assertEqual(value, "Sound Blaster AE-9")
        self.assertEqual(cmd[-5:], ["amixer", "-c", "2", "cget", "iface=CARD,name=Card Model"])

    def test_older_driver(self):
        self.assertIsNone(self.run_amixer("amixer: Cannot find the given element from control hw:2\n", 1)[0])

    def test_no_amixer(self):
        with mock.patch.object(core.subprocess, "run", side_effect=FileNotFoundError):
            self.assertIsNone(self.read(2))
        with mock.patch.object(core.subprocess, "run", side_effect=subprocess.TimeoutExpired("amixer", 3)):
            self.assertIsNone(self.read(2))


class Detection(unittest.TestCase):
    def tearDown(self):
        support.reset()

    def test_names_the_card(self):
        support.CARD_MODELS[1] = "Sound Blaster AE-9"
        support.sound_tree(testing.HDMI_CARD, testing.AE9_CARD)
        self.assertEqual((core.card().short, core.card().source), ("AE-9", "card_model"))

    def test_older_driver_falls_back(self):
        support.sound_tree(testing.AE9_CARD)
        self.assertEqual((core.card().short, core.card().source), ("AE-9", "card_id"))

    def test_a_card_only_card_model_names(self):
        cards = (None,) * 5 + (RENAMED,)                     # card 5: a fresh fake PCI device
        support.sound_tree(*cards)
        self.assertIsNone(core.card())                       # an older driver: not recognised
        support.CARD_MODELS[5] = "Sound Blaster AE-7"
        support.sound_tree(*cards)
        self.assertEqual((core.card().id, core.card().short), ("Studio", "AE-7"))
        self.assertEqual(core.card_id(), "Studio")
        self.assertEqual([k for k, *_ in monitor.card_taps(core.card())], ["monitor", "wuh", "mic"])

    def test_only_our_driver_is_asked(self):
        asked = []
        support.CARD_MODELS.clear()
        with mock.patch.object(core, "read_card_model", side_effect=lambda i: asked.append(i)):
            support.sound_tree(testing.HDMI_CARD, testing.AE9_CARD, testing.USB_CARD)
            core.cards()
        self.assertEqual(asked, [1])


class AE7Waiting(unittest.TestCase):
    """The kit's ae7_waiting() on the fake tree, as AudioMon uses it."""

    def tearDown(self):
        support.reset()

    def ae7(self, **kw):
        testing.fake_ae7_pci(support.SOUND_ROOT, **kw)
        return ae9.ae7_waiting()

    def test_none(self):
        self.assertIsNone(ae9.ae7_waiting())
        self.assertIsNone(self.ae7(driver=ae9.boards.DRIVER))                    # ours runs it
        self.assertIsNone(self.ae7(driver=ae9.boards.DRIVER, parked=True))

    def test_states(self):
        self.assertEqual(self.ae7(driver="snd_hda_intel"), "kernel")
        self.assertEqual(self.ae7(driver="snd_hda_intel", parked=True), "kernel")   # a driver wins
        self.assertEqual(self.ae7(), "unbound")                                   # no park files
        self.assertEqual(self.ae7(parked=True), "parked")

    def test_reset_clears_it(self):
        self.ae7(parked=True)
        support.reset()
        self.assertIsNone(ae9.ae7_waiting())

    def test_notices(self):
        kernel, parked, unbound = (monitor.ae7_notice(w) for w in ("kernel", "parked", "unbound"))
        self.assertTrue(kernel.endswith("Run in a terminal: sudo ae9-ae7-enable, then reboot."))
        self.assertIn("parked", parked)
        self.assertIn("ASMedia bridge, most from 2022 on", parked)
        self.assertTrue(parked.endswith("Run in a terminal: sudo ae9-ae7-enable, then reboot."))
        self.assertTrue(unbound.endswith("Run in a terminal: ae9-ae7-enable --status."))
        for text in (kernel, parked, unbound):
            self.assertTrue(text.startswith("Sound Blaster AE-7 found. "))
            self.assertNotIn("--stock", text)
            self.assertNotIn("--disable", text)


class AE7NoticeShown(MonitorTest):
    cards = (testing.AE7_INTREE,)

    def setUp(self):
        # found by AE7Waiting's PCI ids; with no card of ours, no controls to follow
        with mock.patch.object(ae9, "ae7_waiting", return_value="kernel"), \
                mock.patch.dict(support.DRIVER_STATE, clear=True):
            super().setUp()

    def tearDown(self):
        super().tearDown()
        support.reset()

    def test_enable_command(self):
        process(100)
        self.assertTrue(self.view.card_notice.isVisible())
        self.assertIn("sudo ae9-ae7-enable", self.view.card_notice.text())
        self.assertIn("experimental", self.view.card_notice.text())
        self.assertIn("sudo ae9-ae7-enable", self.log_text())


class NoNoticeOnAnAE9(MonitorTest):
    def test_hidden(self):
        process(100)
        self.assertFalse(self.view.card_notice.isVisible())
        self.assertNotIn("AE-7", self.log_text())


if __name__ == "__main__":
    unittest.main()
