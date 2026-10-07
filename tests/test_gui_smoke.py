import os

import pytest

pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from tests.fakegame import make_game  # noqa: E402
from tests.qtutil import wait_for  # noqa: E402


def test_gui_flow(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    app = QApplication.instance() or QApplication([])
    from rpgm_upscaler.gui.main_window import MainWindow
    g = make_game(tmp_path / "g", "MZ")
    w = MainWindow()
    w.src_edit.setText(str(g)); w.out_edit.setText(str(tmp_path / "out"))
    w.movies.setChecked(False); w.workers.setValue(1)
    w.analyze()
    assert wait_for(lambda: w.plan is not None)
    assert w.table.rowCount() >= 8 and "MZ" in w.info.text()
    assert "x1.625" in w.scale_info.text()
    w.table.selectRow(0)
    assert w.files.count() > 0
    w.files.setCurrentRow(0)
    assert wait_for(lambda: w.before_lbl.pixmap() is not None and not w.before_lbl.pixmap().isNull())
    w.start()
    assert wait_for(lambda: w.worker is None and not w._busy)
    assert (tmp_path / "out/js/plugins/UpscalerPatch.js").is_file()
    assert "failed" in w.progress_lbl.text() and "0 failed" in w.progress_lbl.text()
    w.close()
