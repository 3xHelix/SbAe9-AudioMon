# SPDX-License-Identifier: GPL-2.0-only
"""Make the app icon PNGs (appicon/sbae9-audiomon-<size>.png) from the artwork.

    python3 tools/make_icons.py "Neon VU Meter Diagnostics.png"

Adapted from SbAe9-SBC's tools/make_icons.py, so both apps' icons are made
the same way. The artwork is a rounded tile on a dark, grainy backdrop with no
transparency. This cuts the tile out along a rounded square (drawn 4x and
scaled down, so the edge is smooth) and saves it at the sizes desktops ask
for. Re-measure TILE if the artwork changes (--tile LEFT TOP SIDE RADIUS).
"""
import argparse
import os

from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "appicon")
NAME = "sbae9-audiomon"
SIZES = (16, 24, 32, 48, 64, 128, 256, 512)
# left, top, side, corner radius in the 1254 px artwork. Measured: the rim runs
# x 76..1176 and from y 73 (its bottom edge is in shadow); same tile as SBC's.
TILE = (76, 73, 1101, 196)


def cut(src, tile=TILE):
    left, top, side, radius = tile
    im = Image.open(src).convert("RGBA")
    k = 4
    mask = Image.new("L", (im.width * k, im.height * k), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (left * k, top * k, (left + side) * k - 1, (top + side) * k - 1), radius * k, fill=255)
    im.putalpha(mask.resize(im.size, Image.LANCZOS))
    return im.crop((left, top, left + side, top + side))


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("artwork")
    ap.add_argument("--tile", type=int, nargs=4, default=TILE, metavar=("LEFT", "TOP", "SIDE", "RADIUS"))
    ap.add_argument("--out", default=OUT)
    args = ap.parse_args()
    tile = cut(args.artwork, tuple(args.tile))
    os.makedirs(args.out, exist_ok=True)
    for size in SIZES:
        path = os.path.join(args.out, f"{NAME}-{size}.png")
        tile.resize((size, size), Image.LANCZOS).save(path, optimize=True)
        print(path)


if __name__ == "__main__":
    main()
