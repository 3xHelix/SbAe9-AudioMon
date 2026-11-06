# SPDX-License-Identifier: GPL-2.0-only
"""Glass: an app's backgrounds go see-through on any palette, text and
controls stay solid, and KDE frosts what's behind.

KDE only blurs behind X11 windows for apps without KDE's own libraries, so
a glass app runs through XWayland (see desktop.use_xwayland) and sets
_KDE_NET_WM_BLUR_BEHIND_REGION on its window with xprop. Elsewhere the glass
is just clear.
"""

import shutil

RANGE = (30, 95)          # opacity in percent, for the Settings slider
DEFAULT = 70


def clamp(pct):
    """An opacity in percent, snapped to 5s and kept inside RANGE."""
    try:
        pct = int(round(int(pct) / 5) * 5)
    except (TypeError, ValueError):
        return DEFAULT
    return max(RANGE[0], min(RANGE[1], pct))


def alpha(opacity, depth=1.0):
    """The alpha for a surface: `opacity` (0-1) times `depth` (< 1 makes that
    surface a little clearer than the others), kept within 0-1."""
    return round(max(0.0, min(1.0, opacity * depth)), 3)


def surface(hex_color, on, opacity, depth=1.0):
    """A background colour for QSS: as is, or rgba() with Glass on."""
    if not on:
        return hex_color
    from .palettes import rgba
    return rgba(hex_color, alpha(opacity, depth))


def surface_q(hex_color, on, opacity, depth=1.0):
    """surface() as a QColor, for widgets that paint their own background."""
    from PySide6.QtGui import QColor
    c = QColor(hex_color)
    if on:
        c.setAlphaF(alpha(opacity, depth))
    return c


def blur_behind(win):
    """Ask KDE to blur what's behind a glass window. Works for X11 windows
    (XWayland); elsewhere it's a no-op. Returns whether it was asked."""
    from PySide6.QtCore import QProcess
    from PySide6.QtGui import QGuiApplication
    if QGuiApplication.platformName() != "xcb" or not shutil.which("xprop"):
        return False
    # an empty region (a single 0) means "the whole window" to KWin
    prop = "_KDE_NET_WM_BLUR_BEHIND_REGION"
    return QProcess.startDetached("xprop", ["-id", str(int(win.winId())),
                                           "-f", prop, "32c", "-set", prop, "0"])[0]
