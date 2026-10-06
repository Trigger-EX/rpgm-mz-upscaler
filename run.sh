#!/usr/bin/env bash
# Creates a virtualenv on first use, installs dependencies, launches the GUI.
set -e
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  python3 -m venv .venv
fi
if ! .venv/bin/python -c "import PySide6, PIL, numpy" 2>/dev/null; then
  .venv/bin/pip install -r requirements.txt
fi
exec .venv/bin/python -m rpgm_upscaler "$@"
