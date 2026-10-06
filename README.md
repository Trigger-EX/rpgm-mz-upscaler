# RPGM Upscaler

A Linux GUI (and CLI) that writes an **upscaled copy** of an RPG Maker MV/MZ HTML5 game so it runs at
**1920x1080**. The original game is never modified.

```
./run.sh                      # creates .venv, installs PySide6/Pillow/numpy, starts the GUI
./.venv/bin/python -m rpgm_upscaler.cli plan /path/to/game
./.venv/bin/python -m rpgm_upscaler.cli run /path/to/game -o /path/to/game_1080p
```

Select the game folder (the one with `index.html`, `js/`, `data/`; a `www/` subfolder is detected), pick an
output folder, press **Analyze**, review the per-folder table and the before/after preview, then **Start**.
Runs can be cancelled and resumed.

## What it does

* **One uniform scale N** (a multiple of 1/8, default "fit": 816x624 -> x1.625, 1280x720 -> x1.5) for images,
  tile size, fonts, UI metrics and pixel coordinates.
* **Sprite sheets are resized cell by cell** (characters, faces, SV actors, tilesets incl. 24px autotile
  half-tiles, icons, balloons, states, weapons, MV animations) so colours never bleed between cells and the engine's
  cell arithmetic stays exact. Titles and battlebacks are scaled to *cover* the screen. `Window.png` is left alone
  (the engine reads it at hard-coded pixel positions).
* **Engines:** Lanczos (default), nearest, "sharp" (crisp pixel art at non-integer scales), and optional
  `realesrgan-ncnn-vulkan` / `waifu2x-ncnn-vulkan` (found on PATH or chosen by path). Per-folder override.
* **Encrypted games** (`.rpgmvp`, `.png_`) are decrypted, upscaled and re-encrypted with the same key (or written
  as plain PNG). The key is read from `System.json`, or recovered from an image.
* **Movies** are scaled with ffmpeg (needs `ffmpeg`/`ffprobe`). Audio, fonts, data and everything else are copied.
* **Engine patch (in the output only):** MZ `System.json` (screen, UI area, tile size, font size), `package.json`
  window size, and a generated plugin `js/plugins/UpscalerPatch.js` (added last in `plugins.js`) that scales icon/face
  sizes, window metrics, fonts, balloon/state/weapon/animation cells, character shifts, picture/enemy/zoom coordinates
  and raises tilemap texture limits. Everything in the plugin is feature-detected and logs to the console as `[UpscalerPatch]`.

## What has been verified

* **RPG Maker MV (engine v1.3b from the MIT-licensed [rpgtkoolmv/corescript](https://github.com/rpgtkoolmv/corescript))**:
  a generated sample game is upscaled, then both the original and the result are booted in headless Chromium and
  compared (title, map with tilesets and characters, message with face, picture, animation, balloon, menu, battle).
  Plain and encrypted images, and scales x1.625 and x2 pass. See `tools/run_e2e.sh` and `tests/test_e2e_browser.py`.
* **RPG Maker MZ: not tested against the real engine** (its scripts are not public). Only the data patches
  (`System.json`, `package.json`, `plugins.js`) and a stubbed run of the plugin are tested; MZ method names and the MZ tilemap
  texture fix come from memory and are feature-detected at runtime (console warnings `[UpscalerPatch]`).

## Limitations (read this)

* Only core-engine layouts are patched. Third-party plugins and any literal pixel values elsewhere in the engine are not
  changed; the planner flags unknown `img/<folder>`s. Expect to touch up odd screens (shops, equip, save list, custom HUDs).
* Tilesets need big map textures: the patch grows pixi-tilemap's 2048px textures by ceil(768*N/1024). That is fine up to
  about x2.7 (4096px textures); beyond it the GPU memory use rises quickly (the planner warns).
* Packaged games (`.nw`, exe with archive) must be extracted first. Encrypted audio is copied unchanged.
* One factor N for everything, so a 4:3 game at 1920x1080 keeps its aspect and shows more map rather than stretching the UI.

## Development

```
.venv/bin/pip install -r requirements.txt -r requirements-dev.txt
.venv/bin/python -m pytest          # GUI test needs QT_QPA_PLATFORM=offscreen and system libEGL/libGL

# end-to-end in a real engine (needs node, playwright, Chromium; any .ttf for the engine's game font)
git clone --depth 1 https://github.com/rpgtkoolmv/corescript ~/corescript
tools/run_e2e.sh ~/corescript /tmp/e2e /path/to/font.ttf [--scale 2]     # ENCRYPT=1 for encrypted images
```
`tools/make_sample_game.py` builds the sample game (engine files are never stored in this repo).
Architecture and rationale: `docs/plan.md`.
