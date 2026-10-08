"""The Upscale tab of RPGM Hub (MV / MZ / VX / VX Ace)."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from PIL import Image
from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QGuiApplication, QImage, QPixmap
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QFileDialog, QGridLayout, QGroupBox,
                               QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget, QMainWindow,
                               QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QSpinBox,
                               QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from ..core import engines, video
from ..core.planner import Plan, make_scale_plan
from ..core.project import ProjectError
from ..detect import detect_engine
from ..core.settings import Options, load_settings, save_settings
from ..rgss import mkxp
from .worker import MkxpInstallWorker, PlanWorker, PreviewWorker, RunWorker

SCALES = ["fit", "1.5", "2", "2.5", "3", "4"]
MAX_LOG_LINES = 5000


def pil_to_pixmap(img: Image.Image, max_side: int = 520, dpr: float | None = None) -> QPixmap:
    """A preview pixmap `max_side` device-independent pixels at most. Shrinking is smooth; a small image is enlarged by a
    whole factor with hard pixel edges, so pixel art stays crisp instead of being blurred. `dpr` is the screen's device pixel
    ratio (HiDPI): the pixmap holds that many real pixels per logical pixel."""
    dpr = dpr or (QGuiApplication.primaryScreen().devicePixelRatio() if QGuiApplication.primaryScreen() else 1.0)
    img = img.convert("RGBA")
    data = img.tobytes("raw", "RGBA")
    qi = QImage(data, img.width, img.height, img.width * 4, QImage.Format_RGBA8888).copy()
    pm = QPixmap.fromImage(qi)
    limit = max(1, int(round(max_side * dpr)))
    side = max(pm.width(), pm.height())
    if side > limit:
        pm = pm.scaled(limit, limit, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    elif side * 2 <= limit:
        k = limit // side
        pm = pm.scaled(pm.width() * k, pm.height() * k, Qt.KeepAspectRatio, Qt.FastTransformation)
    pm.setDevicePixelRatio(dpr)
    return pm


class UpscaleTab(QWidget):
    output_ready = Signal(str)             # folder of a finished run

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._prepared = None          # temp extraction of an encrypted VX/Ace game, kept for previews
        self.plan: Plan | None = None
        self.worker: RunWorker | None = None
        self._plan_worker: PlanWorker | None = None
        self._preview_worker: PreviewWorker | None = None
        self._preview_pending: int | None = None      # a file picked while a preview was still rendering
        self._busy = False
        self._build_ui()
        self.setAcceptDrops(True)
        self.src_edit.setAcceptDrops(False); self.out_edit.setAcceptDrops(False)
        self._load_settings()
        self.update_scale_info()

    # ---- UI construction ----------------------------------------------------------------------
    def _build_ui(self) -> None:
        v = QVBoxLayout(self)

        paths = QGridLayout()
        self.src_edit, self.out_edit = QLineEdit(), QLineEdit()
        b1, b2 = QPushButton("Browse…"), QPushButton("Browse…")
        self.analyze_btn = QPushButton("Analyze")
        paths.addWidget(QLabel("Game folder:"), 0, 0); paths.addWidget(self.src_edit, 0, 1)
        paths.addWidget(b1, 0, 2); paths.addWidget(self.analyze_btn, 0, 3)
        paths.addWidget(QLabel("Output folder:"), 1, 0); paths.addWidget(self.out_edit, 1, 1)
        paths.addWidget(b2, 1, 2)
        v.addLayout(paths)
        b1.clicked.connect(lambda: self._browse(self.src_edit))
        b2.clicked.connect(lambda: self._browse(self.out_edit))
        self.analyze_btn.clicked.connect(self.analyze)
        self.src_edit.editingFinished.connect(self._suggest_output)

        self.info = QLabel("No project loaded.")
        self.info.setTextFormat(Qt.PlainText)
        self.info.setWordWrap(True)
        v.addWidget(self.info)

        # options
        box = QGroupBox("Options")
        g = QGridLayout(box)
        self.tw, self.th = QSpinBox(), QSpinBox()
        for s, val in ((self.tw, 1920), (self.th, 1080)):
            s.setRange(320, 7680); s.setValue(val)
        self.scale = QComboBox(); self.scale.setEditable(True); self.scale.addItems(SCALES)
        self.engine = QComboBox(); self.engine.addItems([*engines.PILLOW_ENGINES, *engines.NCNN_ENGINES])
        self.engine_path = QLineEdit(); self.engine_path.setPlaceholderText("AI engine binary (optional, else PATH)")
        self.model = QLineEdit(); self.model.setPlaceholderText("model (optional)")
        self.engine_status = QLabel()
        self.movies = QCheckBox("Scale movies"); self.movies.setChecked(True)
        self.patch = QCheckBox("Patch engine for 1920x1080"); self.patch.setChecked(True)
        self.reenc = QCheckBox("Keep images encrypted"); self.reenc.setChecked(True)
        self.winskin = QCheckBox("Also scale windowskin (breaks UI frames)")
        self.ui_fill = QCheckBox("UI fills whole screen")
        self.anchor = QComboBox(); self.anchor.addItems(["center", "topleft"])
        self.vx_mode = QComboBox(); self.vx_mode.addItems(["hires", "stock640"])
        self.vx_mode.setToolTip("VX / VX Ace only. hires: HD asset pack + mkxp.json for the mkxp-z player (real 1080p).\n"
                                "stock640: only sets the stock player's screen to 640x480; assets are not upscaled.")
        self.vx_label = QLabel("VX/Ace mode:")
        self.workers = QSpinBox(); self.workers.setRange(0, 64); self.workers.setSpecialValueText("auto")
        self.resume = QCheckBox("Resume (skip finished)"); self.resume.setChecked(True)
        g.addWidget(QLabel("Target:"), 0, 0); g.addWidget(self.tw, 0, 1); g.addWidget(QLabel("x"), 0, 2); g.addWidget(self.th, 0, 3)
        g.addWidget(QLabel("Scale:"), 0, 4); g.addWidget(self.scale, 0, 5)
        g.addWidget(QLabel("Anchor:"), 0, 6); g.addWidget(self.anchor, 0, 7)
        g.addWidget(QLabel("Engine:"), 1, 0); g.addWidget(self.engine, 1, 1, 1, 3)
        g.addWidget(self.engine_path, 1, 4, 1, 3); g.addWidget(self.model, 1, 7)
        g.addWidget(self.engine_status, 2, 0, 1, 8)
        g.addWidget(self.movies, 3, 0, 1, 2); g.addWidget(self.patch, 3, 2, 1, 3); g.addWidget(self.reenc, 3, 5, 1, 3)
        g.addWidget(self.winskin, 4, 0, 1, 4); g.addWidget(self.ui_fill, 4, 4, 1, 2)
        g.addWidget(QLabel("Workers:"), 5, 0); g.addWidget(self.workers, 5, 1); g.addWidget(self.resume, 5, 2, 1, 3)
        g.addWidget(self.vx_label, 5, 5); g.addWidget(self.vx_mode, 5, 6, 1, 2)
        self._set_vx_visible(False)
        self.scale_info = QLabel()
        g.addWidget(self.scale_info, 6, 0, 1, 8)
        v.addWidget(box)
        self._build_mkxp_box(v)
        for w in (self.tw, self.th):
            w.valueChanged.connect(self.update_scale_info)
        self.scale.currentTextChanged.connect(self.update_scale_info)
        self.anchor.currentTextChanged.connect(self.update_scale_info)
        self.ui_fill.toggled.connect(self.update_scale_info)
        self.engine.currentTextChanged.connect(self.update_engine_status)
        self.engine_path.editingFinished.connect(self.update_engine_status)

        # folders table + preview
        split = QSplitter(Qt.Horizontal)
        left = QWidget(); lv = QVBoxLayout(left); lv.setContentsMargins(0, 0, 0, 0)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Folder", "Files", "Policy", "Resampler", "Skip (copy)"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.itemSelectionChanged.connect(self._category_selected)
        lv.addWidget(self.table)
        self.files = QListWidget()
        self.files.currentRowChanged.connect(self._file_selected)
        lv.addWidget(self.files)
        split.addWidget(left)
        right = QWidget(); rv = QVBoxLayout(right); rv.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        self.before_lbl, self.after_lbl = QLabel("before"), QLabel("after")
        for lb in (self.before_lbl, self.after_lbl):
            lb.setAlignment(Qt.AlignCenter); lb.setMinimumSize(200, 200)
            lb.setStyleSheet("background:palette(dark);color:palette(light);")
            row.addWidget(lb)
        rv.addWidget(QLabel("Preview (select a file)"))
        rv.addLayout(row)
        self.warn_box = QPlainTextEdit(); self.warn_box.setReadOnly(True)
        self.warn_box.setPlaceholderText("Warnings appear here after analysis.")
        self.warn_box.setMaximumHeight(130)
        rv.addWidget(self.warn_box)
        split.addWidget(right)
        split.setSizes([560, 620])
        v.addWidget(split, 3)

        # run controls
        h = QHBoxLayout()
        self.start_btn, self.cancel_btn, self.open_btn = QPushButton("Start"), QPushButton("Cancel"), QPushButton("Open output")
        self.cancel_btn.setEnabled(False)
        self.progress = QProgressBar()
        self.progress_lbl = QLabel("")
        for w in (self.start_btn, self.cancel_btn, self.open_btn):
            h.addWidget(w)
        h.addWidget(self.progress, 1); h.addWidget(self.progress_lbl)
        v.addLayout(h)
        self.start_btn.clicked.connect(self.start)
        self.cancel_btn.clicked.connect(self.cancel)
        self.open_btn.clicked.connect(self.open_output)

        self.log = QPlainTextEdit(); self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(MAX_LOG_LINES)
        v.addWidget(self.log, 2)

        self._locked = [self.src_edit, self.out_edit, b1, b2, self.analyze_btn, box, self.table, self.mkxp_box]

    def _build_mkxp_box(self, v: QVBoxLayout) -> None:
        self.mkxp_box = QGroupBox("mkxp-z player (VX / VX Ace / XP, hires mode)")
        g = QGridLayout(self.mkxp_box)
        self.mkxp_status = QLabel(); self.mkxp_status.setWordWrap(True); self.mkxp_status.setTextFormat(Qt.PlainText)
        self.mkxp_install_btn = QPushButton("Install mkxp-z")
        self.mkxp_install_btn.setToolTip("Downloads the newest Linux build once (about 20 MB, from nightly.link, a mirror of mkxp-z's own "
                                         "automatic builds). Every exported game then carries it and starts with ./Game.")
        self.mkxp_use_btn = QPushButton("Use existing…")
        self.mkxp_use_btn.setToolTip("Pick a folder that already holds mkxp-z (the program, scripts/ and stdlib/).")
        self.mkxp_page_btn = QPushButton("Open download page")
        self.mkxp_fix_btn = QPushButton("Fix an existing export…")
        self.mkxp_fix_btn.setToolTip("Re-applies the fonts, Win32API preloads and the mkxp-z player to a hires export that is already upscaled, and restores files "
                                     "the original (the Game folder above) has but the export lacks, such as Audio/. "
                                     "No images are redone.")
        self.mkxp_help = QLabel("mkxp-z has no releases, only automatic builds. Alternatives: use the button above, pick a build yourself on the "
                                "download page (the Linux x86_64 one), or build it from github.com/mkxp-z/mkxp-z. The player is copied into "
                                "each export, so the exported folder starts with ./Game and nothing else to install.")
        self.mkxp_help.setWordWrap(True)
        self.mkxp_bundle = QCheckBox("Include the player in the export"); self.mkxp_bundle.setChecked(True)
        self.mkxp_skip = QCheckBox("Upscale without mkxp-z (I will add the player myself)")
        self.mkxp_progress = QProgressBar(); self.mkxp_progress.setVisible(False)
        self.mkxp_path = ""
        g.addWidget(self.mkxp_status, 0, 0, 1, 4)
        g.addWidget(self.mkxp_install_btn, 1, 0); g.addWidget(self.mkxp_use_btn, 1, 1); g.addWidget(self.mkxp_page_btn, 1, 2)
        g.addWidget(self.mkxp_progress, 1, 3)
        g.addWidget(self.mkxp_help, 2, 0, 1, 4)
        g.addWidget(self.mkxp_bundle, 3, 0, 1, 2); g.addWidget(self.mkxp_skip, 3, 2, 1, 2)
        g.addWidget(self.mkxp_fix_btn, 4, 0, 1, 2)
        self.mkxp_box.setVisible(False)                 # shown once a VX / Ace / XP game is analysed
        v.addWidget(self.mkxp_box)
        self._mkxp_worker = None
        self.mkxp_install_btn.clicked.connect(self.install_mkxp)
        self.mkxp_use_btn.clicked.connect(self._use_existing_mkxp)
        self.mkxp_page_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(mkxp.PAGE)))
        self.mkxp_fix_btn.clicked.connect(self._fix_export)
        self.mkxp_bundle.toggled.connect(lambda _on: self.update_mkxp_status())
        self.mkxp_skip.toggled.connect(lambda _on: self.update_mkxp_status())

    def update_mkxp_status(self) -> None:
        where = mkxp.locate(self.mkxp_path)
        if where is not None:
            ver = mkxp.version(where)
            self.mkxp_status.setText(f"mkxp-z is installed: {where}" + (f" ({ver})" if ver else ""))
            self.mkxp_install_btn.setText("Update mkxp-z")
        else:
            self.mkxp_status.setText("mkxp-z is NOT installed. Hires exports of VX / VX Ace / XP games need it: click 'Install mkxp-z'."
                                     + ("" if mkxp.supported() else " (No prebuilt Linux build for this system: use an existing folder or build it.)"))
            self.mkxp_install_btn.setText("Install mkxp-z")
        self.mkxp_install_btn.setEnabled(mkxp.supported() and self._mkxp_worker is None)
        self.mkxp_skip.setEnabled(self.mkxp_bundle.isChecked())

    def install_mkxp(self) -> None:
        if self._mkxp_worker is not None:
            return
        self.mkxp_install_btn.setEnabled(False)
        self.mkxp_progress.setVisible(True); self.mkxp_progress.setRange(0, 0)
        self._append_log("info", "downloading the mkxp-z player…")
        w = self._mkxp_worker = MkxpInstallWorker(self)
        w.progress.connect(lambda done, total: (self.mkxp_progress.setRange(0, max(total, 0)), self.mkxp_progress.setValue(done)))
        w.finished_run.connect(lambda where: self._append_log("info", f"mkxp-z installed in {where}"))
        w.failed.connect(lambda msg: (self._append_log("error", f"mkxp-z: {msg}"), QMessageBox.warning(self, "mkxp-z", msg + "\n\n" + mkxp.HELP)))
        w.finished.connect(self._mkxp_finished)
        w.start()

    def _mkxp_finished(self) -> None:
        w, self._mkxp_worker = self._mkxp_worker, None
        if w is not None:
            w.deleteLater()
        self.mkxp_progress.setVisible(False)
        self.update_mkxp_status()

    def _fix_export(self) -> None:
        from ..rgss import patch
        d = QFileDialog.getExistingDirectory(self, "The upscaled game folder to fix", self.out_edit.text().strip() or str(Path.home()))
        if not d:
            return
        try:
            src = self.src_edit.text().strip()
            lines = patch.refresh_export(Path(d), self.mkxp_path, src if src and Path(src).is_dir() and Path(src) != Path(d) else None)
        except (ValueError, OSError) as e:
            self._append_log("error", f"fix export: {e}")
            QMessageBox.warning(self, "Fix an existing export", str(e))
            return
        for i, line in enumerate(lines):
            self._append_log("info" if i == 0 else "warning", line)
        QMessageBox.information(self, "Fix an existing export", "\n".join(lines))

    def _use_existing_mkxp(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Folder that holds mkxp-z (the program, scripts/ and stdlib/)", self.mkxp_path or str(Path.home()))
        if not d:
            return
        if mkxp.player_exe(Path(d)) is None:
            QMessageBox.warning(self, "mkxp-z", "No mkxp-z program (mkxp-z, mkxp-z.x86_64 ...) in that folder.")
            return
        self.mkxp_path = d
        self.update_mkxp_status()

    # ---- options <-> widgets ------------------------------------------------------------------
    def options(self) -> Options:
        res, skip = {}, []
        for r in range(self.table.rowCount()):
            name = self.table.item(r, 0).text()
            combo = self.table.cellWidget(r, 3)
            chk = self.table.cellWidget(r, 4)
            if combo is not None and combo.currentText() != "default":
                res[name] = combo.currentText()
            if chk is not None and chk.isChecked():
                skip.append(name)
        return Options(target=(self.tw.value(), self.th.value()), scale=self.scale.currentText().strip() or "fit",
                       engine=self.engine.currentText(), engine_path=self.engine_path.text().strip(),
                       model=self.model.text().strip(), resamplers=res, skip=skip, movies=self.movies.isChecked(),
                       patch=self.patch.isChecked(), reencrypt=self.reenc.isChecked(),
                       scale_windowskin=self.winskin.isChecked(), ui_fill=self.ui_fill.isChecked(),
                       anchor=self.anchor.currentText(), workers=self.workers.value(), resume=self.resume.isChecked(),
                       bundle_player=self.mkxp_bundle.isChecked(), allow_no_player=self.mkxp_skip.isChecked(), mkxp_path=self.mkxp_path)

    def _load_settings(self) -> None:
        s = load_settings()
        self.src_edit.setText(s.get("source", "")); self.out_edit.setText(s.get("output", ""))
        o = Options.from_dict(s.get("options", {}))
        self.tw.setValue(o.target[0]); self.th.setValue(o.target[1])
        self.scale.setCurrentText(o.scale); self.engine.setCurrentText(o.engine)
        self.engine_path.setText(o.engine_path); self.model.setText(o.model)
        self.movies.setChecked(o.movies); self.patch.setChecked(o.patch); self.reenc.setChecked(o.reencrypt)
        self.winskin.setChecked(o.scale_windowskin); self.ui_fill.setChecked(o.ui_fill)
        self.anchor.setCurrentText(o.anchor); self.workers.setValue(o.workers); self.resume.setChecked(o.resume)
        self._saved_resamplers, self._saved_skip = o.resamplers, o.skip
        self.mkxp_bundle.setChecked(o.bundle_player); self.mkxp_skip.setChecked(o.allow_no_player); self.mkxp_path = o.mkxp_path
        self.update_mkxp_status()
        self.update_engine_status()

    def _save_settings(self) -> None:
        from dataclasses import asdict
        save_settings({"source": self.src_edit.text(), "output": self.out_edit.text(), "options": asdict(self.options())})

    def update_engine_status(self) -> None:
        name = self.engine.currentText()
        paths = {name: self.engine_path.text().strip()} if self.engine_path.text().strip() else None
        ok, detail = engines.detect_engines(paths)[name]
        self.engine_status.setText(f"Engine {name}: {'available' if ok else 'NOT available'} ({detail})"
                                   + ("" if video.available() else "   |   ffmpeg not found: movies will be copied"))

    def update_scale_info(self) -> None:
        proj = getattr(self.plan, "project", None) if self.plan else None
        if proj is None:
            self.scale_info.setText("Analyze a game to see the computed scale.")
            return
        try:
            sp = make_scale_plan(proj, self.options())
        except Exception as e:  # noqa: BLE001
            self.scale_info.setText(f"Invalid scale: {e}")
            return
        self.scale_info.setText(f"Scale x{sp.n:g}: tile {sp.tile}px, UI area {sp.ui_area[0]}x{sp.ui_area[1]}, "
                                f"content offset {sp.offset[0]},{sp.offset[1]}")

    # ---- actions ------------------------------------------------------------------------------
    def _browse(self, edit: QLineEdit) -> None:
        d = QFileDialog.getExistingDirectory(self, "Select folder", edit.text() or str(Path.home()))
        if d:
            edit.setText(d)
            if edit is self.src_edit:
                self._suggest_output()
                self.analyze()

    def _suggest_output(self) -> None:
        src = self.src_edit.text().strip().rstrip("/")
        if src and not self.out_edit.text().strip():
            self.out_edit.setText(src + "_1080p")

    def _append_log(self, level: str, msg: str) -> None:
        self.log.appendPlainText(("[!] " if level in ("error", "warning") else "") + msg)

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        for w in self._locked:
            w.setEnabled(not busy)
        self.start_btn.setEnabled(not busy)
        self.cancel_btn.setEnabled(busy and self.worker is not None)

    def analyze(self) -> None:
        path = self.src_edit.text().strip()
        if not path or self._busy:
            return
        self._set_busy(True)
        self.info.setText("Analyzing…")
        self._plan_worker = PlanWorker(path, self.options(), self.vx_mode.currentText(), self)
        self._plan_worker.done.connect(self._plan_ready)
        self._plan_worker.failed.connect(self._plan_failed)
        self._plan_worker.start()

    def _plan_failed(self, msg: str) -> None:
        self._set_busy(False)
        self.plan = None
        self.info.setText("Error: " + msg)
        self.table.setRowCount(0)

    def _set_vx_visible(self, visible: bool) -> None:
        self.vx_label.setVisible(visible)
        self.vx_mode.setVisible(visible)
        if hasattr(self, "mkxp_box"):
            self.mkxp_box.setVisible(visible)

    def _release_prepared(self) -> None:
        if self._prepared is not None:
            self._prepared.cleanup()
            self._prepared = None

    def _plan_ready(self, plan: Plan, prepared=None) -> None:
        self._set_busy(False)
        self._release_prepared()
        self._prepared = prepared
        self.plan = plan
        p = plan.project
        rgss = p.engine in ("ACE", "VX")
        self._set_vx_visible(rgss)
        counts = f"{len(plan.jobs)} assets to process, {plan.copies} copied as-is"
        if rgss:
            from ..detect import LABELS
            self.info.setText(f"{LABELS[p.engine]}  |  {p.screen[0]}x{p.screen[1]}  |  tile {p.tile_size}px  |  mode {plan.mode}  |  {counts}")
        else:
            enc = "encrypted images" + (" (key ok)" if p.key else " (NO KEY)") if p.has_encrypted_images else "plain images"
            self.info.setText(f"{p.engine} {p.version}  |  {p.screen[0]}x{p.screen[1]}  |  tile {p.tile_size}px  |  {enc}  |  "
                              f"{len(p.plugins)} plugins  |  {counts}")
        cats: dict[str, dict] = {}
        for j in plan.jobs:
            c = cats.setdefault(j.category, {"n": 0, "policy": j.policy})
            c["n"] += 1
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        for name, c in sorted(cats.items()):
            r = self.table.rowCount(); self.table.insertRow(r)
            for col, text in ((0, name), (1, str(c["n"])), (2, c["policy"])):
                it = QTableWidgetItem(text); it.setFlags(it.flags() & ~Qt.ItemIsEditable)
                self.table.setItem(r, col, it)
            combo = QComboBox(); combo.addItems(engines.RESAMPLERS)
            combo.setCurrentText(self._saved_resamplers.get(name, "default"))
            self.table.setCellWidget(r, 3, combo)
            chk = QCheckBox(); chk.setChecked(name in self._saved_skip)
            self.table.setCellWidget(r, 4, chk)
        self.table.blockSignals(False)
        self.files.clear()
        self.warn_box.setPlainText("\n".join(plan.warnings + [f"Windowskin kept unscaled: {w}" for w in plan.skipped_windowskins]))
        self.update_scale_info()
        self._append_log("info", f"Analyzed {p.base}: {plan.summary()}")

    def _category_selected(self) -> None:
        rows = self.table.selectionModel().selectedRows()
        self.files.clear()
        if not rows or not self.plan:
            return
        name = self.table.item(rows[0].row(), 0).text()
        self._cat_jobs = [j for j in self.plan.jobs if j.category == name and j.kind == "image" and not j.passthrough]
        self.files.addItems([j.src for j in self._cat_jobs])

    def _file_selected(self, row: int) -> None:
        if row < 0 or not self.plan or row >= len(self._cat_jobs):
            return
        if self._preview_worker and self._preview_worker.isRunning():
            self._preview_pending = row               # shown as soon as the current one is done, never silently dropped
            return
        plan = self.plan
        import copy
        plan = copy.copy(plan); plan.options = self.options()
        self.after_lbl.setText("rendering…")
        self._preview_worker = PreviewWorker(plan, self._cat_jobs[row], self)
        self._preview_worker.done.connect(self._preview_ready)
        self._preview_worker.failed.connect(lambda m: self.after_lbl.setText("preview failed: " + m))
        self._preview_worker.finished.connect(self._preview_finished)
        self._preview_worker.start()

    def _preview_finished(self) -> None:
        row, self._preview_pending = self._preview_pending, None
        if row is not None and row == self.files.currentRow():
            self._file_selected(row)

    def _preview_ready(self, before: Image.Image, after: Image.Image) -> None:
        self.before_lbl.setPixmap(pil_to_pixmap(before)); self.after_lbl.setPixmap(pil_to_pixmap(after))
        self.before_lbl.setToolTip(f"{before.width}x{before.height}"); self.after_lbl.setToolTip(f"{after.width}x{after.height}")

    def start(self) -> None:
        src, out = self.src_edit.text().strip(), self.out_edit.text().strip()
        if not src or not out:
            QMessageBox.warning(self, "Missing path", "Select a game folder and an output folder.")
            return
        info = detect_engine(src)
        if info is None:
            QMessageBox.critical(self, "Not a project", "No RPG Maker project found (need index.html or Game.ini)."); return
        opts = self.options()
        problem = mkxp.requirement(info.engine, self.vx_mode.currentText(), opts)
        if problem:
            QMessageBox.warning(self, "mkxp-z is not installed", problem)
            return
        ok, detail = engines.detect_engines({opts.engine: opts.engine_path} if opts.engine_path else None)[opts.engine]
        if not ok:
            QMessageBox.critical(self, "Engine unavailable", f"{opts.engine}: {detail}"); return
        self._save_settings()
        self.log.clear(); self.progress.setValue(0)
        self.worker = RunWorker(src, out, opts, self.vx_mode.currentText(), self)
        self.worker.progress.connect(self._progress)
        self.worker.log.connect(self._append_log)
        self.worker.finished_run.connect(self._run_done)
        self.worker.failed.connect(self._run_failed)
        self._set_busy(True)
        self.cancel_btn.setEnabled(True)
        self.worker.start()

    def cancel(self) -> None:
        if self.worker:
            self.cancel_btn.setEnabled(False)
            self._append_log("warning", "Cancelling…")
            self.worker.cancel()

    def _progress(self, done: int, total: int, name: str) -> None:
        self.progress.setMaximum(max(total, 1)); self.progress.setValue(done)
        self.progress_lbl.setText(f"{done}/{total}  {name[-40:]}")

    def _run_done(self, res) -> None:
        self.worker = None
        self._set_busy(False)
        msg = f"Done: {res.ok} upscaled, {res.skipped} resumed, {len(res.failed)} failed" + (", CANCELLED" if res.cancelled else "")
        self._append_log("info", msg)
        for name, err in res.failed:
            self._append_log("error", f"{name}: {err}")
        self.progress_lbl.setText(msg)
        out = self.out_edit.text().strip()
        if out and not res.cancelled and res.ok + res.skipped > 0:
            self.output_ready.emit(out)

    def _run_failed(self, msg: str) -> None:
        self.worker = None
        self._set_busy(False)
        self._append_log("error", msg)
        QMessageBox.critical(self, "Run failed", msg)

    def open_output(self) -> None:
        p = self.out_edit.text().strip()
        if p and Path(p).is_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(p))

    def dragEnterEvent(self, ev) -> None:  # noqa: N802
        if ev.mimeData().hasUrls() and any(u.isLocalFile() for u in ev.mimeData().urls()):
            ev.acceptProposedAction()

    def dropEvent(self, ev) -> None:  # noqa: N802
        for u in ev.mimeData().urls():
            if u.isLocalFile():
                p = Path(u.toLocalFile())
                self.set_project(str(p if p.is_dir() else p.parent))
                ev.acceptProposedAction()
                return

    def set_project(self, path: str) -> None:
        """Called by the hub when the user opens a game."""
        self.src_edit.setText(path)
        self.out_edit.clear()
        self._suggest_output()
        self.analyze()

    def shutdown(self) -> bool:
        """Stop work and persist settings. False = the user chose to keep the running job."""
        if self.worker and self.worker.isRunning():
            if QMessageBox.question(self, "Quit", "A run is in progress. Cancel and quit?") != QMessageBox.Yes:
                return False
            self.worker.cancel()
            if not self.worker.wait(60000):               # a QThread destroyed while running aborts the whole process
                QMessageBox.information(self, "Quit", "The run is still stopping. Try closing again in a moment.")
                return False
        for w in (self._plan_worker, self._preview_worker):   # short jobs that cannot be cancelled: let them finish
            if w is not None and w.isRunning() and not w.wait(60000):
                return False
        self._release_prepared()
        self._save_settings()
        return True
