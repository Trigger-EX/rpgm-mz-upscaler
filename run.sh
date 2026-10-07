#!/usr/bin/env bash
# Creates a virtualenv on first use, installs dependencies, launches the GUI.
set -e
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  python3 -m venv .venv
fi
# reinstall when requirements.txt changed since the last install (a hash is kept inside the venv)
want=$(sha256sum requirements.txt | cut -d' ' -f1)
if ! .venv/bin/python -c "import PySide6, PIL, numpy" 2>/dev/null || [ "$(cat .venv/.requirements.sha 2>/dev/null)" != "$want" ]; then
  .venv/bin/pip install -r requirements.txt
  echo "$want" > .venv/.requirements.sha
fi
exec .venv/bin/python -m rpgm_upscaler "$@"
