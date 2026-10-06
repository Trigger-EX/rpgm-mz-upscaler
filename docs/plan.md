# RPGM MV/MZ HD Upscaler: Implementation Plan

Goal: a Linux GUI program, with a headless core and a CLI, that takes an RPG Maker MV/MZ HTML5 project
(`index.html, js/, data/, img/, audio/, movies/, fonts/, css/`, optionally under `www/`) and writes a **new
copy** of it whose assets are upscaled and whose engine is patched to run at 1920x1080 (or another target).
Size budget: about 1500-2500 lines of Python plus about 300 lines of generated JS. License: GPL-3.0 (repo LICENSE).

Legend: **[VERIFY]** marks an RPGM fact I have not checked against source. Read the project's own
`rpg_*.js` / `rmmz_*.js` before relying on it. The generated plugin must feature-detect (`if (X && X.prototype.y)`) and `console.warn` when something is missing. It must never throw.

---

## 1. Key decisions

| Topic | Decision | Why |
|---|---|---|
| GUI | **PySide6** (LGPL, official Qt binding) via pip | tkinter is absent, and apt may not work. `pip install PySide6` ships manylinux wheels with Qt bundled, so no system Qt is needed. It runs headless with `QT_QPA_PLATFORM=offscreen` for smoke tests. LGPL is compatible with the GPL app. |
| Core deps | `Pillow>=10`, `numpy>=1.26` | Pillow does the resampling. numpy handles the alpha bleed, the grid ops and the fast XOR. |
| Video | system `ffmpeg`/`ffprobe` via subprocess | It is already installed. Encoders are probed at runtime (`ffmpeg -hide_banner -encoders`). |
| AI engines | optional external `realesrgan-ncnn-vulkan` / `waifu2x-ncnn-vulkan` (PATH or a user-chosen path) | No GPU is needed for the default path. Tests use a stub executable. |
| Layering | `rpgm_upscaler/core` has no Qt import. `cli.py` and `gui/` both use core | Core is testable headless. |
| Safety | The source is **never** modified. All output goes to a separate directory, and the program refuses to run if `out` equals the source or one is inside the other. | Mistakes stay recoverable. |
| Data edits | Edit as few data files as possible: MZ `System.json` (native settings), `package.json` window size, `js/plugins.js` (one appended entry). Everything else is a **runtime patch plugin**. | Runtime hooks also catch values computed by scripts and variables, and there is no risk of double-scaling JSON. |

Files to ship: `requirements.txt` (`PySide6>=6.6`, `Pillow>=10`, `numpy>=1.26`), `requirements-dev.txt` (`pytest`),
`run.sh` (creates `.venv` if missing, pip installs, then `exec python -m rpgm_upscaler "$@"`), and `pyproject.toml` with entry points
`rpgm-upscaler` (GUI) and `rpgm-upscaler-cli`.

---

## 2. Scaling model (the core correctness decision)

Notation: `L = (Lw, Lh)` is the original screen size, `U = (Uw, Uh)` the original UI/box area, `T = (Tw, Th)` the target (default 1920x1080), and
`N` the single **world scale factor**, used for assets, tile size, UI metrics and pixel coordinates.

**Use one uniform N everywhere.** Mixing an asset factor with a separate layout factor breaks faces, icons and cells
against line heights, and it breaks picture positions.

**N must be a multiple of 1/8.** Autotiles are drawn from half-tiles of 24 px, icons are 32 px, and the other cells are 48/64/96/144/192 px.
With `N = k/8`, every default cell maps to an integer size. A cell that is not a multiple of 8 is rounded per cell, and the engine re-derives it from the image size.

Scale presets (GUI combo / CLI `--scale`):
- `fit` (default): `N = floor(8 * min(Tw/Lw, Th/Lh)) / 8`. 816x624 gives 1.625 (tile 78, content 1326x1014). 1280x720 gives 1.5 (tile 72, exact fit).
- `2`, `3`, ...: integer factors for crisp pixel art. Warn when `L*N > T`; for 816x624 at 2x, 1248 > 1080, so fewer UI lines are visible.
- `custom`: any k/8 value.

Derived settings:
- screen = `T`; tileSize = `48*N` (MZ reads `System.json tileSize` **[VERIFY: key present in MZ ≥1.0]**; MV hard-codes 48 in `Game_Map.prototype.tileWidth/tileHeight`).
- UI area = `min(U*N, T)` rounded to even numbers, centered. This is the default and keeps the original menu proportions. The option "UI fills screen" uses UI area = `T`.
- Content offset for screen-pixel coordinates (pictures, troop enemies, zoom centers): `ox = (Tw - Lw*N)/2`, `oy = (Th - Lh*N)/2`. The option "anchor: center | top-left" applies.
- The map simply shows more tiles. Maps smaller than the screen are centered by the engine (`setDisplayPos` uses `endX < 0 ? endX/2`) **[VERIFY]**.

Per-category image factor:
- **Exact N** (cell/grid math or map alignment depends on it): characters, faces, sv_actors, sv_enemies, enemies, tilesets, animations, system/*, parallaxes (parallax-mapped maps must equal `mapW*tileSize`), pictures.
- **Cover** `max(N, Tw/w, Th/h)` rounded up to an integer size, aspect kept: titles1/2, battlebacks1/2, system/GameOver. The engine centers or tiles these. MV battlebacks are TilingSprites, so too small a factor shows seams **[VERIFY]**.
- **Never scaled (copy)**: windowskins (`img/system/Window*.png` that are 192x192). Window frame, cursor and text-color sampling (`96 + (n%8)*12 + 6`) use hard-coded coordinates in MV and MZ. Borders get thinner, but the window stays correct. Add an override checkbox, off by default, labeled as breaking.
- Unknown `img/<custom>` folders (from plugins): default exact N with Lanczos, flagged "plugin asset, layout may need manual fix". The user can set skip/copy per folder.
- `effects/Texture/*` (MZ Effekseer): upscale by N. UVs are normalized, so this is safe **[VERIFY]**. `.efkefc` files are copied.
- audio, fonts, css, index.html, data (except below): copied unchanged.
- movies: ffmpeg Lanczos scale to fit inside T, aspect kept, even dimensions. The engine stretches video to the canvas, so this only improves sharpness.

### Sprite-sheet grids (cell counts; the cell size is always derived as `image/cols`, `image/rows`)
| Folder / file | Grid (cols x rows) | Notes |
|---|---|---|
| characters, name contains `$` prefix | 3 x 4 | `!` prefix = object (no 6px shift), size-neutral |
| characters, otherwise | 12 x 8 | 4x2 characters of 3x4 |
| faces | 4 x 2 | default cell 144 |
| sv_actors | 9 x 6 | default cell 64 |
| animations (MV-style) | 5 x ceil(h/192) | cell 192 |
| tilesets A1-A4 | half-tile grid (24) | kind from `data/Tilesets.json` `tilesetNames[0..8]` → A1,A2,A3,A4,A5,B,C,D,E; fall back to `_A1.._E` suffix |
| tilesets A5, B-E | tile grid (48) | |
| system/IconSet | cols = w/32 (16) | |
| system/Balloon | 8 x 15 (48) | |
| system/States | 96-px cells | |
| system/Weapons1-3 | 96x64 cells | |
| system/ButtonSet | 48-px blocks | |
| system/Damage | 10 x 5 | MV; MZ draws text |
| other system, pictures, enemies, sv_enemies, parallaxes, titles, battlebacks | none (whole image) | |

**Grid-aware resize (all engines):** "explode" the sheet into cells, each padded with a 2-4 px edge-replicated
gutter. Upscale the exploded atlas in **one** engine call, crop each cell back out, resize it to the exact
`round(cell*N)` target, and "implode" the cells into the sheet. This removes bleeding across cell and tile boundaries and keeps the
`cols*cell'` math exact.

### Engines (`engines.py`, pluggable: `upscale(img_rgba, scale_hint) -> img`, then exact resize to target)
- `lanczos` (default, Pillow LANCZOS; Pillow premultiplies RGBA internally, which avoids dark fringes).
- `nearest` (pixel art; exact only for integer N).
- `sharp`: nearest to `ceil(N)` integer, then a LANCZOS/BOX downscale to target. This is crisp pixel art for non-integer N.
- `realesrgan` (`realesrgan-ncnn-vulkan -i in -o out -s S -n MODEL [-g gpu] [-t tile]`; models `realesr-animevideov3` (s=2/3/4),
  `realesrgan-x4plus-anime`, `realesrgan-x4plus` (s=4 only)) and `waifu2x` (`waifu2x-ncnn-vulkan -i -o -n NOISE -s 2|4 -m models-cunet`).
  Choose `S` as the smallest supported scale ≥ N, then Lanczos down to the exact size. Use batch mode (directory in/out) to amortize model load.
  **Alpha:** before the engine runs, bleed RGB into transparent pixels (iterative dilation, numpy). The engine gets RGB only. Alpha is scaled
  with Lanczos (or nearest when the source alpha is binary) and then recombined.
  Detection: `shutil.which` or a user path. Probe by running it on an 8x8 PNG. Failure (no Vulkan) makes the engine unavailable, with the reason shown.
- Per-category resampler override, for example "nearest for tilesets/characters" (GUI table column, CLI `--resampler tilesets=nearest`).

---

## 3. Engine/runtime patching (`patcher.py` + `templates/UpscalerPatch.js`)

### File edits (in the output copy only)
1. **MZ `data/System.json`**: `advanced.screenWidth/screenHeight = T`, `advanced.uiAreaWidth/uiAreaHeight = UI area`,
   `tileSize = 48N`, `advanced.fontSize *= N` (round). `advanced.screenScale` (if present) **[VERIFY]**: set it to 1. Load with
   `json.load` (dicts keep order) and dump with `ensure_ascii=False, separators=(',',':')`. The editor's compact style is fine.
   These are native MZ settings, so the plugin does **not** rescale them; it only asserts them and warns on a mismatch.
2. **`package.json`** (NW.js): `window.width/height = T`. Keep the other keys.
3. **`js/plugins.js`**: parse it by stripping the `var $plugins =` prefix and the trailing `;`, then `json.loads`. Remove any existing
   `UpscalerPatch` entry (idempotent), **append** the new one last so it wraps other plugins' overrides of L-space values, and re-emit
   it in editor format (one entry per line).
4. **`js/plugins/UpscalerPatch.js`**: rendered from a template with baked constants
   (`N, Lw, Lh, Uw, Uh, Tw, Th, OX, OY, ENGINE`) and `@target MZ`/`@plugindesc` headers. The same file has MV and MZ branches,
   chosen by `Utils.RPGMAKER_NAME`.

### What the plugin patches
Helper: `scaleRet(proto, name)` wraps a method and multiplies a numeric return value by N. It skips the patch and warns if the method is missing.

| Concern | MZ | MV |
|---|---|---|
| Screen/box size | System.json (above) | `SceneManager._screenWidth/_screenHeight/_boxWidth/_boxHeight` |
| Tile size | System.json `tileSize` | `Game_Map.prototype.tileWidth/tileHeight` return 48N |
| **Tilemap texture limit (HIGH RISK)** | `Tilemap.Renderer` packs each tileset into a 1024² quadrant of 2048² textures **[VERIFY]**. Scaled B-E sheets (768N) exceed this for N > 1.33. The app **extracts** `Tilemap.Renderer.prototype._createInternalTextures`, `updateTextures`/`_updateInternalTextures`, `Tilemap.Layer` `addRect`/vertex code and the shader source from the project's own `rmmz_core.js` by regex. It replaces 1024→1024k and 2048→2048k inside those functions only (k = smallest power of 2 that fits) and emits them into the plugin. If extraction fails, it aborts with a clear error, unless the user caps tilesets. | ShaderTilemap uses pixi-tilemap: set `PIXI.tilemap.Constant.boundSize/bufferSize` to 2048/4096 if present **[VERIFY]**. Otherwise fall back by overriding `Spriteset_Map.prototype.createTilemap` to use the canvas `Tilemap`, which has no size limit. |
| Icons/faces | `ImageManager.iconWidth/iconHeight/faceWidth/faceHeight *= N` | `Window_Base._iconWidth/_iconHeight/_faceWidth/_faceHeight *= N` |
| Fonts | System.json fontSize; `makeFontBigger/Smaller` step 12→12N, limits 96N/24N; `Bitmap` `outlineWidth *= N` (wrap initialize) | `Window_Base.standardFontSize`, `makeFontBigger/Smaller`, `Bitmap` outlineWidth |
| Window metrics | `scaleRet`: `Window_Base.lineHeight`, `itemPadding`, `Game_System.windowPadding`, `Scene_Base.mainCommandWidth`, `buttonAreaHeight`, `Sprite_Button.blockWidth/blockHeight`, `Sprite_Gauge.bitmapWidth/bitmapHeight/gaugeHeight` | `Window_Base.lineHeight`, `standardPadding`, `textPadding`; `Window_NumberInput` button size 48→48N |
| Sheet frames with literals | `Sprite_Balloon` 48, `Sprite_StateOverlay` 96, `Sprite_Weapon` 96x64: override `updateFrame` with scaled constants | same, plus `Sprite_Animation` cell 192→192N and cell `x,y` positions ×N (override `updateCellSprite`) |
| Animations | Effekseer `Sprite_Animation`: scale ×N, `offsetX/offsetY` ×N **[VERIFY method names]**; `Sprite_AnimationMV` as in the MV column | see above |
| Characters | `Game_CharacterBase.shiftY` 6→6N, `jumpHeight` ×N, `refreshBushDepth` 12→12N, `Game_Vehicle.maxAltitude` 48→48N | same |
| Screen-pixel coordinates (L-space) | wrap `Game_Picture.show/move` (x→x*N+OX, y→y*N+OY); `Game_Enemy.setup` (troop x,y ×N + battlefield offset); `Game_Screen.startZoom` x,y; shake: scale `Spriteset_Base` shake offset by N | same |
| Misc (best effort) | `Sprite_Timer` bitmap/font; damage popup motion; weather sprites `scale.set(N)` | same |

The plugin is placed last and patches **core** methods only. Third-party plugins with hard-coded pixel literals cannot be
fixed generically. The analyzer greps `js/plugins/*.js` and the `plugins.js` numeric parameters for likely pixel values
(names containing x/y/width/height/size/padding/font) and lists them in the report as "review manually". It does **not** auto-edit them.

### Encryption (`crypto.py`)
- Formats: MV `.rpgmvp/.rpgmvo/.rpgmvm`, MZ `.png_/.ogg_/.m4a_`. `System.json`: `hasEncryptedImages`, `hasEncryptedAudio`, `encryptionKey`
  (32 hex chars → 16 bytes).
- Layout: a 16-byte fake header `52 50 47 4D 56 00 00 00 00 03 01 00 00 00 00 00` (check only the first 5 bytes "RPGMV"), then the
  original file with its **first 16 bytes XORed** with the key.
- Images: decrypt, upscale, then re-encrypt with the same key and extension (default; option to emit plain `.png` and set
  `hasEncryptedImages=false`). Audio: copied unchanged. If the key is missing, recover it from any image by XORing its bytes 16..32 with the
  standard PNG header `89504E470D0A1A0A0000000D49484452`.
- Packaged games (`package.nw` zip, or an exe with an appended archive): detect them and show "extract first". This is out of scope.

---

## 4. Architecture

```
rpgm_upscaler/
  __init__.py, __main__.py        # __main__ -> gui.app.main(); fall back to the CLI message if PySide6 is missing
  cli.py                          # argparse: analyze | plan | run  (see below)
  core/
    project.py      # find root (index.html+js/+data/, also www/); MV vs MZ (rmmz_core.js / rpg_core.js,
                    # Utils.RPGMAKER_VERSION); L/U (MZ System.json advanced; MV parse SceneManager._screenWidth
                    # in rpg_managers.js + plugin overrides, else 816x624); tileSize; encryption; tileset kinds; plugins
    categories.py   # folder rules -> Category(name, policy exact|cover|copy, grid resolver, default resampler)
    scaling.py      # ScalePlan: N quantization, target sizes, cover factors, UI area, offsets (pure functions)
    imageops.py     # load (decrypting), P/L->RGBA, explode/implode grid, alpha bleed, exact resize, save (re-encrypt)
    engines.py      # Engine ABC; PillowEngine(lanczos|nearest|sharp); NcnnEngine(realesrgan|waifu2x); detect_engines()
    crypto.py       # header, decrypt/encrypt, key recovery
    video.py        # ffprobe dims, encoder probe, scale job (webm: libvpx-vp9 -crf 32 -b:v 0 -row-mt 1 -cpu-used 4,
                    # fall back to libvpx; mp4: libx264 -crf 18; audio -c:a copy)
    patcher.py      # System.json, package.json, plugins.js, render plugin, MZ tilemap function extraction
    templates/UpscalerPatch.js.tmpl
    planner.py      # Options + Project -> Plan(jobs: list[Job], copies, patches, warnings); manifest/resume
    runner.py       # executes Plan: copy tree, pools, progress/cancel callbacks, manifest updates
    settings.py     # Options dataclass <-> JSON; app settings at $XDG_CONFIG_HOME/rpgm-upscaler/settings.json
    log.py          # logging setup: file in <out>/.upscaler/upscale.log + callback handler
  gui/
    app.py, main_window.py, worker.py (QThread + signals wrapping runner), preview.py (before/after widget)
tests/ (see §6)
requirements.txt, requirements-dev.txt, run.sh, pyproject.toml, README (short usage)
```

**Job model:** `Job(kind=image|video|copy, src, dst, category, grid, factor, target_size, resampler, engine, encrypted, est_cost)`.
The plan is pure data. **Dry run** = analyze + plan + print/show, with no writes. `--json` emits the plan.

**Runner:**
1. Validate `out` (not equal to the source and not nested; create it). Copy all non-job files (`shutil.copy2`, skipping existing files when resuming).
2. Image jobs with Pillow engines go to a `ProcessPoolExecutor(max_workers=opt.workers or cpu_count)` (CPU-bound). Workers are top-level functions that receive
   picklable Job+Options and write to `dst.tmp`, then `os.replace` (atomic, so a resume never sees half-written files).
3. AI-engine jobs run serially in a single thread (GPU). Batch the exploded atlases into a temp dir and make one ncnn call per batch of about 50.
4. Video jobs run one at a time (ffmpeg is already multithreaded). Track the `Popen` so cancel can `terminate()` it, then `kill()` after 5 s.
5. Patches are applied last, after the assets succeed.
6. **Cancel:** a `threading.Event` is checked before each submission. Pending futures are `cancel()`ed, running ones finish, and the manifest stays consistent.
7. **Progress:** `on_progress(done, total, current_name)`, `on_log(level, msg)`, `on_job_done(job, ok, err)`. ETA uses `est_cost` (pixels).
8. **Resume:** the manifest is at `<out>/.upscaler/manifest.json`, keyed by relpath → `{src_size, src_mtime_ns, options_hash, ok}`. A job is skipped when the entry matches and
   `dst` exists. "Overwrite" ignores the manifest. Changing N invalidates everything through `options_hash`.
9. Errors in one job are logged and the run continues. The summary lists failures, and the exit code is non-zero if any job failed.

**CLI:**
`rpgm-upscaler-cli analyze GAME` · `plan GAME -o OUT [opts] [--json]` · `run GAME -o OUT [opts]` with
`--target 1920x1080 --scale fit|2|3|1.625 --engine lanczos|nearest|sharp|realesrgan|waifu2x --engine-path P --model M
--resampler cat=nearest ... --skip cat ... --no-movies --no-patch --plain-images --ui-fill --anchor center|topleft
--workers N --resume/--overwrite`.

---

## 5. GUI layout (PySide6, single `QMainWindow`)

```
┌ Project: [/path/game      ][Browse]  Output: [/path/game_HD   ][Browse]  [Analyze] ┐
├ Info: MZ 1.8.0 · 816x624 (UI 816x624) · tile 48 · images encrypted (key ok) · 42 plugins
├ Options ───────────────────────────────────────────────────────────────────────────┤
│ Target [1920]x[1080]  Scale [Fit ▾] N=1.625 → tile 78, UI 1326x1014, offset 297,33  │
│ Engine [Lanczos ▾] path [....][…] model [▾] (status: available / not found / reason) │
│ [x] Movies  [x] Re-encrypt  [x] Patch engine  [ ] UI fills screen  Anchor [Center ▾] │
│ Workers [8]  [x] Resume                                                            │
├ Folders (QTableWidget) ─────────────────────┬ Preview ─────────────────────────────┤
│ Folder | Files | Policy | Grid | Resampler | │ file list for the selected folder     │
│ characters 34 exact 12x8 [nearest▾]          │ Before | After (crop, 1:1 zoom toggle)│
│ ...                                          │ after = rendered in a worker on demand│
├ Warnings / plugin pixel params (collapsible) ┴──────────────────────────────────────┤
├ [Dry run] [Start] [Cancel] [Open output]   ▓▓▓▓▓▓░░░ 312/840 · ETA 2m · Faces/Actor1 │
└ Log (QPlainTextEdit, auto-scroll, max 5000 lines)                                    ┘
```
- Every long operation (analyze, preview, run) runs in `worker.py` (QThread). Signals marshal to the UI. No core code touches Qt.
- Options are saved to `settings.json` on change and restored at start (last project, output, engine path).
- While a run is in progress, the option widgets are disabled, Cancel is enabled, and the close event asks for confirmation and cancels.

---

## 6. Tests (pytest, all headless)

`tests/fakegame.py` builds a synthetic project in `tmp_path`: `make_game(engine="MZ"|"MV", screen=(816,624), encrypted=False)`.
- `js/rmmz_core.js` stub with `Utils.RPGMAKER_NAME="MZ"`, a version, and **fake `Tilemap.Renderer` functions containing 1024/2048** for the
  extraction test. MV gets a `rpg_core.js`/`rpg_managers.js` stub with `SceneManager._screenWidth = 816`.
- `data/System.json` (advanced block for MZ, encryptionKey), `Tilesets.json`, `js/plugins.js` with two entries, `package.json`.
- Images at real default sizes, with every cell filled by a **unique solid color** (characters 576x384 and `$Big.png`, faces
  576x288, sv_actors 576x384, IconSet 512x64, tilesets A1/A2/A5/B, Window.png 192x192, a picture, a title 816x624,
  a battleback 1000x740, an MV animation 960x384, a palette-mode PNG, an RGBA one with transparency, a custom `img/hud/` folder).
- Optional 1-second 64x48 webm/mp4 made with `ffmpeg -f lavfi testsrc` (skip when the encoder is missing).

| Test file | Asserts |
|---|---|
| test_project | MV/MZ detection, `www/` root, resolution, tileSize, tileset kinds, encryption flags, packaged-game warning |
| test_scaling | N quantization (816x624→1.625, 1280x720→1.5, presets), UI area/offsets, cover sizes, L*N>T warning |
| test_imageops | output size = cols*round(cell*N); **cell centers keep their unique color** (no bleed, nearest and lanczos); palette/RGBA handling; alpha bleed leaves alpha unchanged |
| test_crypto | encrypt/decrypt round trip; byte-exact header; key recovery from a PNG; wrong key detected (PNG magic check) |
| test_patcher | System.json changes only the intended keys, order kept; plugins.js parse/emit round trip; **idempotent** (run twice, one entry); package.json; MZ tilemap extraction replaces only inside the functions; **`node --check` on the rendered plugin** (skip if node is absent; node is at /opt/node22/bin/node here) |
| test_engines | Pillow engines; **stub `realesrgan-ncnn-vulkan`** (python script on a tmp PATH that implements `-i -o -s -n` with Pillow); detection and probe failure path |
| test_video | ffprobe dims; scaled output dims even and within T (skip without the encoder) |
| test_planner_runner | dry run writes nothing; full run on a fake MZ and MV game; **source tree hash unchanged**; Window.png copied byte-identical; audio copied; resume skips (mtime unchanged); cancel mid-run leaves no `.tmp` files and a consistent manifest; failing job → non-zero exit, others done |
| test_cli | `analyze`/`plan --json`/`run` via `main(argv)` |
| test_gui_smoke | `QT_QPA_PLATFORM=offscreen`, `pytest.importorskip("PySide6")`: build MainWindow, load the fake game, analyze, dry run, check the table rows; run a tiny job with `qtbot`-free waiting (a QEventLoop with a timeout) |

---

## 7. Ordered implementation steps (each step ends with green tests)

1. Scaffolding: `pyproject.toml`, requirements, `run.sh`, package skeleton, `tests/fakegame.py`, pytest config. `pip install -r requirements.txt -r requirements-dev.txt`.
2. `crypto.py` + tests.
3. `project.py` (detection and metadata) + tests.
4. `scaling.py` (pure math) + tests.
5. `categories.py` (folder rules, grid resolution from file names/Tilesets.json) + tests.
6. `imageops.py` + `engines.py` Pillow engines (explode/implode, alpha bleed, exact resize) + tests.
7. `planner.py` (jobs, warnings, plugin-param scan, dry run) + tests.
8. `runner.py` (copy tree, process pool, manifest/resume, cancel, atomic writes, logging) + tests.
9. `patcher.py` + `UpscalerPatch.js.tmpl` (MZ and MV branches, `scaleRet`, feature detection, MZ tilemap extraction) + tests incl. `node --check`.
10. `video.py` + tests.
11. `NcnnEngine` (batching, alpha split, probe) + stub-based tests.
12. `cli.py` + tests.
13. GUI: main window, worker, preview, settings persistence + offscreen smoke test.
14. README usage and limitations; a manual check list for verification on a real MV and MZ game (title, map with autotiles, menu, message with face, battle with SV actors and animations, picture show, movie).

---

## 8. Pitfalls checklist

- **Tilemap texture quadrant limit** (1024 px per tileset sheet in the MZ Tilemap renderer and in MV pixi-tilemap). Upscaled tilesets render garbage without the patch. This is the biggest risk, so verify it on a real game first.
- Autotiles need an **even** tile size, because half-tiles are drawn independently. N = k/8 guarantees it for 48-px tiles. Validate it anyway.
- Never scale the windowskin (hard-coded coordinates). Never resize a sheet as a whole image with a smooth filter (cell bleed). Use explode/implode.
- Parallaxes must be scaled by exactly N, or parallax-mapped maps misalign. Titles and battlebacks use "cover".
- Very large outputs (big parallax maps): warn above 8192 px and refuse above 16384 px (WebGL max texture size).
- Pillow `P`/`LA`/`I;16` modes: convert to RGBA first. Keep the PNG filename and case exactly (the engine is case-sensitive on Linux).
- Do not double-scale: in MZ, fontSize and screen size live in System.json, and the plugin must not multiply them again.
- `plugins.js` from some tools is not strict JSON (trailing commas, comments). Fall back to a tolerant parse (strip `//` comments and trailing commas). If that fails, abort the patch step with a clear message and do not corrupt the file.
- The MV editor cannot edit a project with a non-48 tile size. MZ can (tileSize setting). State this in the README.
- Encrypted files: check the decrypted PNG magic to detect a wrong key. Re-encrypt with the identical key and header.
- AI engines: alpha is handled separately; there is no Vulkan in CI or sandboxes (probe and degrade); an `-s` value the model does not support (x4plus is 4x only).
- ProcessPool: worker functions must be top level and picklable. Do not pass Qt objects. Use the `forkserver`/`spawn` start method in the GUI process, because forking with Qt threads is unsafe.
- Atomic writes (`.tmp` then `os.replace`). Never write into the source directory (resolve symlinks before the nesting check).
- Third-party plugins with pixel literals stay unscaled. Report them; do not edit them. Everything marked [VERIFY] must be feature-detected in the plugin and logged in the browser console as `[UpscalerPatch]`.
