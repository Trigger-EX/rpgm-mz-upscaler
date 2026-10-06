# RPGM Hub: plan for VX/Ace support, a save editor, offline translation and the hub GUI

This plan builds on `docs/plan.md`. The existing MV/MZ upscaler (`core/`, `cli.py`, `gui/`, 31 tests) keeps working
unchanged at every phase. Legend: **[VERIFY]** means a fact from memory that has not been checked against the source or a
real game. **[SANDBOX]** means it cannot be verified here.

What I checked in this sandbox (2026-10-06):
* `ruby 3.3.6` is installed at `/usr/local/bin/ruby`, and `node 22` is installed. Ruby can therefore produce **real Marshal
  fixtures** and run `ruby -c` syntax checks. Tests must still pass without Ruby: they use committed fixtures and skip
  the Ruby-dependent cases.
* pip works. `rubymarshal 1.2.10` (WTFPL) **fails the byte round-trip**. On a Ruby-made stream of 176 bytes it wrote
  202 bytes. It drops object links (`@`), re-formats floats (`1/3` becomes `0.33333333333333331483`), and loses the
  `_dump` payload of user-defined types (`Table` came back as `UserDef({})`). **Decision: write our own Marshal codec**
  (about 450 lines). rubymarshal stays useful as a reference only.
* `argostranslate 1.11` depends on `spacy` and `stanza==1.10.1`, and stanza pulls in torch. That is far too heavy.
  `ctranslate2 4.8.2` (cp313 manylinux wheel, 40 MB) and `sentencepiece 0.2.2` (1.4 MB) install fine. **Decision: run
  Argos `.argosmodel` packages directly with ctranslate2 and sentencepiece.** We do not depend on argostranslate.
* The Argos index (raw.githubusercontent.com) is reachable and lists `translate-ja_en` 1.1 at
  `https://argos-net.com/v1/translate-ja_en-1_1.argosmodel`. **argos-net.com is blocked by the sandbox proxy (403)**,
  so the real model cannot be tested here [SANDBOX]. Tests use a fake backend, plus one opt-in test that runs when the
  `RPGM_HUB_ARGOS_MODEL=/path/file.argosmodel` environment variable is set.

---

## 0. Corrections to the brief (they change the design)

| Brief says | Reality (to [VERIFY] against RGSS docs or real games) | Consequence |
|---|---|---|
| The Ace script calls `Graphics.resize_screen(1920,1080)` | Stock RGSS3 (and RGSS2) caps `resize_screen` at **640x480** and raises `ArgumentError` above it. The built-in `Tilemap` is native C code with a **fixed 32 px tile**. No Ruby script can make the stock engine draw upscaled tilesets. | A pure-script 1080p mode for stock `Game.exe` is **not feasible**. See §2.4 for what we do instead. |
| VX cannot resize its screen at all | RGSS2 has `Graphics.resize_screen` up to 640x480. RGSS1 (XP) has none. | VX and Ace get the same treatment. |
| An Ace save is one Marshal array | `DataManager.save_game_without_rescue` writes **two consecutive Marshal streams**: a header (`{:characters, :playtime_s}`) and then a contents Hash (`:system,:timer,:message,:switches,:variables,:self_switches,:actors,:party,:troop,:map,:player`). | The codec must support `load_all(bytes) -> list` and `dump_all(list)`. |
| VX save layout | `Scene_File#write_save_data` writes about 14 consecutive streams: characters, frame_count, last_bgm, last_bgs, `$game_system`, message, switches, variables, self_switches, actors, party, troop, map, player [VERIFY order]. | `saves/vx.py` maps the streams by position and checks each one's class name. If the layout does not match, it opens read-only. |
| MZ saves use LZString like MV | MZ `StorageManager.jsonToZip` uses `pako.deflate(..., {to:"string", level:1})`, which produces zlib data. The file may be raw zlib bytes or the latin-1 string encoded as UTF-8 [VERIFY]. | `saves/mvmz.py` sniffs the codec and writes back the same variant. |

---

## 1. Package layout and naming

**Decision:** keep the import name `rpgm_upscaler`. Renaming it would churn every test and entry point for no gain.
Rename the *product*: the README title, window title and `app.setApplicationName` become **"RPGM Hub"**. Add the
console scripts `rpgm-hub` (GUI) and `rpgm-hub-cli`, and keep `rpgm-upscaler`/`rpgm-upscaler-cli` as aliases.
Keep the settings directory `rpgm-upscaler` so user settings are not lost.

```
rpgm_upscaler/
  core/                      # existing MV/MZ upscaler, unchanged API
    project.py               # + detect_engine(path) -> EngineInfo (engine in MV|MZ|VX|ACE|XP, root, archive)
  rgss/                      # RPG Maker XP/VX/Ace shared code (no Qt)
    marshal.py               # Ruby Marshal 4.8 reader/writer, byte-exact (§2.1)
    archive.py               # RGSSAD v1 (rgssad/rgss2a) and v3 (rgss3a): list, extract, pack (packing is for tests) (§2.2)
    scripts.py               # Scripts.rvdata(2): list, get/set source, insert before "Main", remove by title
    project.py               # RgssProject: engine, Game.ini (Title, Library=RGSS30x.dll, Scripts=...), archive, data dir
    categories.py            # VX/Ace folder rules and cell grids (§2.3)
    planner.py               # builds core.planner.Job objects for Graphics/ -> reuses core.runner
    patch.py                 # mkxp-z Hires pack + mkxp.json; optional 640x480 script (§2.4)
    templates/HubResolution.rb
  saves/                     # no Qt
    model.py                 # SaveFile/SaveDoc: neutral view (switches, variables, gold, party, inventory, position)
    mvmz.py                  # MV .rpgsave (LZString base64) and MZ .rmmzsave (zlib): codec + JsonEx-tree accessors
    lzstring.py              # compressToBase64/decompressFromBase64 (port, about 150 lines)
    rgss.py                  # VX .rvdata / Ace .rvdata2 accessors on Marshal objects
    database.py              # names: MV/MZ data/*.json, or VX/Ace Data/*.rvdata(2), read from the folder or the archive
    io.py                    # backup, verify, atomic write (§3.3)
  translate/                 # no Qt
    service.py               # Translator: translate(text), translate_many(list), backends, cache, overrides
    detect.py                # is_japanese(), protect/restore control codes
    glossary.py              # bundled RPG glossary + kana->romaji transliteration
    argos.py                 # ctranslate2 + sentencepiece runner for .argosmodel; install/import/locate
    cache.py                 # sqlite cache
    data/glossary_ja_en.json # curated, about 800 entries
  gui/
    hub.py                   # HubWindow (QMainWindow): sidebar + QStackedWidget, shared HubContext
    upscale_tab.py           # current MainWindow body moved into UpscaleTab(QWidget)
    saves_tab.py, translate_tab.py, project_tab.py
    main_window.py           # kept: MainWindow = thin QMainWindow hosting an UpscaleTab and forwarding attributes (smoke test)
  cli.py                     # existing analyze|plan|run + new subcommands (§5)
```

`pyproject` extras: `gui = [PySide6-Essentials]`, `translate = [ctranslate2>=4,<5, sentencepiece>=0.2,<0.3]`. The base
install stays at Pillow and numpy. Marshal, LZString, archive handling and the glossary are pure Python.

---

## 2. A) RPG Maker VX (RGSS2) and VX Ace (RGSS3)

### 2.1 Marshal codec (`rgss/marshal.py`), the foundation for everything RGSS

* Python types: `None/True/False/int` map to themselves. `RFloat(value, raw: bytes)` keeps the original text and writes it
  back unless the value changed; new values are written in Ruby's format: `"%.17g"` trimmed to the shortest repr, plus
  `inf`, `-inf` and `nan`. `RString(data: bytes, ivars: dict)` has a `.text` property that decodes UTF-8 and falls back
  to cp932 for VX, and it re-encodes on set while keeping the ivars `E`/`encoding`. VX (Ruby 1.8) strings carry no ivar,
  and none is added. `Symbol(str)`. `RObject(cls: str, ivars: dict)` keeps ivar order. `RUserDef(cls, data: bytes)` handles
  `u` types (`Table`, `Color`, `Tone`, `Rect`) and keeps their bytes verbatim; `Table` gets a helper decoder
  (dims, xsize/ysize/zsize, int16 data). `RUserMarshal(cls, obj)` for `U`. `RHash(dict, default)` for `{`/`}`.
  `RStruct`, `RRegexp`, `RClassRef`/`RModuleRef` (`c`/`m`), `RExtended` (`e`) and `RBignum` complete the set.
* **Identity:** the reader keeps the object table. Repeated `@n` references resolve to the **same Python object**. The
  writer rebuilds `@` links by `id()` and symlinks (`;`) by symbol name, in the same order Ruby uses (Floats, Strings,
  Arrays, Hashes, Objects and Bignums count; Fixnums, nil and booleans do not; `u`/`U` count [VERIFY]). This is what
  makes the output byte-exact, and it keeps Ruby's aliasing intact after an edit.
* `int` writes as `i` when it fits in 30 bits (Fixnum is 31-bit on 32-bit RGSS). Larger values write as `l`, and the
  editor clamps game values to the engine limits anyway.
* `loads`, `dumps`, `load_all(buf)` (consecutive streams) and `dump_all(objs)`. Every hash keeps insertion order. Unknown
  type bytes raise `MarshalError(offset, byte)`. We never guess.
* Tests: (a) committed fixtures `tests/fixtures/rgss/*.bin` generated by `tools/make_marshal_fixtures.rb` (Ruby) that cover
  every type byte, shared objects, Japanese strings, floats such as 0.1, 1/3, 1e20 and -0.0, Bignums, `Table`, and
  `RPG::Actor`-like objects. For each fixture, `dumps(loads(b)) == b`. (b) An edit test: change one ivar, dump, then
  check with Ruby (skipped if Ruby is absent) that `Marshal.load` yields the edited value and that shared objects are still
  `equal?`. (c) Hypothesis-style random trees from our own writer round-trip.

### 2.2 Archives (`rgss/archive.py`)

* **v1** (`Game.rgssad` for XP, `Game.rgss2a` for VX): magic `RGSSAD\0\x01`, key = `0xDEADCAFE`, and
  `adv(k) = (k*7+3) & 0xFFFFFFFF`. For each entry: `namelen = u32 ^ k`, adv. Each name byte is `^ (k & 0xFF)`, with an adv per byte.
  `size = u32 ^ k`, adv. Then come the data bytes at the current position, decrypted with a *copy* of k (see below). The
  main key does not advance over the data.
* **v3** (`Game.rgss3a`): magic `RGSSAD\0\x03`, `seed = u32`, `k = seed*9+3`. The table is a series of entries: `offset, size,
  ekey, namelen` (each `u32 ^ k`), then `name[i] ^ ((k >> 8*(i%4)) & 0xFF)`. The table ends when `offset == 0`.
* Data decryption (both versions): XOR each little-endian u32 word with `dk`, then `dk = dk*7+3`. The trailing partial
  word uses the low bytes of `dk`. Names use `\` separators; we turn them into `/`.
* API: `open_archive(path) -> Archive(entries)`, `read(name)`, `extract_all(dest, progress, cancel)` with path
  sanitising (reject `..` and absolute paths), and `pack(files, version, seed)` for tests only. Also a
  read-only `VirtualFS(root, archive)` so the save editor can read `Data/*.rvdata2` without extracting anything.
* Tests: pack a synthetic tree, then unpack and compare bytes, for both versions, odd sizes (0, 1, 3, 5 bytes), Japanese
  file names (cp932 in v1, UTF-8 in v3 [VERIFY]) and a malicious name. [SANDBOX] No real `.rgss3a` is available. Add a
  `tools/check_archive.py` that the user can run on a real game.

### 2.3 Upscaling Graphics/ (`rgss/categories.py`, `rgss/planner.py`)

The VX/Ace default screen is 544x416, with a 32 px tile. Grids, with cell sizes derived from the image size where possible:

| Folder | Rule |
|---|---|
| Characters | normal sheets: 12x8 cells (8 characters of 3x4); `$` prefix: 3x4; `!` only affects the y shift, so the grid is the same |
| Faces | 4x2 cells (96 px) |
| System/IconSet | 24 px cells, 16 per row |
| System/Balloon | 32 px cells (8x10) [VERIFY] |
| System/Window | **never scaled** by default (128x128 skin read at fixed coordinates, same as MV) |
| Other System files | GameOver: cover; Shadow, BattleStart, others: whole image |
| Tilesets | A1-A4: 16 px half-tiles (autotiles); A5 and B-E: 32 px tiles; kind from `Tilesets.rvdata2` `@tileset_names` (A1..E order), else from the `_A1`..`_E` suffix |
| Animations | 192 px cells, 5 columns |
| Battlers, Pictures, Parallaxes | whole image, exact N |
| Titles1/2, Battlebacks1/2 | cover |
| VX (RGSS2) specifics | `TileA1..TileE` single sheets with the same 32/16 px rules; `Graphics/System/IconSet` the same |

* Inputs: `.png`, `.jpg` and `.bmp`. Outputs keep the same base name. JPG stays JPG at quality 95; BMP becomes PNG only when RGSS
  can load it (RGSS loads files by base name without an extension [VERIFY]), and PNG is the default.
* This reuses `core.imageops.upscale_image`, the engines and the runner. `core.planner.Job` is engine-agnostic, so the
  RGSS planner only has to emit Jobs. The runner needs a `patcher` hook, `plan.apply_patches(out)`, instead of the
  hard-coded MV/MZ `apply_patches`.
* Encrypted games: extract the archive into the output folder first, then plan on the extracted files. The original
  archive is never modified, and the output does not contain an archive.
* Scale: `N = k/8`, the same as MV. The default target is decided in §2.4.

### 2.4 Running at a higher resolution: what is possible

**Primary path: the mkxp-z "Hires" pack.** mkxp-z is an open-source RGSS1/2/3 player for Linux, macOS and Windows. It has a
hires mode: `enableHires`, `textureScalingFactor`, `framebufferScalingFactor`, `atlasScalingFactor` in `mkxp.json`, with
replacement images loaded from `Hires/Graphics/...`, while game logic keeps the original coordinates [VERIFY the exact key
names and folder against the current mkxp-z docs and version]. This is exactly what we need. No Ruby layout patch is
required, every script and plugin keeps working, and the 32 px Tilemap still works. The output is:
* `Hires/Graphics/**` holding the upscaled assets (N is an integer or k/8; mkxp-z takes a float factor [VERIFY]), with the originals left in place.
* `mkxp.json` with the hires keys, `defScreenW/H` = window size (`544*N x 416*N`, fit to 1920x1080),
  `fixedAspectRatio: true` and `smoothScaling` set.
* `README-HUB.txt` explaining how to start it with mkxp-z (the user downloads mkxp-z; we do not bundle it).
* Tests: the folder layout, the JSON content, and an idempotent re-run. [SANDBOX] We cannot launch mkxp-z here
  (no binary, no GPU, no RTP).

**Secondary path: stock Game.exe, script injection.** `HubResolution.rb` is inserted into `Scripts.rvdata2` before
`Main` under the title `▼ RPGM Hub Resolution`:
* `Graphics.resize_screen(640, 480)` wrapped in `rescue` and guarded with `if Graphics.respond_to?(:resize_screen)`.
* Ace: re-center `Spriteset_Map`, `Game_Map#screen_tile_x/y` (Ruby already reads `Graphics.width`), and nudge the
  battle/title sprites so they use `Graphics.width/height` instead of the 544/416 literals found in the default scripts
  (`Sprite_Battler`/`Spriteset_Battle` `create_battleback`, `Window_*` widths). Each override is guarded with
  `if defined?(Klass) && Klass.method_defined?(:m)` and wrapped with `alias`. Errors go to `msgbox`-free `p`.
* Assets are **not** upscaled in this mode, because the stock engine cannot use them. The UI says so plainly.
* A third-party "HD" RGSS replacement (RGD, a DirectX RGSS3) is mentioned as an alternative but is not targeted.

**A full script-scaled 1080p for stock RGSS** (the brief's line_height, standard_padding, Font.default_size, icon/face
scaling) is **rejected**. It is capped at 640x480 and the native Tilemap cannot draw 32*N tiles, so it would need a
Ruby re-implementation of Tilemap, which is slow on RGSS and a project of its own.

**VX (RGSS2):** the same two paths apply. Data lives in `Data/*.rvdata` and scripts in `Data/Scripts.rvdata` (same
`[id, title, zlib]` structure). The 640x480 script uses VX class names (`Game_Map#calc_parallax_x`, `Spriteset_Map`, no
`Graphics.width` in some scripts [VERIFY]), so it is a separate template. **XP** is detected and its saves are not
supported. Hires could be added later cheaply (different grids: Characters 4x4, Autotiles 96x128, Windowskin 192x128).

**Script syntax check:** `ruby -c` (Ruby 3.3 is here) runs in a test that is skipped when `ruby` is missing. We write
1.9-compatible syntax: no keyword args, no `&.`, no `%i[]`. A second test runs the script under Ruby with stub
`Graphics`/`Window_Base` classes, so the guards are exercised. [SANDBOX] Behaviour inside real RGSS3 or RGSS2 is unverified.

---

## 3. B) Save editor

### 3.1 Formats

| Engine | Files | Codec |
|---|---|---|
| MV | `save/fileN.rpgsave`, `global.rpgsave`, `config.rpgsave` | `LZString.compressToBase64(JsonEx.stringify(contents))` |
| MZ | `save/fileN.rmmzsave`, `global.rmmzsave`, `config.rmmzsave` | pako zlib; sniff `78 01/9C/DA` (raw) vs UTF-8-encoded latin-1 string [VERIFY] |
| Ace | `SaveNN.rvdata2` (game root) | 2 Marshal streams: header, contents Hash |
| VX | `SaveN.rvdata` | about 14 Marshal streams by position |

MV/MZ JSON: decode with `json.loads` (dicts keep their order) and leave the JsonEx metadata alone: `"@"` (class), `"@a"` (arrays with
extra properties), and MV's `"@c"`/`"@r"` (circular ids). Edits change values in place in the original tree and never rebuild
objects. Encode with `json.dumps(ensure_ascii=False, separators=(",", ":"))` (the same as `JSON.stringify`). Check that
NaN and Infinity cannot appear, and refuse the file if they do.

### 3.2 Neutral model (`saves/model.py`)

`SaveDoc` exposes typed accessors over the native tree (it is not a copy):
`switches[i]: bool`, `variables[i]: int|float|str` (other values are read-only), `gold`, `party_members -> [ActorView(id,
name, level, exp, hp, mp, param_plus[8])]`, `inventory(kind in items|weapons|armors) -> {id: count}`, `map_id, x, y`,
and `playtime` (read-only). Paths:
* MV/MZ: `switches._data`, `variables._data`, `party._gold`, `party._items/_weapons/_armors` (`{"id": n}`), `party._actors`,
  `actors._data[i]._level/_exp{classId}/_hp/_mp/_paramPlus`, `map._mapId`, `player._x/_y` (also `_realX/_realY`).
* Ace/VX: `@data` of Game_Switches/Variables, `@gold`, `@items/@weapons/@armors` (Hash int→int), `@actors` (ids),
  `Game_Actors@data[i]` ivars `@level @exp @hp @mp @param_plus`, `Game_Map@map_id`, `Game_Player@x @y @real_x @real_y`.
  VX actors use `@maxhp_plus` etc. instead of `@param_plus` [VERIFY].
* Guards: clamp values (gold 0..99999999 for MV/MZ and 0..9999999 for Ace; level 1..99; items 0..99 for the engine max), and
  when the level changes, offer "sync EXP" using the class `expParams`/`@exp_params` formula. Teleporting resets `_realX/_realY`
  and sets `_transferring` false. A missing path makes that field read-only, and the rest of the editor still works.

### 3.3 Safety (`saves/io.py`)

1. Read the bytes, decode, and **self-check before any edit**: `encode(decode(b))` must decode to an equal tree. For
   Marshal it must also be byte-equal. If it is not, the file opens **read-only** with the reason shown.
2. On save: encode, decode again, and compare with the edited tree (deep equality with type-aware floats). Only then write
   `file.bak` (or `file.bak.N` if one exists, keeping the last 5) and atomically replace the file (`tmp` + `os.replace`).
3. Refuse the write if the game appears to be running (best effort: the file's mtime changed since it was loaded).

### 3.4 Names (`saves/database.py`)

MV/MZ: `System.json` (`switches`, `variables`, `gameTitle`, `currencyUnit`) plus `Actors`, `Classes`, `Items`, `Weapons`,
`Armors`, `MapInfos.json`, found from the save dir (`<web>/save/..` → `<web>/data`). Ace/VX: `Data/System.rvdata2`
`@switches/@variables`, `Actors`, `Classes`, `Items`, `Weapons`, `Armors`, `MapInfos.rvdata2`, read through `VirtualFS`
(folder or archive). If the database is missing, labels are shown as "#0012". Names carry `\C[n]`/`\I[n]` codes. Show them stripped.

---

## 4. C) Offline translation (`translate/`)

**Recommendation: a layered service, with the glossary always on and Argos when installed.** Lookup order per string:
1. User overrides (`$XDG_CONFIG_HOME/rpgm-upscaler/translations.json`, exact text → text, editable in the UI).
2. The sqlite cache `$XDG_CACHE_HOME/rpgm-upscaler/translate.sqlite` (`text, backend, model_ver → result, ts`).
3. The **Argos backend** (if installed): unzip `.argosmodel` into `$XDG_DATA_HOME/rpgm-upscaler/models/translate-ja_en-1_1/`
   (`model/` for CTranslate2 and `sentencepiece.model`; read `metadata.json`). Run
   `ctranslate2.Translator(model_dir, device="cpu", inter_threads=1)` and `translate_batch` with `beam_size=2`
   and `max_decoding_length=64`, encoding and decoding with sentencepiece. Batches of 32. Glossary terms are pre-substituted
   only for exact whole-string matches. Mixing scripts confuses the model.
4. The **glossary backend** (always present): an exact match, then a longest-match segmentation over the glossary (only when
   it covers 100% of the string), then **katakana/hiragana → romaji** (Hepburn, long vowels, small tsu) for strings made only of kana.
   This handles names like アレックス → "Arekkusu". Anything else is returned unchanged with `confidence=0`,
   and the UI shows the original.
* Pre- and post-processing: `is_japanese()` checks for any char in Hiragana `3040–309F`, Katakana `30A0–30FF/31F0–31FF`,
  half-width `FF66–FF9F`, or CJK `3400–4DBF/4E00–9FFF`. Anything else is returned untouched. Control codes (`\C[2]`, `\V[1]`,
  `\N[1]`, `\I[64]`, `\\`, `%1`) are swapped for placeholders before translation and restored after. Full-width digits and
  letters are NFKC-normalised.
* API: `Translator.translate(text) -> Result(text, source, backend, confidence)`,
  `translate_many(texts, progress, cancel) -> list[Result]` (dedupes, cache first, then backend batches), and
  `status() -> {argos: installed|missing|deps-missing, model_path, glossary_size}`.
* **Install step (UX):** the Translation tab shows the status and has two buttons. "Install translation model" downloads
  the Argos index from GitHub raw, then the `ja_en` link, into a temp file with progress and cancel; it
  checks that the zip contains `model/model.bin` and `sentencepiece.model`, then moves it into place. "Import .argosmodel
  file…" does the same for a local file, and also accepts an existing `~/.local/share/argos-translate/packages/translate-ja_en*`
  folder. If `ctranslate2` is missing, the tab shows `pip install 'rpgm-upscaler[translate]'` (from run.sh:
  `.venv/bin/pip install ctranslate2 sentencepiece`). After install, nothing touches the network. The CLI has
  `translate install|import PATH|status`.
* Never block the UI: all translation runs in a `TranslateWorker(QThread)` and results stream in by signal. The save
  editor fills in its "English" column as results arrive.
* Quality, honestly: Argos ja→en (OPUS-trained, small) is fair on sentences and **weak on short game terms and names**.
  It produces literal or odd output and sometimes hallucinates. Glossary-first and user overrides are what make it usable.
  JMdict (CC-BY-SA 4.0, about 10 MB, needs segmentation) is **deferred**, so the light install stays light.
  An MT model this small should not be relied on for proper nouns.
* Tests: detection, placeholder round-trip, romaji table, glossary segmentation, cache hit/miss with a fake backend (a counter
  checks the backend runs once), override precedence, batch dedupe, a "deps missing" status with ctranslate2 import
  monkeypatched away, and archive validation of a fake `.argosmodel` zip. The real-model test needs `RPGM_HUB_ARGOS_MODEL` [SANDBOX].

---

## 5. D) Hub restructure and CLI

* `core.project.detect_engine(path) -> EngineInfo(engine, root, web, archive, version)`:
  MV/MZ through the existing `find_web_root`/`_detect_engine`. ACE if `Game.ini` contains `Library=...RGSS3` or there is
  `Data/*.rvdata2` or `Game.rgss3a`; VX for `RGSS2`, `*.rvdata` or `Game.rgss2a`; XP for `RGSS1`/`RGSS10`, `*.rxdata` or `Game.rgssad`
  (detected but unsupported). Also accepts a save file or the `save/` folder (it walks up). `load_project` stays MV/MZ-only and
  unchanged. `rgss.project.load_rgss_project` is its sibling.
* GUI: `HubWindow(QMainWindow)` has a left `QListWidget` sidebar (Project, Upscale, Saves, Translation, Log) and a
  `QStackedWidget`. `HubContext(QObject)` holds the current game path and `EngineInfo` and emits `project_changed`. The Project page
  is a folder picker, detection result and engine badge, with drag & drop. The Upscale page is the old MainWindow body as
  `UpscaleTab`. It picks the MV/MZ planner or the RGSS planner by engine and shows "unsupported" for XP. `MainWindow` stays as a thin
  wrapper so `test_gui_smoke.py` passes unchanged. `gui/app.py` launches `HubWindow`.
* Saves page: a slot list (file, mtime, playtime, party/leader from the header or the MV `global` info) on the left, and tabs on the right:
  Switches (table: id, name, EN, checkbox, filter, "changed only"), Variables, Party (actor cards: level, exp, hp, mp, params),
  Inventory (items, weapons, armors with spinbox counts and "add item" from the database), Position (map combo + x/y), and Raw (a
  read-only tree for MV/MZ JSON and Marshal). It has Save, Revert, and Open backup folder. Dirty-state tracking warns on close.
* Translation page: status, install/import, glossary override table, "clear cache".
* CLI (`rpgm-hub-cli`, alias of the existing CLI module):
  `detect PATH` · `saves list GAME` · `saves dump SAVE [--json]` (neutral model, plus names with `--names`) ·
  `saves set SAVE --switch 12=on --var 5=100 --gold 5000 --item 3=10 [--no-backup is NOT offered]` ·
  `unpack GAME [-o DIR]` · `scripts list|extract|insert GAME` · `translate TEXT...|--file F` ·
  `translate install|import|status` · existing `analyze|plan|run` (route by engine: VX/ACE → RGSS planner, with `--mode hires|stock640`).

---

## 6. E) Phases (each one ends with all tests green, the old 31 included)

**Phase 1: Marshal, LZString and the save editor core plus CLI (no GUI).** This phase stands alone and is fully testable.
`rgss/marshal.py`, `saves/{lzstring,mvmz,rgss,model,database,io}.py`, `core.project.detect_engine`, and CLI
`detect`/`saves list|dump|set`. Fixtures: `tools/make_marshal_fixtures.rb` (Ruby makes Ace-like and VX-like saves and
`System.rvdata2` with fake `Game_*`/`RPG::*` classes; outputs committed), and `tests/fakesaves.py` (MV/MZ saves built in Python, plus
one MV save produced by **node + lz-string from corescript `js/libs`**, committed, so we interoperate with the real
library). Done when: round-trip byte-exactness on every fixture, edit→Ruby `Marshal.load` check (skipped without Ruby),
`.bak` created, a corrupt file opens read-only, and the CLI set/dump works.

**Phase 2: Saves GUI and the hub shell.** `gui/hub.py`, `project_tab.py`, `saves_tab.py`, and `upscale_tab.py` (moved, not
rewritten), with the product renamed. Offscreen smoke test: open a fake MV and a fake Ace save, toggle a switch, change gold, save,
reload, check the `.bak`. The old GUI smoke test passes as is.

**Phase 3: Translation.** The `translate/` package, glossary data, Translation tab, the "EN" column in the save editor through a QThread,
and CLI `translate`. Tests as in §4. Install and import are tested against a fake zip and a local HTTP server fixture (`http.server`
on localhost) for the download path.

**Phase 4: RGSS archives and the VX/Ace upscale.** `rgss/archive.py` (plus the packer), `scripts.py`, `categories.py`,
`planner.py`, `patch.py`, the runner patch hook, `unpack`/`scripts` CLI, and the Upscale tab routing. Fixture `tests/fakeace.py` builds a
full fake Ace project (Game.ini, Data/*.rvdata2 including Scripts and Tilesets, Graphics/** with grid images as in
`fakegame.py`), packed into `Game.rgss3a` by our packer. Tests: archive round-trips v1/v3, plan categories and cell sizes,
full hires run (layout, `mkxp.json`), stock640 script inserted before `Main` and idempotent, `ruby -c` and stub-run of the template,
and Scripts.rvdata2 byte-exact when nothing changed.

**Phase 5: Polish.** README rewrite ("RPGM Hub"), `tools/check_archive.py`/`tools/check_save.py` for users with real games,
and the XP hires pack if cheap.

### Risks and unknowns

* [VERIFY] The mkxp-z hires keys, folder name and float factor support. If they differ, phase 4's hires output changes (isolated in `rgss/patch.py`).
* [VERIFY] The MZ save byte encoding (raw zlib vs UTF-8 latin-1). The sniffer handles both, so test both.
* [VERIFY] The VX save stream order and actor ivars, and the RGSS1/2 string encoding (cp932 vs UTF-8) in old games.
* [VERIFY] The Marshal object-table counting rules for `u`/`U`/`e`/`C`. The fixtures from real Ruby settle this; Ruby 3.3 vs RGSS's Ruby
  1.8/1.9 could differ in float formatting (only matters for new floats; unchanged floats keep their raw bytes).
* Saves of games using custom scripts or plugins hold extra classes or keys. They are preserved untouched (generic objects), but the
  neutral view may not find e.g. a custom gold variable.
* Translation quality on single words is weak (see §4).

### Not verifiable in this sandbox

Running any RGSS game (no Windows, Wine, RTP or mkxp-z); real `.rgss2a`/`.rgss3a` archives; that a real Ace or VX game loads an
edited save or the injected script; real MZ saves; the real Argos model download (argos-net.com is blocked) and its output
quality. The MV save path *can* be checked end-to-end with corescript in headless Chromium (load an edited `file1.rpgsave` and
read `$gameParty.gold()`). Add this to `tools/e2e_browser.js` as an optional phase-2 step.
