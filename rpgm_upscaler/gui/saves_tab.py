"""Save editor tab: MV / MZ / VX / VX Ace saves with database names and offline English translations."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QFileDialog, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                               QListWidget, QMessageBox, QPlainTextEdit, QPushButton, QSpinBox, QSplitter, QTableWidget,
                               QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget)

from ..saves.database import Names, load_names, strip_codes
from ..saves.files import find_saves, open_save
from ..saves.model import INVENTORY_KINDS, SaveDoc, SaveError
from .theme import error_color
from .worker import SaveLoadWorker, TranslateWorker

RO = Qt.ItemIsSelectable | Qt.ItemIsEnabled


def _ro(text) -> QTableWidgetItem:
    it = QTableWidgetItem("" if text is None else str(text))
    it.setFlags(RO)
    return it


def _editable(text) -> QTableWidgetItem:
    it = QTableWidgetItem("" if text is None else str(text))
    it.setFlags(RO | Qt.ItemIsEditable)
    return it


class SavesTab(QWidget):
    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.doc: SaveDoc | None = None
        self.names = Names()
        self._loading = False
        self._worker: TranslateWorker | None = None
        self._english: dict[str, str] = {}
        self._auto_open = True            # opening a project selects its first save; the hub switches this off when it opens one itself
        self._load_seq = 0
        self._loader: SaveLoadWorker | None = None
        self._build()
        ctx.project_changed.connect(lambda _info: self.refresh_slots())

    # ---- UI ---------------------------------------------------------------------------------------
    def _build(self) -> None:
        outer = QVBoxLayout(self)
        split = QSplitter(Qt.Horizontal)
        outer.addWidget(split)

        left = QWidget(); lv = QVBoxLayout(left); lv.setContentsMargins(0, 0, 0, 0)
        lv.addWidget(QLabel("Save files"))
        self.slots = QListWidget()
        self.slots.currentRowChanged.connect(self._slot_selected)
        lv.addWidget(self.slots)
        row = QHBoxLayout()
        self.open_btn = QPushButton("Open file…"); self.reload_btn = QPushButton("Rescan")
        row.addWidget(self.open_btn); row.addWidget(self.reload_btn)
        lv.addLayout(row)
        self.open_btn.clicked.connect(self.open_dialog)
        self.reload_btn.clicked.connect(self.refresh_slots)
        split.addWidget(left)

        right = QWidget(); rv = QVBoxLayout(right); rv.setContentsMargins(0, 0, 0, 0)
        self.header = QLabel("Open a game in the Project page, or open a save file.")
        self.header.setWordWrap(True)
        self.banner = QLabel(); self.banner.setStyleSheet(f"color:{error_color(self)};font-weight:bold"); self.banner.setWordWrap(True)
        rv.addWidget(self.header); rv.addWidget(self.banner)
        self.tabs = QTabWidget()
        rv.addWidget(self.tabs, 1)

        # switches
        self.sw_filter = QLineEdit(); self.sw_filter.setPlaceholderText("filter by id, name or English…")
        self.sw_changed = QCheckBox("on only")
        self.sw_table = QTableWidget(0, 4); self.sw_table.setHorizontalHeaderLabels(["ID", "Name", "English", "On"])
        self.tabs.addTab(self._table_page(self.sw_table, self.sw_filter, self.sw_changed), "Switches")
        # variables
        self.var_filter = QLineEdit(); self.var_filter.setPlaceholderText("filter by id, name or English…")
        self.var_changed = QCheckBox("non-zero only")
        self.var_table = QTableWidget(0, 4); self.var_table.setHorizontalHeaderLabels(["ID", "Name", "English", "Value"])
        self.tabs.addTab(self._table_page(self.var_table, self.var_filter, self.var_changed), "Variables")
        # party
        party = QWidget(); pv = QVBoxLayout(party)
        grow = QHBoxLayout()
        grow.addWidget(QLabel("Gold:"))
        self.gold = QSpinBox(); self.gold.setRange(0, 999999999)
        grow.addWidget(self.gold); grow.addStretch(1)
        pv.addLayout(grow)
        self.party_table = QTableWidget(0, 6); self.party_table.setHorizontalHeaderLabels(["ID", "Name", "Level", "EXP", "HP", "MP"])
        pv.addWidget(self.party_table)
        self.tabs.addTab(party, "Party")
        # inventory
        inv = QWidget(); iv = QVBoxLayout(inv)
        self.inv_table = QTableWidget(0, 5); self.inv_table.setHorizontalHeaderLabels(["Kind", "ID", "Name", "English", "Count"])
        iv.addWidget(self.inv_table)
        add = QHBoxLayout()
        self.add_kind = QComboBox(); self.add_kind.addItems(INVENTORY_KINDS)
        self.add_item = QComboBox(); self.add_item.setMinimumWidth(260)
        self.add_count = QSpinBox(); self.add_count.setRange(1, 99); self.add_count.setValue(1)
        self.add_btn = QPushButton("Add / set")
        for w in (QLabel("Add:"), self.add_kind, self.add_item, QLabel("x"), self.add_count, self.add_btn):
            add.addWidget(w)
        add.addStretch(1)
        iv.addLayout(add)
        self.tabs.addTab(inv, "Inventory")
        # position
        pos = QWidget(); pg = QHBoxLayout(pos)
        self.map_id = QSpinBox(); self.map_id.setRange(0, 9999)
        self.map_name = QLabel("")
        self.pos_x = QSpinBox(); self.pos_x.setRange(0, 999)
        self.pos_y = QSpinBox(); self.pos_y.setRange(0, 999)
        self.pos_btn = QPushButton("Apply")
        for w in (QLabel("Map:"), self.map_id, self.map_name, QLabel("X"), self.pos_x, QLabel("Y"), self.pos_y, self.pos_btn):
            pg.addWidget(w)
        pg.addStretch(1)
        self.tabs.addTab(pos, "Position")
        # info
        self.info_box = QPlainTextEdit(); self.info_box.setReadOnly(True)
        self.tabs.addTab(self.info_box, "Info")

        brow = QHBoxLayout()
        self.save_btn = QPushButton("Save"); self.revert_btn = QPushButton("Revert"); self.bak_btn = QPushButton("Open backup folder")
        self.translate_chk = QCheckBox("Show English (offline)"); self.translate_chk.setChecked(True)
        for w in (self.save_btn, self.revert_btn, self.bak_btn):
            brow.addWidget(w)
        brow.addStretch(1); brow.addWidget(self.translate_chk)
        rv.addLayout(brow)
        split.addWidget(right)
        split.setSizes([260, 900])

        self.sw_filter.textChanged.connect(lambda: self._apply_filter(self.sw_table, self.sw_filter, self.sw_changed, 3))
        self.sw_changed.toggled.connect(lambda: self._apply_filter(self.sw_table, self.sw_filter, self.sw_changed, 3))
        self.var_filter.textChanged.connect(lambda: self._apply_filter(self.var_table, self.var_filter, self.var_changed, 3))
        self.var_changed.toggled.connect(lambda: self._apply_filter(self.var_table, self.var_filter, self.var_changed, 3))
        self.sw_table.itemChanged.connect(self._switch_edited)
        self.var_table.itemChanged.connect(self._variable_edited)
        self.party_table.itemChanged.connect(self._party_edited)
        self.inv_table.itemChanged.connect(self._inv_edited)
        self.gold.editingFinished.connect(self._gold_edited)
        self.add_btn.clicked.connect(self._add_item)
        self.add_kind.currentTextChanged.connect(self._fill_add_items)
        self.pos_btn.clicked.connect(self._apply_position)
        self.save_btn.clicked.connect(self.save)
        self.revert_btn.clicked.connect(self.revert)
        self.bak_btn.clicked.connect(self.open_backups)
        self.translate_chk.toggled.connect(lambda on: self._start_translation() if on else None)
        self._set_enabled(False)

    def _table_page(self, table: QTableWidget, flt: QLineEdit, chk: QCheckBox) -> QWidget:
        w = QWidget(); v = QVBoxLayout(w)
        top = QHBoxLayout(); top.addWidget(flt, 1); top.addWidget(chk)
        v.addLayout(top); v.addWidget(table)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setStretchLastSection(False)
        return w

    def _size_columns(self) -> None:
        """Name fixed-ish, English takes the slack, ids/values fit their content."""
        for t, name_col, en_col in ((self.sw_table, 1, 2), (self.var_table, 1, 2), (self.inv_table, 2, 3)):
            h = t.horizontalHeader()
            for c in range(t.columnCount()):
                h.setSectionResizeMode(c, QHeaderView.ResizeToContents)
            h.setSectionResizeMode(name_col, QHeaderView.Interactive)
            t.setColumnWidth(name_col, 240)
            h.setSectionResizeMode(en_col, QHeaderView.Stretch)
        h = self.party_table.horizontalHeader()
        h.setSectionResizeMode(QHeaderView.ResizeToContents)
        h.setSectionResizeMode(1, QHeaderView.Stretch)

    # ---- slots / loading ----------------------------------------------------------------------------
    def refresh_slots(self) -> None:
        self.slots.blockSignals(True)
        self.slots.clear()
        self._files: list[Path] = []
        path = self.ctx.path
        if path:
            for f in find_saves(path):
                self._files.append(f)
                self.slots.addItem(f.name)
        self.slots.blockSignals(False)
        if not path:
            return
        if self._files and self._auto_open:
            self.slots.blockSignals(True)
            self.slots.setCurrentRow(0)
            self.slots.blockSignals(False)
            self.open_file(self._files[0])
        elif not self._files:
            self.header.setText("No save files found in this game yet.")

    def select_slot_for(self, path: str | Path) -> None:
        """Highlight the slot that holds `path` without opening it again."""
        p = Path(path)
        for i, f in enumerate(getattr(self, "_files", [])):
            if f == p or f.resolve() == p.resolve():
                self.slots.blockSignals(True)
                self.slots.setCurrentRow(i)
                self.slots.blockSignals(False)
                return

    def open_dialog(self) -> None:
        f, _ = QFileDialog.getOpenFileName(self, "Open a save file", self.ctx.path or str(Path.home()),
                                           "Saves (*.rpgsave *.rmmzsave *.rvdata2 *.rvdata *.rxdata)")
        if f:
            self.open_file_async(f)

    def open_file_async(self, path: str | Path) -> None:
        """Same as open_file, but the file is read on a worker thread; the tab shows 'Loading' meanwhile. If the user picks
        another file before it finishes, only the last request is shown."""
        if not self.maybe_discard():
            self._restore_slot()
            return
        self._load_seq += 1
        self.header.setText(f"Loading {Path(path).name}…")
        self._set_enabled(False)
        self._loader = SaveLoadWorker(path, self._load_seq, self)
        self._loader.loaded.connect(self._load_done)
        self._loader.failed.connect(self._load_failed)
        self._loader.start()

    def _load_done(self, doc, names, seq: int) -> None:
        if seq != self._load_seq:
            return
        self._adopt(doc, names)

    def _load_failed(self, message: str, seq: int) -> None:
        if seq != self._load_seq:
            return
        QMessageBox.critical(self, "Cannot open save", message)
        self.header.setText(str(self.doc.path) if self.doc else "Open a game in the Project page, or open a save file.")
        self._set_enabled(self.doc is not None)
        self._restore_slot()

    def _restore_slot(self) -> None:
        self.slots.blockSignals(True)
        cur = self._files.index(self.doc.path) if self.doc and self.doc.path in getattr(self, "_files", []) else -1
        self.slots.setCurrentRow(cur)
        self.slots.blockSignals(False)

    def _adopt(self, doc, names) -> None:
        self.doc = doc
        self.names = names
        self._english = {}
        self._populate()
        self._start_translation()

    def open_file(self, path: str | Path) -> bool:
        if not self.maybe_discard():
            return False
        try:
            doc = open_save(path)
        except SaveError as e:
            QMessageBox.critical(self, "Cannot open save", str(e))
            return False
        self._load_seq += 1                               # a slow background load that is still running must not replace this
        self._adopt(doc, load_names(path))
        return True

    def _slot_selected(self, row: int) -> None:
        if 0 <= row < len(self._files):
            self.open_file_async(self._files[row])

    # ---- population -----------------------------------------------------------------------------------
    def _label(self, kind: str, i: int) -> str:
        n = self.names
        text = n.switch(i) if kind == "switch" else n.variable(i) if kind == "variable" else n.inventory(kind).get(i, "")
        return strip_codes(text)

    def _populate(self) -> None:
        d = self.doc
        assert d is not None
        self._loading = True
        ro = d.readonly
        self.banner.setText(f"READ-ONLY: {ro}" if ro else "")
        self.header.setText(f"{d.path}\n{d.engine}  |  playtime {d.playtime() or '-'}  |  gold {d.gold()}")
        # switches
        n = max(d.switch_count(), len(self.names.switches) - 1)
        self.sw_table.setRowCount(n)
        for i in range(1, n + 1):
            name = self._label("switch", i)
            self.sw_table.setItem(i - 1, 0, _ro(i)); self.sw_table.setItem(i - 1, 1, _ro(name)); self.sw_table.setItem(i - 1, 2, _ro(""))
            c = QTableWidgetItem(); c.setFlags(RO | Qt.ItemIsUserCheckable)
            c.setCheckState(Qt.Checked if d.get_switch(i) else Qt.Unchecked)
            self.sw_table.setItem(i - 1, 3, c)
        # variables
        n = max(d.variable_count(), len(self.names.variables) - 1)
        self.var_table.setRowCount(n)
        for i in range(1, n + 1):
            v = d.get_variable(i)
            v = getattr(v, "text", v)
            self.var_table.setItem(i - 1, 0, _ro(i)); self.var_table.setItem(i - 1, 1, _ro(self._label("variable", i)))
            self.var_table.setItem(i - 1, 2, _ro("")); self.var_table.setItem(i - 1, 3, _editable(v))
        # party
        self.gold.setValue(int(d.gold() or 0))
        ids = d.party_ids()
        self.party_table.setRowCount(len(ids))
        for r, aid in enumerate(ids):
            a = d.actor(aid)
            self.party_table.setItem(r, 0, _ro(aid)); self.party_table.setItem(r, 1, _ro(a.name if a else "?"))
            for c, val in ((2, a.level if a else ""), (3, a.exp if a else ""), (4, a.hp if a else ""), (5, a.mp if a else "")):
                self.party_table.setItem(r, c, _editable(val))
        # inventory
        rows = [(k, i, c) for k in INVENTORY_KINDS for i, c in sorted(d.inventory(k).items())]
        self.inv_table.setRowCount(len(rows))
        for r, (k, i, c) in enumerate(rows):
            self.inv_table.setItem(r, 0, _ro(k)); self.inv_table.setItem(r, 1, _ro(i))
            self.inv_table.setItem(r, 2, _ro(self._label(k, i))); self.inv_table.setItem(r, 3, _ro(""))
            self.inv_table.setItem(r, 4, _editable(c))
        self._fill_add_items()
        # position
        pos = d.position()
        self.pos_btn.setEnabled(pos is not None and not ro)
        if pos:
            self.map_id.setValue(pos[0]); self.pos_x.setValue(pos[1]); self.pos_y.setValue(pos[2])
            self.map_name.setText(strip_codes(self.names.maps.get(pos[0], "")))
        self.info_box.setPlainText(self._info_text())
        self._size_columns()
        self._loading = False
        self._set_enabled(True)
        self._apply_filter(self.sw_table, self.sw_filter, self.sw_changed, 3)
        self._apply_filter(self.var_table, self.var_filter, self.var_changed, 3)

    def _info_text(self) -> str:
        d = self.doc
        baks = sorted(d.path.parent.glob(d.path.name + ".bak*"))
        return "\n".join([f"file: {d.path}", f"engine: {d.engine}", f"codec: {getattr(d, 'codec', 'Ruby Marshal')}",
                          f"read-only: {d.readonly or 'no'}", f"playtime: {d.playtime() or '-'}",
                          f"database: {self.names.source or 'not found (names unavailable)'}",
                          f"backups: {', '.join(b.name for b in baks) or 'none yet'}"])

    def _fill_add_items(self) -> None:
        kind = self.add_kind.currentText()
        self.add_item.clear()
        for i, name in sorted(self.names.inventory(kind).items()):
            en = self._english.get(name, "")
            self.add_item.addItem(f"{i}: {strip_codes(name)}" + (f"  [{en}]" if en else ""), i)

    def _set_enabled(self, on: bool) -> None:
        was, self._loading = self._loading, True          # setFlags emits itemChanged; those are not user edits
        try:
            self._set_enabled_inner(on)
        finally:
            self._loading = was

    def _set_enabled_inner(self, on: bool) -> None:
        writable = on and self.doc is not None and not self.doc.readonly
        for w in (self.gold, self.add_btn, self.add_item, self.add_kind, self.add_count):
            w.setEnabled(writable)
        for w in (self.revert_btn, self.bak_btn):
            w.setEnabled(on)
        self.save_btn.setEnabled(writable and bool(self.doc and self.doc.dirty))
        for t in (self.sw_table, self.var_table, self.party_table, self.inv_table):
            t.setEnabled(on)
        editable = RO | (Qt.ItemIsEditable if writable else Qt.NoItemFlags)
        for t, cols in ((self.var_table, (3,)), (self.party_table, (2, 3, 4, 5)), (self.inv_table, (4,))):
            for r in range(t.rowCount()):
                for c in cols:
                    if t.item(r, c) is not None:
                        t.item(r, c).setFlags(editable)
        for r in range(self.sw_table.rowCount()):
            if self.sw_table.item(r, 3) is not None:
                self.sw_table.item(r, 3).setFlags(RO | (Qt.ItemIsUserCheckable if writable else Qt.NoItemFlags))

    def _mark_dirty(self) -> None:
        self.save_btn.setEnabled(bool(self.doc and not self.doc.readonly and self.doc.dirty))
        self.ctx.log.emit("info", f"edited {self.doc.path.name}")

    # ---- filtering --------------------------------------------------------------------------------------
    def _apply_filter(self, table: QTableWidget, flt: QLineEdit, chk: QCheckBox, value_col: int) -> None:
        text = flt.text().strip().lower()
        for r in range(table.rowCount()):
            hide = False
            if text:
                hide = text not in " ".join((table.item(r, c).text() if table.item(r, c) else "") for c in range(3)).lower()
            if not hide and chk.isChecked():
                it = table.item(r, value_col)
                if table is self.sw_table:
                    hide = it is None or it.checkState() != Qt.Checked
                else:
                    hide = it is None or it.text().strip() in ("", "0")
            table.setRowHidden(r, hide)

    # ---- edits ------------------------------------------------------------------------------------------
    def _guard(self, fn) -> bool:
        try:
            fn()
            return True
        except (SaveError, ValueError) as e:
            QMessageBox.warning(self, "Cannot edit", str(e))
            return False

    def _switch_edited(self, item: QTableWidgetItem) -> None:
        if self._loading or item.column() != 3 or not self.doc:
            return
        i = int(self.sw_table.item(item.row(), 0).text())
        want = item.checkState() == Qt.Checked
        if want != self.doc.get_switch(i) and self._guard(lambda: self.doc.set_switch(i, want)):
            self._mark_dirty()

    def _variable_edited(self, item: QTableWidgetItem) -> None:
        if self._loading or item.column() != 3 or not self.doc:
            return
        i = int(self.var_table.item(item.row(), 0).text())
        text = item.text().strip()
        old = self.doc.get_variable(i)
        if isinstance(getattr(old, "text", old), str) and not text.lstrip("-").isdigit():
            val = text
        else:
            try:
                val = int(text)
            except ValueError:
                try:
                    val = float(text)
                except ValueError:
                    val = text
        if str(getattr(old, "text", old)) == str(val):
            return
        if self._guard(lambda: self.doc.set_variable(i, val)):
            self._mark_dirty()
        else:
            self._restore_cell(item, getattr(old, "text", old))

    def _party_edited(self, item: QTableWidgetItem) -> None:
        if self._loading or not self.doc or item.column() < 2:
            return
        aid = int(self.party_table.item(item.row(), 0).text())
        field = {2: "level", 3: "exp", 4: "hp", 5: "mp"}[item.column()]

        cur = self.doc.actor(aid)
        if cur is not None and str(getattr(cur, field, None)) == item.text().strip():
            return

        def apply():
            self.doc.set_actor(aid, **{field: int(item.text())})

        if self._guard(apply):
            self._mark_dirty()
        else:
            self._reload_party_cell(item, aid, field)

    def _reload_party_cell(self, item, aid, field) -> None:
        a = self.doc.actor(aid)
        self._loading = True
        item.setText(str(getattr(a, field, "")))
        self._loading = False

    def _inv_edited(self, item: QTableWidgetItem) -> None:
        if self._loading or not self.doc or item.column() != 4:
            return
        kind = self.inv_table.item(item.row(), 0).text()
        iid = int(self.inv_table.item(item.row(), 1).text())
        try:
            count = int(item.text())
        except ValueError:
            count = None
        if count is not None and self.doc.inventory(kind).get(iid) == count:
            return
        if self._guard(lambda: self.doc.set_item(kind, iid, int(item.text()))):
            self._mark_dirty()
        else:
            self._restore_cell(item, self.doc.inventory(kind).get(iid, ""))

    def _restore_cell(self, item: QTableWidgetItem, value) -> None:
        """A refused edit must not leave the rejected text in the table as if it had been applied."""
        was, self._loading = self._loading, True
        item.setText("" if value is None else str(value))
        self._loading = was

    def _gold_edited(self) -> None:
        if self._loading or not self.doc or self.doc.readonly or self.gold.value() == (self.doc.gold() or 0):
            return
        if self._guard(lambda: self.doc.set_gold(self.gold.value())):
            self.gold.setValue(self.doc.gold())
            self._mark_dirty()

    def _add_item(self) -> None:
        if not self.doc or self.add_item.currentData() is None:
            return
        kind, iid = self.add_kind.currentText(), int(self.add_item.currentData())
        if self._guard(lambda: self.doc.set_item(kind, iid, self.add_count.value())):
            self._mark_dirty()
            self._populate_keep_tab()

    def _populate_keep_tab(self) -> None:
        cur = self.tabs.currentIndex()
        self._populate()
        self.tabs.setCurrentIndex(cur)
        self._apply_english()

    def _apply_position(self) -> None:
        if self.doc and self._guard(lambda: self.doc.set_position(self.map_id.value(), self.pos_x.value(), self.pos_y.value())):
            self.map_name.setText(strip_codes(self.names.maps.get(self.map_id.value(), "")))
            self._mark_dirty()

    # ---- save / revert ----------------------------------------------------------------------------------
    def save(self) -> bool:
        if not self.doc:
            return False
        try:
            self.doc.save()
        except SaveError as e:
            QMessageBox.critical(self, "Save failed", str(e))
            return False
        self.ctx.log.emit("info", f"saved {self.doc.path} (backup kept next to it)")
        self.save_btn.setEnabled(False)
        self.info_box.setPlainText(self._info_text())
        return True

    def revert(self) -> None:
        if self.doc:
            path = self.doc.path
            self.doc.dirty = False
            self.open_file(path)

    def open_backups(self) -> None:
        if self.doc:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.doc.path.parent)))

    def maybe_discard(self) -> bool:
        if self.doc is not None and self.doc.dirty:
            r = QMessageBox.question(self, "Unsaved changes", f"{self.doc.path.name} has unsaved changes. Discard them?")
            return r == QMessageBox.Yes
        return True

    # ---- translation ------------------------------------------------------------------------------------
    def _labels(self) -> list[str]:
        n = self.names
        labels = set(n.switches) | set(n.variables) | set(n.actors.values()) | set(n.items.values()) | set(n.weapons.values()) \
            | set(n.armors.values()) | set(n.maps.values())
        return sorted(strip_codes(x) for x in labels if x)

    def _start_translation(self) -> None:
        if not self.translate_chk.isChecked() or self.doc is None:
            return
        if self._worker is not None and self._worker.isRunning():
            self._worker.stop()
            self._worker.wait(3000)
        from ..translate.detect import is_japanese
        todo = [t for t in self._labels() if is_japanese(t) and t not in self._english]
        if not todo:
            self._apply_english()
            return
        self._worker = TranslateWorker(self.ctx.translator, todo, parent=self)
        self._worker.chunk.connect(self._got_english)
        self._worker.start()

    def _got_english(self, mapping: dict) -> None:
        self._english.update(mapping)
        self._apply_english()

    def _apply_english(self) -> None:
        if not self.translate_chk.isChecked():
            return
        self._loading = True
        for table, name_col, en_col in ((self.sw_table, 1, 2), (self.var_table, 1, 2), (self.inv_table, 2, 3)):
            for r in range(table.rowCount()):
                it = table.item(r, name_col)
                en = self._english.get(it.text()) if it else None
                if en and table.item(r, en_col) is not None:
                    table.item(r, en_col).setText(en)
        pos = self._english.get(strip_codes(self.names.maps.get(self.map_id.value(), "")))
        if pos:
            self.map_name.setText(f"{strip_codes(self.names.maps.get(self.map_id.value(), ''))}  [{pos}]")
        self._fill_add_items()
        self._loading = False

    def wait_for_translation(self, ms: int = 10000) -> None:
        if self._worker is not None:
            self._worker.wait(ms)

    def shutdown(self) -> bool:
        if not self.maybe_discard():
            return False
        if self._loader is not None and self._loader.isRunning():
            self._load_seq += 1
            if not self._loader.wait(20000):
                return False
        if self._worker is not None and self._worker.isRunning():
            self._worker.stop()
            if not self._worker.wait(20000):
                return False
        return True
