"""Background threads: analyze/plan, run, and single-image preview. Core code never touches Qt."""
from __future__ import annotations

import threading

from PySide6.QtCore import QThread, Signal

from ..core.imageops import load_image, upscale_image
from ..core.planner import build_plan
from ..core.project import load_project
from ..core.runner import Runner, RunResult
from ..core.settings import Options
from ..detect import detect_engine
from ..rgss.pipeline import PrepareCancelled


def _plan_for(path: str, opts: Options, mode: str, cancel=None):
    """(plan, prepared) for any supported engine; `prepared` owns a temporary extraction (VX/Ace archives)."""
    info = detect_engine(path)
    if info is not None and info.engine in ("ACE", "VX", "XP"):
        from ..rgss import pipeline
        from ..rgss.planner import build_plan as rgss_plan
        prepared = pipeline.prepare(path, cancel=cancel)
        try:
            return rgss_plan(prepared.project, opts, mode), prepared
        except Exception:
            prepared.cleanup()
            raise
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
        self._cancelled = False          # a cancel that arrives while the plan is still being built must not be lost
        self._cancel_event = threading.Event()      # also stops the unpacking of an encrypted archive

    def cancel(self) -> None:
        self._cancelled = True
        self._cancel_event.set()
        if self.runner:
            self.runner.cancel()

    def run(self) -> None:
        prepared = None
        try:
            plan, prepared = _plan_for(self.path, self.opts, self.mode, self._cancel_event)
            for w in plan.warnings:
                self.log.emit("warning", w)
            self.runner = Runner(plan, self.out, self.progress.emit, self.log.emit)
            if self._cancelled:
                self.runner.cancel()
            self.finished_run.emit(self.runner.run())
        except PrepareCancelled:
            self.finished_run.emit(RunResult(cancelled=True))
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


class SaveLoadWorker(QThread):
    """Opens a save file and its database names off the UI thread (a large MV save takes seconds to decompress)."""
    loaded = Signal(object, object, int)      # doc, names, request number
    failed = Signal(str, int)

    def __init__(self, path, seq: int, parent=None):
        super().__init__(parent)
        self.path, self.seq = path, seq

    def run(self) -> None:
        from ..saves.database import load_names
        from ..saves.files import open_save
        from ..saves.model import SaveError
        try:
            doc = open_save(self.path)
            self.loaded.emit(doc, load_names(self.path), self.seq)
        except SaveError as e:
            self.failed.emit(str(e), self.seq)
        except Exception as e:  # noqa: BLE001  (a malformed save must not kill the thread silently)
            self.failed.emit(f"{type(e).__name__}: {e}", self.seq)


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

    def __init__(self, source: str | None = None, parent=None, model: str = "argos", entry: dict | None = None):
        super().__init__(parent)
        self.source = source            # None = download, else path to a .argosmodel / extracted folder
        self.model, self.entry = model, entry      # which model to download, and the link a Fetch found for it (or None)
        import threading
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    def run(self) -> None:
        from ..translate import argos
        try:
            if self.source:
                path = argos.import_package(self.source)
            elif self.model == "nllb":
                from ..translate import nllb
                e = self.entry
                path = (nllb.install(self.progress.emit, self._cancel, base_url=e["url"], files=e["files"]) if e
                        else nllb.install(self.progress.emit, self._cancel))
            else:
                path = argos.install(progress=self.progress.emit, cancel=self._cancel,
                                     entry=self.entry["entry"] if self.entry else None)
            self.done.emit(str(path))
        except Exception as e:  # noqa: BLE001
            self.failed.emit(str(e))


class FetchWorker(QThread):
    """Looks up the download link of the newest version of a model (network, but no download)."""
    done = Signal(str, object)      # model key, entry
    failed = Signal(str)

    def __init__(self, model: str, parent=None):
        super().__init__(parent)
        self.model = model

    def run(self) -> None:
        from ..translate import argos, nllb
        try:
            self.done.emit(self.model, (nllb if self.model == "nllb" else argos).fetch_entry())
        except Exception as e:  # noqa: BLE001
            self.failed.emit(str(e))


class DepsWorker(QThread):
    """pip-installs the translation packages, into a private venv when the system Python is externally managed."""
    line = Signal(str)
    done = Signal(str)
    failed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    def run(self) -> None:
        from ..translate import pyenv
        try:
            self.done.emit(pyenv.install_packages(pyenv.TRANSLATE_PACKAGES, self.line.emit, self._cancel))
        except Exception as e:  # noqa: BLE001
            self.failed.emit(str(e))


class GameTranslateWorker(QThread):
    """Translates a whole game into a new folder (see translate/gamerun.py)."""
    progress = Signal(str, int, int)        # stage, done, total
    finished_run = Signal(object)           # gamerun.Result
    failed = Signal(str)

    def __init__(self, translator, path: str, out: str, opts, parent=None):
        super().__init__(parent)
        import threading
        self.translator, self.path, self.out, self.opts = translator, path, out, opts
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    def run(self) -> None:
        from ..translate.gamerun import translate_game
        try:
            self.finished_run.emit(translate_game(self.path, self.out, self.translator, self.opts,
                                                  progress=self.progress.emit, cancel=self._cancel))
        except Exception as e:  # noqa: BLE001
            self.failed.emit(str(e))
