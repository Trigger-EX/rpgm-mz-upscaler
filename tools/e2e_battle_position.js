// Starts the sample game's first battle in headless Chromium and prints where the first enemy sprite is on screen.
// usage: node e2e_battle_position.js <url> <viewportW> <viewportH>   (MV or MZ)
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
(async () => {
  const [url, w, h] = process.argv.slice(2);
  const b = await chromium.launch({ executablePath: process.env.CHROMIUM || '/opt/pw-browsers/chromium-1194/chrome-linux/chrome', args: ['--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--no-sandbox'] });
  const p = await b.newPage({ viewport: { width: +w, height: +h } });
  await p.goto(url);
  await p.waitForFunction(() => window.SceneManager && SceneManager._scene && SceneManager._scene.constructor.name === 'Scene_Title', null, { timeout: 90000 });
  await p.evaluate(() => { DataManager.setupNewGame(); BattleManager.setup(1, true, false); SceneManager.goto(Scene_Battle); });
  await p.waitForFunction(() => SceneManager._scene && SceneManager._scene.constructor.name === 'Scene_Battle' && SceneManager._scene._spriteset && SceneManager._scene._spriteset._enemySprites.length && SceneManager._scene._spriteset._enemySprites[0].bitmap && SceneManager._scene._spriteset._enemySprites[0].bitmap.isReady(), null, { timeout: 90000 });
  await p.waitForTimeout(1500);
  const r = await p.evaluate(() => { const s = SceneManager._scene._spriteset; const e = s._enemySprites[0]; const g = e.worldTransform; const a = s._actorSprites[0]; return { gw: Graphics.width, gh: Graphics.height, bw: Graphics.boxWidth, bh: Graphics.boxHeight, enemy: [Math.round(g.tx), Math.round(g.ty)], actor: a ? [Math.round(a.worldTransform.tx), Math.round(a.worldTransform.ty)] : null }; });
  console.log(JSON.stringify(r));
  await b.close();
})();
