"""Translation tab: offline model status, overrides and a try-it box. Installing lives on the Setup page."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPushButton,
                               QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from .worker import TranslateWorker


class TranslateTab(QWidget):
    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self._try_worker: TranslateWorker | None = None
        self._loading = False
        v = QVBoxLayout(self)
        self.status = QLabel(); self.status.setWordWrap(True); self.status.setTextFormat(Qt.PlainText)
        v.addWidget(self.status)
        row = QHBoxLayout()
        self.clear_btn = QPushButton("Clear cache")
        row.addWidget(self.clear_btn); row.addStretch(1)
        v.addLayout(row)
        note = QLabel("Without the model, names are translated with the built-in RPG glossary and romaji (always offline). "
                      "Models and Python packages are installed on the Setup page.")
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
            lines.append("To use the neural model you also need: " + st["deps_hint"].split(": ", 1)[-1] +
                         "\n(install them on the Setup page)")
        self.status.setText("\n".join(lines))
        self._loading = True
        self.table.setRowCount(0)
        for ja, en in sorted(t.overrides.items()):
            r = self.table.rowCount(); self.table.insertRow(r)
            self.table.setItem(r, 0, QTableWidgetItem(ja)); self.table.setItem(r, 1, QTableWidgetItem(en))
        self._loading = False

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
        w = self._try_worker
        return w is None or not w.isRunning() or w.wait(20000)
