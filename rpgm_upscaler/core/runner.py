"""Execute a Plan: copy tree, process images/videos, apply engine patches. Cancellable, resumable."""
from __future__ import annotations

import json
import logging
import multiprocessing
import os
import shutil
import time
import threading
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import video
from .engines import NCNN_ENGINES, make_engine
from .imageops import enlarge_inputs, load_image, save_image, upscale_image
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
        for t in self.out.rglob("*.tmp"):                # half-written images from a run that was killed
            if Path(t.name[:-4]).suffix.lower() in (".png", ".png_", ".rpgmvp", ".jpg", ".jpeg", ".bmp", ".webp"):
                t.unlink(missing_ok=True)
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

        last_save = [time.monotonic()]

        def finish(job: Job, err: Exception | None) -> None:
            nonlocal done
            done += 1
            if err is None:
                result.ok += 1
                manifest[job.src] = {"opts": digest, "sig": _file_sig(base / job.src)}
                self._log("info", f"ok   {job.src}")
                if time.monotonic() - last_save[0] > 10:      # resume must survive a kill or power loss, not only a clean exit
                    self._save_manifest(manifest)
                    last_save[0] = time.monotonic()
            else:
                result.failed.append((job.src, str(err)))
                self._log("error", f"FAIL {job.src}: {err}")
                if not job.keep_source:                       # never leave the game without the asset it asks for
                    try:
                        keep = self.out / job.src
                        keep.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(base / job.src, keep)
                        self._log("warning", f"kept the original (not upscaled): {job.src}")
                    except OSError as e2:
                        self._log("error", f"could not keep the original of {job.src}: {e2}")
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

    PREFETCH_PIXELS = 6_000_000          # AI engine: how many source pixels are held and enlarged per batch
    PREFETCH_COUNT = 24

    @staticmethod
    def _available_memory() -> int | None:
        try:
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith("MemAvailable:"):
                        return int(line.split()[1]) * 1024
        except (OSError, ValueError, IndexError):
            pass
        try:
            return os.sysconf("SC_AVPHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
        except (ValueError, OSError, AttributeError):
            return None

    def _budget_workers(self, jobs: list[Job], wanted: int, n: float) -> int:
        """A big parallax or title image can take a gigabyte to resample, so the worker count is cut until the largest
        jobs fit in the memory that is free (about 60% of it), rather than letting the OS kill a worker."""
        avail = self._available_memory()
        if not avail or wanted <= 1:
            return wanted
        biggest = max((j.cost for j in jobs), default=1)
        per_job = biggest * max(n, 1.0) ** 2 * 4 * 8          # RGBA, a handful of working copies (resampler, atlas, output)
        fit = int(avail * 0.6 // max(per_job, 1))
        if fit < wanted:
            self._log("info", f"Using {max(1, fit)} worker(s) instead of {wanted}: the largest image needs about {per_job / 2**20:.0f} MB")
        return max(1, min(wanted, fit))

    def _prefetch_ai(self, chunk: list[Job], base: Path, n: float, key) -> None:
        """Run a chunk of images through the AI binary in one process; process_image then finds the results waiting."""
        engine = _engine(self.plan.options)
        if not hasattr(engine, "prefetch"):
            return
        items = []
        for j in chunk:
            if j.passthrough:
                continue
            try:
                items += enlarge_inputs(load_image(Path(base, j.src), key), engine, j.target, n, j.cell, j.resampler)
            except Exception:  # noqa: BLE001  (the real run reports an unreadable image)
                continue
        if items:
            try:
                engine.prefetch(items)
            except Exception as e:  # noqa: BLE001
                self._log("warning", f"batch upscaling failed ({e}); falling back to one image at a time")

    def _run_serial(self, jobs: list[Job], args: tuple, finish, batch: bool = False) -> None:
        base, n, key = Path(args[0]), args[3], args[4]
        i = 0
        while i < len(jobs):
            if self.cancel_event.is_set():
                return
            chunk = [jobs[i]]
            if batch:                                           # take jobs until the pixel or count budget is used
                px = jobs[i].cost
                while i + len(chunk) < len(jobs) and len(chunk) < self.PREFETCH_COUNT and px + jobs[i + len(chunk)].cost <= self.PREFETCH_PIXELS:
                    px += jobs[i + len(chunk)].cost
                    chunk.append(jobs[i + len(chunk)])
                self._prefetch_ai(chunk, base, n, key)
            for j in chunk:
                if self.cancel_event.is_set():
                    return
                try:
                    process_image(j, *args)
                    finish(j, None)
                except Exception as e:  # noqa: BLE001
                    finish(j, e)
            i += len(chunk)

    def _retry_isolated(self, jobs: list[Job], args: tuple, finish) -> None:
        """Jobs whose worker process died are retried one at a time, each in a fresh process: if one of them is what kills a
        worker (out of memory, a crashing decoder), only that image fails and the original is kept."""
        ctx = multiprocessing.get_context("spawn")
        for j in jobs:
            if self.cancel_event.is_set():
                return
            try:
                with ProcessPoolExecutor(max_workers=1, mp_context=ctx) as pool:
                    pool.submit(process_image, j, *args).result()
                finish(j, None)
            except BrokenProcessPool:
                finish(j, RuntimeError("the worker process died (probably out of memory) while processing this image"))
            except Exception as e:  # noqa: BLE001
                finish(j, e)

    def _run_images(self, jobs: list[Job], base: Path, n: float, finish) -> None:
        if not jobs:
            return
        opts, key = self.plan.options, self.plan.project.key
        ai = opts.engine in NCNN_ENGINES
        workers = 1 if ai else self._budget_workers(jobs, opts.workers or os.cpu_count() or 1, n)
        workers = max(1, min(workers, len(jobs)))
        args = (str(base), str(self.out), opts, n, key)
        if workers == 1:
            self._run_serial(jobs, args, finish, batch=ai)
            return
        ctx = multiprocessing.get_context("spawn")
        retry: list[Job] = []
        with ProcessPoolExecutor(max_workers=workers, mp_context=ctx) as pool:
            it = iter(jobs)
            inflight: dict = {}
            broken = False
            while not broken:
                while len(inflight) < workers * 2 and not self.cancel_event.is_set():
                    j = next(it, None)
                    if j is None:
                        break
                    try:
                        inflight[pool.submit(process_image, j, *args)] = j
                    except BrokenProcessPool:
                        retry.append(j)
                        broken = True
                        break
                if not inflight:
                    break
                finished, _ = wait(inflight, return_when=FIRST_COMPLETED, timeout=0.5)
                for f in finished:
                    j = inflight.pop(f)
                    exc = f.exception()
                    if isinstance(exc, BrokenProcessPool):
                        retry.append(j)
                        broken = True
                    else:
                        finish(j, exc)
                if self.cancel_event.is_set():
                    for f in list(inflight):
                        f.cancel()
                    # let running ones finish
                    for f in list(inflight):
                        try:
                            f.result()
                            finish(inflight.pop(f), None)
                        except BrokenProcessPool:
                            retry.append(inflight.pop(f))
                        except Exception:  # noqa: BLE001
                            inflight.pop(f, None)
                    break
            if broken:
                retry += list(inflight.values()) + list(it)
        if retry and not self.cancel_event.is_set():
            self._log("warning", f"A worker process died; retrying {len(retry)} image(s) one at a time")
            self._retry_isolated(retry, args, finish)
