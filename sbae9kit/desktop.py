# SPDX-License-Identifier: GPL-2.0-only
"""Desktop plumbing shared by the AE-9 apps: UI scale, the XWayland choice,
the window's X11 name, relaunching, and the app icon.

The environment variables an app sets are named after its id, e.g.
SBAE9_SBC_BASE_SCALE_FACTOR for "sbae9-sbc".
"""

import os
import sys

SCALES = (70, 150)        # UI scale in percent


def env_prefix(app_id):
    return app_id.upper().replace("-", "_")


def clamp_scale(pct):
    """A UI scale in percent, snapped to 5s and kept inside SCALES."""
    try:
        pct = int(round(int(pct) / 5) * 5)
    except (TypeError, ValueError):
        return 100
    return max(SCALES[0], min(SCALES[1], pct))


def set_scale(app_id, pct, env=os.environ):
    """Ask Qt to draw everything at pct percent, on top of any factor the
    user's environment set. Qt reads QT_SCALE_FACTOR once, so this must run
    before QApplication exists. The user's own factor is remembered in
    <APP>_BASE_SCALE_FACTOR, so relaunched copies don't stack scales.
    Returns the scale used."""
    scale = clamp_scale(pct)
    base_key = env_prefix(app_id) + "_BASE_SCALE_FACTOR"
    base = env.setdefault(base_key, env.get("QT_SCALE_FACTOR", ""))
    try:
        factor = float(base or 1) * scale / 100
    except ValueError:
        factor = scale / 100
    if factor == 1 and not base:
        env.pop("QT_SCALE_FACTOR", None)
    else:
        env["QT_SCALE_FACTOR"] = f"{factor:g}"
    return scale


def xcb_available():
    from PySide6.QtCore import QLibraryInfo
    plugins = QLibraryInfo.path(QLibraryInfo.LibraryPath.PluginsPath)
    return os.path.exists(os.path.join(plugins, "platforms", "libqxcb.so"))


def use_xwayland(app_id, wanted, env=os.environ, xcb_ok=None):
    """Run through XWayland when `wanted` (a UI scale other than 100%, or
    Glass): Qt on Wayland misplaces popups at a custom scale, and KDE only
    blurs behind X11 windows. Otherwise the app stays native Wayland. A
    QT_QPA_PLATFORM the user set is left alone. Must run before QApplication."""
    mark = env_prefix(app_id) + "_SET_QPA"
    if env.pop(mark, None):                   # set by us on an earlier launch
        env.pop("QT_QPA_PLATFORM", None)
    wayland = env.get("WAYLAND_DISPLAY") or env.get("XDG_SESSION_TYPE") == "wayland"
    if not (wanted and wayland and env.get("DISPLAY") and "QT_QPA_PLATFORM" not in env):
        return False
    if not (xcb_available() if xcb_ok is None else xcb_ok):
        return False
    env["QT_QPA_PLATFORM"] = "xcb"
    env[mark] = "1"
    return True


def qt_argv(app_id, qt_args=()):
    """Qt's argv. Its first entry names the window to X11 (WM_CLASS), which
    is how docks match a window to its menu entry through XWayland, so it's
    the app's id (matching StartupWMClass), not the launcher's argv[0]."""
    return [app_id] + list(qt_args)


def relaunch_args(argv, drop=()):
    """`argv` minus one-off options in `drop` (e.g. "--scale", "--glass"),
    as "--opt value", "--opt=value" or a bare flag."""
    out, skip = [], False
    for a in argv:
        if skip:
            skip = False
            continue
        name = a.split("=", 1)[0]
        if name in drop:
            # "--scale 80" takes the next word; flags and "--x=y" don't
            if "=" not in a and a in VALUED:
                skip = True
            continue
        out.append(a)
    return out


VALUED = {"--scale", "--palette", "--channels", "-c", "--log"}


def relaunch(drop=(), before=None):
    """Start a fresh copy of this app with the same command line (minus
    `drop`), after calling `before` (e.g. to stop meters whose node names
    the new copy will use). Returns whether the new copy started; the caller
    then quits."""
    from PySide6.QtCore import QProcess
    if before:
        before()
    return QProcess.startDetached(sys.executable, relaunch_args(sys.orig_argv[1:], drop))[0]


def app_icon(folder, app_id, fallback=None):
    """A QIcon from <folder>/<app_id>-<size>.png in every size there, or
    `fallback` (a QIcon) when there are none."""
    from PySide6.QtGui import QIcon
    ic = QIcon()
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        names = []
    for name in names:
        if name.startswith(app_id + "-") and name.endswith(".png"):
            ic.addFile(os.path.join(folder, name))
    if ic.isNull() and fallback is not None:
        return fallback
    return ic
