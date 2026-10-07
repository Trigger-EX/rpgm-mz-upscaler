import pytest


@pytest.fixture(autouse=True)
def dialogs(monkeypatch):
    """A modal QMessageBox would block an offscreen test forever. Every one is answered at once (Yes / Ok) and recorded as
    (kind, title, text) in the list this fixture returns, so a test can also assert that a dialog did or did not appear."""
    calls: list[tuple[str, str, str]] = []
    try:
        from PySide6.QtWidgets import QMessageBox
    except ImportError:
        return calls

    def stub(kind, answer):
        def f(*args, **kw):
            calls.append((kind, str(args[1]) if len(args) > 1 else "", str(args[2]) if len(args) > 2 else ""))
            return answer
        return staticmethod(f)

    monkeypatch.setattr(QMessageBox, "question", stub("question", QMessageBox.Yes))
    for kind in ("warning", "critical", "information"):
        monkeypatch.setattr(QMessageBox, kind, stub(kind, QMessageBox.Ok))
    return calls
