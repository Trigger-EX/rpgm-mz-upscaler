"""End-to-end VX / VX Ace upscale: unpack an encrypted archive if needed, plan, run the shared runner."""
from __future__ import annotations

import os
import re
import shutil
import tempfile
import threading
from pathlib import Path
from typing import Callable

from ..core.runner import Runner, RunResult
from ..core.settings import Options
from ..detect import detect_engine
from .archive import open_archive
from .planner import build_plan
from .project import RgssProjectError, load_rgss_project


class PrepareCancelled(RgssProjectError):
    """The user cancelled while an encrypted archive was being unpacked."""


# what reading a project's basics (screen, title, tilesets, script list) needs from an archive: two small files
_BASICS = re.compile(r"^data/(tilesets|scripts)\.rv(data2?)$", re.I)


class Prepared:
    """A source tree ready for planning; `cleanup()` removes a temporary extraction."""

    def __init__(self, project, tmp: Path | None):
        self.project, self._tmp = project, tmp

    def cleanup(self) -> None:
        if self._tmp is not None:
            shutil.rmtree(self._tmp, ignore_errors=True)
            self._tmp = None


def prepare(game: str | Path, on_progress: Callable[[int, int, str], None] | None = None,
            cancel: threading.Event | None = None, basics_only: bool = False) -> Prepared:
    """`basics_only` unpacks just the files needed to read the project's settings (analyze, scripts): the images stay
    in the archive. Raises PrepareCancelled if `cancel` is set while unpacking."""
    info = detect_engine(game)
    if info is None or info.engine not in ("ACE", "VX"):
        raise RgssProjectError("not a VX / VX Ace project" if info is None else f"{info.label} is not supported for upscaling")
    tmp = None
    base = None
    if info.archive is not None and not (info.root / "Data").is_dir():
        tmp = Path(tempfile.mkdtemp(prefix="rpgmhub_"))
        stamp = info.archive.stat().st_mtime
        try:
            with open_archive(info.archive) as arc:
                for f in arc.extract(tmp, (lambda n: bool(_BASICS.match(n.replace("\\", "/")))) if basics_only else None,
                                     on_progress, cancel):
                    os.utime(f, (stamp, stamp))       # stable mtimes, so a resumed run recognises finished work
        except BaseException:
            shutil.rmtree(tmp, ignore_errors=True)
            raise
        if cancel is not None and cancel.is_set():
            shutil.rmtree(tmp, ignore_errors=True)
            raise PrepareCancelled("cancelled")
        # keep loose files that sit next to the archive (Game.ini, audio ...); archived files win
        for f in info.root.iterdir():
            if f.is_file() and f.suffix.lower() not in (".rgss3a", ".rgss2a", ".rgssad"):
                shutil.copy2(f, tmp / f.name)
        base = tmp
    return Prepared(load_rgss_project(game, base=base, info=info), tmp)


def make_runner(game, out, opts: Options, mode: str = "hires", on_progress=None, on_log=None):
    """Returns (runner, prepared). Call prepared.cleanup() when the run is over."""
    prepared = prepare(game, on_progress)
    try:
        plan = build_plan(prepared.project, opts, mode)
        return Runner(plan, out, on_progress, on_log), prepared
    except Exception:
        prepared.cleanup()
        raise


def run(game, out, opts: Options, mode: str = "hires", on_progress=None, on_log=None) -> RunResult:
    runner, prepared = make_runner(game, out, opts, mode, on_progress, on_log)
    try:
        return runner.run()
    finally:
        prepared.cleanup()
