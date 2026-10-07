import os
import shutil
from pathlib import Path

import pytest

pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from rpgm_upscaler.saves.files import open_save  # noqa: E402
from rpgm_upscaler.translate.cache import Cache  # noqa: E402
from rpgm_upscaler.translate.service import Translator  # noqa: E402
from tests import fakesaves  # noqa: E402
from tests.fakegame import make_game  # noqa: E402
from tests.qtutil import wait_for  # noqa: E402

FIX = Path(__file__).parent / "fixtures" / "rgss"


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


def test_opening_a_save_file_reads_it_once_and_selects_its_slot(hub, tmp_path, monkeypatch, dialogs):
    from rpgm_upscaler.gui import saves_tab
    g = make_game(tmp_path / "g", "MV")
    fakesaves.write_mv(g / "save" / "file1.rpgsave")
    second = fakesaves.write_mv(g / "save" / "file2.rpgsave")
    opened = []
    real = saves_tab.open_save
    monkeypatch.setattr(saves_tab, "open_save", lambda p: (opened.append(Path(p).name), real(p))[1])
    hub.open_save_file(str(second))
    assert opened == ["file2.rpgsave"]                              # not file1 first and file2 after it
    assert hub.saves.doc.path.name == "file2.rpgsave" and hub.saves.slots.currentRow() == 1
    assert not [d for d in dialogs if d[0] == "question"]
    hub.saves.wait_for_translation()


def test_clicking_a_slot_loads_on_a_worker_and_only_the_last_click_wins(hub, tmp_path):
    g = make_game(tmp_path / "g", "MV")
    for n in (1, 2, 3):
        fakesaves.write_mv(g / "save" / f"file{n}.rpgsave")
    hub.open_project(str(g))
    s = hub.saves
    s.slots.setCurrentRow(1)
    s.slots.setCurrentRow(2)                                        # a second request while the first may still be reading
    assert wait_for(lambda: s.doc is not None and s.doc.path.name == "file3.rpgsave" and s.sw_table.isEnabled())
    assert s.slots.currentRow() == 2
    s.wait_for_translation()


def test_a_refused_edit_puts_the_old_value_back_in_the_cell(hub, tmp_path, dialogs):
    g = make_game(tmp_path / "g", "MV")
    fakesaves.write_database(g)
    fakesaves.write_mv(g / "save" / "file1.rpgsave")
    hub.open_project(str(g))
    s = hub.saves
    r = 0
    old = s.inv_table.item(r, 4).text()
    s.inv_table.item(r, 4).setText("many")
    assert s.inv_table.item(r, 4).text() == old and any(d[0] == "warning" for d in dialogs)
    assert not s.doc.dirty
    s.wait_for_translation()


def test_dropping_a_game_folder_or_a_save_opens_it(hub, tmp_path):
    from PySide6.QtCore import QMimeData, QUrl
    g = make_game(tmp_path / "g", "MV")
    f = fakesaves.write_mv(g / "save" / "file1.rpgsave")

    class Ev:
        def __init__(self, path):
            self.m = QMimeData(); self.m.setUrls([QUrl.fromLocalFile(str(path))]); self.ok = False
        def mimeData(self): return self.m
        def acceptProposedAction(self): self.ok = True
    ev = Ev(g)
    hub.dragEnterEvent(ev); assert ev.ok
    hub.dropEvent(Ev(g))
    assert hub.ctx.info is not None and hub.ctx.info.engine == "MV"
    hub.dropEvent(Ev(f))
    assert hub.saves.doc is not None and hub.saves.doc.path.name == "file1.rpgsave" and hub.stack.currentWidget() is hub.saves
    assert not Ev(tmp_path).ok
    hub.saves.wait_for_translation()


def test_settings_of_every_tab_survive_each_others_saves(tmp_path, monkeypatch):
    from rpgm_upscaler.core.settings import load_settings, save_settings
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    save_settings({"source": "/a", "options": {"scale": "2"}})
    save_settings({"translate_game": {"fast": True}})
    save_settings({"source": "/b"})
    assert load_settings() == {"source": "/b", "options": {"scale": "2"}, "translate_game": {"fast": True}}


def test_game_translate_options_are_remembered(hub, tmp_path):
    t = hub.game_translate
    t.cb_fast.setChecked(True); t.cb_dialogue.setChecked(False); t.wrap.setValue(33); t.font_edit.setText("/f.ttf")
    t.shutdown()
    from rpgm_upscaler.gui.gametranslate_tab import GameTranslateTab
    again = GameTranslateTab(hub.ctx)
    assert again.cb_fast.isChecked() and not again.cb_dialogue.isChecked() and again.wrap.value() == 33 and again.font_edit.text() == "/f.ttf"


def test_preview_picked_while_one_renders_is_queued_not_dropped(hub, tmp_path):
    g = make_game(tmp_path / "g", "MZ")
    up = hub.upscale
    up.src_edit.setText(str(g)); up.out_edit.setText(str(tmp_path / "out"))
    up.analyze()
    assert wait_for(lambda: up.plan is not None)
    up.table.selectRow(0)
    assert up.files.count() > 0

    class Busy:
        def isRunning(self): return True
    up._preview_worker = Busy()
    up.files.setCurrentRow(0)
    assert up._preview_pending == 0
    up._preview_worker = None
    up._preview_finished()
    assert wait_for(lambda: up.before_lbl.pixmap() is not None and not up.before_lbl.pixmap().isNull())
    assert up._preview_pending is None


def test_preview_pixmaps_are_crisp_for_small_images_and_hidpi_aware():
    from PIL import Image
    from rpgm_upscaler.gui.upscale_tab import pil_to_pixmap
    img = Image.new("RGBA", (4, 4), (255, 0, 0, 255)); img.putpixel((1, 1), (0, 0, 255, 255))
    pm = pil_to_pixmap(img, max_side=64, dpr=1.0)
    assert pm.width() == 64                                         # enlarged by a whole factor of 16
    qi = pm.toImage()
    assert qi.pixelColor(20, 20).blue() == 255 and qi.pixelColor(20, 20).red() == 0 and qi.pixelColor(15, 15).red() == 255
    big = pil_to_pixmap(Image.new("RGBA", (400, 100)), max_side=100, dpr=2.0)
    assert big.devicePixelRatio() == 2.0 and big.width() == 200


def test_opening_a_save_with_unsaved_edits_asks_once(hub, tmp_path, dialogs):
    g = make_game(tmp_path / "g", "MV")
    fakesaves.write_mv(g / "save" / "file1.rpgsave")
    second = fakesaves.write_mv(g / "save" / "file2.rpgsave")
    hub.open_project(str(g))
    hub.saves.gold.setValue(7); hub.saves.gold.editingFinished.emit()
    assert hub.saves.doc.dirty
    dialogs.clear()
    hub.open_save_file(str(second))
    assert [d[0] for d in dialogs].count("question") == 1 and hub.saves.doc.path.name == "file2.rpgsave"
    hub.saves.wait_for_translation()
