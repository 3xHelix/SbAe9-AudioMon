# SPDX-License-Identifier: GPL-2.0-only
"""ControlWatcher lets go of the card when "Card Answering" goes off (driver
0.12.9+) and opens it again only after the driver has been reloaded."""

import os
import shutil
import tempfile
import threading
import time
import unittest
from unittest import mock

import support  # noqa: F401  (loads the app's modules the way the other tests do)
from audiomon import driverstate

ControlWatcher = support.REAL.get("ControlWatcher", driverstate.ControlWatcher)

EVENTS = ["event add: numid=70,iface=MIXER,name='Card Answering'\n",
          "event value: numid=70,iface=MIXER,name='Card Answering'\n",
          "event value: numid=12,iface=MIXER,name='SBX Switch'\n"]    # never read: already let go


class FakeProc:
    def __init__(self, lines, block=None):
        self.lines, self.block = lines, block
        self.terminated = False

    @property
    def stdout(self):
        yield from self.lines
        if self.block:
            self.block.wait(5)

    def terminate(self):
        self.terminated = True
        if self.block:
            self.block.set()

    def wait(self):
        return 0

    def poll(self):
        return 0 if self.terminated else None


def wait_for(cond, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


class CardAnswering(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.paths = tuple(os.path.join(self.tmp, n) for n in ("module", "card"))
        for p in self.paths:
            os.mkdir(p)
        self.procs = [FakeProc(EVENTS), FakeProc([], block=threading.Event())]
        self.cgets = []
        values = iter(["on", "off"])

        def cget(_self, numid):
            self.cgets.append(numid)
            return next(values)

        for target, attr, new in ((driverstate, "RELOAD_PATHS", self.paths),
                                  (driverstate.subprocess, "Popen",
                                   mock.Mock(side_effect=lambda *a, **k: self.procs.pop(0))),
                                  (ControlWatcher, "_cget", cget),
                                  (driverstate, "child_cmd", lambda cmd: cmd)):
            p = mock.patch.object(target, attr, new)
            p.start()
            self.addCleanup(p.stop)
        self.popen = driverstate.subprocess.Popen

    def test_lets_go_until_the_driver_reloads(self):
        first = self.procs[0]
        w = ControlWatcher()
        self.addCleanup(w.stop)
        self.assertTrue(wait_for(lambda: w.released))
        self.assertTrue(first.terminated)
        self.assertEqual(self.cgets, [70, 70])          # the SBX event after it isn't read
        seen = w.poll()
        self.assertEqual(seen, [("Card Answering", "on"), ("Card Answering", "off")])
        time.sleep(1.0)
        self.assertEqual(self.popen.call_count, 1)      # not opened again while it's the same card
        os.rmdir(self.paths[1])                         # modprobe -r: the card goes away...
        time.sleep(0.8)
        self.assertEqual(self.popen.call_count, 1)
        os.mkdir(self.paths[1])                         # ...and modprobe brings a new one
        self.assertTrue(wait_for(lambda: self.popen.call_count == 2))
        self.assertFalse(w.released)


if __name__ == "__main__":
    unittest.main()
