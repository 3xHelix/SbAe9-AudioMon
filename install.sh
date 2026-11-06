#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
# Installs SbAe9-AudioMon for the current user: a copy of the app in
# ~/.local/share/sbae9-audiomon, the `sbae9-audiomon` command, its icon and a
# menu entry. It's a copy, like Sound Blaster Command's: the menu app is
# always the build you last installed, never a half-edited working tree.
# Re-run after each tested change to update it.
set -e
HERE="$(dirname "$(readlink -f "$0")")"
APP="$HOME/.local/share/sbae9-audiomon"
BIN="$HOME/.local/bin"
APPS="$HOME/.local/share/applications"
ICONS="$HOME/.local/share/icons/hicolor"
mkdir -p "$BIN" "$APPS"

# the app: launcher, package, shared kit and icons, replacing the previous copy
rm -rf "$APP.new"
mkdir -p "$APP.new"
install -m 755 "$HERE/sbae9-audiomon" "$APP.new/sbae9-audiomon"
cp -r "$HERE/audiomon" "$HERE/sbae9kit" "$HERE/appicon" "$APP.new/"
find "$APP.new" -name __pycache__ -prune -exec rm -rf {} +
git -C "$HERE" describe --always --dirty > "$APP.new/BUILD" 2>/dev/null || echo build > "$APP.new/BUILD"
rm -rf "$APP"
mv "$APP.new" "$APP"
# the command (before 0.3 it was a single-file copy; a link to the copy replaces it)
rm -f "$BIN/sbae9-audiomon"
ln -s "$APP/sbae9-audiomon" "$BIN/sbae9-audiomon"

# app icon, one PNG per size (made by SbAe9-Kit's tools/make_icons.py)
for png in "$HERE"/appicon/sbae9-audiomon-*.png; do
    size=$(basename "$png" .png); size=${size##*-}
    install -D -m 644 "$png" "$ICONS/${size}x${size}/apps/sbae9-audiomon.png"
done
rm -f "$ICONS/scalable/apps/sbae9-audiomon.svg"     # the old icon, so it doesn't win
touch "$ICONS"

cat > "$APPS/sbae9-audiomon.desktop" <<DESK
[Desktop Entry]
Type=Application
Name=SbAe9-AudioMon
GenericName=AE-9 Audio Monitor
Comment=VU meters and dropout watch for the Creative Sound Blaster AE-9 (PipeWire)
Exec=$BIN/sbae9-audiomon
Icon=sbae9-audiomon
Terminal=false
Categories=AudioVideo;Audio;
Keywords=Creative;Sound Blaster;AE-9;VU;Meter;PipeWire;What U Hear;
StartupWMClass=sbae9-audiomon
DESK
update-desktop-database "$APPS" 2>/dev/null || true
gtk-update-icon-cache -f -t "$ICONS" 2>/dev/null || true
echo "Installed $(cat "$APP/BUILD") to $APP."
echo "Launch 'SbAe9-AudioMon' from the menu or run: sbae9-audiomon"
