# PyInstaller spec: `pyinstaller --noconfirm packaging/rpgm-hub.spec` (see tools/build_bundle.sh).
# One folder with a single `rpgm-hub` executable: `rpgm-hub [GAME]` opens the window, `rpgm-hub analyze|plan|run|... ` is the CLI.
import os
import sys

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = os.path.dirname(SPECPATH)               # SPECPATH is this file's folder; the package lives one level up
sys.path.insert(0, ROOT)

hidden = collect_submodules("rpgm_upscaler")
for optional in ("ctranslate2", "sentencepiece", "cv2"):           # bundled only when installed in the build environment
    try:
        hidden += collect_submodules(optional)
    except Exception:
        pass
datas = collect_data_files("rpgm_upscaler") + collect_data_files("ctranslate2") + collect_data_files("cv2")

a = Analysis([os.path.join(ROOT, "packaging", "rpgm_hub_entry.py")], pathex=[ROOT], datas=datas, hiddenimports=hidden,
             excludes=["tkinter", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.Qt3DCore", "PySide6.QtQuick",
                       "PySide6.QtQml", "PySide6.QtMultimedia", "PySide6.QtCharts", "PySide6.QtDataVisualization",
                       "PySide6.QtPdf", "PySide6.QtDesigner", "PySide6.QtSql", "PySide6.QtTest"])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="rpgm-hub", console=True)
coll = COLLECT(exe, a.binaries, a.datas, name="rpgm-hub")
