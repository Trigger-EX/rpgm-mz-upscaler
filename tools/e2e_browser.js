#!/usr/bin/env node
// Boots an RPG Maker MV game in headless Chromium and screenshots key scenes.
// usage: node e2e_browser.js <url> <outdir> <viewportW> <viewportH>
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const fs = require('fs');

(async () => {
  const [url, outdir, vw, vh] = process.argv.slice(2);
  fs.mkdirSync(outdir, { recursive: true });
  const browser = await chromium.launch({
    executablePath: process.env.CHROMIUM || '/opt/pw-browsers/chromium-1194/chrome-linux/chrome',
    args: ['--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist', '--no-sandbox'],
  });
  const page = await browser.newPage({ viewport: { width: +vw, height: +vh } });
  const logs = [];
  page.on('console', (m) => logs.push(`${m.type()}: ${m.text()}`));
  page.on('pageerror', (e) => logs.push(`pageerror: ${e.message}`));
  page.on('requestfailed', (r) => logs.push(`requestfailed: ${r.url()}`));
  page.on('response', (r) => { if (r.status() >= 400) logs.push(`http ${r.status()}: ${r.url()}`); });
  const report = { steps: {} };
  const frames = (n) => page.evaluate((n) => new Promise((res) => { let i = 0; const f = () => (++i >= n ? res() : requestAnimationFrame(f)); f(); }), n);
  const waitFor = async (expr, label, timeout = 30000) => {
    try { await page.waitForFunction(expr, null, { timeout }); return true; }
    catch (e) { report.steps[label] = 'TIMEOUT waiting: ' + expr; return false; }
  };
  const shot = async (name, wait = 20) => { if (wait) await frames(wait); await page.screenshot({ path: `${outdir}/${name}.png` }); };
  const info = (name) => page.evaluate(() => ({
    graphics: [Graphics.width, Graphics.height, Graphics.boxWidth, Graphics.boxHeight],
    tile: $gameMap ? $gameMap.tileWidth() : null,
    scene: SceneManager._scene && SceneManager._scene.constructor.name,
    renderer: Graphics.isWebGL() ? 'webgl' : 'canvas',
  }));

  await page.goto(url);
  if (await waitFor(() => window.SceneManager && SceneManager._scene && SceneManager._scene.constructor.name === 'Scene_Title' && SceneManager._scene._active, 'title')) {
    report.steps.title = await info(); await shot('1_title');
  }
  // map
  await page.evaluate(() => { DataManager.setupNewGame(); SceneManager.goto(Scene_Map); });
  if (await waitFor(() => SceneManager._scene.constructor.name === 'Scene_Map' && SceneManager._scene._active && !SceneManager.isSceneChanging(), 'map')) {
    await frames(30); report.steps.map = await info(); await shot('2_map');
    // message with face
    await page.evaluate(() => { $gameMessage.setFaceImage('Actor1', 0); $gameMessage.add('Hello! Face + text scaling test.\nSecond line.'); });
    await frames(60); await shot('3_message');
    await page.evaluate(() => { Input._currentState.ok = true; });
    await frames(5); await page.evaluate(() => { Input._currentState.ok = false; $gameMessage.clear(); });
    // picture positioned for original 816x624 layout at (100,100)
    await page.evaluate(() => { $gameScreen.showPicture(1, 'Pic', 0, 100, 100, 100, 100, 255, 0); });
    await frames(10); await shot('4_picture');
    report.pictureX = await page.evaluate(() => { const sp = SceneManager._scene._spriteset._pictureContainer.children[0]; return [sp.worldTransform.tx, sp.worldTransform.ty]; });
    await page.evaluate(() => { $gameScreen.erasePicture(1); $gamePlayer.requestAnimation(1); });
    // game logic runs several ticks per frame on slow software GL, so wait for the sprite itself and shoot at once
    if (await waitFor(() => SceneManager._scene._spriteset._characterSprites.some((s) => s._animationSprites && s._animationSprites.length > 0), 'animation')) await shot('4b_animation', 0);
    await frames(120);
    // balloon over the NPC + hold-right walking check
    await page.evaluate(() => { $gameMap.event(1).requestBalloon(1); });
    if (await waitFor(() => SceneManager._scene._spriteset._characterSprites.some((s) => s._balloonSprite), 'balloon')) await shot('4c_balloon', 0);
    // menu
    await page.evaluate(() => SceneManager.push(Scene_Menu));
    if (await waitFor(() => SceneManager._scene.constructor.name === 'Scene_Menu' && SceneManager._scene._active, 'menu')) {
      await frames(20); await shot('5_menu');
    }
    await page.evaluate(() => SceneManager.goto(Scene_Map));
    await frames(30);
  }
  // battle
  await page.evaluate(() => { BattleManager.setup(1, false, true); BattleManager.setEventCallback(() => {}); SceneManager.goto(Scene_Battle); });
  if (await waitFor(() => SceneManager._scene.constructor.name === 'Scene_Battle' && SceneManager._scene._active, 'battle')) {
    await frames(120); report.steps.battle = await info(); await shot('6_battle');
  }
  const counts = {};
  for (const l of logs) if (!/GPU stall|swiftshader|WebGL: INVALID|Automatic fallback|GL Driver/i.test(l)) counts[l] = (counts[l] || 0) + 1;
  report.logs = Object.entries(counts).map(([l, n]) => (n > 1 ? `${l}  (x${n})` : l));
  fs.writeFileSync(`${outdir}/report.json`, JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report, null, 2));
  await browser.close();
})().catch((e) => { console.error('HARNESS ERROR', e); process.exit(1); });
