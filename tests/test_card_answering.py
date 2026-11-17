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

        self.notice = os.path.join(self.tmp, "ae9-reload")
        for target, attr, new in ((driverstate, "RELOAD_PATHS", self.paths),
                                  (driverstate, "RELOAD_NOTICE", self.notice),
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


    def test_lets_go_while_ae9_reload_runs(self):
        self.procs[0] = FakeProc([], block=threading.Event())     # a healthy card: no events
        first = self.procs[0]
        w = ControlWatcher()
        self.addCleanup(w.stop)
        self.assertTrue(wait_for(lambda: self.popen.call_count == 1))
        with open(self.notice, "w") as f:                          # ae9-reload starts
            f.write(f"{os.getpid()}\n")
        self.assertTrue(wait_for(lambda: first.terminated, 2.0))   # within 0.5 s or so
        self.assertIn(("ae9-reload", "released"), w.poll())
        os.rmdir(self.paths[1])                                    # unload...
        os.mkdir(self.paths[1])                                    # ...and load
        time.sleep(1.0)
        self.assertEqual(self.popen.call_count, 1)                 # not while the notice is there
        os.remove(self.notice)                                     # ae9-reload is done
        self.assertTrue(wait_for(lambda: self.popen.call_count == 2))
        self.assertIn(("ae9-reload", "reopened"), w.poll())
        self.assertFalse(w.released)


class Notice(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.path = os.path.join(self.tmp, "ae9-reload")
        self.n = driverstate.ReloadNotice(self.path)

    def write(self, text):
        with open(self.path, "w") as f:
            f.write(text)

    def test_missing(self):
        self.assertFalse(self.n.active())

    def test_live_and_dead_pid(self):
        self.write(f"{os.getpid()}\n")
        self.assertTrue(self.n.active())
        self.write("999999999\n")              # a killed reload left it behind
        self.assertFalse(self.n.active())

    def test_empty_counts_for_a_while(self):
        self.write("")
        self.assertTrue(self.n.active(now=100.0))
        self.assertTrue(self.n.active(now=219.0))
        self.assertFalse(self.n.active(now=221.0))
        os.remove(self.path)
        self.assertFalse(self.n.active(now=222.0))
        self.write("")                          # a new reload starts the clock again
        self.assertTrue(self.n.active(now=300.0))


if __name__ == "__main__":
    unittest.main()
