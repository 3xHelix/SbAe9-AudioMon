# SPDX-License-Identifier: GPL-2.0-only
"""sbae9kit.testing: the fake AE-9 both apps test against.

No card, no PipeWire, no real settings: tests run against a fake PipeWire
graph built from a snapshot of the real machine (fixtures/pw_ae9_stereo.json:
the AE-9 sink and the head unit's Mic / Aux input on driver 0.6.8), with What U Hear, Optical
In and (optionally) Rear RCA In added the way the driver's PipeWire config adds them.

    pipewire   FakePipeWire (graph, ports, links, running / idle / suspended,
               levels, a run() dispatcher for PipeWire commands), FakeLinker
               (pw-link), make_fake_capture (pw-record meters)
    (here)     write_reload_notice / dead_pid: ae9-reload's notice file;
               fake_sound_tree + AE9_CARD, AE7_CARD, ...: cards for detection
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


# Cards for fake_sound_tree(): (id, shortname, longname, subsystem vendor:device,
# PCI driver, PCMs). Our driver binds the PCI device itself.
AE9_CARD = ("AE9", "Sound Blaster AE-9", "Creative Labs Sound Blaster AE-9 at 0xfbf04000 irq 44",
            (0x1102, 0x0071), "snd-hda-codec-creative-ae9", ("0p", "0c", "1c", "2c", "3c", "4p"))
AE7_CARD = ("AE7", "Sound Blaster AE-7", "Creative Labs Sound Blaster AE-7 at 0xfc004000 irq 45",
            (0x1102, 0x0081), "snd-hda-codec-creative-ae9", ("0p", "0c", "2c"))   # kit 11: no PCM 4 (encoder)
AE7_INTREE = ("Generic", "HD-Audio Generic", "HD-Audio Generic at 0xfc004000 irq 45",
              (0x1102, 0x0081), "snd_hda_intel", ("0p", "0c"))   # an AE-7 on the in-tree driver
AE5_CARD = ("AE5", "Sound Blaster AE-5", "Creative Labs Sound Blaster AE-5 at 0xfc104000 irq 46",
            (0x1102, 0x0051), "snd-hda-codec-creative-ae9", ("0p", "0c"))   # reserved
HDMI_CARD = ("HDMI", "HDA ATI HDMI", "HDA ATI HDMI at 0xfcb20000 irq 229",
             (0x1002, 0xab28), "snd_hda_intel", ("3p",))
USB_CARD = ("C920", "HD Pro Webcam C920", "HD Pro Webcam C920 at usb-0000:13:00.3-4.3, high speed",
            None, "snd-usb-audio", ("0c",))


def fake_sound_tree(root, cards=(AE9_CARD,), point=True):
    """A fake /sys/class/sound and /proc/asound under `root`, one entry per card
    (cards[i] is card i; None leaves a gap). `point`: aim sbae9kit.boards at it
    (boards.ROOT) and forget the cached card. Returns `root`."""
    from .. import boards
    sys_sound = os.path.join(root, "sys", "class", "sound")
    proc = os.path.join(root, "proc", "asound")
    drivers = os.path.join(root, "sys", "bus", "pci", "drivers")
    shutil.rmtree(sys_sound, ignore_errors=True)
    shutil.rmtree(proc, ignore_errors=True)
    os.makedirs(sys_sound)
    os.makedirs(proc)
    listing = []
    for i, card in enumerate(cards):
        if card is None:
            continue
        cid, short, longname, sub, driver, pcms = card
        dev = os.path.join(root, "sys", "devices", f"card{i}-device")
        os.makedirs(dev, exist_ok=True)
        os.makedirs(os.path.join(drivers, driver), exist_ok=True)
        link = os.path.join(dev, "driver")
        if os.path.lexists(link):
            os.remove(link)
        os.symlink(os.path.join(drivers, driver), link)
        if sub:
            for name, val in zip(("subsystem_vendor", "subsystem_device"), sub):
                with open(os.path.join(dev, name), "w") as f:
                    f.write(f"0x{val:04x}\n")
        cdir = os.path.join(sys_sound, f"card{i}")
        os.makedirs(cdir)
        os.symlink(dev, os.path.join(cdir, "device"))
        with open(os.path.join(cdir, "id"), "w") as f:
            f.write(cid + "\n")
        for pcm in pcms:
            os.makedirs(os.path.join(proc, cid, f"pcm{pcm}"), exist_ok=True)
        listing.append(f"{i:2d} [{cid:<15}]: {driver} - {short}\n                      {longname}\n")
    with open(os.path.join(proc, "cards"), "w") as f:
        f.write("".join(listing) or "--- no soundcards ---\n")
    if point:
        boards.ROOT = root
        boards.refresh()
    return root


from .pipewire import (  # noqa: E402,F401
    FakeLinker, FakePipeWire, make_fake_capture,
    MIC, MIC_DESC, OPTICAL, OPTICAL_OUT, REAR_RCA, SINK, WHAT_U_HEAR, WUH_OFFSET_DB,
)

