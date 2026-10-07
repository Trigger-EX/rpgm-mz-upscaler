#!/usr/bin/env node
// Boots an RPG Maker MZ game in headless Chromium, visits the main scenes (title, map, menu, battle, shop, options, save, name input)
// and screenshots each, plus checks that audio decodes. Errors from the page are collected into report.json.
// usage: node e2e_mz.js <url> <outdir> <viewportW> <viewportH>
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const fs = require('fs');

(async () => {
  const [url, outdir, vw, vh] = process.argv.slice(2);
  fs.mkdirSync(outdir, { recursive: true });
  const browser = await chromium.launch({
    executablePath: process.env.CHROMIUM || '/opt/pw-browsers/chromium-1194/chrome-linux/chrome',
    args: ['--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist', '--no-sandbox', '--autoplay-policy=no-user-gesture-required'],
  });
  const page = await browser.newPage({ viewport: { width: +vw, height: +vh } });
  const logs = [];
  page.on('console', (m) => { if (['error', 'warning'].includes(m.type())) logs.push(`${m.type()}: ${m.text().slice(0, 300)}`); });
  page.on('pageerror', (e) => logs.push(`pageerror: ${e.message}`));
  page.on('requestfailed', (r) => logs.push(`requestfailed: ${r.url()}`));
  page.on('response', (r) => { if (r.status() >= 400) logs.push(`http ${r.status()}: ${r.url()}`); });
  const report = { scenes: {}, logs };
  const frames = (n) => page.evaluate((n) => new Promise((res) => { let i = 0; const f = () => (++i >= n ? res() : requestAnimationFrame(f)); f(); }), n);
  const sceneIs = (name) => page.waitForFunction((name) => SceneManager._scene && SceneManager._scene.constructor.name === name && SceneManager._scene.isReady && SceneManager._scene.isReady() && !SceneManager.isSceneChanging(), name, { timeout: 60000 });
  const visit = async (name, scene, setup) => {
    try {
      if (setup) await page.evaluate(setup);
      await sceneIs(scene);
      await frames(40);
      report.scenes[name] = await page.evaluate(() => ({
        graphics: [Graphics.width, Graphics.height, Graphics.boxWidth, Graphics.boxHeight],
        tile: $gameMap && $dataMap ? [$gameMap.tileWidth(), $gameMap.tileHeight()] : null,
        scene: SceneManager._scene.constructor.name,
        windows: (SceneManager._scene._windowLayer ? SceneManager._scene._windowLayer.children : []).filter((w) => w.visible && w.width > 0).map((w) => [w.constructor.name, Math.round(w.x), Math.round(w.y), Math.round(w.width), Math.round(w.height)]),
      }));
      await page.screenshot({ path: `${outdir}/${name}.png` });
    } catch (e) { report.scenes[name] = 'FAILED: ' + String(e).slice(0, 200); }
  };
  await page.goto(url);
  await visit('title', 'Scene_Title', null);
  await visit('map', 'Scene_Map', () => { DataManager.setupNewGame(); SceneManager.goto(Scene_Map); });
  await visit('menu', 'Scene_Menu', () => SceneManager.push(Scene_Menu));
  await visit('items', 'Scene_Item', () => SceneManager.push(Scene_Item));
  await visit('skills', 'Scene_Skill', () => SceneManager.push(Scene_Skill));
  await visit('equip', 'Scene_Equip', () => SceneManager.push(Scene_Equip));
  await visit('status', 'Scene_Status', () => SceneManager.push(Scene_Status));
  await visit('options', 'Scene_Options', () => SceneManager.push(Scene_Options));
  await visit('save', 'Scene_Save', () => SceneManager.push(Scene_Save));
  await visit('name', 'Scene_Name', () => { SceneManager.push(Scene_Name); SceneManager.prepareNextScene(1, 8); });
  await visit('shop', 'Scene_Shop', () => {
    SceneManager.goto(Scene_Map);
    return new Promise((r) => setTimeout(r, 500));
  });
  await visit('shop', 'Scene_Shop', () => { SceneManager.push(Scene_Shop); SceneManager.prepareNextScene([[0, 1, 0], [0, 2, 0], [1, 1, 0]], false); });
  await visit('battle', 'Scene_Battle', () => {
    DataManager.setupNewGame();
    const troop = $dataTroops.findIndex((t, i) => i > 0 && t && t.members.length > 0);
    $gameTemp.reserveCommonEvent && 0;
    BattleManager.setup(Math.max(troop, 1), true, false);
    $gamePlayer.setTransparent && 0;
    SceneManager.goto(Scene_Battle);
  });
  // the battle command windows (party / actor commands and the status window)
  try {
    await page.evaluate(() => { const sc = SceneManager._scene; sc._messageWindow && sc._messageWindow.terminateMessage && sc._messageWindow.terminateMessage(); BattleManager._phase = 'input'; sc.startPartyCommandSelection(); });
    await frames(40);
    report.scenes.battle_commands = await page.evaluate(() => ({ windows: SceneManager._scene._windowLayer.children.filter((w) => w.visible && w.width > 0).map((w) => [w.constructor.name, Math.round(w.x), Math.round(w.y), Math.round(w.width), Math.round(w.height)]),
      enemies: SceneManager._scene._spriteset._enemySprites.map((s) => { const b = s.getBounds(); return [Math.round(b.x), Math.round(b.y), Math.round(b.width), Math.round(b.height)]; }),
      actors: SceneManager._scene._spriteset._actorSprites.map((s) => { const b = s.getBounds(); return [Math.round(b.x), Math.round(b.y), Math.round(b.width), Math.round(b.height)]; }) }));
    await page.screenshot({ path: `${outdir}/battle_commands.png` });
  } catch (e) { report.scenes.battle_commands = 'FAILED: ' + String(e).slice(0, 200); }
  report.audio = await page.evaluate(async () => {
    try {
      const bgm = [$dataSystem.titleBgm, $dataSystem.battleBgm, ...($dataMap && $dataMap.bgm ? [$dataMap.bgm] : [])].find((b) => b && b.name) || null;
      if (!bgm) return 'no title bgm';
      AudioManager.playBgm(bgm);
      const t0 = Date.now();
      while (Date.now() - t0 < 15000) {
        const b = AudioManager._bgmBuffer;
        if (b && b.isReady()) return { bgm: bgm.name, ready: true, playing: b.isPlaying(), ms: Date.now() - t0 };
        await new Promise((r) => setTimeout(r, 200));
      }
      return { bgm: bgm.name, ready: false };
    } catch (e) { return 'ERROR ' + e; }
  });
  fs.writeFileSync(`${outdir}/report.json`, JSON.stringify(report, null, 1));
  console.log(JSON.stringify({ scenes: Object.fromEntries(Object.entries(report.scenes).map(([k, v]) => [k, typeof v === 'string' ? v : 'ok'])), audio: report.audio, logs: logs.length }));
  await browser.close();
})().catch((e) => { console.error('HARNESS ERROR', e); process.exit(1); });
