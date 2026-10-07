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


def test_enemy_position_follows_the_content_offset(tmp_path):
    """The sample troop has a slime at the middle of the 816x624 screen; upscaled, it must stay at the middle of the scaled content
    (MV: engine coordinates + the content offset; the MZ engine already centres its battle field)."""
    import functools
    import json
    import threading
    from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
    py = os.sys.executable
    sample, out = tmp_path / "sample", tmp_path / "out"
    cmds = ([py, str(ROOT / "tools/make_sample_game.py"), "--corescript", CORE, "--out", str(sample)]
            + (["--font", FONT] if FONT else []),
            [py, "-m", "rpgm_upscaler.cli", "run", str(sample), "-o", str(out), "--no-movies"])
    for c in cmds:
        assert subprocess.run(c, capture_output=True, text=True, cwd=ROOT).returncode == 0

    def serve(folder):
        h = functools.partial(SimpleHTTPRequestHandler, directory=str(folder))
        h.log_message = lambda *a, **k: None
        srv = ThreadingHTTPServer(("127.0.0.1", 0), h)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        return srv

    env = {**os.environ, "NODE_PATH": os.environ.get("NODE_PATH", "/opt/node22/lib/node_modules"),
           "PLAYWRIGHT_MODULE": os.environ.get("PLAYWRIGHT_MODULE", "/opt/node22/lib/node_modules/playwright")}
    pos = {}
    for name, folder, size in (("orig", sample, ("816", "624")), ("up", out, ("1920", "1080"))):
        srv = serve(folder)
        r = subprocess.run(["node", str(ROOT / "tools/e2e_battle_position.js"), f"http://127.0.0.1:{srv.server_address[1]}/index.html", *size],
                           capture_output=True, text=True, env=env, timeout=300)
        srv.shutdown()
        assert r.returncode == 0, r.stderr[-1500:]
        pos[name] = json.loads(r.stdout.strip().splitlines()[-1])
    n, (ox, oy) = 1.625, (297, 33)
    ex, ey = pos["orig"]["enemy"][0] * n + ox, pos["orig"]["enemy"][1] * n + oy
    assert abs(pos["up"]["enemy"][0] - ex) <= 3 and abs(pos["up"]["enemy"][1] - ey) <= 3, (pos, ex, ey)
