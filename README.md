# RPGM Hub

One Linux app for all things RPG Maker: **upscale** a game to 1920×1080, **edit its saves**, and **translate a whole
Japanese game into English offline** (dialogue, database, system text, plugin text and, optionally, lettering inside images). It covers RPG Maker **MV, MZ, VX Ace, VX and XP**. A GUI and a CLI share the same core.

```
./run.sh                     # creates .venv, installs PySide6/Pillow/numpy, starts the hub
./run.sh /path/to/game       # ... and opens that game
.venv/bin/python -m rpgm_upscaler.cli --help
```
Single-file builds (Linux): `tools/build_bundle.sh` makes `dist/rpgm-hub/` with PyInstaller (add `--with-translate` /
`--with-ocr` for the optional parts; they make it much bigger), and `tools/build_appimage.sh` wraps it into
`RPGM_Hub-x86_64.AppImage`. `rpgm-hub GAME` opens the window, `rpgm-hub analyze|plan|run|translate-game|... ARGS` is the CLI.

Optional neural translation: click *Translation → Install Python packages* (the hub creates its own virtual environment under `~/.local/share/rpgm-upscaler/venv` if the system Python refuses pip, as on Linux Mint; on Debian/Ubuntu this needs `python3-venv`), or run `.venv/bin/pip install -r requirements-translate.txt`. Then *Translation → Install model*.
Optional image translation (OCR): `.venv/bin/pip install -r requirements-ocr.txt` and install Tesseract with its Japanese data
(`apt install tesseract-ocr tesseract-ocr-jpn tesseract-ocr-jpn-vert`).

## What works for which engine

| | MV | MZ | VX Ace | VX | XP |
|---|---|---|---|---|---|
| Upscale to 1080p | yes (patched engine) | yes (patched engine, checked on one real MZ game) | yes, via an mkxp-z *Hires* pack | yes, via an mkxp-z *Hires* pack | yes, via an mkxp-z *Hires* pack (RGSS1) |
| Save editor | yes | yes | yes | yes | yes |
| Translate a whole game | yes | yes (plugin-command text arguments too) | yes (the `Vocab` script too) | yes | yes |
| Names from the game database | yes | yes | yes | yes | yes |
| Encrypted games | images decrypted/re-encrypted | same | `.rgss3a` unpacked | `.rgss2a` unpacked | `.rgssad` unpacked |

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
**Whole game** (`translate-game GAME -o OUT`): writes a translated *copy* (the original is never touched). By default it translates

* event text: messages (a whole message is translated as one passage, then re-wrapped to the message window, split over several
  windows if needed, speaker labels like `【アレックス】` kept as their own line), choices, scrolling text, name/nickname/profile changes;
* the database: names, descriptions and battle messages of actors, classes, items, weapons, armor, skills, states, enemies;
* system text: game title, currency, terms, map display names;
* visible plugin parameters (MV/MZ): only strings that are not file names, code, switch/variable references or typed as non-text.

It never touches script calls, plugin commands, notes, file names, or **names that scripts or plugins compare against** (those are
listed in the report as "kept"). Control codes (`\C[2]`, `\N[1]`, `%1`...) are protected and verified after translation.
Every translation is listed in `OUT/.translation/report.tsv`; `OUT/.translation/memory.tsv` holds the unique pairs: edit the
English column and re-run with `--memory memory.tsv` to apply your corrections. Options: `--no-dialogue --no-database --no-system
--no-plugin-params`, `--wrap-chars N`, `--link` (hard-link unchanged files), `--overwrite`, `--resume` (continue in an existing output:
text is redone from the cache, images already overlaid are kept), `--fast` / `--beam N` (greedy decoding is about twice as fast, with rougher wording).
Actor and enemy names are translated first and then reused verbatim in dialogue; in VX / VX Ace the `Vocab` script's messages are translated
too, and in MZ the plainly textual arguments (`text`, `message`, `title` ...) of plugin commands.

**Images** (`--ocr`): finds Japanese lettering in images with OpenCV + Tesseract, erases it (inpainting) and draws the English over
it in the same place, size and colour. `--ocr-scope likely` (default: pictures, titles, system) or `all` (every image file),
`--ocr-min-conf`, `--font`. Works on encrypted MV/MZ images (re-encrypted) and RGSS PNG/JPG/BMP. Results depend on the artwork: clean
UI lettering works well; stylised or heavily decorated text may be missed or misread, so check the result.

**Models**: names and short terms first go through overrides → a bundled RPG glossary → katakana romaji (names only). Running text
goes to a neural model run directly with ctranslate2 + sentencepiece (no torch): **NLLB-200 600M** if installed
(`translate install nllb`, 620 MB, CC-BY-NC, noticeably better on colloquial lines) and the small **Argos** `ja→en` model as the
fast fallback (`translate install`). Each result is cached in sqlite. Model quality is the limit: expect readable but imperfect English.

## CLI cheat sheet
```
rpgm-hub-cli detect GAME
rpgm-hub-cli analyze|plan|run GAME [-o OUT] [--scale fit|2|...] [--engine lanczos|nearest|sharp|realesrgan|waifu2x] [--mode hires|stock640]
rpgm-hub-cli saves list GAME
rpgm-hub-cli saves dump SAVE [--json] [--names] [--translate]
rpgm-hub-cli saves set SAVE --switch 12=on --var 5=100 --gold 5000 --item weapons:2=3 --actor 1:level=20 --map 3 --pos 7,9
rpgm-hub-cli unpack GAME [-o DIR]                 # encrypted RGSS archive
rpgm-hub-cli scripts GAME [--extract DIR]         # VX / Ace Ruby scripts
rpgm-hub-cli translate TEXT... | --file F | status | install [nllb] | import FILE.argosmodel
rpgm-hub-cli translate-game GAME -o OUT [--ocr] [--ocr-scope likely|all] [--memory memory.tsv] [--no-plugin-params] [--link]
```

## What has actually been verified

* **MV (engine v1.3b, the MIT-licensed [rpgtkoolmv/corescript](https://github.com/rpgtkoolmv/corescript))**: the upscaled sample
  game boots in headless Chromium and is compared scene by scene with the original (plain and encrypted images, ×1.625 and ×2);
  the engine itself writes a save, the editor changes it, and the engine loads the edited save back (`tools/e2e_saves.py`).
* **Save formats**: the LZString port is byte-identical to the library MV ships; the Ruby Marshal codec round-trips fixtures made
  by real Ruby 3.3 byte for byte, and Ruby loads what the editor writes. The RGSS archive algorithm was cross-checked against
  mkxp-z's own `rgssad.cpp`, and the mkxp.json keys and the `Hires/` convention against mkxp-z's source.
* **Real engines and games**: the MV corescript e2e passes; a real **mkxp-z** build runs a generated VX Ace game and the free *Crysalis*
  Ace game (with the official RTP) with the Hires pack, and `tests/test_e2e_mkxpz.py` checks that the Hires texture is what gets drawn.
  A real, full **Japanese MZ 1.3.0 game with encrypted images and 34 plugins** was translated (4352 strings, 18 files rewritten, control
  codes preserved in all but 2 of 1707 strings that have them) and upscaled to 1920x1080; its title, menu and options screens were
  compared with the original in headless Chromium (UI scaled 1.625x, windows proportional, art sharp). All 131 real Crysalis `.rvdata2`
  files load and re-save byte-identically. The real Argos and NLLB models were run.
* **Not verified**: MZ battle, shop and event screens and MZ audio; VX (not Ace) and real `.rgss3a`/`.rgssad` archives;
  the stock RGSS player (`stock640`); image OCR on real Japanese game artwork (only synthetic images so far);
  games with plugin-defined resolutions (e.g. a resolution-option plugin) are likely to need manual touch-up.

## Limitations
* Only core-engine layouts of MV/MZ are patched (including every core `*Width/*Height/*Spacing/*Padding` method that returns a bare
  number); third-party plugins with hard-coded pixel values are not. Expect to touch up odd screens (custom HUDs and menus).
* Games whose plugins let the player pick the resolution at run time are upscaled for one fixed size (the tool warns); a plugin that
  sets one fixed resolution (Community_Basic, YEP Core Engine) is detected.
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
