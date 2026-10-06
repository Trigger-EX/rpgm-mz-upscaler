"""Execute a Plan: copy tree, process images/videos, apply engine patches. Cancellable, resumable."""
from __future__ import annotations

import json
import logging
import multiprocessing
import os
import shutil
import threading
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import video
from .engines import NCNN_ENGINES, make_engine
from .imageops import load_image, save_image, upscale_image
from .planner import Job, Plan
from .settings import Options

log = logging.getLogger("rpgm_upscaler")
MANIFEST = Path(".upscaler") / "manifest.json"


class RunError(Exception):
    pass


@dataclass
class RunResult:
    ok: int = 0
    skipped: int = 0
    failed: list[tuple[str, str]] = field(default_factory=list)
    cancelled: bool = False
    patched: list[str] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return not self.failed and not self.cancelled


def validate_output(src: Path, out: Path) -> Path:
    src, out = src.resolve(), out.expanduser().resolve()
    if out == src or src in out.parents or out in src.parents:
        raise RunError("Output directory must be separate from the game (not equal to, inside or containing it).")
    return out


_ENGINE_CACHE: dict = {}


def _engine(opts: Options):
    k = (opts.engine, opts.engine_path, opts.model)
    if k not in _ENGINE_CACHE:
        _ENGINE_CACHE[k] = make_engine(opts.engine, opts.engine_path or None, opts.model or None)
    return _ENGINE_CACHE[k]


def process_image(job: Job, base: str, out: str, opts: Options, n: float, key: bytes | None) -> None:
    """Top-level (picklable) worker."""
    if job.passthrough:
        from . import crypto
        data = crypto.decrypt(Path(base, job.src).read_bytes(), key)
        dst = Path(out, job.dst)
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_name(dst.name + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(dst)
        return
    img = load_image(Path(base, job.src), key)
    res = upscale_image(img, _engine(opts), job.target, n, job.cell, job.resampler)
    save_image(res, Path(out, job.dst), key, job.out_encrypted)


def _file_sig(p: Path) -> dict:
    st = p.stat()
    return {"size": st.st_size, "mtime": st.st_mtime_ns}


class Runner:
    def __init__(self, plan: Plan, out: str | Path,
                 on_progress: Callable[[int, int, str], None] | None = None,
                 on_log: Callable[[str, str], None] | None = None):
        self.plan = plan
        self.out = validate_output(plan.project.base, Path(out))
        self.on_progress = on_progress or (lambda d, t, n: None)
        self.on_log = on_log or (lambda lvl, msg: None)
        self.cancel_event = threading.Event()
        self._procs: list = []

    def cancel(self) -> None:
        self.cancel_event.set()
        for p in list(self._procs):
            try:
                p.terminate()
            except Exception:  # noqa: BLE001
                pass

    def _log(self, lvl: str, msg: str) -> None:
        getattr(log, lvl, log.info)(msg)
        self.on_log(lvl, msg)

    def _load_manifest(self) -> dict:
        try:
            return json.loads((self.out / MANIFEST).read_text())
        except (OSError, ValueError):
            return {}

    def run(self) -> RunResult:
        plan, opts = self.plan, self.plan.options
        base = plan.project.base
        n = plan.scale.n
        result = RunResult()
        self.out.mkdir(parents=True, exist_ok=True)
        digest = opts.digest(n)
        manifest = self._load_manifest() if opts.resume else {}
        manifest = {k: v for k, v in manifest.items() if v.get("opts") == digest}

        job_srcs = {j.src for j in plan.jobs if not j.keep_source}
        copies = [f for f in base.rglob("*") if f.is_file() and f.relative_to(base).as_posix() not in job_srcs]
        total = len(copies) + len(plan.jobs)
        done = 0

        self._log("info", f"Copying {len(copies)} unchanged files to {self.out}")
        for f in copies:
            if self.cancel_event.is_set():
                break
            rel = f.relative_to(base)
            dst = self.out / rel
            try:
                if not (opts.resume and dst.is_file() and dst.stat().st_size == f.stat().st_size
                        and dst.stat().st_mtime_ns >= f.stat().st_mtime_ns):
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(f, dst)
            except OSError as e:
                result.failed.append((rel.as_posix(), str(e)))
                self._log("error", f"copy failed: {rel}: {e}")
            done += 1
            if done % 25 == 0:
                self.on_progress(done, total, rel.as_posix())

        pending: list[Job] = []
        for j in plan.jobs:
            m = manifest.get(j.src)
            dst = self.out / j.dst
            if m and dst.is_file() and m.get("sig") == _file_sig(base / j.src):
                result.skipped += 1
                done += 1
            else:
                pending.append(j)
        if result.skipped:
            self._log("info", f"Resuming: {result.skipped} assets already done")
        self.on_progress(done, total, "")

        def finish(job: Job, err: Exception | None) -> None:
            nonlocal done
            done += 1
            if err is None:
                result.ok += 1
                manifest[job.src] = {"opts": digest, "sig": _file_sig(base / job.src)}
                self._log("info", f"ok   {job.src}")
            else:
                result.failed.append((job.src, str(err)))
                self._log("error", f"FAIL {job.src}: {err}")
            self.on_progress(done, total, job.src)

        try:
            images = [j for j in pending if j.kind == "image"]
            videos = [j for j in pending if j.kind == "video"]
            self._run_images(images, base, n, finish)
            for j in videos:
                if self.cancel_event.is_set():
                    break
                try:
                    video.scale_video(base / j.src, self.out / j.dst, tuple(opts.target), self._procs, self.cancel_event)
                    finish(j, None)
                except Exception as e:  # noqa: BLE001
                    finish(j, e)
        finally:
            self._save_manifest(manifest)
        result.cancelled = self.cancel_event.is_set()
        if result.cancelled:
            self._log("warning", "Cancelled; run again with Resume to continue.")
            return result
        if opts.patch:
            from . import patcher
            try:
                hook = plan.patch_hook or patcher.apply_patches
                result.patched = hook(plan, self.out)
                for p in result.patched:
                    self._log("info", f"patched {p}")
            except Exception as e:  # noqa: BLE001
                result.failed.append(("engine patch", str(e)))
                self._log("error", f"engine patch failed: {e}")
        return result

    def _save_manifest(self, manifest: dict) -> None:
        p = self.out / MANIFEST
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(manifest))
        tmp.replace(p)

    def _run_images(self, jobs: list[Job], base: Path, n: float, finish) -> None:
        if not jobs:
            return
        opts, key = self.plan.options, self.plan.project.key
        ai = opts.engine in NCNN_ENGINES
        workers = 1 if ai else (opts.workers or os.cpu_count() or 1)
        workers = max(1, min(workers, len(jobs)))
        args = (str(base), str(self.out), opts, n, key)
        if workers == 1:
            for j in jobs:
                if self.cancel_event.is_set():
                    return
                try:
                    process_image(j, *args)
                    finish(j, None)
                except Exception as e:  # noqa: BLE001
                    finish(j, e)
            return
        ctx = multiprocessing.get_context("spawn")
        with ProcessPoolExecutor(max_workers=workers, mp_context=ctx) as pool:
            it = iter(jobs)
            inflight: dict = {}
            while True:
                while len(inflight) < workers * 2 and not self.cancel_event.is_set():
                    j = next(it, None)
                    if j is None:
                        break
                    inflight[pool.submit(process_image, j, *args)] = j
                if not inflight:
                    break
                finished, _ = wait(inflight, return_when=FIRST_COMPLETED, timeout=0.5)
                for f in finished:
                    j = inflight.pop(f)
                    finish(j, f.exception())
                if self.cancel_event.is_set():
                    for f in list(inflight):
                        f.cancel()
                    # let running ones finish
                    for f in list(inflight):
                        try:
                            f.result()
                            finish(inflight.pop(f), None)
                        except Exception:  # noqa: BLE001
                            inflight.pop(f, None)
                    break
