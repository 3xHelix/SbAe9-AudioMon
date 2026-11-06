# SPDX-License-Identifier: GPL-2.0-only
"""The colour palettes shared by the AE-9 apps.

Each palette is a dict of named colours (upper case) plus a "label". Apps
copy the active one into their own theme module (see SbAe9-SBC's
sbcommand/theme.py) and build their style sheet from it.
"""

PALETTES = {
    # Navy and purple with teal, like Creative's app.
    "command": {
        "label": "Command",
        "ACCENT": "#1fa6b8", "ACCENT_HI": "#35c3d6", "ACCENT_TEXT": "#ffffff",
        "TEXT": "#e8eaf3", "MUTED": "#a4a8bf", "DIM": "#6b6f86",
        "TRACK": "#4a4b5c", "PANEL": "#4b4c58", "PANEL_HI": "#585a68", "PANEL_LO": "#3d3e4b",
        "TITLE_BG": "#1b2034", "BOTTOM_BG": "#191d30",
        "SIDEBAR_TOP": "#272c5c", "SIDEBAR_BOTTOM": "#2d2a63",
        "MAIN_TOP": "#2b2b55", "MAIN_BOTTOM": "#33294f",
        "POPUP": "#1e2238", "POPUP_BORDER": "#343957", "LIST_BG": "#2d2f45",
        "DIALOG": "#24284a", "TOOLTIP": "#303247",
        "SWITCH_OFF": "#5a5c6c", "DIAL_TRACK": "#4a4b58", "IDLE": "#7d8296",
        "GRAPH_BG": "#45454f", "GRAPH_TEXT": "#d5d6de", "EQ_LINE": "#7f9cf5",
        "SEG": "#3d3e4c", "SEG_HOVER": "#4c4e5e", "CARD_CAP": "#3f3f4a", "SPEAKER_BOX": "#33355a",
        "OK": "#33d14f", "WARN": "#e9c64a",
        "METER": ["#1fa6b8", "#7fd16a", "#e9c64a", "#e2505c"],
        "METER_ZONES": ["#7fd16a", "#e9c64a", "#e2505c"],   # level meters: normal, high, over
        "OVERLAY": "#ffffff", "SHADE": "#000000", "DOT_IDLE": "#f2f2f2",
        "HANDLE": "#f2f2f6", "HANDLE_BORDER": "#f2f2f6", "HANDLE_HOVER": "#ffffff",
    },
    # From the 3xHelix avatar: blue-black, violet, coral-orange to amber, mint.
    "helix": {
        "label": "Helix",
        "ACCENT": "#f57a45", "ACCENT_HI": "#ffb63a", "ACCENT_TEXT": "#1b1230",
        "TEXT": "#f2edff", "MUTED": "#ab9fcb", "DIM": "#6d6190",
        "TRACK": "#3a2f5a", "PANEL": "#3b2e5e", "PANEL_HI": "#4b3b74", "PANEL_LO": "#2e2449",
        "TITLE_BG": "#120f21", "BOTTOM_BG": "#110e1f",
        "SIDEBAR_TOP": "#2a1850", "SIDEBAR_BOTTOM": "#3a1452",
        "MAIN_TOP": "#1d1832", "MAIN_BOTTOM": "#26183f",
        "POPUP": "#16122a", "POPUP_BORDER": "#3b2f5c", "LIST_BG": "#211b38",
        "DIALOG": "#1d1832", "TOOLTIP": "#2a2145",
        "SWITCH_OFF": "#4a3d6e", "DIAL_TRACK": "#3a2f5a", "IDLE": "#7a6c9e",
        "GRAPH_BG": "#241d3d", "GRAPH_TEXT": "#d9d0f0", "EQ_LINE": "#5dcaa8",
        "SEG": "#2e2449", "SEG_HOVER": "#3b2e5e", "CARD_CAP": "#2a2145", "SPEAKER_BOX": "#3b2e5e",
        "OK": "#5dcaa8", "WARN": "#ffb63a",
        "METER": ["#5dcaa8", "#5dcaa8", "#ffb63a", "#e31b49"],
        "METER_ZONES": ["#5dcaa8", "#ffb63a", "#e31b49"],   # level meters: normal, high, over
        "OVERLAY": "#ffffff", "SHADE": "#000000", "DOT_IDLE": "#f2f2f2",
        "HANDLE": "#f2f2f6", "HANDLE_BORDER": "#f2f2f6", "HANDLE_HOVER": "#ffffff",
    },
    # Light: off-white and cream with blush and peach, coral accent.
    "albino": {
        "label": "Albino",
        "ACCENT": "#cf7166", "ACCENT_HI": "#b4554b", "ACCENT_TEXT": "#ffffff",
        "TEXT": "#4a3a38", "MUTED": "#8c7a76", "DIM": "#b9aaa5",
        "TRACK": "#e6d9d2", "PANEL": "#efe6df", "PANEL_HI": "#e8d9d1", "PANEL_LO": "#e2d4cc",
        "TITLE_BG": "#f7faf3", "BOTTOM_BG": "#f3efe6",
        "SIDEBAR_TOP": "#f4e7e3", "SIDEBAR_BOTTOM": "#eed3d2",
        "MAIN_TOP": "#f8f7f1", "MAIN_BOTTOM": "#eeecdf",
        "POPUP": "#fbfaf6", "POPUP_BORDER": "#e2d4cc", "LIST_BG": "#fbfaf6",
        "DIALOG": "#f8f7f1", "TOOLTIP": "#fbfaf6",
        "SWITCH_OFF": "#dccdc6", "DIAL_TRACK": "#ebe0d9", "IDLE": "#c9b8b2",
        "GRAPH_BG": "#fbfaf6", "GRAPH_TEXT": "#7a6864", "EQ_LINE": "#d0838c",
        "SEG": "#ebe1da", "SEG_HOVER": "#e3d4cc", "CARD_CAP": "#f5eee8", "SPEAKER_BOX": "#efe3dd",
        "OK": "#7fae8c", "WARN": "#d9a441",
        "METER": ["#dda394", "#db8176", "#cf6a5e", "#b8433a"],
        "METER_ZONES": ["#7fae8c", "#d9a441", "#b8433a"],   # level meters: normal, high, over
        "OVERLAY": "#5a3a36", "SHADE": "#8a5a50", "DOT_IDLE": "#b9aaa5",
        "HANDLE": "#ffffff", "HANDLE_BORDER": "#cf7166", "HANDLE_HOVER": "#fff4f1",
    },
    # Wololo: the monk's royal-blue robe and white cloth, sun gold, grass
    # green, sky blue, and his staff's brown.
    "wololo": {
        "label": "Wololo",
        "ACCENT": "#f0d050", "ACCENT_HI": "#fdf2a0", "ACCENT_TEXT": "#1a2350",
        "TEXT": "#eef3e6", "MUTED": "#a9c6d6", "DIM": "#6a7eaa",
        "TRACK": "#33448a", "PANEL": "#2e3f86", "PANEL_HI": "#3a4d9c", "PANEL_LO": "#26356f",
        "TITLE_BG": "#121a3f", "BOTTOM_BG": "#101735",
        "SIDEBAR_TOP": "#2a3d9a", "SIDEBAR_BOTTOM": "#1f2f7a",
        "MAIN_TOP": "#1a2558", "MAIN_BOTTOM": "#1b2f52",
        "POPUP": "#141d48", "POPUP_BORDER": "#33458f", "LIST_BG": "#1a2558",
        "DIALOG": "#1a2558", "TOOLTIP": "#22306a",
        "SWITCH_OFF": "#44558f", "DIAL_TRACK": "#2e3f80", "IDLE": "#7183b3",
        "GRAPH_BG": "#17214f", "GRAPH_TEXT": "#c9dbe3", "EQ_LINE": "#8fce6f",
        "SEG": "#26356f", "SEG_HOVER": "#33458f", "CARD_CAP": "#3d3527", "SPEAKER_BOX": "#2e3f86",
        "OK": "#8fce6f", "WARN": "#f0d050",
        "METER": ["#6fb660", "#94ce7b", "#f0d050", "#e05a4f"],
        "METER_ZONES": ["#94ce7b", "#f0d050", "#e05a4f"],   # level meters: normal, high, over
        "OVERLAY": "#ffffff", "SHADE": "#000000", "DOT_IDLE": "#eef3e6",
        "HANDLE": "#eef3e6", "HANDLE_BORDER": "#eef3e6", "HANDLE_HOVER": "#ffffff",
    },
}

DEFAULT = "command"

# Warnings use one red in every palette, so they always look the same.
DANGER = "#e2505c"


def get(name):
    """The palette called `name`, or the default one."""
    return PALETTES.get(name) or PALETTES[DEFAULT]


def rgba(hex_color, alpha):
    h = hex_color.lstrip("#")
    return f"rgba({int(h[0:2], 16)},{int(h[2:4], 16)},{int(h[4:6], 16)},{alpha})"


def meter_zones(name):
    """Three level-meter colours (normal, high, over) for a palette. They're
    their own entry, not METER's stops: a light palette's gradient can be
    shades of one colour, and meter zones must read as green / amber / red."""
    return tuple(get(name)["METER_ZONES"])
