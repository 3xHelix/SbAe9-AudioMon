# SPDX-License-Identifier: GPL-2.0-only
"""Meter state: VU ballistics, peak hold and stats."""

import math

import numpy as np

from .core import CHANNEL_MAPS, FLOOR_DB, PEAK_FALL, PEAK_HOLD, VU_TAU, amp_db, pow_db


class MeterState:
    def __init__(self, channels):
        self.channels = channels
        self.names = CHANNEL_MAPS[channels]
        self.rms_pow = np.zeros(channels)          # VU-smoothed power
        self.peak_db = np.full(channels, FLOOR_DB)  # falling peak bar
        self.hold_db = np.full(channels, FLOOR_DB)  # peak hold marker
        self.hold_t = np.zeros(channels)
        self.reset_stats()

    def reset_stats(self):
        self.max_amp = np.zeros(self.channels)
        self.tot_ss = np.zeros(self.channels)
        self.tot_frames = 0
        self.clipped = np.zeros(self.channels, dtype=bool)

    def update(self, peak, sumsq, frames, last_data, now, dt):
        if frames == 0 and now - last_data < 0.2:
            return  # stream is live, block just hasn't arrived yet
        inst_pow = sumsq / frames if frames else np.zeros(self.channels)
        self.rms_pow += (inst_pow - self.rms_pow) * (1.0 - math.exp(-dt / VU_TAU))

        pk_db = amp_db(peak)
        self.peak_db = np.maximum(pk_db, self.peak_db - PEAK_FALL * dt)
        new_hold = pk_db >= self.hold_db
        self.hold_db = np.where(new_hold, pk_db, self.hold_db)
        self.hold_t = np.where(new_hold, now, self.hold_t)
        expired = (now - self.hold_t) > PEAK_HOLD
        self.hold_db = np.where(expired, np.maximum(self.hold_db - PEAK_FALL * dt, self.peak_db),
                                self.hold_db)

        if frames:
            self.max_amp = np.maximum(self.max_amp, peak)
            self.clipped |= peak >= 1.0
            self.tot_ss += sumsq
            self.tot_frames += frames

    @property
    def rms_db(self):
        return pow_db(self.rms_pow)

    @property
    def avg_db(self):
        if not self.tot_frames:
            return np.full(self.channels, FLOOR_DB)
        return pow_db(self.tot_ss / self.tot_frames)
