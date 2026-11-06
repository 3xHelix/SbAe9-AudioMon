# SbAe9-AudioMon

Lightweight PipeWire VU meters (Qt 6 / PySide6), with a debug mode for the
Sound Blaster AE-9.

## Modes

### AE-9 mode (default)

SbAe9-AudioMon finds the AE-9's PipeWire nodes by name/card and wires a meter to each:

| Meter | Source | Meter node |
|-------|--------|-----------|
| AE-9 Output (monitor) | the AE-9 sink's monitor ports (what PipeWire sends to the card) | `sbae9-audiomon.ae9.monitor` |
| AE-9 What U Hear | `alsa_input.ae9_what_u_hear` (what the card actually plays, after its DSP) | `sbae9-audiomon.ae9.wuh` |
| AE-9 Mic / Line In | the AE-9 analog source | `sbae9-audiomon.ae9.mic` |
| AE-9 Optical In | `alsa_input.ae9_optical` (shows "no signal" without light on the jack) | `sbae9-audiomon.ae9.optical` |
| one per app stream | every app stream playing to the AE-9 sink, added/removed as they come and go | `sbae9-audiomon.stream.<id>` |

Nodes are re-found and re-wired automatically when the card drops off and comes
back (PSU cycle, driver reload, suspend).

**Listen-only.** SbAe9-AudioMon never changes the default sink/source, never moves or
relinks app streams, and never touches ALSA mixer controls. The only links it
creates or removes are its own, from AE-9 / stream ports into its meter nodes.

**SILENT flag.** Lights up (and counts a dropout) when the output monitor has
signal (> -50 dBFS peak) but What U Hear stays below -60 dBFS for more than
0.5 s. What U Hear is ignored for 1 s after it's (re)wired so it can start up.
Thresholds are constants near the top of `sbae9-audiomon.py`.

**Let the AE-9 idle** (on by default). Meters must not stop the card from
suspending:

- The output-monitor and app-stream meters are passive nodes (`node.passive`),
  so they only receive data while the card is running anyway.
- What U Hear, Mic/Line and Optical have to actively capture, so they're only
  wired while the AE-9 sink is `running`, and they're unwired as soon as it goes idle.
  This means nothing of ours holds an AE-9 PCM open while it idles or suspends.

Turn it off (or run with `--keep-awake`) to keep everything wired all the time.
That keeps the AE-9 running permanently.

**Always capture inputs** (off by default, `--always-capture`) is a comparison
mode. The output and app meters stay passive, so the sink can still suspend,
but What U Hear, Mic and Optical stay wired all the time. That keeps the card's
capture converters and DMA running while playback is idle. Use it to check
whether open captures change the bug's behaviour.

**Debug log.** Saved to `~/.local/state/sbae9-audiomon/ae9-debug.log` and shown at
the bottom of the window. Times are local, to the second, to line up with
`journalctl -k` (e.g. the driver's `ae9-pause-test:` lines). It records:
- AE-9 nodes appearing and disappearing;
- sink state changes (running / idle / suspended);
- wiring changes;
- app streams starting and ending;
- SILENT start and end.

SILENT lines and card (re)appearances include the driver version and srcversion
from `/sys/module/snd_hda_codec_creative_ae9/`.

### Manual mode

Untick **Auto-wire AE-9** (or run `--manual`) to get the original two meters,
**AudioMon In** (`sbae9-audiomon.in`) and **AudioMon Out** (`sbae9-audiomon.out`). Each
has 2/4/6/8 unlinked input ports to wire up yourself with Helvum, qpwgraph or
`pw-link`. Changing the channel count recreates that node, so its links are dropped.

## Requirements

Python 3, PySide6 (Qt 6), numpy, and `pw-record` / `pw-link` / `pw-dump` (pipewire-utils).
On Fedora: `sudo dnf install python3-pyside6 python3-numpy pipewire-utils`

## Run

    ./sbae9-audiomon.py                  # last-used mode (AE-9 mode the first time)
    ./sbae9-audiomon.py --ae9            # AE-9 mode
    ./sbae9-audiomon.py --ae9 --keep-awake
    ./sbae9-audiomon.py --manual -c 8    # two 8-channel meters, wire by hand
    ./sbae9-audiomon.py --log /path/to/file.log

Install for your user:

    install -Dm755 sbae9-audiomon.py ~/.local/bin/sbae9-audiomon
    install -Dm644 sbae9-audiomon.desktop ~/.local/share/applications/sbae9-audiomon.desktop

## Reading the meters

- **Solid bar**: RMS with VU ballistics (300 ms). **Translucent bar**: sample peak (falls at 20 dB/s).
- **White line**: peak hold (2 s).
- **Red LED**: clip (sample >= 0 dBFS), latched until reset.
- **Stats**: `Peak` = held peak, `RMS` = current VU level, `Avg` = average RMS
  (Leq) since last reset, `Max` = highest peak since last reset.
- Colour zones: green < -18 dBFS < yellow < -6 dBFS < red.
- Click a meter or its ⟳ button to reset its stats. All values are dBFS.

## Config

Saved to `~/.config/sbae9-audiomon/config.json`:

```json
{
  "mode": "ae9",
  "let_idle": true,
  "meters": [
    {"name": "sbae9-audiomon.in",  "label": "AudioMon In",  "channels": 2},
    {"name": "sbae9-audiomon.out", "label": "AudioMon Out", "channels": 2}
  ]
}
```
