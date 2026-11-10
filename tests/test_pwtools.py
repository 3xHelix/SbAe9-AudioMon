# SPDX-License-Identifier: GPL-2.0-only
"""pw-record / pw-link options picked by what the installed PipeWire has:
PipeWire 1.0.x (Ubuntu 24.04, Mint 22) has no `pw-record --raw` and no
`pw-link --wait`."""

import subprocess
import unittest
from unittest import mock

import support  # noqa: F401  (loads the app's modules the way the other tests do)
from audiomon import capture, core, pipewire

Linker = support.REAL.get("Linker", pipewire.Linker)
Capture = support.REAL.get("Capture", capture.Capture)

HELP_105 = {"pw-record": "pw-record [options] [<file>|-]\n      --format\n      --target\n",
            "pw-link": "Connect: pw-link [options] output input\n  -L, --linger\n"}
HELP_16 = {"pw-record": "      --raw    RAW mode\n      --format\n",
           "pw-link": "  -w, --wait    Wait until link creation attempt\n"}


def fake_run(helps):
    def run(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 0, helps[cmd[0]], "")
    return run


class Options(unittest.TestCase):
    def setUp(self):
        core.has_option.cache_clear()
        self.addCleanup(core.has_option.cache_clear)

    def with_pipewire(self, helps):
        p = mock.patch.object(core.subprocess, "run", fake_run(helps))
        p.start()
        self.addCleanup(p.stop)

    def record_cmd(self):
        cap = object.__new__(Capture)       # no reader thread, no real pw-record
        cap.node_name, cap.label, cap.channels, cap.passive = "n", "l", 2, False
        return cap._command()

    def test_pipewire_105(self):
        self.with_pipewire(HELP_105)
        self.assertNotIn("--raw", self.record_cmd())
        self.assertEqual(Linker().wait, [])

    def test_pipewire_16(self):
        self.with_pipewire(HELP_16)
        self.assertIn("--raw", self.record_cmd())
        self.assertEqual(Linker().wait, ["--wait"])

    def test_missing_tool(self):
        with mock.patch.object(core.subprocess, "run", side_effect=FileNotFoundError):
            self.assertFalse(core.has_option("pw-record", "--raw"))


if __name__ == "__main__":
    unittest.main()
