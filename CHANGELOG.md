# Changelog

SbAe9-AudioMon: PipeWire VU meters and a dropout watch for the Creative Sound
Blaster AE-9. A debugging companion to
[SbAe9-Drv](https://github.com/3xHelix/SbAe9-Drv) and
[SbAe9-SBC](https://github.com/3xHelix/SbAe9-SBC).

## Unreleased

Built on SbAe9-Kit, the base it now shares with Sound Blaster Command.

### Added

- **Palettes**: Helix (still the default), Command, Albino and Wololo, the same
  as Sound Blaster Command (Settings → Window, or `--palette`).
- **UI scale**: 70–150% (Settings → Window, or `--scale`).

### Fixed

- The meters' colour zones come from each palette's own meter colours (kit
  0.3.1), so Albino shows green / gold / red instead of three shades of coral.
- The peak-hold line uses the palette's handle edge colour: white as before on
  the dark palettes, coral on Albino, where white vanished on the light bars.

### Changed

- The app is now a package (`audiomon/`) plus a launcher (`sbae9-audiomon`),
  like SBC. `install.sh` installs a copy in `~/.local/share/sbae9-audiomon`
  and links the command to it. The menu entry, command, settings, debug log
  and meter node names are unchanged.
- The theme, Glass, XWayland, relaunch, frameless title bar and edge grips,
  app icon and AE-9 detection now come from the shared kit (`sbae9kit/`).
- The tests run on the shared fake AE-9 (`sbae9kit.testing`). A new test
  checks the vendored kit is unchanged, and that AudioMon never loads the
  kit's mixer (it stays listen-only).
- The icon tool moved to SbAe9-Kit.

## 0.2.1 (2026-11-06)

### Fixed

- **Optical In with no light** read "live" at -inf on the real card, which
  keeps the optical input running and sends digital silence. It now reads "no
  signal" when nothing above silence has arrived for half a second (or nothing
  at all arrives).
- The fake AE-9 rig's Optical In now does the same as the real card (silence,
  not no data), and its test covers dark → lit → dark.

## 0.2.0 (2026-11-06)

### Added

- **Glass**, from Sound Blaster Command: see-through, frosted backgrounds with
  an Opacity slider (30–95%, live) in Settings → Window, or `--glass`. On KDE
  the app runs through XWayland so KWin can blur what's behind the window.
  Switching Glass on or off reopens the window, and the debug log notes the
  brief monitoring restart.

## 0.1.0 (2026-11-06)

First release. It watches what goes into the AE-9 and what actually comes out
of it, and flags the moment they disagree. It's built to catch the rare
"silent restart" after the card idles and suspends. Tested on driver 0.9.2.

### Added

- **AE-9 monitor**, on by default. It finds the card's nodes by name (not by
  card number or PCI address) and meters:
  - the output monitor (what PipeWire sends to the card);
  - What U Hear (what the card actually plays, after its DSP);
  - Mic / Line In;
  - Optical In, which shows "no signal" without light on the jack;
  - every app playing to the AE-9, which come and go as they start and stop.

  When the card drops off and comes back (PSU cycle, driver reload, suspend),
  everything is found and rewired.
- **Listen-only.** It never changes the default output or input, never moves
  apps, and never touches the card's mixer controls. The only links it makes
  go into its own meters, and links you make by hand are left alone.
- **Let the AE-9 idle**, on by default. The meters never keep the card awake:
  - the output and app meters are passive;
  - What U Hear, Mic and Optical are only connected while the card is playing.

  The card idles and suspends exactly as it would without the app.
  **Always capture inputs** keeps the inputs connected for comparison.
  **Keep awake** keeps everything connected.
- **SILENT flag**: the output has signal (above -50 dBFS) but What U Hear stays
  below -60 dBFS for over half a second. It shows a red badge, a dropout
  count and a red What U Hear card.
- **Debug log** (`~/.local/state/sbae9-audiomon/ae9-debug.log`, plus the Event
  Log page). Times are local, to the second, so they line up with
  `journalctl -k`. It records:
  - the card's nodes coming and going;
  - running / idle / suspended changes;
  - wiring;
  - apps starting and stopping;
  - SILENT start and end.

  SILENT lines carry the driver version and srcversion.
- **Meters**: RMS with VU ballistics, sample peak, a 2 s peak hold and a clip
  light. Stats: Peak, RMS, Avg since reset, Max since reset.
- **Manual meters**: two meters with 2 / 4 / 6 / 8 unlinked ports, to wire by
  hand in Helvum, qpwgraph or pw-link.
- **Looks like Sound Blaster Command**, in its Helix palette:
  - its own title bar (or the desktop's, with `--frame`);
  - sidebar pages: Monitor, Event Log, Manual Meters and Settings;
  - SBC's status bar showing the card's state;
  - quick toggles for the three monitoring options.
- **Fake AE-9 test rig**, built like SBC's from its snapshot of the real
  machine. It runs 13 automated tests, and `tools/fake_ae9.py` shows the real
  window on a fake card going through playback, suspend, a silent restart
  and a drop-out. Nothing real is touched.
- **install.sh**: the `sbae9-audiomon` command, a menu entry and the app icon
  ("Neon VU Meter Diagnostics", cut out by `tools/make_icons.py`, adapted
  from SBC's).
