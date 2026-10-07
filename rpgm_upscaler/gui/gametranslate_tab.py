"""Translate a whole game (dialogue, database, system and plugin text; optionally text inside images) into a new folder."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel,
                               QLineEdit, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QSpinBox, QVBoxLayout, QWidget)

from .worker import GameTranslateWorker

_STAGES = {"copy": "Copying the game", "scan": "Reading game data", "translate": "Translating", "images": "Scanning images"}


class GameTranslateTab(QWidget):
    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self._worker: GameTranslateWorker | None = None
        self._out_dir: Path | None = None
        v = QVBoxLayout(self)
        self.game_label = QLabel("Open a game on the Project page first."); self.game_label.setTextFormat(Qt.PlainText)
        self.game_label.setWordWrap(True)
        v.addWidget(self.game_label)
        self.model_label = QLabel(); self.model_label.setWordWrap(True); self.model_label.setTextFormat(Qt.PlainText)
        v.addWidget(self.model_label)

        row = QHBoxLayout()
        self.out_edit = QLineEdit(); self.out_edit.setPlaceholderText("Folder for the translated copy (the original is never changed)")
        self.out_browse = QPushButton("Browse…")
        row.addWidget(QLabel("Output:")); row.addWidget(self.out_edit, 1); row.addWidget(self.out_browse)
        v.addLayout(row)

        what = QGroupBox("What to translate")
        wl = QVBoxLayout(what)
        self.cb_dialogue = QCheckBox("Event text (messages, choices, scrolling text)")
        self.cb_database = QCheckBox("Database (actors, items, skills, states, enemies, classes)")
        self.cb_system = QCheckBox("System text (game title, terms, map names)")
        self.cb_plugins = QCheckBox("Visible plugin parameters (MV/MZ; file names and code are never touched)")
        self.cb_keep = QCheckBox("Keep names that scripts or plugins compare against (recommended)")
        for cb in (self.cb_dialogue, self.cb_database, self.cb_system, self.cb_plugins, self.cb_keep):
            cb.setChecked(True)
            wl.addWidget(cb)
        v.addWidget(what)

        img = QGroupBox("Images (computer vision)")
        il = QFormLayout(img)
        self.cb_ocr = QCheckBox("Find Japanese text in images, erase it and overlay the English")
        il.addRow(self.cb_ocr)
        self.scope = QComboBox(); self.scope.addItem("Pictures, titles and system images (recommended)", "likely"); self.scope.addItem("Every image file", "all")
        self.conf = QDoubleSpinBox(); self.conf.setRange(10, 99); self.conf.setValue(55); self.conf.setSuffix(" % minimum confidence")
        self.font_edit = QLineEdit(); self.font_edit.setPlaceholderText("Font for the English text (optional)")
        self.font_browse = QPushButton("Browse…")
        frow = QHBoxLayout(); frow.addWidget(self.font_edit, 1); frow.addWidget(self.font_browse)
        il.addRow("Scan:", self.scope); il.addRow("Sensitivity:", self.conf); il.addRow("Font:", frow)
        self.ocr_status = QLabel(); self.ocr_status.setWordWrap(True); self.ocr_status.setTextFormat(Qt.PlainText)
        il.addRow(self.ocr_status)
        v.addWidget(img)

        more = QGroupBox("Advanced")
        ml = QFormLayout(more)
        self.wrap = QSpinBox(); self.wrap.setRange(0, 120); self.wrap.setSpecialValueText("automatic"); self.wrap.setSuffix(" characters per line")
        self.mem_edit = QLineEdit(); self.mem_edit.setPlaceholderText("Optional: your corrections (memory.tsv from an earlier run)")
        self.mem_browse = QPushButton("Browse…")
        mrow = QHBoxLayout(); mrow.addWidget(self.mem_edit, 1); mrow.addWidget(self.mem_browse)
        self.cb_fast = QCheckBox("Fast mode (several times quicker, slightly rougher wording)")
        self.cb_link = QCheckBox("Hard-link unchanged files instead of copying (saves disk space)")
        self.cb_resume = QCheckBox("Resume an earlier run in this folder (images already translated are kept)")
        self.cb_over = QCheckBox("Allow a non-empty output folder")
        ml.addRow("Message width:", self.wrap); ml.addRow("Memory:", mrow); ml.addRow(self.cb_fast); ml.addRow(self.cb_link); ml.addRow(self.cb_resume); ml.addRow(self.cb_over)
        v.addWidget(more)

        brow = QHBoxLayout()
        self.start_btn = QPushButton("Translate game"); self.cancel_btn = QPushButton("Cancel"); self.cancel_btn.setEnabled(False)
        self.open_btn = QPushButton("Open output folder"); self.open_btn.setEnabled(False)
        for w in (self.start_btn, self.cancel_btn, self.open_btn):
            brow.addWidget(w)
        brow.addStretch(1)
        v.addLayout(brow)
        self.stage = QLabel(""); v.addWidget(self.stage)
        self.progress = QProgressBar(); self.progress.setVisible(False); v.addWidget(self.progress)
        self.result = QPlainTextEdit(); self.result.setReadOnly(True); self.result.setMaximumBlockCount(500)
        v.addWidget(self.result, 1)

        self.out_browse.clicked.connect(self._browse_out)
        self.font_browse.clicked.connect(lambda: self._browse_file(self.font_edit, "Fonts (*.ttf *.otf *.ttc)"))
        self.mem_browse.clicked.connect(lambda: self._browse_file(self.mem_edit, "Tab-separated (*.tsv *.txt)"))
        self.cb_ocr.toggled.connect(self._refresh_ocr)
        self.start_btn.clicked.connect(self.start)
        self.cancel_btn.clicked.connect(lambda: self._worker and self._worker.cancel())
        self.open_btn.clicked.connect(self._open_out)
        ctx.project_changed.connect(self._project_changed)
        self._project_changed(ctx.info)
        self._load_settings()
        self._refresh_ocr()

    # ---- remembered options (the output folder is per game and is not kept)
    def _checks(self) -> dict:
        return {"dialogue": self.cb_dialogue, "database": self.cb_database, "system": self.cb_system, "plugins": self.cb_plugins,
                "keep": self.cb_keep, "ocr": self.cb_ocr, "fast": self.cb_fast, "link": self.cb_link}

    def _load_settings(self) -> None:
        from ..core.settings import load_settings
        s = load_settings().get("translate_game", {})
        for k, cb in self._checks().items():
            if isinstance(s.get(k), bool):
                cb.setChecked(s[k])
        if isinstance(s.get("conf"), (int, float)):
            self.conf.setValue(float(s["conf"]))
        if isinstance(s.get("wrap"), int):
            self.wrap.setValue(s["wrap"])
        i = self.scope.findData(s.get("scope"))
        if i >= 0:
            self.scope.setCurrentIndex(i)
        for key, edit in (("font", self.font_edit), ("memory", self.mem_edit)):
            if isinstance(s.get(key), str):
                edit.setText(s[key])

    def _save_settings(self) -> None:
        from ..core.settings import save_settings
        data = {k: cb.isChecked() for k, cb in self._checks().items()}
        data.update(conf=self.conf.value(), wrap=self.wrap.value(), scope=self.scope.currentData(),
                    font=self.font_edit.text().strip(), memory=self.mem_edit.text().strip())
        save_settings({"translate_game": data})

    # ---- helpers
    def _project_changed(self, info) -> None:
        if info is None:
            self.game_label.setText("Open a supported game on the Project page first.")
            self.start_btn.setEnabled(False)
            return
        self.game_label.setText(f"Game: {info.label}  —  {info.root}")
        if not self.out_edit.text():
            self.out_edit.setText(str(Path(info.root).parent / (Path(info.root).name + "_EN")))
        self.start_btn.setEnabled(self._worker is None)
        self.refresh_model()

    def refresh_model(self) -> None:
        try:
            st = self.ctx.translator.status()
        except Exception as e:  # noqa: BLE001
            self.model_label.setText(f"Translation model: {e}")
            return
        if st.get("model") == "ready":
            self.model_label.setText(f"Translation model: {st.get('active')} ready.")
        else:
            self.model_label.setText("No neural model is installed: only dictionary words will be translated. "
                                     "Install one on the Translation page (NLLB gives the best results).")

    def _refresh_ocr(self) -> None:
        if not self.cb_ocr.isChecked():
            self.ocr_status.setText("")
            return
        from ..translate.ocr import ocr_available
        ok, why = ocr_available(("jpn",))
        self.ocr_status.setText("Tesseract and OpenCV found." if ok else f"Image translation is not available: {why}")

    def _browse_out(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Output folder", self.out_edit.text() or str(Path.home()))
        if d:
            self.out_edit.setText(d)

    def _browse_file(self, edit: QLineEdit, flt: str) -> None:
        f, _ = QFileDialog.getOpenFileName(self, "Select a file", edit.text() or str(Path.home()), flt)
        if f:
            edit.setText(f)

    def _open_out(self) -> None:
        if self._out_dir and self._out_dir.is_dir():
            try:
                subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(self._out_dir)])
            except OSError:
                pass

    # ---- run
    def options(self):
        from ..translate.gamerun import Options
        return Options(dialogue=self.cb_dialogue.isChecked(), database=self.cb_database.isChecked(), system=self.cb_system.isChecked(),
                       plugin_params=self.cb_plugins.isChecked(), keep_referenced=self.cb_keep.isChecked(),
                       ocr=self.cb_ocr.isChecked(), ocr_scope=self.scope.currentData(), ocr_min_conf=self.conf.value(),
                       font=self.font_edit.text().strip() or None, wrap_chars=self.wrap.value() or None,
                       memory=self.mem_edit.text().strip() or None, copy_mode="link" if self.cb_link.isChecked() else "copy", beam=1 if self.cb_fast.isChecked() else 0, resume=self.cb_resume.isChecked(),
                       overwrite=self.cb_over.isChecked())

    def start(self) -> None:
        if self._worker is not None or self.ctx.info is None:
            return
        out = self.out_edit.text().strip()
        if not out:
            QMessageBox.warning(self, "Translate game", "Choose an output folder.")
            return
        self._out_dir = Path(out)
        self._save_settings()
        self.result.clear()
        self._set_running(True)
        self._worker = GameTranslateWorker(self.ctx.translator, str(self.ctx.info.root), out, self.options(), self)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished_run.connect(self._on_done)
        self._worker.failed.connect(self._on_failed)
        self._worker.finished.connect(self._worker_finished)
        self._worker.start()

    def _set_running(self, on: bool) -> None:
        self.start_btn.setEnabled(not on and self.ctx.info is not None)
        self.cancel_btn.setEnabled(on)
        self.progress.setVisible(on)
        if on:
            self.progress.setRange(0, 0)
            self.open_btn.setEnabled(False)

    def _on_progress(self, stage: str, done: int, total: int) -> None:
        self.stage.setText(f"{_STAGES.get(stage, stage)}… {done}/{total}")
        self.progress.setRange(0, max(1, total)); self.progress.setValue(done)

    def _on_done(self, res) -> None:
        lines = []
        if res.cancelled:
            lines.append("Cancelled. The output folder holds an untranslated copy.")
        else:
            lines.append(f"{res.engine}: {res.translated} of {res.strings} strings translated, {res.files_changed} files rewritten"
                         + (f", {res.skipped} names kept because scripts refer to them" if res.skipped else "") + ".")
            if res.images_scanned or res.images_changed:
                lines.append(f"Images: {res.images_changed} changed of {res.images_scanned} scanned ({res.regions} text regions).")
            lines.append(f"Review the translations in {res.out}/.translation/report.tsv; edit .translation/memory.tsv and re-run to correct them.")
        lines += [f"Warning: {w}" for w in res.warnings[:30]]
        self.result.setPlainText("\n".join(lines))
        self.open_btn.setEnabled(not res.cancelled and res.out is not None)
        self.ctx.log.emit("info", lines[0])

    def _on_failed(self, msg: str) -> None:
        self.result.setPlainText(f"Failed: {msg}")
        self.ctx.log.emit("error", f"translate game: {msg}")

    def _worker_finished(self) -> None:
        w, self._worker = self._worker, None
        if w is not None:
            w.deleteLater()
        self._set_running(False)
        self.stage.setText("")

    def shutdown(self) -> bool:
        self._save_settings()
        w = self._worker
        if w is None:
            return True
        w.cancel()
        return w.wait(15000)
