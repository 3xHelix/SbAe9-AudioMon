# SPDX-License-Identifier: GPL-2.0-only
"""Test harness: SbAe9-AudioMon on the shared fake AE-9 (sbae9kit.testing).

setup() must run before anything imports the app. It:
  - points XDG_CONFIG / CACHE / STATE / DATA_HOME at a temp folder (settings,
    the debug log and icons never touch the real ones) and runs Qt offscreen,
  - makes any real subprocess start fail,
  - swaps the app's PipeWire graph (pw-dump), pw-link worker and pw-record
    meters for the kit's fakes, and the driver info for a fixed string,
  - shortens the modelled waits (TIME_SCALE) and the app's SILENT timings.

The fake card is support.PW (reset() makes a fresh one per test).
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from sbae9kit import testing  # noqa: E402

FAKE_DRIVER = "snd_hda_codec_creative_ae9 0.9.2 srcversion FAKE0000000000000000000"
PW = None
_done = False


def reset(**card):
    """A fresh fake card for the next test or scenario."""
    global PW
    PW = testing.FakePipeWire(**card)
    return PW


def current():
    return PW


def setup(offscreen=True, time_scale=0.06, quiet=True):
    """Install the fakes. time_scale 0.06 turns PipeWire's 5 s idle -> suspend
    into 0.3 s; the app's What U Hear grace and SILENT wait shrink to match.
    Runs once per process (every test module calls it)."""
    global _done
    if _done:
        return
    _done = True
    testing.isolate("sbae9-audiomon", offscreen=offscreen)
    testing.block_subprocesses()
    testing.TIME_SCALE = time_scale

    from audiomon import capture, core, eventlog, monitor, pipewire
    pipewire.Graph = current
    pipewire.Linker = lambda: testing.FakeLinker(current)
    capture.Capture = testing.make_fake_capture(current, core.CHANNEL_MAPS, core.RATE, app_name=core.APP_NAME)
    core.driver_info = lambda: FAKE_DRIVER
    if time_scale <= 0.1:          # test speed (the demo keeps the real timings)
        monitor.WIRE_GRACE = 0.3
        monitor.SILENT_AFTER = 0.25
    if quiet:
        eventlog.print = lambda *a, **k: None   # the log also echoes to stdout
    reset()
