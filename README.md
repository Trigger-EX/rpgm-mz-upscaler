# RPGM Hub

One Linux app for all things RPG Maker: **upscale** a game to 1920×1080, **edit its saves**, and read the
Japanese names in them through **offline translation**. It covers RPG Maker **MV, MZ, VX Ace and VX** (XP is recognised,
not supported yet). A GUI and a CLI share the same core.

```
./run.sh                     # creates .venv, installs PySide6/Pillow/numpy, starts the hub
./run.sh /path/to/game       # ... and opens that game
.venv/bin/python -m rpgm_upscaler.cli --help
```
Optional neural translation: `.venv/bin/pip install -r requirements-translate.txt`, then *Translation → Install model*.

## What works for which engine

| | MV | MZ | VX Ace | VX |
|---|---|---|---|---|
| Upscale to 1080p | yes (patched engine) | yes (patched engine, **untested on a real MZ**) | yes, via an mkxp-z *Hires* pack | yes, via an mkxp-z *Hires* pack |
| Save editor | yes | yes | yes | yes |
| Names from the game database | yes | yes | yes | yes |
| Encrypted games | images decrypted/re-encrypted | same | `.rgss3a` unpacked | `.rgss2a` unpacked |

### Upscaling MV / MZ
Writes a separate copy of the game: sprite sheets are resized cell by cell (no colour bleeding), titles and battle
backgrounds cover the screen, `Window.png` is left alone, movies are rescaled with ffmpeg, and the engine is patched in the
copy (`System.json`, `package.json`, the tilemap texture limit in `pixi-tilemap.js`, and a generated plugin
`js/plugins/UpscalerPatch.js` for fonts, window metrics, picture positions ...). One factor N (a multiple of 1/8;
816×624 → ×1.625) is used for everything. Engines: Lanczos, nearest, "sharp" (pixel art), or `realesrgan-ncnn-vulkan` /
`waifu2x-ncnn-vulkan` when installed.

### Upscaling VX Ace / VX
The stock player cannot do this: its screen stops at 640×480 and its map renderer is fixed at 32 px tiles. So the hub writes
an **HD pack for [mkxp-z](https://github.com/mkxp-z/mkxp-z)**, an open-source RGSS player: `Hires/Graphics/**` (upscaled copies,
the originals stay) plus an `mkxp.json` with `enableHires` and the scaling factors. Encrypted `.rgss3a`/`.rgss2a` archives are
unpacked first (the original is never modified). Mode `stock640` is also available: it only inserts a tiny script that sets
640×480 (assets are not upscaled). RTP graphics are not part of the game folder; copy the RTP `Graphics` folder into the game
first if you want those upscaled too (see `README-HUB.txt`, written into the output).

### Save editor
Lists the saves of a game, shows **switches, variables, gold, party (level / EXP / HP / MP), inventory, map and position**
with their names from the database, and writes changes back safely: a `.bak` backup is made first (the last five are kept),
the new file is decoded again and compared before it replaces the old one, a file that changed on disk meanwhile is refused,
and a file that cannot be round-tripped opens **read-only**. Formats: MV `.rpgsave` (LZString), MZ `.rmmzsave` (zlib; both
byte variants), Ace `.rvdata2` and VX `.rvdata` (Ruby Marshal, written back byte-exact apart from your edits).

### Offline translation
Names are translated without any network: user overrides → a bundled RPG glossary with longest-match segmentation and
katakana → romaji (`ボス撃破` → *Boss Defeated*, `アレックス` → *Arekkusu*) → an optional neural model (Argos
`ja→en` run directly with ctranslate2 + sentencepiece, no torch) → left unchanged. Results from the model are cached in sqlite.
The model is a one-time download (or *Import .argosmodel…*); afterwards nothing touches the network. Quality note: a small
model is fair on sentences and **weak on short names and terms**, which is why the glossary and your overrides come first.

## CLI cheat sheet
```
rpgm-hub-cli detect GAME
rpgm-hub-cli analyze|plan|run GAME [-o OUT] [--scale fit|2|...] [--engine lanczos|nearest|sharp|realesrgan|waifu2x] [--mode hires|stock640]
rpgm-hub-cli saves list GAME
rpgm-hub-cli saves dump SAVE [--json] [--names] [--translate]
rpgm-hub-cli saves set SAVE --switch 12=on --var 5=100 --gold 5000 --item weapons:2=3 --actor 1:level=20 --map 3 --pos 7,9
rpgm-hub-cli unpack GAME [-o DIR]                 # encrypted RGSS archive
rpgm-hub-cli scripts GAME [--extract DIR]         # VX / Ace Ruby scripts
rpgm-hub-cli translate TEXT... | --file F | status | install | import FILE.argosmodel
```

## What has actually been verified

* **MV (engine v1.3b, the MIT-licensed [rpgtkoolmv/corescript](https://github.com/rpgtkoolmv/corescript))**: the upscaled sample
  game boots in headless Chromium and is compared scene by scene with the original (plain and encrypted images, ×1.625 and ×2);
  the engine itself writes a save, the editor changes it, and the engine loads the edited save back (`tools/e2e_saves.py`).
* **Save formats**: the LZString port is byte-identical to the library MV ships; the Ruby Marshal codec round-trips fixtures made
  by real Ruby 3.3 byte for byte, and Ruby loads what the editor writes. The RGSS archive algorithm was cross-checked against
  mkxp-z's own `rgssad.cpp`, and the mkxp.json keys and the `Hires/` convention against mkxp-z's source.
* **Not verified**: MZ against its real engine (its scripts are not public); any VX/VX Ace game actually running in mkxp-z or the
  stock player; real `.rgss3a` files and real Ace/VX saves (only synthetic and Ruby-made ones); the real Argos model (its download host
  is blocked in the development sandbox, so only the install/import flow and a stubbed runtime are tested).

## Limitations
* Only core-engine layouts of MV/MZ are patched; third-party plugins with hard-coded pixel values are not. Expect to touch up odd
  screens (shops, equip, save list, custom HUDs).
* MV/MZ tilesets need big map textures (about ×2.7 is the practical limit; the planner warns).
* Packaged games (`.nw`, exe with an archive) must be extracted first. Encrypted audio is copied unchanged.
* Saves of games with custom scripts keep their extra data untouched, but the editor may not find non-standard gold/inventory.

## Development
```
.venv/bin/pip install -r requirements.txt -r requirements-dev.txt
.venv/bin/python -m pytest        # QT_QPA_PLATFORM=offscreen and system libEGL/libGL needed for the GUI tests, else they skip
```
Optional real-engine checks (need node, playwright, Chromium and `git clone --depth 1 https://github.com/rpgtkoolmv/corescript`):
`tools/run_e2e.sh CORESCRIPT WORKDIR FONT.ttf [--scale 2]` (ENCRYPT=1 for encrypted images) and `tools/e2e_saves.py SAMPLE_GAME`.
VX/Ace Hires pack against a real mkxp-z: `RPGM_MKXPZ=/path/to/mkxp-z python -m pytest tests/test_e2e_mkxpz.py` (needs xvfb-run, openbox, xdotool, ImageMagick `import`;
`RPGM_MKXPZ_RUBYLIB` for Ruby's zlib if the binary cannot find it). The translation model test takes `RPGM_HUB_ARGOS_MODEL=/path/ja_en.argosmodel`
(https://argos-net.com/v1/translate-ja_en-1_1.argosmodel). mkxp-z reads `<exe name>.ini`, so the binary must be copied next to the game as `Game`.
Fixtures: `tools/make_marshal_fixtures.rb`, `tools/make_save_fixtures.rb` (real Ruby). Design: `docs/plan.md`, `docs/plan-hub.md`.

Licence: GPL-3.0 (see LICENSE). Not affiliated with Gotcha Gotcha Games / Kadokawa; "RPG Maker" is their trademark.
