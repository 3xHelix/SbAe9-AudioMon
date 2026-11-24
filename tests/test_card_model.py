# SPDX-License-Identifier: GPL-2.0-only
"""Driver 0.13.0's "Card Model" control, and an AE-7 the driver doesn't run.

"Card Model" (iface CARD, read-only, one item) names each card on the AE
driver; AudioMon hands it to the kit's detection, with the ALSA id, names and
PCI subsystem as the fallback for older drivers. An AE-7 left on the kernel's
own driver, or on none, gets a notice with the command to run."""

import os
import subprocess
import unittest
from types import SimpleNamespace
from unittest import mock

import support  # noqa: E402

support.setup()

from sbae9kit import testing  # noqa: E402
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


def pci_device(slot, ids, driver=None):
    """A fake /sys/bus/pci/devices entry (vendor, device, subsystem ids)."""
    d = os.path.join(core.PCI_DEVICES, slot)
    os.makedirs(d, exist_ok=True)
    for name, v in zip(("vendor", "device", "subsystem_vendor", "subsystem_device"), ids):
        with open(os.path.join(d, name), "w") as f:
            f.write(f"0x{v:04x}\n")
    if driver:
        target = os.path.join(support.SOUND_ROOT, "drivers", driver)
        os.makedirs(target, exist_ok=True)
        os.symlink(target, os.path.join(d, "driver"))


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
    def tearDown(self):
        support.reset()

    def test_none(self):
        self.assertIsNone(core.ae7_waiting())
        pci_device("0000:0a:00.0", (0x1102, 0x0010, 0x1102, 0x0071), "snd-hda-codec-creative-ae9")   # an AE-9
        self.assertIsNone(core.ae7_waiting())

    def test_on_the_kernels_driver(self):
        pci_device("0000:0b:00.0", (0x1102, 0x0010, 0x1102, 0x0081), "snd_hda_intel")
        self.assertEqual(core.ae7_waiting(), "kernel")

    def test_on_no_driver(self):
        pci_device("0000:0b:00.0", (0x1102, 0x0010, 0x1102, 0x0081))
        self.assertEqual(core.ae7_waiting(), "unbound")      # no park files: enabling isn't finished

    def park_file(self, i):
        os.makedirs(os.path.dirname(core.AE7_PARK_FILES[i]), exist_ok=True)
        open(core.AE7_PARK_FILES[i], "w").close()

    def test_parked(self):
        """0.13.0-rc3+: support off parks it (no driver) on purpose."""
        for i in range(2):
            support.reset()
            pci_device("0000:0b:00.0", (0x1102, 0x0010, 0x1102, 0x0081))
            self.park_file(i)
            self.assertEqual(core.ae7_waiting(), "parked", core.AE7_PARK_FILES[i])

    def test_park_files_dont_matter_on_a_driver(self):
        self.park_file(0)
        self.park_file(1)
        pci_device("0000:0b:00.0", (0x1102, 0x0010, 0x1102, 0x0081), "snd_hda_intel")
        self.assertEqual(core.ae7_waiting(), "kernel")
        support.reset()
        self.park_file(0)
        self.assertIsNone(core.ae7_waiting())                # no AE-7 at all

    def test_notices(self):
        self.assertIn("sudo ae9-ae7-enable", monitor.ae7_notice("kernel"))
        self.assertIn("isn't finished", monitor.ae7_notice("unbound"))
        self.assertIn("ae9-ae7-enable --status", monitor.ae7_notice("unbound"))
        parked = monitor.ae7_notice("parked")
        self.assertIn("parked (no driver)", parked)
        self.assertIn("ASMedia", parked)
        self.assertIn("sudo ae9-ae7-enable", parked)
        self.assertNotIn("isn't finished", parked)

    def test_ours_runs_it(self):
        pci_device("0000:0b:00.0", (0x1102, 0x0010, 0x1102, 0x0081), "snd-hda-codec-creative-ae9")
        self.assertIsNone(core.ae7_waiting())


class AE7NoticeShown(MonitorTest):
    cards = (testing.AE7_INTREE,)

    def setUp(self):
        # found by AE7Waiting's PCI ids; with no card of ours, no controls to follow
        with mock.patch.object(core, "ae7_waiting", return_value="kernel"), \
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
