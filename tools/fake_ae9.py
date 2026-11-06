#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Run SbAe9-AudioMon on the fake AE-9 and play a scenario on a loop, so you
can watch how it behaves without the card (or without risking a test run).

Nothing real is touched: no PipeWire, no card, no ~/.config, and the debug
log goes to a temp folder, not your real ae9-debug.log.

    python3 tools/fake_ae9.py            # the window, looping the scenario
    python3 tools/fake_ae9.py --list     # print the scenario and exit
"""

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tests"))
import fakeae9 as F  # noqa: E402

# (seconds into the loop, what happens, action)
SCENARIO = [
    (0, "card idle, nothing playing", lambda pw, s: None),
    (4, "Music Player starts (-14 dBFS), mic picks up the room (-35 dBFS)",
     lambda pw, s: (s.update(music=pw.add_stream("Music Player", "Night Drive.flac", -14)),
                    setattr(pw, "mic_db", -35))),
    (14, "Music Player stops: the AE-9 goes idle, then suspends",
     lambda pw, s: (pw.stop_stream(s.pop("music")), setattr(pw, "mic_db", None))),
    (22, "a tone starts but the card comes back silent (the bug)",
     lambda pw, s: (setattr(pw, "silent_restart", True),
                    s.update(tone=pw.add_stream("mpv", "1 kHz tone", -20)))),
    (30, "tone stops, card healthy again",
     lambda pw, s: (pw.stop_stream(s.pop("tone")), setattr(pw, "silent_restart", False))),
    (36, "the card drops off (PSU cycle / driver reload)", lambda pw, s: pw.card_present(False)),
    (41, "the card comes back", lambda pw, s: pw.card_present(True)),
    (48, "light on the optical jack (-24 dBFS) and a game plays (-10 dBFS)",
     lambda pw, s: (setattr(pw, "optical_db", -24), s.update(game=pw.add_stream("Game", "Effects", -10)))),
    (56, "game stops, optical goes dark",
     lambda pw, s: (pw.stop_stream(s.pop("game")), setattr(pw, "optical_db", None))),
]
LOOP = 62


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--list", action="store_true", help="print the scenario and exit")
    args = ap.parse_args()
    if args.list:
        for t, what, _ in SCENARIO:
            print(f"{t:>3}s  {what}")
        print(f"{LOOP:>3}s  (loop)")
        return 0

    am = F.install(offscreen=False)
    F.FakePipeWire.SUSPEND_AFTER = 3.0   # real PipeWire waits 5 s
    pw = F.reset()
    pw.jitter = 2.5                      # make the levels move like real audio

    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    app = QApplication([sys.argv[0]])
    app.setApplicationName("SbAe9-AudioMon (fake AE-9)")
    app.setStyle("Fusion")
    app.setStyleSheet(am.stylesheet())

    cfg = am.load_config()
    log = am.EventLog(am.LOG_PATH)       # LOG_PATH is under the temp XDG_STATE_HOME
    win = am.MainWindow(cfg, log)
    win.setWindowTitle("SbAe9-AudioMon — FAKE AE-9 (test rig)")
    win.title_bar.findChildren(type(win.state_lbl))[1].setText("FAKE AE-9")
    win.show()

    state = {"t0": time.monotonic(), "step": 0, "streams": {}}

    def run():
        t = time.monotonic() - state["t0"]
        while state["step"] < len(SCENARIO) and t >= SCENARIO[state["step"]][0]:
            _, what, action = SCENARIO[state["step"]]
            log(f"[fake AE-9] {what}")
            action(F.PW, state["streams"])
            state["step"] += 1
        if t >= LOOP:
            for sid in list(state["streams"].values()):
                F.PW.stop_stream(sid)
            state["streams"].clear()
            F.PW.silent_restart = False
            F.PW.mic_db = F.PW.optical_db = None
            state.update(t0=time.monotonic(), step=0)
            log("[fake AE-9] scenario restarts")

    timer = QTimer()
    timer.timeout.connect(run)
    timer.start(200)
    app.aboutToQuit.connect(win.stop)
    ret = app.exec()
    win.timer.stop()
    win.deleteLater()
    del win
    app.processEvents()
    return ret


if __name__ == "__main__":
    sys.exit(main())
