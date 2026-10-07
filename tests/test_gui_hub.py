import os
import shutil
from pathlib import Path

import pytest

pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEventLoop, Qt, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from rpgm_upscaler.saves.files import open_save  # noqa: E402
from rpgm_upscaler.translate.cache import Cache  # noqa: E402
from rpgm_upscaler.translate.service import Translator  # noqa: E402
from tests import fakesaves  # noqa: E402
from tests.fakegame import make_game  # noqa: E402

FIX = Path(__file__).parent / "fixtures" / "rgss"


def wait_for(cond, timeout=30000):
    loop = QEventLoop()
    t = QTimer(); t.setInterval(40)
    waited = [0]

    def tick():
        waited[0] += 40
        if cond() or waited[0] > timeout:
            loop.quit()
    t.timeout.connect(tick); t.start()
    loop.exec(); t.stop()
    return cond()


@pytest.fixture(autouse=True)
def no_modal_dialogs(monkeypatch):
    """A modal box would block an offscreen test forever: answer every dialog instead."""
    for name in ("question",):
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda *a, **k: QMessageBox.Yes))
    for name in ("warning", "critical", "information"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda *a, **k: QMessageBox.Ok))


@pytest.fixture
def hub(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    QApplication.instance() or QApplication([])
    from rpgm_upscaler.gui.hub import HubWindow
    tr = Translator(overrides_path=tmp_path / "ov.json", cache=Cache(None), use_default_backend=False)
    w = HubWindow(translator=tr)
    w.show()
    yield w
    w.saves.doc and setattr(w.saves.doc, "dirty", False)
    w.close()


def row_for(table, id_):
    return next(r for r in range(table.rowCount()) if table.item(r, 0).text() == str(id_))


def test_hub_mv_save_editing_flow(hub, tmp_path):
    g = make_game(tmp_path / "g", "MV")
    fakesaves.write_database(g)
    fakesaves.write_mv(g / "save" / "file1.rpgsave")
    fakesaves.write_mv(g / "save" / "file2.rpgsave")
    info = hub.open_project(str(g))
    assert info.engine == "MV" and "RPG Maker MV" in hub.project_page.badge.text() and "2 save" in hub.project_page.badge.text()
    hub.show_page("Saves")
    s = hub.saves
    assert s.slots.count() == 2 and s.doc is not None and s.doc.path.name == "file1.rpgsave"
    assert s.sw_table.item(1, 1).text() == "ボス撃破" and s.gold.value() == 1234
    assert wait_for(lambda: s.sw_table.item(1, 2).text() == "Boss Defeated")        # offline translation fills in
    assert s.inv_table.item(0, 2).text() == "ポーション" and wait_for(lambda: s.inv_table.item(0, 3).text() == "Potion")
    # edit: toggle switch 2, change variable 3, gold, actor level, item count
    s.sw_table.item(row_for(s.sw_table, 2), 3).setCheckState(Qt.Checked)
    s.var_table.item(row_for(s.var_table, 3), 3).setText("999")
    s.gold.setValue(5000); s.gold.editingFinished.emit()
    s.party_table.item(0, 2).setText("42")
    s.inv_table.item(0, 4).setText("77")
    assert s.doc.dirty and s.save_btn.isEnabled()
    assert s.save()
    assert (g / "save" / "file1.rpgsave.bak").exists()
    again = open_save(g / "save" / "file1.rpgsave")
    assert again.get_switch(2) and again.get_variable(3) == 999 and again.gold() == 5000
    assert again.actor(1).level == 42 and again.inventory("items")[1] == 77
    # filter + "on only"
    s.sw_filter.setText("door")
    assert wait_for(lambda: True)
    s.sw_filter.setText("ドア")
    assert not s.sw_table.isRowHidden(0) and s.sw_table.isRowHidden(1)
    s.sw_filter.setText(""); s.sw_changed.setChecked(True)
    assert not s.sw_table.isRowHidden(row_for(s.sw_table, 1)) and s.sw_table.isRowHidden(row_for(s.sw_table, 4))
    # add item through the combo
    s.tabs.setCurrentIndex(3)
    s.add_kind.setCurrentText("armors"); s.add_count.setValue(3); s.add_btn.click()
    assert s.doc.inventory("armors") == {1: 3}
    # revert drops unsaved edits
    s.gold.setValue(1); s.gold.editingFinished.emit()
    assert s.doc.gold() == 1
    s.doc.dirty = False
    s.revert()
    assert s.doc.gold() == 5000 and s.gold.value() == 5000
    s.wait_for_translation()


def test_hub_ace_save_and_readonly(hub, tmp_path):
    g = tmp_path / "ace_game"
    shutil.copytree(FIX / "ace_game", g)
    hub.open_project(str(g))
    s = hub.saves
    assert hub.ctx.info.engine == "ACE" and s.doc is not None and s.doc.engine == "ACE"
    assert s.sw_table.item(0, 1).text() == "ドア開放" and s.party_table.item(0, 1).text() == "アレックス"
    s.gold.setValue(777); s.gold.editingFinished.emit()
    s.party_table.item(1, 2).setText("12")
    assert s.save()
    t = open_save(g / "Save01.rvdata2")
    assert t.gold() == 777 and t.actor(2).level == 12 and t.readonly is None
    # a save the codec cannot round-trip opens read-only and refuses edits
    bad = tmp_path / "Save09.rvdata2"
    bad.write_bytes(b"\x04\x08[\x06i\x06")
    assert s.open_file(bad)
    assert "READ-ONLY" in s.banner.text() and not s.save_btn.isEnabled() and not s.gold.isEnabled()
    s.wait_for_translation()


def test_hub_open_save_file_and_not_a_game(hub, tmp_path):
    g = make_game(tmp_path / "g", "MZ")
    f = fakesaves.write_mz(g / "save" / "file1.rmmzsave")
    hub.open_save_file(str(f))
    assert hub.saves.doc.engine == "MZ" and hub.stack.currentWidget() is hub.saves
    assert not hub.saves.doc.dirty                       # merely opening a save must not mark it modified
    assert hub.open_project(str(tmp_path / "nothing")) is None
    assert "does not look like" in hub.project_page.badge.text() and not hub.project_page.go_up.isEnabled()
    hub.saves.wait_for_translation()


def test_hub_translation_tab(hub, tmp_path, monkeypatch):
    t = hub.translation
    assert "Neural model: missing" in t.status.text() and "Glossary:" in t.status.text()
    t.try_in.setText("ボス撃破")
    t.try_btn.click()
    assert wait_for(lambda: t.try_out.text() == "Boss Defeated")
    t.table.insertRow(0)
    t.table.setItem(0, 0, __import__("PySide6.QtWidgets", fromlist=["x"]).QTableWidgetItem("ボス撃破"))
    t.table.setItem(0, 1, __import__("PySide6.QtWidgets", fromlist=["x"]).QTableWidgetItem("Boss Slain"))
    assert hub.ctx.translator.overrides == {"ボス撃破": "Boss Slain"}
    t.table.selectRow(0); t.del_row.click()
    assert hub.ctx.translator.overrides == {}
    # importing a broken model reports an error instead of crashing
    shown = []
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: shown.append(a[2])))
    bad = tmp_path / "bad.argosmodel"; bad.write_bytes(b"nope")
    t._start_model(str(bad))
    assert wait_for(lambda: bool(shown)) and "not a readable" in shown[0]


def test_hub_upscale_tab_handles_vx_ace(hub, tmp_path):
    g = tmp_path / "ace_game"
    shutil.copytree(FIX / "ace_game", g)
    from tests.fakeace import make_ace
    ace = make_ace(tmp_path / "aceart", archive=True)
    hub.open_project(str(ace))
    up = hub.upscale
    assert wait_for(lambda: up.plan is not None)
    assert up.plan.mode == "hires" and "VX Ace" in up.info.text() and not up.vx_mode.isHidden()
    up.out_edit.setText(str(tmp_path / "out"))
    up.movies.setChecked(False); up.workers.setValue(1)
    up.table.selectRow(0)
    assert up.files.count() > 0
    up.files.setCurrentRow(0)
    assert wait_for(lambda: up.before_lbl.pixmap() is not None and not up.before_lbl.pixmap().isNull())   # preview works on archived games
    up.start()
    assert wait_for(lambda: up.worker is None and not up._busy, 120000)
    assert (tmp_path / "out/mkxp.json").exists() and (tmp_path / "out/Hires/Graphics/Faces/Actor1.png").exists()


def test_hub_translate_game_tab(hub, tmp_path):
    from tests.test_gametranslate import DictBackend, mv_game
    g = mv_game(tmp_path, "MV")
    hub.ctx.translator._backend, hub.ctx.translator._backend_tried = DictBackend(), True
    assert not hub.game_translate.start_btn.isEnabled()                    # nothing open yet
    hub.open_project(str(g))
    t = hub.game_translate
    assert t.start_btn.isEnabled() and "RPG Maker MV" in t.game_label.text() and t.out_edit.text().endswith("_EN")
    t.out_edit.setText(str(tmp_path / "out"))
    t.cb_plugins.setChecked(True)
    t.start()
    assert t.cancel_btn.isEnabled() or t._worker is None
    assert wait_for(lambda: t._worker is None and "strings translated" in t.result.toPlainText())
    assert t.open_btn.isEnabled() and (tmp_path / "out/.translation/report.tsv").is_file()
    assert "Village" in (tmp_path / "out/data/Map001.json").read_text(encoding="utf-8")
    # asking for image translation without the tools reports it instead of failing
    t.cb_ocr.setChecked(True)
    assert t.ocr_status.text()
    assert t.shutdown()
