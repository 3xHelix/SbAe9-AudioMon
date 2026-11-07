# SPDX-License-Identifier: GPL-2.0-only
"""ONE-SIDED: one What U Hear channel stops matching what was sent to the
card, while the other still does. No Qt.

It catches the card's router dropping one channel's route into the DSP
after a stream restart (static or silence on one side only), which SILENT
can't see because the other side keeps playing. Each check lines up the
last half second of What U Hear with the output monitor (What U Hear runs a
little behind) and compares the two channel by channel:

  - one side stops following the output while the other still does,
  - one side carries much more noise (whatever doesn't match the output)
    than the other side, or much more above 8 kHz,
  - one side's level is far off compared with the other side's,
  - the output's L and R match each other but What U Hear's no longer do
    (not judged with SBX on in DSP mode, whose Surround spreads L and R).

Every rule compares one side with the other, so effects that change both
sides alike (SBX, Crystalizer, EQ) don't trip it.
"""

import os
import time
import wave

import numpy as np

from . import core
from .core import RATE

WINDOW = 0.5            # seconds of What U Hear judged per check
MAX_LAG = 0.5           # how far What U Hear may run behind the output
EVAL_EVERY = 0.2        # seconds between checks
ONE_SIDED_AFTER = 0.5   # a side must stay off this long to be flagged (and back to end)
KEEP = 4.0              # seconds of samples the two meters keep
SAVE_SECONDS = 3.0      # length of the WAV saved when it's flagged
MAX_SAVES = 20          # WAVs per run, at most
STALE = 0.25            # samples older than this don't count

MON_MIN_DB = -45.0      # both output channels must be at least this loud (RMS)
ALIGN_MIN = 0.3         # below this the two can't be lined up: no verdict
TRACK_BAD, TRACK_GOOD = 0.5, 0.85
TRACK_BAD_SBX, TRACK_GOOD_SBX = 0.3, 0.7
LEVEL_SKEW_DB = 10.0
HF_CUT = 8000.0
HF_MIN_DB = -50.0
HF_SKEW_DB = 10.0
NOISE_SKEW_DB = 12.0    # one side's mismatch (1 - match^2) this far above the other's...
NOISE_MIN_DB = -16.0    # ...and at least this much (about a 0.987 match)
SILENT_DB = -90.0
LR_MON, LR_WUH = 0.9, 0.5

SIDES = ("L", "R")
BASS_CUT_HZ = 150.0     # with Bass Redirect on, judge only what's above this
SPEED_OF_SOUND = 34300.0  # cm/s, for Speaker Distance


def _db(power):
    return 10 * np.log10(max(float(power), 1e-20))


def _dot(a, b):
    # not np.dot: with a threaded BLAS it can cost milliseconds per call
    return float(np.einsum("i,i->", a, b))


def _ncc(a, b):
    den = np.sqrt(_dot(a, a) * _dot(b, b))
    return _dot(a, b) / den if den > 0 else 0.0


def align(mon, wuh):
    """Where What U Hear lines up in the monitor: (offset into mon, how well
    the two match there, 0..1), from the L+R sums."""
    return _align1(mon[:, 0].astype(np.float64) + mon[:, 1], wuh[:, 0].astype(np.float64) + wuh[:, 1])


def _align1(m, w):
    big, small = len(m), len(w)
    n = 1 << (big + small).bit_length()
    c = np.fft.irfft(np.fft.rfft(m, n) * np.conj(np.fft.rfft(w, n)), n)[:big - small + 1]
    cs = np.concatenate(([0.0], np.cumsum(m * m)))
    den = np.sqrt((cs[small:] - cs[:-small]) * _dot(w, w))
    r = np.divide(c, den, out=np.zeros_like(c), where=den > 0)
    off = int(np.argmax(r))
    return off, float(r[off])


def delay_frames(dist_l_cm, dist_r_cm):
    """The delay between the sides that Speaker Distance asks for, in frames."""
    return int(round(abs(dist_l_cm - dist_r_cm) / SPEED_OF_SOUND * RATE))


def _no_lows(a):
    """a (frames x channels) with everything below BASS_CUT_HZ taken out
    (an FFT high-pass, tapered over the octave below so it doesn't ring)."""
    n = len(a)
    spec = np.fft.rfft(a.astype(np.float64), axis=0)
    f = np.fft.rfftfreq(n, 1 / RATE)
    gain = np.clip((f - BASS_CUT_HZ / 2) / (BASS_CUT_HZ / 2), 0.0, 1.0)
    return np.fft.irfft(spec * gain[:, None], n, axis=0)


def _hf_power(rows):
    """Mean power above HF_CUT of each row."""
    n = rows.shape[1]
    win = np.hanning(n)
    spec = np.abs(np.fft.rfft(rows * win, axis=1)) ** 2
    cut = int(np.ceil(HF_CUT * n / RATE))
    return 2 * spec[:, cut:].sum(axis=1) / (n * _dot(win, win))


def analyze(mon, wuh, sbx=False, level_offset_db=0.0, side_delay=0, no_lows=False):
    """Compare What U Hear (frames x 2) with a longer stretch of the monitor
    that ends at the same moment. Returns a dict with the numbers and
    side ("L", "R" or None) plus why.

    What the card does to the sides on purpose, in DSP mode:
      sbx               SBX on: looser match rules, no L/R rule
      level_offset_db   Speaker Level, R minus L: the level skew to expect
      side_delay        Speaker Distance: frames one side may run behind the other
      no_lows           Bass Redirect: compare above BASS_CUT_HZ only
    """
    out = {"side": None, "why": "", "aligned": False}
    if len(mon) < len(wuh) or mon.shape[1] < 2 or wuh.shape[1] < 2:
        return out
    if no_lows:
        mon, wuh = _no_lows(mon[:, :2]), _no_lows(wuh[:, :2])
    small = len(wuh)
    off, match = align(mon, wuh)
    offs = [off, off]
    if side_delay:
        # each side lines up on its own, within the delay asked for
        for c in (0, 1):
            lo = max(0, off - side_delay - 16)
            hi = min(len(mon) - small, off + side_delay + 16)
            offs[c] = lo + _align1(mon[lo:hi + small, c].astype(np.float64), wuh[:, c].astype(np.float64))[0]
    seg = np.ascontiguousarray(np.stack([mon[offs[c]:offs[c] + small, c] for c in (0, 1)]), dtype=np.float64)
    w = np.ascontiguousarray(wuh[:, :2].T, dtype=np.float64)    # one row per channel
    mon_db = [_db(_dot(seg[c], seg[c]) / len(seg[c])) for c in (0, 1)]
    wuh_db = [_db(_dot(w[c], w[c]) / len(w[c])) for c in (0, 1)]
    corr = [_ncc(seg[c], w[c]) for c in (0, 1)]
    hf_mon = [_db(p) for p in _hf_power(seg)]
    hf_wuh = [_db(p) for p in _hf_power(w)]
    out.update(aligned=match >= ALIGN_MIN, match=match, lag_ms=(len(mon) - off - len(wuh)) * 1000 / RATE,
               lag_frames=len(mon) - off - len(wuh), mon_db=mon_db, wuh_db=wuh_db, corr=corr,
               lr_mon=_ncc(seg[0], seg[1]), lr_wuh=_ncc(w[0], w[1]),
               hf_wuh=hf_wuh)
    # how much more there is above 8 kHz than the output had, beyond the side's own gain
    out["hf_excess"] = [hf_wuh[c] - hf_mon[c] - (wuh_db[c] - mon_db[c]) for c in (0, 1)]
    if not out["aligned"] or min(mon_db) < MON_MIN_DB:
        return out
    bad, good = (TRACK_BAD_SBX, TRACK_GOOD_SBX) if sbx else (TRACK_BAD, TRACK_GOOD)
    gain = [wuh_db[c] - mon_db[c] for c in (0, 1)]
    ex = out["hf_excess"]
    noise = [_db(max(1.0 - corr[c] ** 2, 1e-6)) for c in (0, 1)]
    out["noise_db"] = noise

    def verdict(c, why):
        out.update(side=SIDES[c], why=why)
        return out

    for c in (0, 1):
        if corr[c] < bad and corr[1 - c] > good:
            what = "went silent" if wuh_db[c] < SILENT_DB else "stopped following the output"
            return verdict(c, f"{what} (match {corr[c]:.2f}, {SIDES[1 - c]} {corr[1 - c]:.2f})")
    for c in (0, 1):
        o = 1 - c
        if noise[c] - noise[o] > NOISE_SKEW_DB and noise[c] > NOISE_MIN_DB:
            hf = f", {ex[c] - ex[o]:.0f} dB more above {HF_CUT / 1000:.0f} kHz" if ex[c] - ex[o] > 3 else ""
            return verdict(c, f"is noisy (match {corr[c]:.2f}, {SIDES[o]} {corr[o]:.2f}{hf})")
    for c in (0, 1):
        o = 1 - c
        if hf_wuh[c] > HF_MIN_DB and ex[c] - ex[o] > HF_SKEW_DB:
            return verdict(c, f"has noise above {HF_CUT / 1000:.0f} kHz, {ex[c] - ex[o]:.0f} dB more than {SIDES[o]}")
    skew = gain[1] - gain[0] - level_offset_db
    if abs(skew) > LEVEL_SKEW_DB:
        # the side that matches worse is the broken one; if both match, the one further
        # from the output's level (What U Hear plays back level-exact)
        off_level = [abs(gain[0]), abs(gain[1] - level_offset_db)]
        c = int(np.argmin(corr)) if abs(corr[0] - corr[1]) > 0.05 else int(np.argmax(off_level))
        loud = (skew > 0) == (c == 1)
        return verdict(c, f"{abs(skew):.0f} dB {'louder' if loud else 'quieter'} than {SIDES[1 - c]}")
    if not sbx and not side_delay and out["lr_mon"] > LR_MON and out["lr_wuh"] < LR_WUH:
        c = int(np.argmin(corr))
        out.update(side=SIDES[c], why=f"L and R no longer match each other (output {out['lr_mon']:.2f}, "
                                      f"What U Hear {out['lr_wuh']:.2f})")
    return out


def write_wav(path, channels):
    """Save float channels (each a 1-D array, -1..1) as one 24-bit WAV."""
    a = np.stack(channels, axis=1)
    pcm = (np.clip(a, -1.0, 1.0 - 2 ** -23) * 2 ** 23).astype("<i4")
    data = pcm.view(np.uint8).reshape(-1, 4)[:, :3].tobytes()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with wave.open(path, "wb") as f:
        f.setnchannels(a.shape[1])
        f.setsampwidth(3)
        f.setframerate(RATE)
        f.writeframes(data)


def tilde(path):
    home = os.path.expanduser("~")
    return "~" + path[len(home):] if path.startswith(home + os.sep) else path


class OneSidedDetector:
    def __init__(self, log, on_change, save_dir=None, note=lambda now: ""):
        self.log = log
        self.on_change = on_change
        self.save_dir = save_dir
        self.note = note
        self.active = False
        self.side = None
        self.count = 0
        self.saves = 0
        self.started = 0.0
        self.last = None            # the latest analysis
        self._cand = None
        self._since = 0.0
        self._last_flag = -1e9
        self._next = 0.0
        self._why = ""

    def update(self, now, mon_cap, wuh_cap, judge, held_why="", **setup):
        """judge: False while What U Hear can't be compared (not wired yet,
        PipeWire restarting, SILENT); held_why says why, for the end line.
        setup: what the card does to the sides on purpose (see analyze())."""
        recent = (getattr(mon_cap, "recent", None), getattr(wuh_cap, "recent", None))
        if not judge or None in recent:
            self._cand = None
            self.end(now, held_why or "not judged")
            return
        if now < self._next:
            return
        self._next = now + EVAL_EVERY
        res = self._analyze(now, *recent, setup)
        side = res["side"] if res else None
        if side:
            self._last_flag = now
            if side != self._cand:
                self._cand, self._since, self._why = side, now, res["why"]
            elif not self.active and now - self._since >= ONE_SIDED_AFTER:
                self._start(now, res, mon_cap, wuh_cap)
        else:
            self._cand = None
        if self.active and now - self._last_flag > ONE_SIDED_AFTER:
            self.end(now, f"What U Hear {self.side} back in step" if res else "no data")

    def _analyze(self, now, mon_recent, wuh_recent, setup):
        small, lag = int(WINDOW * RATE), int(MAX_LAG * RATE)
        w = wuh_recent(small)
        m = mon_recent(small + lag + int(STALE * RATE))
        if w is None or m is None:
            return None
        (wuh, tw), (mon, tm) = w, m
        if now - tw > STALE or now - tm > STALE:
            return None
        newer = max(0, int(round((tm - tw) * RATE)))        # monitor frames newer than What U Hear's newest
        mon = mon[:len(mon) - newer] if newer else mon
        mon = mon[-(small + lag):]
        if len(mon) < small + lag:
            return None
        res = analyze(mon, wuh, **setup)
        res["newer"] = newer
        self.last = res
        return res

    def _start(self, now, res, mon_cap, wuh_cap):
        self.active, self.side = True, res["side"]
        self.count += 1
        self.started = self._since
        c = SIDES.index(self.side)
        line = (f"ONE-SIDED {self.side} #{self.count} start: What U Hear {self.side} {res['why']} | "
                f"output {res['mon_db'][c]:.1f} dBFS RMS, What U Hear {res['wuh_db'][c]:.1f}, "
                f"{res['lag_ms']:.0f} ms behind | {core.driver_info()}")
        self.log(line + self.note(now))
        saved = self._save(res, mon_cap, wuh_cap)
        if saved:
            self.log(f"ONE-SIDED {self.side} #{self.count}: saved the last {SAVE_SECONDS:.0f} s "
                     f"(output L/R, What U Hear L/R) to {tilde(saved)}")
        self.on_change()

    def _save(self, res, mon_cap, wuh_cap):
        if not self.save_dir or self.saves >= MAX_SAVES:
            return None
        n = int(SAVE_SECONDS * RATE)
        shift = res["lag_frames"] + res["newer"]
        w = wuh_cap.recent(n)
        m = mon_cap.recent(n + shift + int(STALE * RATE))
        if w is None or m is None:
            return None
        (wuh, tw), (mon, tm) = w, m
        shift += max(0, int(round((tm - tw) * RATE))) - res["newer"]
        mon = mon[len(mon) - shift - n:len(mon) - shift] if shift else mon[-n:]
        if len(mon) != n:
            return None
        stamp = time.strftime("%Y%m%d-%H%M%S")
        path = os.path.join(self.save_dir, f"one-sided-{stamp}-{self.side}.wav")
        try:
            write_wav(path, [mon[:, 0], mon[:, 1], wuh[:, 0], wuh[:, 1]])
        except OSError as e:
            self.log(f"ONE-SIDED: couldn't save the WAV: {e}")
            return None
        self.saves += 1
        return path

    def end(self, now, why):
        if self.active:
            self.active = False
            self.log(f"ONE-SIDED {self.side} #{self.count} end after {now - self.started:.1f}s ({why})")
            self.on_change()
