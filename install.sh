#!/bin/sh
# Installs SbAe9-AudioMon for the current user: the `sbae9-audiomon` command,
# its icon and a menu entry. Re-run after changing the app to update the
# installed copy (it's a copy, so the installed one only changes when you do).
set -e
HERE="$(dirname "$(readlink -f "$0")")"
BIN="$HOME/.local/bin"
APPS="$HOME/.local/share/applications"
ICONS="$HOME/.local/share/icons/hicolor"
mkdir -p "$BIN" "$APPS"

install -m 755 "$HERE/sbae9-audiomon.py" "$BIN/sbae9-audiomon"
# app icon, one PNG per size (made by tools/make_icons.py, like SbAe9-SBC's)
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
echo "Installed $(git -C "$HERE" describe --always --dirty 2>/dev/null || echo build) to $BIN/sbae9-audiomon."
echo "Launch 'SbAe9-AudioMon' from the menu or run: sbae9-audiomon"
