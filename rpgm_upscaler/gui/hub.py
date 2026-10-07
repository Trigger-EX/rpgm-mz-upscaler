"""RPGM Hub: one window for upscaling, save editing and offline translation of every RPG Maker engine."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (QFileDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget, QMainWindow, QPlainTextEdit, QPushButton,
                               QStackedWidget, QVBoxLayout, QWidget)

from ..detect import EngineInfo, detect_engine
from ..saves.files import find_saves
from .gametranslate_tab import GameTranslateTab
from .saves_tab import SavesTab
from .setup_page import SetupPage
from .translate_tab import TranslateTab
from .upscale_tab import UpscaleTab

PAGES = ["Project", "Upscale", "Saves", "Translate game", "Translation", "Setup", "Log"]


class HubContext(QObject):
    """State shared by the pages: the open game and the (lazily created) offline translator."""
    project_changed = Signal(object)        # EngineInfo | None
    log = Signal(str, str)                  # level, message
    setup_changed = Signal()                # a package or model was installed, or the Python environment changed

    def __init__(self, translator=None):
        super().__init__()
        self.path: str = ""
        self.info: EngineInfo | None = None
        self._translator = translator

    @property
    def translator(self):
        if self._translator is None:
            from ..translate.service import Translator
            self._translator = Translator()
        return self._translator

    def set_project(self, path: str) -> EngineInfo | None:
        self.path = str(path)
        self.info = detect_engine(path) if path else None
        self.project_changed.emit(self.info)
        return self.info


class ProjectPage(QWidget):
    def __init__(self, ctx: HubContext, hub: "HubWindow"):
        super().__init__()
        self.ctx, self.hub = ctx, hub
        v = QVBoxLayout(self)
        title = QLabel("<h2>RPGM Hub</h2>Open an RPG Maker game (MV, MZ, VX Ace or VX) to upscale it, edit its saves "
                       "and translate its names offline.")
        title.setWordWrap(True)
        v.addWidget(title)
        row = QHBoxLayout()
        self.edit = QLineEdit(); self.edit.setPlaceholderText("Game folder (or any file inside it, such as a save)")
        self.browse = QPushButton("Browse…"); self.open_btn = QPushButton("Open")
        row.addWidget(self.edit, 1); row.addWidget(self.browse); row.addWidget(self.open_btn)
        v.addLayout(row)
        self.badge = QLabel("No game open."); self.badge.setTextFormat(Qt.PlainText); self.badge.setWordWrap(True)
        self.badge.setStyleSheet("font-size:15px;padding:8px")
        self.edit.setAcceptDrops(False)
        v.addWidget(self.badge)
        nav = QHBoxLayout()
        self.go_up = QPushButton("Upscale this game →"); self.go_saves = QPushButton("Edit saves →")
        self.go_tr = QPushButton("Translate this game →")
        self.go_save_file = QPushButton("Open a save file…")
        for w in (self.go_up, self.go_saves, self.go_tr, self.go_save_file):
            nav.addWidget(w)
        nav.addStretch(1)
        v.addLayout(nav)
        v.addStretch(1)
        self.browse.clicked.connect(self._browse)
        self.open_btn.clicked.connect(self.open_path)
        self.edit.returnPressed.connect(self.open_path)
        self.go_up.clicked.connect(lambda: hub.show_page("Upscale"))
        self.go_saves.clicked.connect(lambda: hub.show_page("Saves"))
        self.go_tr.clicked.connect(lambda: hub.show_page("Translate game"))
        self.go_save_file.clicked.connect(self._open_save_file)
        self._enable_nav(False)

    def _enable_nav(self, on: bool) -> None:
        self.go_up.setEnabled(on)
        self.go_saves.setEnabled(on)
        self.go_tr.setEnabled(on)

    def _browse(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Select the game folder", self.edit.text() or str(Path.home()))
        if d:
            self.edit.setText(d)
            self.open_path()

    def _open_save_file(self) -> None:
        f, _ = QFileDialog.getOpenFileName(self, "Open a save file", self.edit.text() or str(Path.home()),
                                           "Saves (*.rpgsave *.rmmzsave *.rvdata2 *.rvdata *.rxdata)")
        if f:
            self.hub.open_save_file(f)

    def open_path(self) -> None:
        self.hub.open_project(self.edit.text().strip())

    def show_info(self, info: EngineInfo | None, path: str) -> None:
        if not path:
            return
        if info is None:
            self.badge.setText("This does not look like an RPG Maker game (no index.html or Game.ini found).")
            self._enable_nav(False)
            return
        saves = len(find_saves(info.root))
        extra = f"  |  encrypted archive: {info.archive.name}" if info.archive else ""
        sup = ""
        self.badge.setText(f"{info.label}\n{info.root}\n{saves} save file(s) found{extra}{sup}")
        self._enable_nav(True)


class HubWindow(QMainWindow):
    def __init__(self, translator=None):
        super().__init__()
        self.setWindowTitle("RPGM Hub")
        self.resize(1280, 880)
        self.setAcceptDrops(True)
        self.ctx = HubContext(translator)
        central = QWidget(); h = QHBoxLayout(central)
        self.sidebar = QListWidget(); self.sidebar.setFixedWidth(150)
        self.sidebar.addItems(PAGES)
        self.stack = QStackedWidget()
        h.addWidget(self.sidebar); h.addWidget(self.stack, 1)
        self.setCentralWidget(central)

        self.project_page = ProjectPage(self.ctx, self)
        self.upscale = UpscaleTab()
        self.saves = SavesTab(self.ctx)
        self.game_translate = GameTranslateTab(self.ctx)
        self.translation = TranslateTab(self.ctx)
        self.setup = SetupPage(self.ctx)
        self.ctx.setup_changed.connect(self.translation.refresh)
        self.ctx.setup_changed.connect(self.game_translate.refresh_model)
        self.log_box = QPlainTextEdit(); self.log_box.setReadOnly(True); self.log_box.setMaximumBlockCount(5000)
        for w in (self.project_page, self.upscale, self.saves, self.game_translate, self.translation, self.setup, self.log_box):
            self.stack.addWidget(w)
        self.sidebar.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.sidebar.setCurrentRow(0)
        self.ctx.log.connect(lambda lvl, msg: self.log_box.appendPlainText(("[!] " if lvl in ("error", "warning") else "") + msg))
        for edit in self.findChildren(QLineEdit):          # a line edit would swallow a dropped path as text
            edit.setAcceptDrops(False)
        from ..core.settings import load_settings
        last = load_settings().get("last_project")
        if isinstance(last, str) and Path(last).is_dir():  # offered in the Project page, not opened behind the user's back
            self.project_page.edit.setText(last)

    def show_page(self, name: str) -> None:
        self.sidebar.setCurrentRow(PAGES.index(name))

    def open_project(self, path: str) -> EngineInfo | None:
        info = self.ctx.set_project(path)
        self.project_page.edit.setText(path)
        self.project_page.show_info(info, path)
        if info is not None:
            self.ctx.log.emit("info", f"opened {info.label}: {info.root}")
            self.upscale.set_project(str(info.root))
        return info

    def open_save_file(self, path: str) -> None:
        """Open the game around a save and then that one save. The saves tab would otherwise open the game's first save
        on its own, so a save would be read twice and an unsaved edit could be asked about twice."""
        if not self.saves.maybe_discard():
            return
        if self.saves.doc is not None:
            self.saves.doc.dirty = False                   # the user just agreed to drop the edits: do not ask again below
        self.saves._auto_open = False
        try:
            info = self.open_project(str(Path(path).parent))
            if info is None:
                self.ctx.set_project(str(Path(path).parent))
        finally:
            self.saves._auto_open = True
        self.show_page("Saves")
        if self.saves.open_file(path):
            self.saves.select_slot_for(path)

    # ---- drag and drop: a game folder, a save file, or any file inside a game
    def dragEnterEvent(self, ev) -> None:  # noqa: N802
        if ev.mimeData().hasUrls() and any(u.isLocalFile() for u in ev.mimeData().urls()):
            ev.acceptProposedAction()

    def dropEvent(self, ev) -> None:  # noqa: N802
        for u in ev.mimeData().urls():
            if not u.isLocalFile():
                continue
            self.open_dropped(u.toLocalFile())
            ev.acceptProposedAction()
            return

    def open_dropped(self, path: str) -> None:
        p = Path(path)
        if p.is_file() and (p.suffix.lower() in (".rpgsave", ".rmmzsave")
                            or (p.suffix.lower() in (".rvdata2", ".rvdata", ".rxdata") and p.stem.lower().startswith("save"))):
            self.open_save_file(str(p))
        elif p.is_file():
            self.open_project(str(p.parent))
        else:
            self.open_project(str(p))

    def closeEvent(self, ev) -> None:  # noqa: N802
        ok = all(t.shutdown() for t in (self.saves, self.upscale, self.game_translate, self.translation, self.setup))
        if ok and self.ctx.path:
            from ..core.settings import save_settings
            save_settings({"last_project": self.ctx.path})
        ev.accept() if ok else ev.ignore()
