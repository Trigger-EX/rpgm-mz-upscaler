"""PyInstaller entry point (same as `python -m rpgm_upscaler`)."""
import multiprocessing
import sys

from rpgm_upscaler.__main__ import main

if __name__ == "__main__":
    multiprocessing.freeze_support()           # the upscaler's worker processes are re-launches of this very executable
    sys.exit(main())
