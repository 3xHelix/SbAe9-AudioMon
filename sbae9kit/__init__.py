# SPDX-License-Identifier: GPL-2.0-only
"""sbae9kit: the shared base of the Sound Blaster AE-9 apps for Linux.

Vendored, byte for byte, into SbAe9-SBC (Sound Blaster Command) and
SbAe9-AudioMon by tools/sync_kit.py from the SbAe9-Kit repo; edit it there,
not in an app. Nothing here keeps state: apps hold their own settings and
pass them in.

    ae9       read-only AE-9 facts: PipeWire node detection, driver version
              and options, what the card's PCMs are open at
    mixer     ALSA mixer controls via ctypes, read and WRITE (control apps only)
    palettes  the colour palettes (Command, Helix, Albino, Wololo)
    glass     Glass: see-through backgrounds, KDE blur behind
    desktop   UI scale, XWayland choice, WM_CLASS, relaunch, app icon
    frame     the frameless window's title bar and edge grips
    testing   the fake AE-9 both apps' tests run against (tests only)
"""

KIT_VERSION = "0.3.1"
