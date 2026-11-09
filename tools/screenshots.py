#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Render the README screenshots into docs/ (2x, HiDPI), like Sound Blaster
Command's tools/screenshots.py.

    python3 tools/screenshots.py [--palette helix] [--glass [BACKDROP.png]] [--out docs]

Runs the real window on the fake AE-9 (sbae9kit.testing via tests/support.py),
so no real card, PipeWire, settings or debug log are touched:

    monitor     music and a browser playing, something on Optical Out, mic, optical
                and rear RCA in use
    silent      a silent restart: the output plays, What U Hear stays silent
    log         the Event Log after all that
    settings    the Settings page
    manual      Manual Meters with the music wired in by hand

--glass also renders docs/glass-monitor.png: Glass at 70% over a built-in
neon backdrop (or BACKDROP), blurred the way KDE frosts it (an offscreen
approximation; on the desktop KWin does the blur).
"""

import argparse
import os
import sys
import time

os.environ["QT_SCALE_FACTOR"] = "2"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tests"))
import support  # noqa: E402

W, H = 1320, 860                  # logical size; the PNGs are 2x
SHOWN_LOG = "~/.local/state/sbae9-audiomon/ae9-debug.log"
SHOWN_CONFIG = "~/.config/sbae9-audiomon/config.json"
SHOWN_DRIVER = "snd_hda_codec_creative_ae9 0.12.2 srcversion D97074682EBE9536970AFB4"


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--palette", default="helix")
    ap.add_argument("--glass", nargs="?", const="", metavar="BACKDROP",
                    help="also render docs/glass-monitor.png, over BACKDROP or a built-in neon backdrop")
    ap.add_argument("--out", default=os.path.join(ROOT, "docs"))
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    # real-time waits (the meters' ballistics need real time to settle)
    support.setup(time_scale=0.06)
    from PySide6.QtWidgets import QApplication
    from audiomon import core, eventlog, monitor, window
    from audiomon import theme as T

    # the card idles and suspends quickly (TIME_SCALE), but SILENT keeps the
    # app's real timings, so the log and Settings read as they do for real
    monitor.SILENT_AFTER, monitor.WIRE_GRACE = 0.5, 1.0

    core.driver_info = lambda: SHOWN_DRIVER          # what the real card reports
    window.CONFIG_PATH = SHOWN_CONFIG                  # paths as on a real install
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    app.setStyle("Fusion")

    def run(seconds):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            app.processEvents()
            time.sleep(0.005)

    def make_window(glass=False):
        T.apply(args.palette)
        T.GLASS, T.GLASS_OPACITY = glass, 0.70
        app.setStyleSheet(T.stylesheet())
        cfg = core.load_config()
        cfg.update(mode="ae9", let_idle=True, always_capture=False, page=0, frameless=True,
                   glass=glass, palette=args.palette)
        log = eventlog.EventLog(core.LOG_PATH)
        log.path = SHOWN_LOG
        w = window.MainWindow(cfg, log)
        w.resize(W, H)
        w.show()
        run(0.5)
        return w

    def shot(w, name):
        run(0.15)
        img = w.grab()
        path = os.path.join(args.out, name + ".png")
        img.save(path)
        print(f"{path}  {img.width()}x{img.height()}")
        return path

    def close(w):
        w.stop()
        w.timer.stop()
        w.close()
        w.deleteLater()
        run(0.1)

    # 1. playing: music and a browser to the AE-9, the room on the mic, light on optical
    pw = support.reset()
    pw.jitter = 2.0
    w = make_window()
    music = pw.add_stream("Music Player", "Night Drive.flac", -11)
    pw.add_stream("Firefox", "YouTube", -21)
    pw.mic_db, pw.optical_db, pw.rear_rca_db = -38, -24, -30
    pw.optical_out_db = -16
    run(2.5)
    shot(w, "monitor")

    # 2. the bug: the music stops, the card idles and suspends, then a tone
    #    starts and the card comes back silent
    pw.stop_stream(music)
    for sid in list(pw.streams):
        pw.stop_stream(sid)
    pw.mic_db = pw.optical_db = pw.rear_rca_db = pw.optical_out_db = None
    run(1.5)
    pw.silent_restart = True
    pw.add_stream("mpv", "1 kHz tone", -18)
    run(3.0)
    shot(w, "silent")
    pw.silent_restart = False
    run(1.0)

    # 3. the Event Log after all that, and 4. Settings
    w.go(1)
    shot(w, "log")
    w.go(3)
    shot(w, "settings")

    # 5. Manual Meters with the music wired in by hand (as in Helvum)
    w.go(2)
    run(0.5)
    sid = pw.add_stream("Music Player", "Night Drive.flac", -11)
    outs = [p["id"] for p in pw.ports(sid) if p["info"]["direction"] == "output"]
    meter = pw.node("sbae9-audiomon.in")
    ins = [p["id"] for p in pw.ports(meter["id"]) if p["info"]["direction"] == "input"]
    for o, i in zip(outs, ins):
        pw.link_external(o, i)
    run(2.5)
    shot(w, "manual")
    close(w)

    if args.glass is not None:
        glass_shot(args, make_window, run, shot, close)
    print("saved to", args.out)


def glass_shot(args, make_window, run, shot, close):
    """Glass at 70% over the backdrop. The window renders see-through; the
    backdrop is blurred like KDE's frost and put behind it (with a margin)."""
    from PIL import Image, ImageFilter
    pw = support.reset()
    pw.jitter = 2.0
    w = make_window(glass=True)
    pw.add_stream("Music Player", "Night Drive.flac", -11)
    pw.mic_db, pw.optical_db = -38, -24
    run(2.5)
    tmp = shot(w, "glass-monitor")
    close(w)
    win = Image.open(tmp).convert("RGBA")
    pad = 200                                   # 100 px of backdrop all round, at 2x
    size = (win.width + 2 * pad, win.height + 2 * pad)
    back = Image.open(args.glass).convert("RGBA") if args.glass else neon_backdrop()
    scale = max(size[0] / back.width, size[1] / back.height)
    back = back.resize((round(back.width * scale), round(back.height * scale)), Image.LANCZOS)
    left, top = (back.width - size[0]) // 2, (back.height - size[1]) // 2
    back = back.crop((left, top, left + size[0], top + size[1]))
    frost = back.crop((pad, pad, pad + win.width, pad + win.height)).filter(ImageFilter.GaussianBlur(24))
    out = back.copy()
    out.paste(frost, (pad, pad))
    out.alpha_composite(win, (pad, pad))
    out.convert("RGB").save(tmp, optimize=True)
    print(f"{tmp}  {out.width}x{out.height} (over {os.path.basename(args.glass) or 'the neon backdrop'})")


def neon_backdrop(w=3840, h=2400):
    """Dark violet with soft coloured light in the Helix colours (the same
    image every time), for the Glass screenshot."""
    import random
    from PIL import Image, ImageDraw, ImageFilter
    rnd = random.Random(7)
    im = Image.new("RGB", (w, h), (14, 8, 30))
    d = ImageDraw.Draw(im)
    for color, n in (((120, 40, 200), 5), ((245, 122, 69), 4), ((93, 202, 168), 3),
                     ((255, 182, 58), 2), ((220, 60, 140), 3)):
        for _ in range(n):
            x, y, r = rnd.randint(0, w), rnd.randint(0, h), rnd.randint(300, 800)
            d.ellipse((x - r, y - r, x + r, y + r), fill=color)
    for _ in range(40):          # sharper streaks, so the frost has something to blur
        x, y = rnd.randint(0, w), rnd.randint(0, h)
        d.line((x, y, x + rnd.randint(-900, 900), y + rnd.randint(-300, 300)),
               fill=rnd.choice([(255, 182, 58), (93, 202, 168), (245, 122, 69)]), width=rnd.randint(6, 18))
    return im.filter(ImageFilter.GaussianBlur(60)).convert("RGBA")


if __name__ == "__main__":
    main()
