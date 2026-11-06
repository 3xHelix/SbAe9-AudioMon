# SPDX-License-Identifier: GPL-2.0-only
"""The active palette, Glass, fonts, icons and the Qt style sheet."""

import os
from functools import lru_cache

from PySide6.QtCore import QByteArray, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from sbae9kit import desktop, glass, palettes

from .core import APP_ID

# The palettes are shared with SbAe9-SBC (sbae9kit.palettes). AudioMon starts
# in Helix; apply() copies the chosen palette's colours into this module, so
# code reads them as ACCENT, TEXT, ... at paint time.
DEFAULT_PALETTE = "helix"
PALETTES = palettes.PALETTES
CURRENT = DEFAULT_PALETTE
DANGER = palettes.DANGER          # warnings use one red in every palette
ZONES = palettes.meter_zones(DEFAULT_PALETTE)   # level-meter colours: low, high, over
rgba = palettes.rgba


# The colours the app uses, set from the active palette by apply().
ACCENT = ACCENT_HI = ACCENT_TEXT = TEXT = MUTED = DIM = IDLE = None
TRACK = PANEL = PANEL_HI = PANEL_LO = TITLE_BG = BOTTOM_BG = None
SIDEBAR_TOP = SIDEBAR_BOTTOM = MAIN_TOP = MAIN_BOTTOM = None
POPUP = POPUP_BORDER = LIST_BG = TOOLTIP = GRAPH_BG = GRAPH_TEXT = None
OK = WARN = OVERLAY = SHADE = HANDLE = HANDLE_BORDER = None


def apply(name):
    """Make `name` the active palette (unknown names fall back to Helix)."""
    global CURRENT, ZONES
    CURRENT = name if name in PALETTES else DEFAULT_PALETTE
    for k, v in PALETTES[CURRENT].items():
        if k.isupper():
            globals()[k] = v
    ZONES = palettes.meter_zones(CURRENT)


apply(DEFAULT_PALETTE)

# UI scale in percent (sbae9kit.desktop). Qt reads it at start-up only, so a
# new scale means reopening the window.
SCALES = desktop.SCALES
SCALE = 100
clamp_scale = desktop.clamp_scale

# Glass (sbae9kit.glass): the window's backgrounds (title and bottom bars,
# sidebar, pages, meter panels) go see-through; text and controls stay solid.
# KDE frosts what's behind when the app runs through XWayland.
GLASS = False
GLASS_OPACITY = glass.DEFAULT / 100    # how solid the glass is (0.3 - 0.95)
GLASS_RANGE = glass.RANGE              # percent, for the Settings slider
clamp_glass = glass.clamp
blur_behind = glass.blur_behind


def surface(hex_color, depth=1.0):
    """A window background colour: as is, or see-through with Glass on.
    depth < 1 makes that surface a little clearer than the others."""
    return glass.surface(hex_color, GLASS, GLASS_OPACITY, depth)


def surface_q(hex_color, depth=1.0):
    """surface() as a QColor, for widgets that paint their own background."""
    return glass.surface_q(hex_color, GLASS, GLASS_OPACITY, depth)


FONT_FAMILIES = ["Roboto Condensed", "Barlow Semi Condensed", "Noto Sans", "DejaVu Sans Condensed"]


def qcolor(hex_color, alpha=1.0):
    c = QColor(hex_color)
    c.setAlphaF(alpha)
    return c


def mono_font(px):
    f = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
    f.setPixelSize(px)
    return f


def ui_font(px, bold=False):
    f = QFont()
    f.setFamilies(FONT_FAMILIES)
    f.setPixelSize(px)
    f.setBold(bold)
    return f


_S = 'fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"'
ICONS = {
    "wave": f'<path {_S} d="M2 12h2l2-6 3 12 3-15 3 15 3-12 2 6h2"/>',
    "log": f'<path {_S} d="M8 6h13M8 12h13M8 18h13"/><circle cx="3.5" cy="6" r="1.2" fill="currentColor"/>'
           f'<circle cx="3.5" cy="12" r="1.2" fill="currentColor"/><circle cx="3.5" cy="18" r="1.2" fill="currentColor"/>',
    "mixer": f'<path {_S} d="M3 6h18M3 12h18M3 18h18"/><circle {_S} cx="15" cy="12" r="2"/>'
             f'<circle {_S} cx="10" cy="18" r="2"/><circle {_S} cx="8" cy="6" r="2"/>',
    "settings": f'<circle {_S} cx="12" cy="12" r="3"/><path {_S} d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>',
    "refresh": f'<path {_S} d="M20 11a8 8 0 1 0-2.3 5.7"/><path {_S} d="M20 4v7h-7"/>',
    "folder": f'<path {_S} d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
    "minimize": f'<path {_S} d="M6 12.5h12"/>',
    "maximize": f'<rect {_S} x="6" y="6" width="12" height="12" rx="1.5"/>',
    "close": f'<path {_S} d="M6.5 6.5l11 11M17.5 6.5l-11 11"/>',
    "chevron-down": f'<path {_S} d="M5 9l7 7 7-7"/>',
    "check": f'<path {_S} d="M5 12.5l4.5 4.5L19 7.5"/>',
}


@lru_cache(maxsize=256)
def icon_pixmap(name, color, size=22, ratio=2.0):
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
           + ICONS[name].replace("currentColor", color) + "</svg>")
    r = QSvgRenderer(QByteArray(svg.encode()))
    px = QPixmap(int(size * ratio), int(size * ratio))
    px.fill(Qt.GlobalColor.transparent)
    p = QPainter(px)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    r.render(p, QRectF(0, 0, px.width(), px.height()))
    p.end()
    px.setDevicePixelRatio(ratio)
    return px


def make_icon(name, color=None, active=None, size=22):
    color = color or TEXT
    ic = QIcon()
    ic.addPixmap(icon_pixmap(name, color, size), QIcon.Mode.Normal, QIcon.State.Off)
    ic.addPixmap(icon_pixmap(name, active or color, size), QIcon.Mode.Normal, QIcon.State.On)
    ic.addPixmap(icon_pixmap(name, active or color, size), QIcon.Mode.Active, QIcon.State.Off)
    ic.addPixmap(icon_pixmap(name, DIM, size), QIcon.Mode.Disabled, QIcon.State.Off)
    return ic


APPICON_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "appicon")


def app_icon():
    """The app icon: from the icon theme (install.sh puts it there), else the
    appicon/ PNGs next to the package (sbae9kit.desktop), else a drawn one."""
    ic = QIcon.fromTheme(APP_ID)
    if not ic.isNull():
        return ic
    return desktop.app_icon(APPICON_DIR, APP_ID, QIcon(icon_pixmap("wave", ACCENT_HI, 64)))


def stylesheet():
    cache = os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"),
                         "sbae9-audiomon")
    os.makedirs(cache, exist_ok=True)
    img = {}
    for name, color in (("chevron-down", TEXT), ("check", ACCENT_TEXT)):
        path = os.path.join(cache, f"qss-{name}-{color.lstrip('#')}.png")
        if not os.path.exists(path):
            icon_pixmap(name, color, 16, 2.0).save(path)
        img[name] = path
    fams = ", ".join(f'"{f}"' for f in FONT_FAMILIES)
    # thin light edges where glass panes meet, like light catching an edge
    edge = rgba(OVERLAY, 0.14)
    edge_bottom = f" border-bottom: 1px solid {edge};" if GLASS else ""
    edge_top = f" border-top: 1px solid {edge};" if GLASS else ""
    edge_right = f" border-right: 1px solid {edge};" if GLASS else ""
    return f"""
* {{ font-family: {fams}, sans-serif; color: {TEXT}; font-size: 14px; }}
#root {{ background: {"transparent" if GLASS else MAIN_TOP}; }}
#titleBar {{ background: {surface(TITLE_BG)};{edge_bottom} }}
#winButton, #winClose {{ background: transparent; border: none; border-radius: 3px; }}
#winButton:hover {{ background: {rgba(OVERLAY, 0.10)}; }}
#winClose:hover {{ background: {DANGER}; }}
#appTitle {{ font-size: 16px; color: {TEXT}; }}
#deviceName {{ font-size: 17px; font-weight: 700; color: {ACCENT}; }}
#sidebar {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {surface(SIDEBAR_TOP)},
                                stop:1 {surface(SIDEBAR_BOTTOM)});{edge_right}
}}
#sidebar QPushButton {{
    background: transparent; border: none; border-left: 3px solid transparent; border-radius: 0;
    text-align: left; padding: 11px 16px; font-size: 16px;
}}
#sidebar QPushButton:hover {{ background: {rgba(OVERLAY, 0.06)}; }}
#sidebar QPushButton:checked {{
    color: {ACCENT_HI}; border-left: 3px solid {ACCENT}; background: {rgba(OVERLAY, 0.06)};
}}
#sideCaption {{ color: {MUTED}; font-size: 13px; padding-left: 18px; }}
#sep {{ background: {rgba(OVERLAY, 0.13)}; max-height: 1px; min-height: 1px; margin: 4px 10px; }}
#content {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {surface(MAIN_TOP, 0.85)},
                                stop:1 {surface(MAIN_BOTTOM, 0.85)});
}}
#bottomBar {{ background: {surface(BOTTOM_BG)};{edge_top} }}
#pageTitle {{ font-size: 23px; font-weight: 700; }}
#sectionTitle {{ font-size: 15px; font-weight: 700; }}
#cardTitle {{ font-size: 15px; font-weight: 700; }}
#muted, #hint {{ color: {MUTED}; font-size: 13px; }}
#bigValue {{ font-size: 15px; font-weight: 700; }}
#profileLabel {{ font-size: 20px; font-weight: 600; }}
#logo {{ font-size: 26px; font-weight: 900; font-style: italic; color: {TEXT}; letter-spacing: 1px; }}
#silentBadge {{
    background: {DANGER}; color: #ffffff; font-size: 15px; font-weight: 800; letter-spacing: 1px;
    padding: 3px 12px; border-radius: 4px;
}}
#banner {{ background: {rgba(ACCENT, 0.16)}; border: 1px solid {rgba(ACCENT, 0.55)}; border-radius: 4px; }}
#card {{ background: {rgba(SHADE, 0.10)}; border-radius: 6px; }}
QToolTip {{ background: {TOOLTIP}; color: {TEXT}; border: 1px solid {POPUP_BORDER}; padding: 4px; }}
QPushButton {{ background: {PANEL}; border: none; padding: 7px 18px; border-radius: 2px; }}
QPushButton:hover {{ background: {PANEL_HI}; }}
QPushButton:pressed {{ background: {PANEL_LO}; }}
QToolButton {{ background: transparent; border: none; padding: 4px; border-radius: 3px; }}
QToolButton:hover {{ background: {rgba(OVERLAY, 0.08)}; }}
QComboBox {{
    background: {PANEL}; border: none; padding: 5px 10px; border-radius: 2px; min-height: 20px;
    font-size: 14px; font-weight: 600;
}}
QComboBox:hover {{ background: {PANEL_HI}; }}
QComboBox::drop-down {{ border: none; width: 28px; }}
QComboBox::down-arrow {{ image: url({img["chevron-down"]}); width: 14px; height: 14px; }}
QComboBox QAbstractItemView {{
    background: {LIST_BG}; border: 1px solid {POPUP_BORDER}; selection-background-color: {ACCENT};
    selection-color: {ACCENT_TEXT}; outline: none; padding: 4px;
}}
QCheckBox {{ spacing: 10px; font-size: 15px; }}
QCheckBox:disabled {{ color: {DIM}; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border: 2px solid {TEXT}; border-radius: 2px; }}
QCheckBox::indicator:disabled {{ border-color: {DIM}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; image: url({img["check"]}); }}
#quickToggle {{ spacing: 7px; font-size: 13px; color: {MUTED}; }}
#quickToggle:checked {{ color: {TEXT}; }}
#quickToggle:disabled {{ color: {DIM}; }}
#quickToggle::indicator {{
    width: 12px; height: 12px; border-radius: 8px; border: 2px solid {MUTED}; background: transparent;
}}
#quickToggle::indicator:checked {{
    border: 2px solid {ACCENT}; background: qradialgradient(cx:0.5, cy:0.5, radius:0.5, fx:0.5, fy:0.5,
        stop:0 {ACCENT}, stop:0.55 {ACCENT}, stop:0.6 transparent);
}}
#quickToggle::indicator:disabled {{ border-color: {DIM}; }}
QPlainTextEdit {{
    background: {rgba(SHADE, 0.18)}; border: none; border-radius: 6px; padding: 6px;
    color: {GRAPH_TEXT}; selection-background-color: {ACCENT}; selection-color: {ACCENT_TEXT};
}}
QSlider::groove:horizontal {{ height: 5px; background: {TRACK}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 2px; }}
QSlider::sub-page:horizontal:disabled {{ background: {DIM}; }}
QSlider::handle:horizontal {{
    background: {HANDLE}; border: 1px solid {HANDLE};
    width: 14px; height: 14px; margin: -6px 0; border-radius: 8px;
}}
QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; border: none; }}
QScrollBar:vertical, QScrollBar:horizontal {{ background: transparent; width: 10px; height: 10px; }}
QScrollBar::handle {{ background: {rgba(OVERLAY, 0.18)}; border-radius: 4px; min-height: 30px; min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
"""
