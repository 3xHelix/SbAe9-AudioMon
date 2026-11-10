# SPDX-License-Identifier: GPL-2.0-only
"""Capture: one pw-record process = one PipeWire meter node."""

import collections
import subprocess
import threading
import time

import numpy as np

from .core import APP_NAME, BLOCK, CHANNEL_MAPS, RATE, child_cmd, has_option, spa_str


class Capture:
    """Runs pw-record as an unlinked PipeWire node and accumulates levels.

    The reader thread adds up per-channel peak and sum-of-squares; the UI
    calls take() each frame to collect and reset them.

    passive=True sets node.passive so the meter never keeps whatever it is
    linked to awake: it only receives data while the peer runs anyway.
    """

    def __init__(self, node_name, label, channels, passive=False):
        self.node_name = node_name
        self.label = label
        self.channels = channels
        self.passive = passive
        self.lock = threading.Lock()
        self.error = None
        self.last_data = 0.0
        self._stopping = False
        self._proc = None
        self._ring = None           # recent samples, when keep_samples() asked for them
        self._ring_pos = 0
        self._ring_filled = 0
        self._ring_time = 0.0
        self._clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _clear(self):
        self._peak = np.zeros(self.channels)
        self._sumsq = np.zeros(self.channels)
        self._frames = 0

    def keep_samples(self, seconds):
        """Also keep the last `seconds` of raw samples, for recent()."""
        with self.lock:
            self._ring = np.zeros((int(seconds * RATE), self.channels), dtype=np.float32)
            self._ring_pos = self._ring_filled = 0

    def recent(self, frames):
        """(the newest `frames` samples as a frames x channels array, the
        time the newest one arrived), or None until that many have come in."""
        with self.lock:
            ring = self._ring
            if ring is None or frames > min(self._ring_filled, len(ring)):
                return None
            idx = (self._ring_pos - frames + np.arange(frames)) % len(ring)
            return ring[idx], self._ring_time

    def _keep(self, a):
        ring = self._ring
        if ring is None:
            return
        n = len(ring)
        a = a[-n:]
        idx = (self._ring_pos + np.arange(len(a))) % n
        ring[idx] = a
        self._ring_pos = (self._ring_pos + len(a)) % n
        self._ring_filled += len(a)
        self._ring_time = self.last_data

    def take(self):
        with self.lock:
            out = (self._peak, self._sumsq, self._frames, self.last_data)
            self._clear()
        return out

    def _command(self):
        label = spa_str(self.label)
        props = (
            "{ "
            f'node.name = "{spa_str(self.node_name)}" '
            f'node.description = "{label}" '
            f'node.nick = "{label}" '
            f'media.name = "{label}" '
            f'application.name = "{APP_NAME}" '
            "stream.dont-remix = true "
            "state.restore-props = false "
            "state.restore-target = false "
            + ("node.passive = true " if self.passive else "")
            + "}"
        )
        return child_cmd([
            # PipeWire 1.4+ needs --raw for raw samples on stdout (else a .snd
            # header and big-endian floats); 1.0.x writes raw and lacks the option
            "pw-record", *(["--raw"] if has_option("pw-record", "--raw") else []),
            "--format", "f32",
            "--rate", str(RATE),
            "--channels", str(self.channels),
            "--channel-map", ",".join(CHANNEL_MAPS[self.channels]),
            "--target", "0",
            "--latency", str(BLOCK),
            "-P", props,
            "-",
        ])

    @staticmethod
    def _drain(stream, errors):
        """Keep pw-record's last stderr lines, so a failure says why."""
        for line in iter(stream.readline, b""):
            line = line.decode(errors="replace").strip()
            if line:
                errors.append(line)
        stream.close()

    def _run(self):
        n = self.channels
        chunk = BLOCK * n * 4
        while not self._stopping:
            try:
                proc = subprocess.Popen(self._command(), stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE)
            except FileNotFoundError:
                self.error = "pw-record not found (install pipewire-utils, or pipewire-bin on Debian / Ubuntu)"
                return
            self._proc = proc
            errors = collections.deque(maxlen=3)
            drain = threading.Thread(target=self._drain, args=(proc.stderr, errors), daemon=True)
            drain.start()
            if self._stopping:
                proc.terminate()
            while True:
                data = proc.stdout.read(chunk)
                if not data:
                    break
                usable = len(data) - len(data) % (n * 4)
                if not usable:
                    continue
                a = np.frombuffer(data[:usable], dtype=np.float32).reshape(-1, n)
                pk = np.abs(a).max(axis=0)
                ss = np.square(a, dtype=np.float64).sum(axis=0)
                with self.lock:
                    np.maximum(self._peak, pk, out=self._peak)
                    self._sumsq += ss
                    self._frames += a.shape[0]
                    self.last_data = time.monotonic()
                    self._keep(a)
            proc.wait()
            drain.join(1.0)
            if not self._stopping:
                why = errors[-1] if errors else f"exit {proc.returncode}"
                self.error = f"pw-record exited ({why}), restarting..."
                time.sleep(1.0)
                self.error = None

    def stop(self):
        self._stopping = True
        proc = self._proc
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
