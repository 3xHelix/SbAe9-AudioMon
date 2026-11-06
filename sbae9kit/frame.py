# SPDX-License-Identifier: GPL-2.0-only
"""The frameless window's parts: a title bar to drag and edge grips to resize.

Both hand the move / resize to the desktop (startSystemMove/Resize), which
works on Wayland, X11 and XWayland alike.
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QWidget


class TitleBar(QFrame):
    """An app's own title bar. Without the desktop's frame it's the window's
    handle: drag to move (the desktop does the moving, so it works on
    Wayland and snaps like a normal window), double-click to maximize."""

    def __init__(self, parent=None, name="titleBar"):
        super().__init__(parent)
        self.setObjectName(name)

    def _frameless(self):
        return bool(self.window().windowFlags() & Qt.FramelessWindowHint)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and self._frameless() and self.window().windowHandle():
            self.window().windowHandle().startSystemMove()   # the desktop moves it (works on Wayland)
            e.accept()
            return
        super().mousePressEvent(e)

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.LeftButton and self._frameless():
            w = self.window()
            w.showNormal() if w.isMaximized() else w.showMaximized()
            e.accept()
            return
        super().mouseDoubleClickEvent(e)


class EdgeGrip(QWidget):
    """An invisible strip along a window edge (or corner) that resizes the
    frameless window."""
    CURSORS = {
        Qt.LeftEdge: Qt.SizeHorCursor, Qt.RightEdge: Qt.SizeHorCursor,
        Qt.TopEdge: Qt.SizeVerCursor, Qt.BottomEdge: Qt.SizeVerCursor,
        Qt.TopEdge | Qt.LeftEdge: Qt.SizeFDiagCursor, Qt.BottomEdge | Qt.RightEdge: Qt.SizeFDiagCursor,
        Qt.TopEdge | Qt.RightEdge: Qt.SizeBDiagCursor, Qt.BottomEdge | Qt.LeftEdge: Qt.SizeBDiagCursor,
    }
    SIZE = 6

    def __init__(self, edges, parent):
        super().__init__(parent)
        self.edges = edges
        self.setCursor(self.CURSORS[edges])

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and self.window().windowHandle():
            self.window().windowHandle().startSystemResize(self.edges)
            e.accept()

    def place(self, w, h):
        s, c, ed = self.SIZE, self.SIZE * 2, self.edges
        horiz = bool(ed & (Qt.LeftEdge | Qt.RightEdge))
        vert = bool(ed & (Qt.TopEdge | Qt.BottomEdge))
        if horiz and vert:
            gw, gh = c, c                 # corner
        elif horiz:
            gw, gh = s, h - 2 * c         # left / right side
        else:
            gw, gh = w - 2 * c, s         # top / bottom side
        x = 0 if ed & Qt.LeftEdge else w - gw if ed & Qt.RightEdge else c
        y = 0 if ed & Qt.TopEdge else h - gh if ed & Qt.BottomEdge else c
        self.setGeometry(x, y, gw, gh)
        self.raise_()


def make_grips(window):
    """The eight edge and corner grips for a frameless window. Call
    place_grips() from its resizeEvent."""
    return [EdgeGrip(edges, window) for edges in EdgeGrip.CURSORS]


def place_grips(window, grips, visible=True):
    for g in grips:
        g.setVisible(visible)
        if visible:
            g.place(window.width(), window.height())
