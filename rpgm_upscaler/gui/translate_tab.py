"""Translation tab: offline model status, install/import, overrides and a try-it box."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QFileDialog, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox, QProgressBar, QPushButton,
                               QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from .worker import ModelWorker, TranslateWorker


class TranslateTab(QWidget):
    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self._model_worker: ModelWorker | None = None
        self._try_worker: TranslateWorker | None = None
        self._loading = False
        v = QVBoxLayout(self)
        self.status = QLabel(); self.status.setWordWrap(True); self.status.setTextFormat(Qt.PlainText)
        v.addWidget(self.status)
        row = QHBoxLayout()
        self.install_btn = QPushButton("Install translation model (download once)")
        self.import_btn = QPushButton("Import .argosmodel file…")
        self.cancel_btn = QPushButton("Cancel"); self.cancel_btn.setEnabled(False)
        self.clear_btn = QPushButton("Clear cache")
        for w in (self.install_btn, self.import_btn, self.cancel_btn, self.clear_btn):
            row.addWidget(w)
        row.addStretch(1)
        v.addLayout(row)
        self.progress = QProgressBar(); self.progress.setVisible(False)
        v.addWidget(self.progress)
        note = QLabel("Without the model, names are translated with the built-in RPG glossary and romaji (always offline). "
                      "After the one-time download nothing touches the network.")
        note.setWordWrap(True)
        v.addWidget(note)

        v.addWidget(QLabel("Try it:"))
        trow = QHBoxLayout()
        self.try_in = QLineEdit(); self.try_in.setPlaceholderText("日本語のテキスト…")
        self.try_btn = QPushButton("Translate")
        trow.addWidget(self.try_in, 1); trow.addWidget(self.try_btn)
        v.addLayout(trow)
        self.try_out = QLabel(""); self.try_out.setTextInteractionFlags(Qt.TextSelectableByMouse)
        v.addWidget(self.try_out)

        v.addWidget(QLabel("Your overrides (exact text → English, always win):"))
        self.table = QTableWidget(0, 2); self.table.setHorizontalHeaderLabels(["Japanese", "English"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        v.addWidget(self.table, 1)
        brow = QHBoxLayout()
        self.add_row = QPushButton("Add row"); self.del_row = QPushButton("Delete selected")
        brow.addWidget(self.add_row); brow.addWidget(self.del_row); brow.addStretch(1)
        v.addLayout(brow)

        self.install_btn.clicked.connect(lambda: self._start_model(None))
        self.import_btn.clicked.connect(self._import_dialog)
        self.cancel_btn.clicked.connect(lambda: self._model_worker and self._model_worker.cancel())
        self.clear_btn.clicked.connect(self._clear_cache)
        self.try_btn.clicked.connect(self._try)
        self.try_in.returnPressed.connect(self._try)
        self.add_row.clicked.connect(lambda: self.table.insertRow(self.table.rowCount()))
        self.del_row.clicked.connect(self._delete_row)
        self.table.itemChanged.connect(self._override_edited)
        self.refresh()

    # ---- status ------------------------------------------------------------------------------------
    def refresh(self) -> None:
        t = self.ctx.translator
        st = t.status()
        lines = [f"Neural model: {st['model']}" + (f"  ({st['model_path']})" if st["model_path"] else ""),
                 f"Glossary: {st['glossary_size']} terms   Overrides: {st['overrides']}"]
        if st["model"] == "deps-missing":
            lines.append(st["deps_hint"])
        if st["error"]:
            lines.append(f"Model error: {st['error']}")
        if st["model"] == "missing" and not st["deps"]:
            lines.append("To use the neural model you also need: " + st["deps_hint"].split(": ", 1)[-1])
        self.status.setText("\n".join(lines))
        self._loading = True
        self.table.setRowCount(0)
        for ja, en in sorted(t.overrides.items()):
            r = self.table.rowCount(); self.table.insertRow(r)
            self.table.setItem(r, 0, QTableWidgetItem(ja)); self.table.setItem(r, 1, QTableWidgetItem(en))
        self._loading = False

    # ---- model install -----------------------------------------------------------------------------------
    def _busy(self, busy: bool) -> None:
        for w in (self.install_btn, self.import_btn):
            w.setEnabled(not busy)
        self.cancel_btn.setEnabled(busy and self._model_worker is not None and self._model_worker.source is None)
        self.progress.setVisible(busy)
        self.progress.setRange(0, 0 if busy else 1)

    def _import_dialog(self) -> None:
        f, _ = QFileDialog.getOpenFileName(self, "Import an Argos model", "", "Argos models (*.argosmodel);;All files (*)")
        if f:
            self._start_model(f)

    def _start_model(self, source: str | None) -> None:
        if self._model_worker is not None and self._model_worker.isRunning():
            return
        self._model_worker = ModelWorker(source, self)
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

    def _model_failed(self, msg: str) -> None:
        self._busy(False)
        self.ctx.log.emit("error", f"translation model: {msg}")
        QMessageBox.warning(self, "Translation model", msg)

    def _clear_cache(self) -> None:
        self.ctx.translator.cache.clear()
        self.ctx.log.emit("info", "translation cache cleared")

    # ---- try it / overrides --------------------------------------------------------------------------------
    def _try(self) -> None:
        text = self.try_in.text().strip()
        if not text:
            return
        self.try_out.setText("translating…")
        self._try_worker = TranslateWorker(self.ctx.translator, [text], parent=self)
        self._try_worker.chunk.connect(lambda m: self.try_out.setText(m.get(text, "(could not translate: the text is unchanged)")))
        self._try_worker.finished_all.connect(lambda: self.try_out.text() == "translating…" and
                                              self.try_out.setText("(could not translate: add an override or install the model)"))
        self._try_worker.start()

    def _override_edited(self, item: QTableWidgetItem) -> None:
        if self._loading:
            return
        ja = self.table.item(item.row(), 0)
        en = self.table.item(item.row(), 1)
        if ja is None or not ja.text().strip():
            return
        self.ctx.translator.set_override(ja.text().strip(), en.text().strip() if en else "")

    def _delete_row(self) -> None:
        for idx in sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True):
            ja = self.table.item(idx, 0)
            if ja is not None and ja.text().strip():
                self.ctx.translator.set_override(ja.text().strip(), None)
            self.table.removeRow(idx)

    def shutdown(self) -> bool:
        for w in (self._model_worker, self._try_worker):
            if w is not None and w.isRunning():
                if isinstance(w, ModelWorker):
                    w.cancel()
                if not w.wait(20000):                     # never destroy a running QThread: refuse to close instead
                    return False
        return True
