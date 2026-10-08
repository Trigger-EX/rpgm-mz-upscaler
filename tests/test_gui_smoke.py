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


def _slow_stub(tmp_path):
    """An ncnn stand-in that takes a moment per call, so a run can be cancelled or closed halfway."""
    exe = tmp_path / "realesrgan-ncnn-vulkan"
    exe.write_text("#!/usr/bin/env python3\nimport sys, time, pathlib\nfrom PIL import Image\n"
                   "a=sys.argv; i=pathlib.Path(a[a.index('-i')+1]); o=pathlib.Path(a[a.index('-o')+1]); s=int(a[a.index('-s')+1])\n"
                   "time.sleep(0.4)\n"
                   "pairs = [(f, o / f.name) for f in sorted(i.iterdir())] if i.is_dir() else [(i, o)]\n"
                   "for f, d in pairs:\n    im = Image.open(f); im.resize((im.width*s, im.height*s)).save(d)\n")
    exe.chmod(0o755)
    return exe


def _window(tmp_path, monkeypatch, slow=False):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    QApplication.instance() or QApplication([])
    from rpgm_upscaler.gui.main_window import MainWindow
    g = make_game(tmp_path / "g", "MZ")
    w = MainWindow()
    w.src_edit.setText(str(g)); w.out_edit.setText(str(tmp_path / "out"))
    w.movies.setChecked(False); w.workers.setValue(1)
    if slow:
        w.engine.setCurrentText("realesrgan"); w.engine_path.setText(str(_slow_stub(tmp_path)))
    return w


def test_a_run_that_cannot_start_reports_failure_and_leaves_the_window_usable(tmp_path, monkeypatch, dialogs):
    w = _window(tmp_path, monkeypatch)
    w.out_edit.setText(str(tmp_path / "g" / "inside"))               # output inside the game is refused
    w.start()
    assert wait_for(lambda: w.worker is None and not w._busy)
    assert any(d[0] == "critical" and d[1] == "Run failed" for d in dialogs)
    assert w.start_btn.isEnabled() and not w.cancel_btn.isEnabled()
    w.close()


def test_cancelling_a_run_stops_it_and_resume_finishes_the_job(tmp_path, monkeypatch, dialogs):
    w = _window(tmp_path, monkeypatch, slow=True)
    w.start()
    assert wait_for(lambda: "/" in w.progress_lbl.text() and w.progress.value() >= 1, 60000)
    w.cancel()
    assert wait_for(lambda: w.worker is None and not w._busy)
    assert "CANCELLED" in w.progress_lbl.text()
    assert w.start_btn.isEnabled()
    w.resume.setChecked(True)
    w.start()
    assert wait_for(lambda: w.worker is None and not w._busy, 120000)
    assert "CANCELLED" not in w.progress_lbl.text() and "0 failed" in w.progress_lbl.text()
    w.close()


def test_closing_during_a_run_cancels_it_cleanly_and_a_no_keeps_it_running(tmp_path, monkeypatch, dialogs):
    from PySide6.QtWidgets import QMessageBox
    w = _window(tmp_path, monkeypatch, slow=True)
    w.start()
    assert wait_for(lambda: w.worker is not None and w.worker.isRunning())
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.No))
    assert w.tab.shutdown() is False and w.worker is not None          # the user kept the job
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))
    assert w.tab.shutdown() is True
    assert w.worker is None or not w.worker.isRunning()
    w.close()


def test_model_dropdown_follows_the_engine(tmp_path, monkeypatch):
    w = _window(tmp_path, monkeypatch)
    w.engine.setCurrentText("lanczos")
    assert not w.model.isEnabled() and w.options().model == ""
    w.engine.setCurrentText("realesrgan")
    assert w.model.isEnabled() and w.model.itemText(0) == "realesr-animevideov3-x4"
    w.model.setCurrentText("realesrgan-x4plus-anime")
    assert w.options().model == "realesrgan-x4plus-anime"
    w.engine.setCurrentText("waifu2x")
    assert w.model.currentText() == "models-cunet"
    w.close()
