# SPDX-License-Identifier: GPL-2.0-only
"""ONE-SIDED (one What U Hear channel off) and the driver-state readers."""

import os
import sys
import time
import unittest
import wave

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import support  # noqa: E402

support.setup()

import numpy as np  # noqa: E402

from audiomon import driverstate, onesided  # noqa: E402
from audiomon.core import RATE  # noqa: E402

RNG = np.random.default_rng(7)
LAG = int(0.037 * RATE)      # What U Hear runs 37 ms behind the output


def music(seconds):
    """Something music-like: shared noise, a little stereo spread, two tones."""
    n = int(seconds * RATE)
    base = np.convolve(RNG.standard_normal(n), np.ones(8) / 8, "same")
    side = np.convolve(RNG.standard_normal(n), np.ones(8) / 8, "same")
    t = np.arange(n) / RATE
    return np.stack([0.2 * base + 0.05 * side + 0.1 * np.sin(2 * np.pi * 440 * t),
                     0.2 * base - 0.05 * side + 0.1 * np.sin(2 * np.pi * 660 * t)], 1).astype(np.float32)


def what_u_hear(mon, broken=None):
    """mon played back LAG frames late, optionally broken by broken(wuh)."""
    wuh = np.concatenate([np.zeros((LAG, 2), np.float32), mon[:-LAG]])
    return broken(wuh.copy()) if broken else wuh


def r_silent(w):
    w[:, 1] = 0
    return w


def r_static(w):
    w[:, 1] += 0.05 * RNG.standard_normal(len(w))
    return w


def r_quiet(w):
    w[:, 1] *= 0.2
    return w


def sbx_like(w):
    """Crossfeed and an edge on both sides alike (Surround / Crystalizer)."""
    a, b = w[:, 0] + 0.4 * w[:, 1], w[:, 1] + 0.4 * w[:, 0]
    k = np.array([1, -0.5])
    return np.stack([np.convolve(a, k, "same"), np.convolve(b, k, "same")], 1).astype(np.float32)


def judge(mon, wuh, sbx=False):
    """analyze() the way the detector calls it: the last half second of What
    U Hear against the last second of the output."""
    w = int(onesided.WINDOW * RATE)
    m = w + int(onesided.MAX_LAG * RATE)
    return onesided.analyze(mon[-m:], wuh[-w:], sbx)


class Analyze(unittest.TestCase):
    def setUp(self):
        self.mon = music(1.5)

    def test_clean_playback_passes_and_the_lag_is_found(self):
        r = judge(self.mon, what_u_hear(self.mon))
        self.assertIsNone(r["side"])
        self.assertAlmostEqual(r["lag_ms"], 37.0, delta=0.1)
        self.assertGreater(min(r["corr"]), 0.99)

    def test_one_side_silent(self):
        r = judge(self.mon, what_u_hear(self.mon, r_silent))
        self.assertEqual(r["side"], "R")
        self.assertIn("went silent", r["why"])

    def test_static_on_one_side(self):
        r = judge(self.mon, what_u_hear(self.mon, r_static))
        self.assertEqual(r["side"], "R")
        self.assertIn("noisy", r["why"])

    def test_one_side_far_off_in_level(self):
        r = judge(self.mon, what_u_hear(self.mon, r_quiet))
        self.assertEqual(r["side"], "R")
        self.assertIn("14 dB quieter than L", r["why"])

    def test_effects_on_both_sides_and_volume_pass(self):
        for broken in (sbx_like, lambda w: w * 0.3):
            self.assertIsNone(judge(self.mon, what_u_hear(self.mon, broken), sbx=True)["side"])
            self.assertIsNone(judge(self.mon, what_u_hear(self.mon, broken))["side"])

    def test_left_right_falling_apart_on_a_mono_source(self):
        mono = np.repeat(self.mon[:, :1], 2, axis=1)

        def r_late(w):
            w[:, 1] = np.roll(w[:, 1], 300)
            return w
        r = judge(mono, what_u_hear(mono, r_late))
        # with a mono source either side lines up as well, so either can be blamed
        self.assertIn(r["side"], ("L", "R"))

    def test_speaker_level_difference_is_expected(self):
        def r_12db(w):
            w[:, 1] *= 10 ** (-12 / 20)
            return w
        wuh = what_u_hear(self.mon, r_12db)
        self.assertEqual(judge(self.mon, wuh)["side"], "R")
        mon, small = self.mon[-int(1.0 * RATE):], wuh[-int(0.5 * RATE):]
        self.assertIsNone(onesided.analyze(mon, small, level_offset_db=-12)["side"])

    def test_speaker_distance_delay_is_expected(self):
        delay = onesided.delay_frames(0, 343)              # 3.43 m further: 10 ms

        def r_late(w):
            w[:, 1] = np.concatenate([np.zeros(delay, np.float32), w[:-delay, 1]])
            return w
        wuh = what_u_hear(self.mon, r_late)
        self.assertEqual(delay, 480)
        self.assertEqual(judge(self.mon, wuh)["side"], "R")
        mon, small = self.mon[-int(1.0 * RATE):], wuh[-int(0.5 * RATE):]
        self.assertIsNone(onesided.analyze(mon, small, side_delay=delay)["side"])

    def test_bass_redirect_sets_the_lows_aside(self):
        t = np.arange(len(self.mon)) / RATE
        bassy = self.mon + (0.5 * np.sin(2 * np.pi * 50 * t))[:, None].astype(np.float32)
        no_bass = what_u_hear(self.mon)             # the bass went to the sub
        mon, small = bassy[-int(1.0 * RATE):], no_bass[-int(0.5 * RATE):]
        self.assertIsNone(onesided.analyze(mon, small, no_lows=True)["side"])
        broken = r_static(no_bass.copy())[-int(0.5 * RATE):]
        self.assertEqual(onesided.analyze(mon, broken, no_lows=True)["side"], "R")

    def test_quiet_output_or_unrelated_audio_gets_no_verdict(self):
        self.assertIsNone(judge(self.mon * 0.001, what_u_hear(self.mon * 0.001, r_silent))["side"])
        self.assertIsNone(judge(self.mon, music(1.5))["side"])


class Source:
    """A meter's recent(): the newest samples of a fixed recording, as if
    they'd just arrived. advance() plays it on."""

    def __init__(self, data):
        self.data, self.pos = data, int(onesided.KEEP * RATE)
        self.now = time.monotonic()     # when the newest sample "arrived"

    def recent(self, frames):
        if frames > self.pos:
            return None
        return self.data[self.pos - frames:self.pos], self.now

    def advance(self, seconds):
        self.pos = min(len(self.data), self.pos + int(seconds * RATE))


class DriverRules(unittest.TestCase):
    """driverstate's rules for driver 0.12.2."""

    def test_voice_clarity_rule(self):
        want = driverstate.voice_clarity_wanted
        self.assertFalse(want({"Noise Reduction Capture Switch": "on"}))            # master off
        self.assertFalse(want({"Voice Clarity Capture Switch": "on"}))              # no effect on
        for k, v in (("Acoustic Echo Cancellation Capture Switch", "on"), ("Noise Reduction Capture Switch", "on"),
                     ("Mic Smart Volume Capture Switch", "on"), ("Mic EQ Capture Enum", "Radio"),
                     ("Voice Morphing Capture Enum", "Robot")):
            self.assertTrue(want({"Voice Clarity Capture Switch": "on", k: v}), k)

    def test_headphone_mode_and_jack(self):
        ds = {"Head Unit Output": "Headphones", "Direct Mode Switch": "off",
              "Headphone Virtualization Switch": "on", "Line Out Virtualization Switch": "off"}
        self.assertTrue(driverstate.headphone_mode(ds))
        self.assertEqual(driverstate.output_jack(ds), "headphones")
        self.assertFalse(driverstate.headphone_mode(dict(ds, **{"Direct Mode Switch": "on"})))
        self.assertFalse(driverstate.headphone_mode(dict(ds, **{"Headphone Virtualization Switch": "off"})))
        self.assertFalse(driverstate.headphone_mode({"Head Unit Output": "Headphones", "Direct Mode Switch": "off"}))
        lov = dict(ds, **{"Line Out Virtualization Switch": "on"})
        self.assertEqual(driverstate.output_jack(lov), "RCA out (headphone mix)")
        self.assertEqual(driverstate.output_jack(dict(lov, **{"Direct Mode Switch": "on"})), "headphones")
        self.assertEqual(driverstate.output_jack({"Head Unit Output": "Speakers"}), "RCA out")
        self.assertIsNone(driverstate.output_jack({}))


class Detector(unittest.TestCase):
    def setUp(self):
        self.lines = []
        self.changes = 0
        self.dir = os.path.join(os.environ["XDG_STATE_HOME"], self.id())
        self.det = onesided.OneSidedDetector(self.lines.append, self.count, save_dir=self.dir,
                                             note=lambda now: " | after: PCM Bit Depth 32-bit → 24-bit, 0.8 s before")
        mon = music(10)
        # fine for 5 s, then the right channel goes to static
        good = what_u_hear(mon)
        bad = what_u_hear(mon, r_static)
        split = int(5 * RATE)
        self.mon = Source(mon)
        self.wuh = Source(np.concatenate([good[:split], bad[split:]]))

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
        self.run_for(0.9)                       # 4.0-4.9 s: fine
        self.assertFalse(self.det.active)
        self.run_for(1.6)                       # into the static
        self.assertTrue(self.det.active)
        self.assertEqual((self.det.side, self.det.count, self.changes), ("R", 1, 1))
        start = next(l for l in self.lines if "start" in l)
        self.assertTrue(start.startswith("ONE-SIDED R #1 start: What U Hear R is noisy"), start)
        self.assertIn("37 ms behind", start)
        self.assertIn(support.FAKE_DRIVER, start)
        self.assertIn("after: PCM Bit Depth 32-bit → 24-bit", start)

        saved = [l for l in self.lines if "saved the last 3 s" in l]
        self.assertEqual(len(saved), 1)
        files = os.listdir(self.dir)
        self.assertEqual(len(files), 1)
        with wave.open(os.path.join(self.dir, files[0])) as f:
            self.assertEqual((f.getnchannels(), f.getsampwidth(), f.getframerate(), f.getnframes()),
                             (4, 3, RATE, 3 * RATE))
            raw = np.frombuffer(f.readframes(f.getnframes()), np.uint8).reshape(-1, 3)
        pcm = (raw[:, 0].astype(np.int32) | raw[:, 1].astype(np.int32) << 8 |
               raw[:, 2].astype(np.int8).astype(np.int32) << 16).reshape(-1, 4) / 2 ** 23
        # the output and What U Hear are lined up in the file: L matches L
        self.assertGreater(np.corrcoef(pcm[:, 0], pcm[:, 2])[0, 1], 0.99)

        self.run_for(0.5, judge=False, held="playback stopped, AE-9 sink idle")
        self.assertFalse(self.det.active)
        self.assertIn("ONE-SIDED R #1 end after", self.lines[-1])
        self.assertIn("(playback stopped, AE-9 sink idle)", self.lines[-1])

    def test_meters_without_samples_are_never_judged(self):
        self.det.update(time.monotonic(), object(), object(), True)
        self.assertIsNone(self.det.last)


class DriverState(unittest.TestCase):
    def test_events_and_values(self):
        self.assertEqual(driverstate.parse_event("event value: numid=2,iface=MIXER,name='PCM Bit Depth'"),
                         ("value", 2, "PCM Bit Depth"))
        self.assertIsNone(driverstate.parse_event("something else"))
        enum = ("numid=2,iface=MIXER,name='PCM Bit Depth'\n"
                "  ; type=ENUMERATED,access=rw------,values=1,items=2\n"
                "  ; Item #0 '24-bit'\n  ; Item #1 '32-bit'\n  : values=1\n")
        self.assertEqual(driverstate.parse_cget(enum), "32-bit")
        boolean = "numid=16,iface=MIXER,name='SBX Switch'\n  ; type=BOOLEAN,access=rwi-----,values=1\n  : values=off\n"
        self.assertEqual(driverstate.parse_cget(boolean), "off")
        self.assertIsNone(driverstate.parse_cget(""))
        level = ("numid=46,iface=MIXER,name='Speaker Level Front L Volume'\n"
                 "  ; type=INTEGER,access=rwi--R--,values=1,min=-12,max=12,step=1\n"
                 "  : values=3\n  | dBscale-min=-12.00dB,step=1.00dB,mute=0\n")
        self.assertEqual(driverstate.parse_cget(level), "+3 dB")
        dist = ("numid=47,iface=MIXER,name='Speaker Distance Front L'\n"
                "  ; type=INTEGER,access=rwi-----,values=1,min=0,max=500,step=1\n  : values=120\n")
        self.assertEqual(driverstate.parse_cget(dist), "120 cm")
        self.assertEqual(driverstate.number("+3 dB"), 3.0)
        self.assertEqual(driverstate.number("-1.5 dB"), -1.5)
        self.assertIsNone(driverstate.number(None))
        self.assertEqual(driverstate.channels("S32_LE, 6 ch, 48000 Hz"), 6)

    def test_stream_format(self):
        support.set_stream("closed\n")
        self.assertIsNone(driverstate.read_hw_params())
        support.set_stream("access: RW_INTERLEAVED\nformat: S32_LE\nsubformat: STD\nchannels: 2\n"
                           "rate: 48000 (48000/1)\nperiod_size: 1024\nbuffer_size: 4096\n")
        self.assertEqual(driverstate.read_hw_params(), "S32_LE, 2 ch, 48000 Hz, period 1024, buffer 4096")
        support.set_stream("closed\n")
        self.assertIsNone(driverstate.read_hw_params(os.path.join(os.environ["XDG_STATE_HOME"], "nope")))


if __name__ == "__main__":
    unittest.main()
