#!/usr/bin/env python3
"""Real-engine check of the MV save editor: the engine writes a save, we edit it, the engine loads it back.

usage: e2e_saves.py <sample-game-dir>   (build one with tools/make_sample_game.py; needs node + playwright + chromium)
"""
import json
import os
import subprocess
import sys
import tempfile
import threading
import functools
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rpgm_upscaler.saves.files import open_save  # noqa: E402

NODE_ENV = {**os.environ, "NODE_PATH": os.environ.get("NODE_PATH", "/opt/node22/lib/node_modules")}
JS = r"""
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || '/opt/node22/lib/node_modules/playwright');
(async () => {
  const [url, mode, file] = process.argv.slice(2);
  const fs = require('fs');
  const b = await chromium.launch({ executablePath: process.env.CHROMIUM || '/opt/pw-browsers/chromium-1194/chrome-linux/chrome',
    args: ['--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--no-sandbox'] });
  const p = await b.newPage();
  p.on('console', (m) => { if (m.type() === 'error') console.error('PAGE ERROR:', m.text().slice(0, 400)); });
  await p.goto(url);
  await p.waitForFunction(() => window.SceneManager && SceneManager._scene && SceneManager._scene.constructor.name === 'Scene_Title', null, { timeout: 60000 });
  if (mode === 'write') {
    const out = await p.evaluate(() => {
      DataManager.setupNewGame(); $gameParty.gainGold(500); $gameSwitches.setValue(3, true); $gameVariables.setValue(2, 42);
      $gameParty.gainItem($dataItems[1], 3); $gameActors.actor(1).changeLevel(4, false);
      DataManager.saveGame(1); return [localStorage.getItem('RPG File1'), localStorage.getItem('RPG Global')];
    });
    fs.writeFileSync(file, out[0]); fs.writeFileSync(file + '.global', out[1]);
  } else {
    await p.evaluate(([data, glob]) => { localStorage.setItem('RPG File1', data); localStorage.setItem('RPG Global', glob); }, [fs.readFileSync(file, 'utf8'), fs.readFileSync(file + '.global', 'utf8')]);
    const r = await p.evaluate(() => { DataManager._globalInfo = null; /* the engine caches it at boot */ const ok = DataManager.loadGame(1);
      return { ok, gold: $gameParty.gold(), sw: [$gameSwitches.value(2), $gameSwitches.value(3)], v2: $gameVariables.value(2),
               item1: $gameParty.numItems($dataItems[1]), item2: $gameParty.numItems($dataItems[2]), level: $gameActors.actor(1).level, hp: $gameActors.actor(1).hp, map: $gameMap.mapId(), x: $gamePlayer.x, y: $gamePlayer.y }; });
    console.log(JSON.stringify(r));
  }
  await b.close();
})().catch((e) => { console.error(e); process.exit(1); });
"""


def node(js_path, *args):
    r = subprocess.run(["node", str(js_path), *args], capture_output=True, text=True, env=NODE_ENV, timeout=300)
    if r.stderr.strip():
        print(r.stderr.strip()[:1500])
    if r.returncode:
        raise SystemExit(r.stderr)
    return r.stdout.strip()


def main() -> int:
    game = Path(sys.argv[1]).resolve()
    work = Path(tempfile.mkdtemp())
    (work / "t.js").write_text(JS)
    handler = functools.partial(SimpleHTTPRequestHandler, directory=str(game))
    handler.log_message = lambda *a, **k: None
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/index.html"
    save = work / "file1.rpgsave"
    node(work / "t.js", url, "write", str(save))
    s = open_save(save)
    assert s.readonly is None, s.readonly
    print("engine-written save parsed:", "gold", s.gold(), "switch3", s.get_switch(3), "var2", s.get_variable(2),
          "items", s.inventory("items"), "level", s.actor(1).level)
    assert (s.gold(), s.get_switch(3), s.get_variable(2), s.inventory("items"), s.actor(1).level) == (500, True, 42, {1: 3}, 4)
    s.set_gold(123456); s.set_switch(2, True); s.set_variable(2, 7); s.set_item("items", 2, 9)
    s.set_actor(1, level=30, hp=555); s.set_position(map_id=1, x=2, y=3)
    s.save()
    got = json.loads(node(work / "t.js", url, "read", str(save)))
    print("engine loaded edited save:", got)
    want = {"ok": True, "gold": 123456, "sw": [True, True], "v2": 7, "item2": 9, "level": 30, "hp": 555, "map": 1, "x": 2, "y": 3}
    bad = {k: (got.get(k), v) for k, v in want.items() if got.get(k) != v}
    print("RESULT:", "FAIL " + str(bad) if bad else "PASS")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
