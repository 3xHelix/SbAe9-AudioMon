# SPDX-License-Identifier: GPL-2.0-only
"""GATED: What U Hear plays in 64-frame bursts (driver 0.10.x half-speed fault)."""

import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import support  # noqa: E402

support.setup()

import numpy as np  # noqa: E402

from audiomon import gated  # noqa: E402
from audiomon.core import RATE  # noqa: E402
from test_onesided import Source, music, what_u_hear  # noqa: E402


def gate(w, offset=0):
    """Every other 64-frame block zeroed, as the faulty DSP plays it."""
    on = ((np.arange(len(w)) + offset) // (gated.PERIOD // 2)) % 2 == 0
    return (w * on[:, None]).astype(np.float32)


class Analyze(unittest.TestCase):
    def setUp(self):
        self.mon = music(1)
        self.wuh = what_u_hear(self.mon)

    def test_clean_audio_is_spread_evenly(self):
        r = gated.analyze(self.mon, self.wuh)
        self.assertFalse(r["gated"])
        self.assertGreater(r["share"], 0.4)

    def test_gated_audio_at_any_phase(self):
        for offset in (0, 17, 63):
            r = gated.analyze(self.mon, gate(self.wuh, offset))
            self.assertTrue(r["gated"], offset)
            self.assertLess(r["share"], 0.05)

    def test_a_375_hz_tone_in_the_music_is_not_judged(self):
        t = np.arange(RATE) / RATE
        tone = np.repeat((0.3 * np.sin(2 * np.pi * 375 * t))[:, None], 2, 1).astype(np.float32)
        self.assertIsNone(gated.analyze(tone, tone)["gated"])

    def test_too_quiet_is_not_judged(self):
        self.assertIsNone(gated.analyze(self.mon * 0.001, gate(self.wuh * 0.001))["gated"])
        self.assertIsNone(gated.analyze(self.mon, np.zeros_like(self.wuh))["gated"])


class Detector(unittest.TestCase):
    def setUp(self):
        self.lines = []
        self.changes = 0
        self.dir = os.path.join(os.environ["XDG_STATE_HOME"], self.id())
        self.det = gated.GatedDetector(self.lines.append, self.count, save_dir=self.dir,
                                       note=lambda now: " | after: Head Unit Output Speakers → Headphones, 0.4 s before")
        mon = music(12)
        wuh = what_u_hear(mon)
        a, b = int(5 * RATE), int(8 * RATE)       # gated from 5 s to 8 s
        wuh[a:b] = gate(wuh[a:b])
        self.mon, self.wuh = Source(mon), Source(wuh)

    def count(self):
        self.changes += 1

    def run_for(self, seconds, judge=True, held=""):
        t = self.clock = getattr(self, "clock", time.monotonic())
        end = t + seconds
        while t < end:
            self.mon.now = self.wuh.now = t
            self.det.update(t, self.mon, self.wuh, judge, held)
            t += 0.05
            self.clock = t
            for s in (self.mon, self.wuh):
                s.advance(0.05)

    def test_flagged_once_logged_saved_and_ended(self):
        self.run_for(0.9)                       # 4.0-4.9 s: clean
        self.assertFalse(self.det.active)
        self.run_for(1.6)                       # into the gating
        self.assertTrue(self.det.active)
        self.assertEqual((self.det.count, self.changes), (1, 1))
        start = next(l for l in self.lines if "start" in l)
        self.assertTrue(start.startswith("GATED #1 start: What U Hear plays in 64-frame bursts"), start)
        self.assertIn(support.FAKE_DRIVER, start)
        self.assertIn("after: Head Unit Output Speakers → Headphones", start)
        self.assertEqual(len([l for l in self.lines if "saved the last 3 s" in l]), 1)
        files = os.listdir(self.dir)
        self.assertEqual(len(files), 1)
        self.assertTrue(files[0].startswith("gated-"))

        self.run_for(3.0)                       # past 8 s: clean again
        self.assertFalse(self.det.active)
        self.assertIn("GATED #1 end after", self.lines[-1])
        self.assertIn("(What U Hear plays steadily again)", self.lines[-1])
        self.assertEqual(self.det.count, 1)

    def test_held_ends_it(self):
        self.run_for(2.5)
        self.assertTrue(self.det.active)
        self.run_for(0.1, judge=False, held="playback stopped, AE-9 sink idle")
        self.assertFalse(self.det.active)
        self.assertIn("(playback stopped, AE-9 sink idle)", self.lines[-1])

    def test_meters_without_samples_are_never_judged(self):
        self.det.update(time.monotonic(), object(), object(), True)
        self.assertFalse(self.det.active)


if __name__ == "__main__":
    unittest.main()
