"""Backwards-compatible single-window wrapper around the Upscale tab (the hub embeds the tab itself)."""
from __future__ import annotations

from PySide6.QtWidgets import QMainWindow

from .upscale_tab import UpscaleTab, pil_to_pixmap  # noqa: F401 (re-export)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("RPGM Upscaler")
        self.resize(1200, 860)
        self.tab = UpscaleTab(self)
        self.setCentralWidget(self.tab)

    def __getattr__(self, name):          # tests and callers use the tab's widgets and methods directly
        if name == "tab":
            raise AttributeError(name)
        return getattr(self.tab, name)

    def closeEvent(self, ev) -> None:  # noqa: N802
        ev.accept() if self.tab.shutdown() else ev.ignore()
