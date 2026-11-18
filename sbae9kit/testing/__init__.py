# SPDX-License-Identifier: GPL-2.0-only
"""sbae9kit.testing: the fake AE-9 both apps test against.

No card, no PipeWire, no real settings: tests run against a fake PipeWire
graph built from a snapshot of the real machine (fixtures/pw_ae9_stereo.json:
the AE-9 sink and the head unit's Mic / Aux input on driver 0.6.8), with What U Hear, Optical
In and (optionally) Rear RCA In added the way the driver's PipeWire config adds them.

    pipewire   FakePipeWire (graph, ports, links, running / idle / suspended,
               levels, a run() dispatcher for PipeWire commands), FakeLinker
               (pw-link), make_fake_capture (pw-record meters)
    (here)     write_reload_notice / dead_pid: ae9-reload's notice file
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


def dead_pid():
    """A PID that isn't running (for a stale notice)."""
    pid = 4194304   # above the kernel's pid_max ceiling: never alive
    while os.path.exists(f"/proc/{pid}"):
        pid += 1
    return pid


def write_reload_notice(path, pid=None, age_s=0.0):
    """A fake ae9-reload notice (driver 0.12.13) at `path`. `pid`: None for
    this test process (a live reload), an int (dead_pid() for a stale
    notice), or "" for an empty notice. `age_s` back-dates its mtime (an
    empty notice stops counting after ae9.RELOAD_NOTICE_MAX_S). Remove the
    file to end the reload. Returns `path`."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        f.write("" if pid == "" else f"{os.getpid() if pid is None else pid}\n")
    if age_s:
        t = os.stat(path).st_mtime - age_s
        os.utime(path, (t, t))
    return path


from .pipewire import (  # noqa: E402,F401
    FakeLinker, FakePipeWire, make_fake_capture,
    MIC, MIC_DESC, OPTICAL, OPTICAL_OUT, REAR_RCA, SINK, WHAT_U_HEAR, WUH_OFFSET_DB,
)

