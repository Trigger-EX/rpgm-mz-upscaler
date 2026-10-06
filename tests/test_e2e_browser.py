"""Optional end-to-end test: boots a real MV engine in headless Chromium, original vs upscaled.

Enabled by setting RPGM_CORESCRIPT to a checkout of https://github.com/rpgtkoolmv/corescript (MIT) and
RPGM_TEST_FONT to any .ttf. Needs node + playwright + Chromium (see tools/run_e2e.sh).
"""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

CORE = os.environ.get("RPGM_CORESCRIPT")
FONT = os.environ.get("RPGM_TEST_FONT", "")
ROOT = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.skipif(not (CORE and shutil.which("node")), reason="set RPGM_CORESCRIPT (and have node) to run")


@pytest.mark.parametrize("extra,env", [([], {}), (["--scale", "2"], {}), ([], {"ENCRYPT": "1"})])
def test_browser_e2e(tmp_path, extra, env):
    r = subprocess.run([str(ROOT / "tools/run_e2e.sh"), CORE, str(tmp_path / "w"), FONT, *extra],
                       capture_output=True, text=True, env={**os.environ, **env, "PY": os.sys.executable}, timeout=900)
    assert "RESULT: PASS" in r.stdout, r.stdout[-3000:] + r.stderr[-1000:]
