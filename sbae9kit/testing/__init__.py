# SPDX-License-Identifier: GPL-2.0-only
"""sbae9kit.testing: the fake AE-9 both apps test against.

No card, no PipeWire, no real settings: tests run against a fake PipeWire
graph built from a snapshot of the real machine (fixtures/pw_ae9_stereo.json:
the AE-9 sink and the head unit's Mic / Aux input on driver 0.6.8), with What U Hear and Optical
In added the way the driver's PipeWire config adds them.

    pipewire   FakePipeWire (graph, ports, links, running / idle / suspended,
               levels, a run() dispatcher for PipeWire commands), FakeLinker
               (pw-link), make_fake_capture (pw-record meters)
    mixer      FakeMixer, the card's ALSA controls (control apps only; it
               imports sbae9kit.mixer, so listen-only apps don't import it)

A test harness in each app (tests/support.py) does, before loading the app:

    from sbae9kit import testing
    tmp = testing.isolate("sbae9-audiomon")   # temp XDG folders, Qt offscreen
    testing.block_subprocesses()               # a real command fails the test
    testing.TIME_SCALE = 0.06                  # 5 s waits become 0.3 s

then swaps the app's PipeWire / pw-link / pw-record hooks for the fakes here.
Nothing here imports either app or Qt; numpy is only needed for
make_fake_capture.
"""

import atexit
import os
import shutil
import subprocess
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURES = os.path.join(HERE, "fixtures")

# One knob for every wait the fakes (and the apps' tests) model. Real time is
# 1.0; tests usually run at a few percent of it.
TIME_SCALE = 1.0


def scaled(seconds):
    """A real-world duration, at the current TIME_SCALE."""
    return seconds * TIME_SCALE


def fixture_path(name):
    return os.path.join(FIXTURES, name)


def isolate(app_id, offscreen=True):
    """Point XDG_CONFIG_HOME / CACHE / STATE / DATA at a fresh temp folder
    (removed at exit) so settings, logs and icons never touch the real ones.
    Call before the app computes its paths. Returns the temp folder."""
    tmp = tempfile.mkdtemp(prefix=f"{app_id}-tests-")
    atexit.register(shutil.rmtree, tmp, True)
    for var in ("XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME"):
        os.environ[var] = os.path.join(tmp, var.split("_")[1].lower())
    if offscreen:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    return tmp


def _no_subprocess(*a, **k):
    raise AssertionError(f"tried to run a real command under the fake AE-9: {a}")


def block_subprocesses():
    """Make subprocess.Popen / run fail the test, so nothing reaches the real
    PipeWire or card. (Apps swap their QProcess uses for fakes themselves.)"""
    subprocess.Popen = _no_subprocess
    subprocess.run = _no_subprocess


from .pipewire import (  # noqa: E402,F401
    FakeLinker, FakePipeWire, make_fake_capture,
    MIC, MIC_DESC, OPTICAL, SINK, WHAT_U_HEAR, WUH_OFFSET_DB,
)
