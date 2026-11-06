# SPDX-License-Identifier: GPL-2.0-only
"""Start the app: options, settings, Qt."""

import argparse
import signal
import sys

from PySide6.QtWidgets import QApplication

from sbae9kit import desktop

from . import __version__
from . import theme as T
from .core import APP_ID, APP_NAME, CHANNEL_CHOICES, LOG_PATH, load_config, save_config
from .eventlog import EventLog
from .theme import stylesheet
from .window import MainWindow


def main():
    ap = argparse.ArgumentParser(prog=APP_ID, description="PipeWire VU meters, with an AE-9 debug mode.")
    ap.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--ae9", action="store_true", help="AE-9 auto-wire mode (default)")
    mode.add_argument("--manual", action="store_true", help="two meters you wire by hand")
    idle = ap.add_mutually_exclusive_group()
    idle.add_argument("--keep-awake", action="store_true",
                      help="AE-9 mode: keep everything wired (the card never suspends)")
    idle.add_argument("--let-idle", action="store_true",
                      help="AE-9 mode: never keep the card awake (default)")
    ap.add_argument("--always-capture", action=argparse.BooleanOptionalAction, default=None,
                    help="AE-9 mode: keep What U Hear/Mic/Optical wired while the card idles")
    ap.add_argument("-c", "--channels", type=int, choices=CHANNEL_CHOICES,
                    help="manual mode: channel count for both meters")
    ap.add_argument("--frame", action=argparse.BooleanOptionalAction, default=None,
                    help="use the desktop's title bar instead of our own")
    ap.add_argument("--glass", action=argparse.BooleanOptionalAction, default=None,
                    help="see-through, frosted backgrounds (saved)")
    ap.add_argument("--palette", choices=sorted(T.PALETTES),
                    help="colour palette for this run (default: the saved one, Helix at first)")
    ap.add_argument("--scale", type=int, metavar="PCT",
                    help=f"UI scale for this run, {T.SCALES[0]}-{T.SCALES[1]}%% (default: the saved one)")
    ap.add_argument("--log", default=LOG_PATH, help=f"debug log file (default {LOG_PATH})")
    args, qt_args = ap.parse_known_args()

    cfg = load_config()
    if args.ae9 or args.manual:
        cfg["mode"] = "ae9" if args.ae9 else "manual"
    if args.keep_awake or args.let_idle:
        cfg["let_idle"] = args.let_idle
    if args.frame is not None:
        cfg["frameless"] = not args.frame
    if args.glass is not None:
        cfg["glass"] = args.glass
        save_config(cfg)
    if args.manual:
        cfg["page"] = 2   # open on Manual Meters
    if args.always_capture is not None:
        cfg["always_capture"] = args.always_capture
    if args.channels:
        for m in cfg["meters"]:
            m["channels"] = args.channels

    T.apply(args.palette or cfg["palette"])
    T.GLASS = bool(cfg["glass"])
    T.GLASS_OPACITY = T.clamp_glass(cfg["glass_opacity"]) / 100
    # both must run before QApplication exists (sbae9kit.desktop)
    T.SCALE = desktop.set_scale(APP_ID, args.scale or cfg["ui_scale"])
    desktop.use_xwayland(APP_ID, T.SCALE != 100 or T.GLASS)

    app = QApplication(desktop.qt_argv(APP_ID, qt_args))
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setDesktopFileName(APP_ID)
    app.setStyle("Fusion")
    app.setStyleSheet(stylesheet())

    log = EventLog(args.log)
    win = None

    def new_window(geometry=None, maximized=False):
        nonlocal win
        win = MainWindow(cfg, log)
        win.restart = restart
        win.repalette = repalette
        if geometry is not None:
            win.setGeometry(geometry)
        win.showMaximized() if maximized else win.show()
        return win

    def restart(drop=()):
        """Reopen the app (Glass on/off, a new UI scale): stop our meters
        first so the new copy doesn't meet our nodes, start it, then quit.
        One-off options in `drop` are left out so the saved choice wins."""
        if desktop.relaunch(drop=drop, before=win.stop):
            app.quit()

    def repalette(name):
        """A new palette: rebuild the window in it (the meters restart)."""
        old = win
        old.stop()
        old.timer.stop()
        T.apply(name)
        app.setStyleSheet(stylesheet())
        new_window(old.geometry(), old.isMaximized())
        old.hide()
        old.deleteLater()

    new_window()
    app.aboutToQuit.connect(lambda: win.stop())
    # The 33 ms meter timer hands control back to Python regularly, so
    # these handlers run promptly even while Qt's event loop is in charge.
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: app.quit())

    ret = app.exec()
    # Tear down the window before the QApplication; letting Python pick the
    # order at exit can segfault.
    win.timer.stop()
    win.deleteLater()
    win = None
    app.processEvents()
    return ret


if __name__ == "__main__":
    sys.exit(main())
