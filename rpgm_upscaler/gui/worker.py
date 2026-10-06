"""Background threads: analyze/plan, run, and single-image preview. Core code never touches Qt."""
from __future__ import annotations

from PySide6.QtCore import QThread, Signal

from ..core import categories
from ..core.imageops import load_image, upscale_image
from ..core.planner import build_plan
from ..core.project import load_project
from ..core.runner import Runner
from ..core.settings import Options


class PlanWorker(QThread):
    done = Signal(object)      # Plan
    failed = Signal(str)

    def __init__(self, path: str, opts: Options, parent=None):
        super().__init__(parent)
        self.path, self.opts = path, opts

    def run(self) -> None:
        try:
            self.done.emit(build_plan(load_project(self.path), self.opts))
        except Exception as e:  # noqa: BLE001
            self.failed.emit(str(e))


class RunWorker(QThread):
    progress = Signal(int, int, str)
    log = Signal(str, str)
    finished_run = Signal(object)   # RunResult
    failed = Signal(str)

    def __init__(self, path: str, out: str, opts: Options, parent=None):
        super().__init__(parent)
        self.path, self.out, self.opts = path, out, opts
        self.runner: Runner | None = None

    def cancel(self) -> None:
        if self.runner:
            self.runner.cancel()

    def run(self) -> None:
        try:
            plan = build_plan(load_project(self.path), self.opts)
            for w in plan.warnings:
                self.log.emit("warning", w)
            self.runner = Runner(plan, self.out, self.progress.emit, self.log.emit)
            self.finished_run.emit(self.runner.run())
        except Exception as e:  # noqa: BLE001
            self.failed.emit(str(e))


class PreviewWorker(QThread):
    done = Signal(object, object)   # before, after (PIL images)
    failed = Signal(str)

    def __init__(self, plan, job, parent=None):
        super().__init__(parent)
        self.plan, self.job = plan, job

    def run(self) -> None:
        try:
            from ..core.runner import _engine
            p, j = self.plan, self.job
            before = load_image(p.project.base / j.src, p.project.key)
            after = upscale_image(before, _engine(p.options), j.target, p.scale.n, j.cell, j.resampler)
            self.done.emit(before, after)
        except Exception as e:  # noqa: BLE001
            self.failed.emit(str(e))
