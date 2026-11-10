# SPDX-License-Identifier: GPL-2.0-only
"""SbAe9-AudioMon: PipeWire VU meters and a dropout watch for the Creative
Sound Blaster AE-9. Listen-only: it never changes defaults, moves app streams
or touches the card's mixer (so it never imports sbae9kit.mixer).

Two modes:

* AE-9 mode (default): finds the AE-9 nodes and wires meters to its output
  monitor, What U Hear loopback, the head unit's Mic / Aux input, the card's
  Optical In and every app stream
  playing to it. Flags "SILENT" dropouts and keeps a timestamped debug log.
* Manual meters: two meters with 2/4/6/8 unlinked input ports, to wire by hand
  with Helvum, qpwgraph, pw-link...

Each meter is a PipeWire capture node created via `pw-record`.
"""

__version__ = "0.4.6"
