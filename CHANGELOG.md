# Changelog

SbAe9-AudioMon: PipeWire VU meters and a dropout watch for the Creative Sound
Blaster AE-9. A debugging companion to
[SbAe9-Drv](https://github.com/3xHelix/SbAe9-Drv) and
[SbAe9-SBC](https://github.com/3xHelix/SbAe9-SBC).

## Unreleased

For driver 0.12.0's separate optical output. Thanks to DarkNik for the
Optical Out meter and the format line.

### Added

- **Optical Out** meter, next to Output: what PipeWire sends to the separate
  optical output (hw:AE9,4: `alsa_output.pci-….optical_out` on driver
  0.12.0+, or `alsa_output.ae9_optical` from the older conf). Passive like the
  Output meter: it never keeps the sink awake. While nothing plays to it, its
  card says "idle: jack carries Output" when the optical jack copies the
  main output (Optical Out Mirror on, driver 0.12.1+; always on older
  drivers), or "idle: silent" when the mirror is off (0.12.1's default).
  The mirror switch is also in the debug log's driver state (read only).
- Every meter shows its stream's **rate and bit depth** under the
  Peak/RMS/Avg/Max rows, e.g. `48 kHz · 24-bit`:
  - Output: the card's live PCM Bit Depth setting;
  - Optical Out: the bits on the wire (24 for S32_LE, 16 for S16_LE), or
    `48 kHz · AC3 passthrough` for AC-3 / DTS passthrough;
  - the inputs: the driver's `cap_bits`;
  - app streams: their own format.

## 0.4.2 (2026-11-08)

A GATED flag for driver 0.10.x's half-speed fault, and the Output meter
ready for driver 0.12.0's optical output. On SbAe9-Kit 0.3.8.

### Added

- **GATED flag**, next to SILENT and ONE-SIDED: What U Hear plays in
  64-frame bursts with 64-frame gaps between (375 times a second) for over
  0.5 s. It's the driver 0.10.x fault where music plays at half speed with a
  buzz after a speakers <-> headphones switch in DSP mode (fixed in driver
  0.11.0). It shows a badge, a count and a red What U Hear card, logs a
  `GATED` line with the driver srcversion and the driver change before it,
  and saves the last 3 s as a WAV in `~/.local/state/sbae9-audiomon/captures/`.
  The output monitor is checked the same way, so music that itself repeats at
  375 Hz isn't flagged. ONE-SIDED isn't judged while GATED is up.

### Fixed

- The Output meter never takes driver 0.12.0's separate Optical Out sink
  (hw:AE9,4) for the main output. It used to pick the first AE-9 sink when
  none had "analog" in its name. Uses SbAe9-Kit 0.3.8's `ae9.output_kind()`.

## 0.4.1 (2026-11-08)

For driver 0.11.0, whose card profile opens What U Hear, Optical In and
Rear RCA In as PipeWire sources by itself. On SbAe9-Kit 0.3.7.

### Changed

- The meters' hover text and the README name the sources driver 0.11.0
  creates (`alsa_input.pci-….what_u_hear`, `….optical_in`, `….rear_rca`)
  next to the older hand-installed ones (`alsa_input.ae9_*`). AudioMon
  already found both.
- The README says to remove the old hand-installed What U Hear / Optical /
  Rear RCA confs on driver 0.11.0+, or each source shows up twice.
- Optical In with no light on the jack reads silence on driver 0.11.0+
  (older drivers: "no signal").
- SbAe9-Kit 0.3.7: the fake card's DAC Filter has driver 0.11.0's seven
  filters (tests only).

## 0.4.0 (2026-11-07)

For driver 0.10.1: the new Rear RCA In meter, a ONE-SIDED flag for one
What U Hear channel going wrong, and the driver's state in the debug log.
On SbAe9-Kit 0.3.6.

### Added

- **Rear RCA In** meter, for driver 0.10.1's new input: the head unit's rear
  RCA L/R jacks (`alsa_input.ae9_rear_rca`, hw:AE9,3, from the driver's
  `config/ae9-rear-rca-in.conf`). It's wired like Mic / Aux: only while the
  AE-9 plays, unless Always capture or Keep awake is on. Without the driver
  or the config it reads "not found".
- **ONE-SIDED flag**, next to SILENT: one What U Hear channel goes silent,
  noisy or far off in level while the other still matches the output, for
  over 0.5 s. It's for the card dropping one side's route after a stream
  restart (static on the right only, after a bit-depth change in DSP mode).
  It shows a badge, a count and a red What U Hear card, logs a
  `ONE-SIDED L|R` line with the driver srcversion, and saves the last 3 s as a
  WAV (output L/R and What U Hear L/R, lined up) in
  `~/.local/state/sbae9-audiomon/captures/`.
  In DSP mode it allows for what the card does to one side on purpose (Speaker
  Level and Distance differences, Bass Redirect), and it isn't judged on
  multichannel streams or for 3 s after a stream starts or a driver setting
  changes.
- **Driver state in the log**: Direct Mode, SBX, PCM Bit Depth, Speaker Config,
  Headphone Profile and Head Unit Output at start and on each change (read with
  `amixer`, never written), and the playback stream's format and rate when it
  opens or changes. SILENT and ONE-SIDED lines name the driver change just
  before them.

### Changed

- Switching Direct Mode (which restarts the running stream on purpose) holds
  SILENT off for 3 s, like a PipeWire restart. So does a new stream in DSP mode
  while the driver moves it into the DSP.

### Fixed

- Quitting with Ctrl-C no longer logs "PipeWire went away" after "stopped".

## 0.3.3 (2026-11-06)

Ready for driver 0.9.8: survives PipeWire restarts and ignores the voice-chat
nodes named after the card. On SbAe9-Kit 0.3.5.

### Fixed

- A PipeWire restart (Sound Blaster Command 0.11's What U Hear rate picker
  does one) is logged as "PipeWire went away" / "PipeWire restarted", and
  SILENT is held off until the meters are rewired, plus 3 s. Before, it went
  unnamed in the log and a slow rewire could look like a dropout.
- Software nodes named after the card, like a WebRTC echo-cancel pair
  ("ae9_voice" / "ae9_voice_ref"), are never taken for the AE-9. Before, if
  the card dropped out, they could stand in for its output and mic.
  (SbAe9-Kit 0.3.5.)

## 0.3.2 (2026-11-06)

Short names on screen, the detail on hover, as in Sound Blaster Command 0.9.1.

### Changed

- The meter cards show short names (Output, What U Hear, Mic / Aux, Optical In)
  and their state. Hover a card's title for the full name, the jacks it covers
  and its PipeWire node, as Sound Blaster Command does. The second line with the
  node name is gone. The meters' nodes and the debug log keep their full names.
- Settings' hints use the short names too.

## 0.3.1 (2026-11-06)

The head unit's input gets its real name, and the README gets screenshots.
On SbAe9-Kit 0.3.4.

### Added

- README screenshots (`docs/`), like Sound Blaster Command's: a real-card
  Monitor page, plus a silent restart, the Event Log, Manual Meters, Settings
  and Glass from the fake AE-9. `tools/screenshots.py` regenerates them.

### Fixed

- Settings → Device and the SILENT badge's tooltip show the SILENT timings
  the app is actually using.
- A little more space above Settings' Palette / UI scale row.
- The fake AE-9 (kit 0.3.2) plays What U Hear back at the output's level. The
  earlier -3.1 dB came from misreading a sine's RMS as an offset; the real
  card is level-exact, and AudioMon measured it within 0.1-0.3 dB.

### Changed

- The mic meter is now "AE-9 Head Unit Mic / Aux" (was "Mic / Line In"): it's
  the head unit's XLR/TRS and 3.5 mm mic jacks, which share one input. Names now
  say where each jack lives (the head unit or the card). The fake AE-9 (kit
  0.3.3) uses the driver's new name too. Node names are unchanged.
- The README's real-card screenshot is retaken with the new name.

## 0.3.0 (2026-11-06)

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
- The repo is public. The icon artwork is no longer tracked; the icons in
  `appicon/` are what the app uses.

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
