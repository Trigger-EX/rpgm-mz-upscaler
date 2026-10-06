import sys

from PySide6.QtWidgets import QApplication

from .hub import HubWindow


def main(argv: list[str] | None = None) -> int:
    app = QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName("RPGM Hub")
    win = HubWindow()
    win.show()
    args = (argv if argv is not None else sys.argv)[1:]
    if args:                                    # rpgm-hub /path/to/game
        win.open_project(args[0])
    return app.exec()
