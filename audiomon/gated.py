# SPDX-License-Identifier: GPL-2.0-only
"""GATED: the card plays in bursts, 64 frames of audio then 64 frames of
silence, 375 times a second. No Qt.

It's the "half speed with a buzz" fault on driver 0.10.x: after a speakers
<-> headphones switch in DSP mode (Direct Mode off), the DSP output only
every other 64-frame block. Driver 0.11.0 fixes it. What U Hear records the
gaps, but SILENT and ONE-SIDED don't see them (there's sound, on both sides).

Each check folds the last half second of What U Hear onto one 2.667 ms
period (128 frames at 48 kHz) and measures how much of the energy lands in
the quietest half of it. Steady audio spreads evenly (about 0.5); gated
audio puts almost nothing there (about 0.0). A pure tone at a multiple of
187.5 Hz can score low too, so the output monitor is checked the same way:
it's only GATED when the output itself is spread evenly.
"""

import os
import time

import numpy as np

from . import core
from .core import RATE
from .onesided import STALE, tilde, write_wav

PERIOD = 128 * RATE // 48000    # frames per gating period (2.667 ms)
WINDOW = 0.5            # seconds judged per check
EVAL_EVERY = 0.2        # seconds between checks
GATED_AFTER = 0.5       # gated this long to be flagged (and clean this long to end)
SAVE_SECONDS = 3.0
MAX_SAVES = 20

GATED_SHARE = 0.25      # What U Hear's quiet-half share below this...
MON_SHARE = 0.4         # ...while the output's stays above this
WUH_MIN_DB = -60.0      # What U Hear must carry this much (RMS)
MON_MIN_DB = -45.0      # and the output this much


def quiet_share(x, period=PERIOD):
    """The share of x's energy (frames x channels) in the quietest half of
    the phases of `period`, and its RMS in dBFS. (None, dB) when silent."""
    n = len(x) // period * period
    if n == 0:
        return None, -200.0
    x = np.asarray(x[-n:], dtype=np.float64)
    e = np.einsum("ij,ij->i", x, x).reshape(-1, period).sum(0)
    total = float(e.sum())
    db = 10 * np.log10(max(total / (n * x.shape[1]), 1e-20))
    if total < 1e-12:
        return None, db
    return float(np.sort(e)[:period // 2].sum() / total), db


def analyze(mon, wuh):
    """mon, wuh: frames x channels. {"gated": bool, "share", "mon_share",
    "wuh_db", "mon_db"}; gated is None when there's too little to judge."""
    share, wuh_db = quiet_share(wuh)
    mon_share, mon_db = quiet_share(mon)
    res = dict(share=share, mon_share=mon_share, wuh_db=wuh_db, mon_db=mon_db, gated=None)
    if share is None or mon_share is None or wuh_db < WUH_MIN_DB or mon_db < MON_MIN_DB:
        return res
    if mon_share < MON_SHARE:       # the music itself repeats at 375 Hz: can't tell
        return res
    res["gated"] = share < GATED_SHARE
    return res


class GatedDetector:
    def __init__(self, log, on_change, save_dir=None, note=lambda now: ""):
        self.log = log
        self.on_change = on_change
        self.save_dir = save_dir
        self.note = note
        self.active = False
        self.count = 0
        self.saves = 0
        self.started = 0.0
        self.last = None
        self._since = None          # gated since (not yet flagged)
        self._clean_since = None    # clean since (while flagged)
        self._next = 0.0

    def update(self, now, mon_cap, wuh_cap, judge, held_why=""):
        recent = (getattr(mon_cap, "recent", None), getattr(wuh_cap, "recent", None))
        if not judge or None in recent:
            self._since = None
            self.end(now, held_why or "not judged")
            return
        if now < self._next:
            return
        self._next = now + EVAL_EVERY
        n = int(WINDOW * RATE)
        m, w = recent[0](n), recent[1](n)
        if m is None or w is None or now - m[1] > STALE or now - w[1] > STALE:
            return
        res = self.last = analyze(m[0], w[0])
        if res["gated"]:
            self._clean_since = None
            if self._since is None:
                self._since = now
            elif not self.active and now - self._since >= GATED_AFTER:
                self._start(now, res, mon_cap, wuh_cap)
        else:
            self._since = None
            if self.active:
                if self._clean_since is None:
                    self._clean_since = now
                elif now - self._clean_since >= GATED_AFTER:
                    self.end(now, "What U Hear plays steadily again" if res["gated"] is False
                             else "too quiet to judge")

    def _start(self, now, res, mon_cap, wuh_cap):
        self.active = True
        self.count += 1
        self.started = self._since
        self._clean_since = None
        self.log(f"GATED #{self.count} start: What U Hear plays in {PERIOD // 2}-frame bursts "
                 f"(quiet-half share {res['share']:.2f}, output {res['mon_share']:.2f}; "
                 f"the driver 0.10.x half-speed fault, fixed in 0.11.0) | "
                 f"What U Hear {res['wuh_db']:.1f} dBFS RMS | {core.driver_info()}" + self.note(now))
        saved = self._save(mon_cap, wuh_cap)
        if saved:
            self.log(f"GATED #{self.count}: saved the last {SAVE_SECONDS:.0f} s "
                     f"(output L/R, What U Hear L/R) to {tilde(saved)}")
        self.on_change()

    def _save(self, mon_cap, wuh_cap):
        if not self.save_dir or self.saves >= MAX_SAVES:
            return None
        n = int(SAVE_SECONDS * RATE)
        m, w = mon_cap.recent(n), wuh_cap.recent(n)
        if m is None or w is None:
            return None
        mon, wuh = m[0], w[0]
        path = os.path.join(self.save_dir, f"gated-{time.strftime('%Y%m%d-%H%M%S')}.wav")
        try:
            write_wav(path, [mon[:, 0], mon[:, 1], wuh[:, 0], wuh[:, 1]])
        except OSError as e:
            self.log(f"GATED: couldn't save the WAV: {e}")
            return None
        self.saves += 1
        return path

    def end(self, now, why):
        if self.active:
            self.active = False
            self._clean_since = None
            self.log(f"GATED #{self.count} end after {now - self.started:.1f}s ({why})")
            self.on_change()
