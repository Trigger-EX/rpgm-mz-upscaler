"""Picklable stand-ins for runner.process_image that kill their worker process (used by the pool-crash tests). In the worker, `runner` is a fresh import, so
runner.process_image is the real function there; only the parent process has it patched.."""
import os
from pathlib import Path

from rpgm_upscaler.core import runner


def crash_once(job, base, out, opts, n, key):
    marker = Path(out) / ".crashed"
    if job.src.endswith("IconSet.png") and not marker.exists():
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("x")
        os._exit(1)
    return runner.process_image(job, base, out, opts, n, key)


def crash_always_on_icons(job, base, out, opts, n, key):
    if job.src.endswith("IconSet.png"):
        os._exit(1)
    return runner.process_image(job, base, out, opts, n, key)
