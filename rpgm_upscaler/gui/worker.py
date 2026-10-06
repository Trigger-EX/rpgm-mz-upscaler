"""Background threads: analyze/plan, run, and single-image preview. Core code never touches Qt."""
from __future__ import annotations

from PySide6.QtCore import QThread, Signal

from ..core.imageops import load_image, upscale_image
from ..core.planner import build_plan
from ..core.project import load_project
from ..core.runner import Runner
from ..core.settings import Options
from ..detect import detect_engine


def _plan_for(path: str, opts: Options, mode: str):
    """(plan, prepared) for any supported engine; `prepared` owns a temporary extraction (VX/Ace archives)."""
    info = detect_engine(path)
    if info is not None and info.engine in ("ACE", "VX"):
        from ..rgss import pipeline
        from ..rgss.planner import build_plan as rgss_plan
        prepared = pipeline.prepare(path)
        try:
            return rgss_plan(prepared.project, opts, mode), prepared
        except Exception:
            prepared.cleanup()
            raise
    if info is not None and info.engine == "XP":
        raise ValueError("RPG Maker XP is detected but not supported yet.")
    return build_plan(load_project(path), opts), None


class PlanWorker(QThread):
    done = Signal(object, object)      # Plan, Prepared | None
    failed = Signal(str)

    def __init__(self, path: str, opts: Options, mode: str = "hires", parent=None):
        super().__init__(parent)
        self.path, self.opts, self.mode = path, opts, mode

    def run(self) -> None:
        try:
            plan, prepared = _plan_for(self.path, self.opts, self.mode)
            self.done.emit(plan, prepared)
        except Exception as e:  # noqa: BLE001
            self.failed.emit(str(e))


class RunWorker(QThread):
    progress = Signal(int, int, str)
    log = Signal(str, str)
    finished_run = Signal(object)   # RunResult
    failed = Signal(str)

    def __init__(self, path: str, out: str, opts: Options, mode: str = "hires", parent=None):
        super().__init__(parent)
        self.path, self.out, self.opts, self.mode = path, out, opts, mode
        self.runner: Runner | None = None

    def cancel(self) -> None:
        if self.runner:
            self.runner.cancel()

    def run(self) -> None:
        prepared = None
        try:
            plan, prepared = _plan_for(self.path, self.opts, self.mode)
            for w in plan.warnings:
                self.log.emit("warning", w)
            self.runner = Runner(plan, self.out, self.progress.emit, self.log.emit)
            self.finished_run.emit(self.runner.run())
        except Exception as e:  # noqa: BLE001
            self.failed.emit(str(e))
        finally:
            if prepared is not None:
                prepared.cleanup()


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


class TranslateWorker(QThread):
    """Translates labels in chunks so the UI can fill its English column as results arrive."""
    chunk = Signal(object)          # {japanese: english}
    finished_all = Signal()

    def __init__(self, translator, texts: list[str], chunk_size: int = 24, parent=None):
        super().__init__(parent)
        self.translator, self.texts, self.chunk_size = translator, list(texts), chunk_size
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        try:
            for i in range(0, len(self.texts), self.chunk_size):
                if self._stop:
                    break
                part = self.texts[i:i + self.chunk_size]
                res = self.translator.translate_many(part)
                self.chunk.emit({t: r.text for t, r in zip(part, res) if r.translated})
        finally:
            self.finished_all.emit()


class ModelWorker(QThread):
    """Downloads or imports the offline translation model."""
    progress = Signal(int, int)
    done = Signal(str)
    failed = Signal(str)

    def __init__(self, source: str | None = None, parent=None):
        super().__init__(parent)
        self.source = source            # None = download, else path to a .argosmodel / extracted folder
        import threading
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    def run(self) -> None:
        from ..translate import argos
        try:
            if self.source:
                path = argos.import_package(self.source)
            else:
                path = argos.install(progress=self.progress.emit, cancel=self._cancel)
            self.done.emit(str(path))
        except Exception as e:  # noqa: BLE001
            self.failed.emit(str(e))
