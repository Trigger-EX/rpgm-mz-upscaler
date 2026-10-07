#!/usr/bin/env bash
# Builds a self-contained folder (dist/rpgm-hub/) with PyInstaller in a throwaway virtualenv.
# usage: tools/build_bundle.sh [--with-translate] [--with-ocr]      (the optional extras make the bundle much bigger)
set -eu
ROOT=$(cd "$(dirname "$0")/.." && pwd); cd "$ROOT"
VENV=${VENV:-$ROOT/.venv-bundle}
[ -d "$VENV" ] || python3 -m venv "$VENV"
"$VENV/bin/pip" install -q -r requirements.txt "pyinstaller>=6"
for a in "$@"; do
  case $a in
    --with-translate) "$VENV/bin/pip" install -q -r requirements-translate.txt ;;
    --with-ocr) "$VENV/bin/pip" install -q -r requirements-ocr.txt ;;
    *) echo "unknown option $a" >&2; exit 2 ;;
  esac
done
"$VENV/bin/pyinstaller" --noconfirm --distpath dist --workpath build packaging/rpgm-hub.spec
echo "built dist/rpgm-hub/rpgm-hub"
