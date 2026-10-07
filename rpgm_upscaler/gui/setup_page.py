"""Setup page: everything that installs something (Python packages, the offline translation model) in one place."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QFileDialog, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QProgressBar,
                               QPushButton, QVBoxLayout, QWidget)

from ..translate import pyenv
from .worker import DepsWorker, FetchWorker, ModelWorker


class SetupPage(QWidget):
    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self._model_worker: ModelWorker | None = None
        self._deps_worker: DepsWorker | None = None
        self._fetch_worker: FetchWorker | None = None
        self._entries: dict[str, dict] = {}         # model key -> the link a Fetch found
        v = QVBoxLayout(self)
        v.addWidget(QLabel("<h2>Setup</h2>Optional components. Nothing here is needed for upscaling with the built-in engines."))

        env = QGroupBox("Python environment")
        el = QVBoxLayout(env)
        row = QHBoxLayout()
        self.env_edit = QLineEdit(); self.env_edit.setAcceptDrops(False)
        self.env_edit.setPlaceholderText("Default: the hub's own environment (leave empty)")
        self.env_edit.setToolTip("A virtualenv folder of your own (the one containing bin/python). Packages are installed into it "
                                 "and loaded from it. An empty or missing folder is created as a new virtualenv.")
        self.env_browse = QPushButton("Browse…"); self.env_default = QPushButton("Use default")
        row.addWidget(self.env_edit, 1); row.addWidget(self.env_browse); row.addWidget(self.env_default)
        el.addLayout(row)
        self.env_label = QLabel(); self.env_label.setWordWrap(True); self.env_label.setTextFormat(Qt.PlainText)
        el.addWidget(self.env_label)
        v.addWidget(env)

        pk = QGroupBox("Python packages")
        pl = QVBoxLayout(pk)
        prow = QHBoxLayout()
        self.deps_btn = QPushButton("Install translation packages")
        self.deps_btn.setToolTip("ctranslate2 and sentencepiece: needed to run the offline translation model.")
        self.ocr_btn = QPushButton("Install image translation packages")
        self.ocr_btn.setToolTip("OpenCV (opencv-python-headless). Tesseract itself is not a pip package: install it with your "
                                "package manager.")
        prow.addWidget(self.deps_btn); prow.addWidget(self.ocr_btn); prow.addStretch(1)
        pl.addLayout(prow)
        self.pkg_label = QLabel(); self.pkg_label.setWordWrap(True); self.pkg_label.setTextFormat(Qt.PlainText)
        pl.addWidget(self.pkg_label)
        v.addWidget(pk)

        md = QGroupBox("Offline translation model")
        ml = QVBoxLayout(md)
        mrow = QHBoxLayout()
        self.model_box = QComboBox()
        self.model_box.addItem("NLLB-200 600M: best quality, about 620 MB, CC-BY-NC", "nllb")
        self.model_box.addItem("Argos ja→en: small and fast, lower quality", "argos")
        self.fetch_btn = QPushButton("Fetch latest")
        self.fetch_btn.setToolTip("Looks up the download link of the newest version of the selected model. Downloads nothing.")
        self.install_btn = QPushButton("Install selected model")
        self.import_btn = QPushButton("Import .argosmodel file…")
        mrow.addWidget(self.model_box, 1); mrow.addWidget(self.fetch_btn); mrow.addWidget(self.install_btn); mrow.addWidget(self.import_btn)
        ml.addLayout(mrow)
        self.fetch_info = QLabel(""); self.fetch_info.setWordWrap(True); self.fetch_info.setTextFormat(Qt.PlainText)
        self.fetch_info.setTextInteractionFlags(Qt.TextSelectableByMouse)
        ml.addWidget(self.fetch_info)
        self.model_label = QLabel(); self.model_label.setWordWrap(True); self.model_label.setTextFormat(Qt.PlainText)
        ml.addWidget(self.model_label)
        v.addWidget(md)

        crow = QHBoxLayout()
        self.cancel_btn = QPushButton("Cancel"); self.cancel_btn.setEnabled(False)
        self.progress = QProgressBar(); self.progress.setVisible(False)
        crow.addWidget(self.progress, 1); crow.addWidget(self.cancel_btn)
        v.addLayout(crow)
        note = QLabel("Progress is shown in the Log page. After the one-time download nothing touches the network.")
        note.setWordWrap(True)
        v.addWidget(note)
        v.addStretch(1)

        self.env_browse.clicked.connect(self._browse_env)
        self.env_default.clicked.connect(lambda: (self.env_edit.setText(""), self._apply_env()))
        self.env_edit.editingFinished.connect(self._apply_env)
        self.deps_btn.clicked.connect(lambda: self._start_deps(pyenv.TRANSLATE_PACKAGES, "translation packages"))
        self.ocr_btn.clicked.connect(lambda: self._start_deps(pyenv.OCR_PACKAGES, "image translation packages"))
        self.fetch_btn.clicked.connect(self._start_fetch)
        self.model_box.currentIndexChanged.connect(self._show_entry)
        self.install_btn.clicked.connect(lambda: self._start_model(None))
        self.import_btn.clicked.connect(self._import_dialog)
        self.cancel_btn.clicked.connect(self._cancel)
        c = pyenv.custom_env()
        self.env_edit.setText(str(c) if c else "")
        self.refresh()

    # ---- environment ---------------------------------------------------------------------------------
    def _browse_env(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Select your virtual environment folder", self.env_edit.text() or str(Path.home()))
        if d:
            self.env_edit.setText(d)
            self._apply_env()

    def _apply_env(self) -> None:
        text = self.env_edit.text().strip()
        if text == (str(pyenv.custom_env() or "")):
            return
        pyenv.set_custom_env(text)
        added = pyenv.activate()
        self.ctx.log.emit("info", f"python environment: {pyenv.describe_target()}")
        if added:
            self.ctx.translator.reload_backend()
        self.refresh()
        self.ctx.setup_changed.emit()

    def refresh(self) -> None:
        self.env_label.setText("Packages are installed into and loaded from: " + pyenv.describe_target())
        try:
            st = self.ctx.translator.status()
        except Exception as e:  # noqa: BLE001
            self.model_label.setText(f"Translation model: {e}")
            st = {}
        lines = []
        if st:
            lines.append(f"Translation model: {st.get('model')}" + (f"  ({st['model_path']})" if st.get("model_path") else ""))
            lines.append("Translation packages: " + ("installed" if st.get("deps") else "missing"))
        from ..translate.ocr import ocr_available
        ok, why = ocr_available(("jpn",))
        lines.append("Image translation: " + ("Tesseract and OpenCV found" if ok else why))
        self.pkg_label.setText("\n".join(lines))
        self.model_label.setText(st.get("error") or "")

    def showEvent(self, ev) -> None:  # noqa: N802
        super().showEvent(ev)
        self.refresh()

    # ---- workers ---------------------------------------------------------------------------------
    def _running(self) -> bool:
        return any(w is not None and w.isRunning() for w in (self._deps_worker, self._model_worker))

    def _busy(self, busy: bool) -> None:
        for w in (self.deps_btn, self.ocr_btn, self.install_btn, self.import_btn, self.fetch_btn, self.model_box,
                  self.env_edit, self.env_browse, self.env_default):
            w.setEnabled(not busy)
        self.cancel_btn.setEnabled(busy and (self._deps_worker is not None and self._deps_worker.isRunning() or
                                             (self._model_worker is not None and self._model_worker.source is None)))
        self.progress.setVisible(busy)
        self.progress.setRange(0, 0 if busy else 1)

    def _cancel(self) -> None:
        for w in (self._deps_worker, self._model_worker):
            if w is not None and w.isRunning():
                w.cancel()

    def _start_deps(self, packages: list[str], what: str) -> None:
        if self._running():
            return
        self._apply_env()
        self._deps_worker = DepsWorker(self, packages)
        self._deps_worker.line.connect(lambda m: self.ctx.log.emit("info", m))
        self._deps_worker.done.connect(lambda where: self._deps_done(what, where))
        self._deps_worker.failed.connect(lambda msg: self._deps_failed(what, msg))
        self._busy(True)
        self._deps_worker.start()

    def _deps_done(self, what: str, where: str) -> None:
        self._busy(False)
        self.ctx.translator.reload_backend()
        self.ctx.log.emit("info", f"{what} installed in {where}")
        self.refresh()
        self.ctx.setup_changed.emit()

    def _deps_failed(self, what: str, msg: str) -> None:
        self._busy(False)
        self.ctx.log.emit("error", f"{what}: {msg}")
        QMessageBox.warning(self, "Python packages", msg)

    def _start_fetch(self) -> None:
        if self._fetch_worker is not None and self._fetch_worker.isRunning():
            return
        self.fetch_info.setText("Looking up the latest version…")
        self.fetch_btn.setEnabled(False)
        self._fetch_worker = FetchWorker(self.model_box.currentData(), self)
        self._fetch_worker.done.connect(self._fetch_done)
        self._fetch_worker.failed.connect(self._fetch_failed)
        self._fetch_worker.start()

    def _fetch_done(self, key: str, entry: dict) -> None:
        self.fetch_btn.setEnabled(True)
        self._entries[key] = entry
        self._show_entry()

    def _fetch_failed(self, msg: str) -> None:
        self.fetch_btn.setEnabled(True)
        self.fetch_info.setText(f"Fetch failed: {msg}")
        self.ctx.log.emit("error", f"model lookup: {msg}")

    def _show_entry(self) -> None:
        e = self._entries.get(self.model_box.currentData())
        if e is None:
            self.fetch_info.setText("")
            return
        size = f", {e['size'] / 1e6:.0f} MB" if e["size"] else ""
        self.fetch_info.setText(f"Latest {e['name']}: version {e['version']}{size}\n{e['url']}")

    def _import_dialog(self) -> None:
        f, _ = QFileDialog.getOpenFileName(self, "Import an Argos model", "", "Argos models (*.argosmodel);;All files (*)")
        if f:
            self._start_model(f)

    def _start_model(self, source: str | None) -> None:
        if self._running():
            return
        key = self.model_box.currentData()
        self._model_worker = ModelWorker(source, self, key, self._entries.get(key))
        self._model_worker.progress.connect(self._model_progress)
        self._model_worker.done.connect(self._model_done)
        self._model_worker.failed.connect(self._model_failed)
        self._busy(True)
        self._model_worker.start()

    def _model_progress(self, done: int, total: int) -> None:
        if total:
            self.progress.setRange(0, total); self.progress.setValue(done)

    def _model_done(self, path: str) -> None:
        self._busy(False)
        self.ctx.translator.reload_backend()
        self.ctx.log.emit("info", f"translation model installed at {path}")
        self.refresh()
        self.ctx.setup_changed.emit()

    def _model_failed(self, msg: str) -> None:
        self._busy(False)
        self.ctx.log.emit("error", f"translation model: {msg}")
        QMessageBox.warning(self, "Translation model", msg)

    def shutdown(self) -> bool:
        for w in (self._model_worker, self._deps_worker, self._fetch_worker):
            if w is not None and w.isRunning():
                if isinstance(w, (ModelWorker, DepsWorker)):
                    w.cancel()
                if not w.wait(20000):                     # never destroy a running QThread: refuse to close instead
                    return False
        return True
