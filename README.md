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

## Limitations (read this)

* The engine patch is **best effort and was only tested against stubs**, not a real MV/MZ install: check the title
  screen, a map with autotiles, the menu, a message with a face, a battle and a movie. Method names are matched
  against the engine's own source at runtime; anything missing is skipped with a console warning.
* Third-party plugins with hard-coded pixel values are not changed. Unknown `img/<folder>`s are scaled by N and flagged.
* Packaged games (`.nw`, exe with archive) must be extracted first. Encrypted audio is copied unchanged.
* Scaling is by one factor, so a 4:3 game at 1920x1080 keeps its aspect and shows more map, not stretched UI.

## Development

```
.venv/bin/pip install -r requirements.txt -r requirements-dev.txt
.venv/bin/python -m pytest          # GUI test needs QT_QPA_PLATFORM=offscreen and system libEGL/libGL
```
Architecture and rationale: `docs/plan.md`.
