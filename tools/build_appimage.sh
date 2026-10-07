#!/usr/bin/env bash
# Wraps dist/rpgm-hub/ (from tools/build_bundle.sh) into RPGM_Hub-x86_64.AppImage.
# Needs appimagetool: set APPIMAGETOOL=/path/to/appimagetool, or it is downloaded from the AppImageKit release page.
set -eu
ROOT=$(cd "$(dirname "$0")/.." && pwd); cd "$ROOT"
[ -x dist/rpgm-hub/rpgm-hub ] || { echo "run tools/build_bundle.sh first" >&2; exit 1; }
APPDIR=build/RPGM_Hub.AppDir
rm -rf "$APPDIR"; mkdir -p "$APPDIR/usr"
cp -r dist/rpgm-hub "$APPDIR/usr/bin"
cp packaging/rpgm-hub.desktop "$APPDIR/"; cp packaging/rpgm-hub.png "$APPDIR/"
printf '#!/bin/sh\nHERE=$(dirname "$(readlink -f "$0")")\nexec "$HERE/usr/bin/rpgm-hub" "$@"\n' > "$APPDIR/AppRun"
chmod +x "$APPDIR/AppRun"
TOOL=${APPIMAGETOOL:-build/appimagetool}
if [ ! -x "$TOOL" ]; then
  curl -fsSL -o "$TOOL" https://github.com/AppImage/AppImageKit/releases/download/continuous/appimagetool-x86_64.AppImage
  chmod +x "$TOOL"
fi
ARCH=x86_64 "$TOOL" --appimage-extract-and-run "$APPDIR" RPGM_Hub-x86_64.AppImage
echo "built RPGM_Hub-x86_64.AppImage"
