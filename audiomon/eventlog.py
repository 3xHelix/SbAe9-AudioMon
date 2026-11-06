# SPDX-License-Identifier: GPL-2.0-only
"""The timestamped debug log (file + on-screen views)."""

import os
import sys
from datetime import datetime


class EventLog:
    """Timestamped (local time, to the second) log: file + on-screen view."""

    def __init__(self, path):
        self.path = path
        self.views = []
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            self.file = open(path, "a", buffering=1)
        except OSError as e:
            print(f"sbae9-audiomon: cannot open log {path}: {e}", file=sys.stderr)
            self.file = None

    def __call__(self, msg):
        line = f"{datetime.now():%Y-%m-%d %H:%M:%S}  {msg}"
        if self.file:
            self.file.write(line + "\n")
        print(line, flush=True)
        for v in self.views:
            bar = v.verticalScrollBar()
            follow = bar.value() >= bar.maximum() - 4   # keep following the newest line
            v.appendPlainText(line)
            if follow:
                bar.setValue(bar.maximum())

    def add_view(self, view, backfill=False):
        """Show new lines in `view`; backfill=True first loads the file's tail."""
        if backfill:
            try:
                with open(self.path, errors="replace") as f:
                    tail = f.readlines()[-500:]
                view.setPlainText("".join(tail).rstrip("\n"))
                view.moveCursor(view.textCursor().MoveOperation.End)
            except OSError:
                pass
        self.views.append(view)

    def remove_view(self, view):
        if view in self.views:
            self.views.remove(view)
