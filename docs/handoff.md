# Handoff

## Goal
RPGM Hub (Linux, PySide6 + CLI): upscale RPG Maker MV/MZ/VX Ace/VX/XP games to 1080p, edit saves, translate whole Japanese games to English offline (data text by default, OCR image overlay optional).

## Decisions and constraints
- Work only on branch `ccr-3bb5b40d-4u069j`. Import name `rpgm_upscaler`.
- Models run on ctranslate2: NLLB-200 600M (`translate install nllb`, preferred) with Argos ja-en fallback. Romaji only for names and short sound-effect lines.
- Translation cache keys carry `PIPELINE` (translate/service.py); bump it when protection or splitting rules change. Cached results that contain character names carry a signature of the names used.
- `translate-game GAME -o OUT` writes a copy; `OUT/.translation/report.tsv`, `memory.tsv` (reuse with `--memory`), `images.json` (`--resume` keeps images already overlaid). `--fast`/`--beam N` trade quality for speed (beam 1 is ~2x faster; it sometimes drops a clause). Names that scripts or plugins compare against are kept.
- OCR = Tesseract `jpn` + OpenCV (`--ocr`). Short readings must occur in the game's own text; `plausible_text` filters art noise.
- mkxp-z exe must be named `Game`; `"RTP": [path]` in mkxp.json (filled automatically when the RTP named in Game.ini is found: `$RPGM_RTP`, a Wine prefix, `~/RTP` ...).
- `CLAUDE.md` Autonomy rules and Stop hook `.claude/hooks/keep-going.sh`: end replies with `STATUS: DONE` or `STATUS: NEEDS_INPUT - <question>`.

## Current state
- Full suite: 196 passed, 8 skipped (`QT_QPA_PLATFORM=offscreen`; the GUI tests need PySide6 and the system libs `libegl1 libgl1 libxkbcommon0 libdbus-1-3 libfontconfig1`; run the suite through a Haiku agent). Skipped: real mkxp-z, real tesseract, `RPGM_REAL_RGSS3A=<Game.rgss3a>`, and the browser e2e tests when node is missing.
- Done: save map transfer (reserved transfer; verified against the real MV engine by `tools/e2e_saves.py`); translation name reuse, sound effects, Ace `Vocab` script, MZ 357 text args, printf fill-ins, `--fast`, `--resume`; ncnn batching / scale choice / crash retry; archives (mmap, partial unpack, symlink and case guards, RTP lookup, cancel); planner (header-only PNG sizes, key check, tileset-based texture slots, `--orig`, MV outline 4); GUI (async slot loading, single read on open, refused edits revert, queued previews, HiDPI/crisp previews, theme colours, drag and drop, merged settings); packaging (`tools/build_bundle.sh`, `tools/build_appimage.sh`, `rpgm-hub analyze|run|...` CLI dispatch); Marshal hash keys 1/true/1.0; XP (Hires pack, translate, saves).
- Real games used (git-ignored scratchpad, never committed): VX Ace *Twenty Years Ago* (real `.rgss3a`), VX *Full Indie Quest* (loose `.rvdata`: analyze/plan/run/scripts work), MZ *The Reids* (itch.io), XP *Never Coming Back* (real `.rgssad`; English text, so only unpack/plan/run were exercised; XP translation is tested on a fake Japanese XP game).
- Real MZ game in headless Chromium (`tools/e2e_mz.js URL OUT W H`): title, map, menu, items, skills, equip, status, options, save, name input, shop, battle and audio load in the original and in the upscaled copy. Fixed on the way: enemy/actor positions got the content offset twice (`UpscalerPatch.js.tmpl`).
- Real `realesrgan-ncnn-vulkan` (release v0.2.5.0, Chromium's SwiftShader as Vulkan: `VK_ICD_FILENAMES=/opt/pw-browsers/chromium-1194/chrome-linux/vk_swiftshader_icd.json`): batch directory mode and `-s 2/3/4` work; a whole game is too slow on a CPU Vulkan to finish.
- Freeware fetch recipe: itch game page -> POST `<game>/download_url` (csrf) -> download page -> POST `<game>/file/<upload_id>?source=game_download&after_download_lightbox=true` -> signed URL (the download page is sometimes empty: retry). `.exe` SFX: `apt-get update && apt-get install p7zip-full unshield`. Plain github.com curl is 403; `git clone` and release-asset URLs work (mkxp-z asset names are unknown here).

## Remaining ranked items
1. **MZ layout polish**: window heights differ a few px from N x original (line-height rounding) in `tools/e2e_mz.js` reports; fixed so far: enemy/actor offset (MZ only), battle status window width (`Graphics.boxWidth - 192`), `Sprite_Name` bitmap (the scan of fixed-size methods now includes `Sprite_*`). Look at the other scenes' screenshots by eye for clipped text (title, menu, status, shop, options, name input).
2. **Translation**: per-speaker context; note tags and `js/` literals (need plugin knowledge); MV plugin command strings (code 356); XP `Game.ini` title.
3. **mkxp-z end-to-end** for XP/VX/Ace (`tests/test_e2e_mkxpz.py` needs a binary; releases are not reachable here, building needs SDL2/OpenAL/Ruby).
4. **GUI**: the old note "`tr()` readiness" is unclear (probably the lazy `HubContext.translator` being created on the UI thread); saves are still written on the UI thread.
5. **Packaging**: PyInstaller bundle verified for CLI, GUI start and multi-process `run`, AppImage built here; not tried on a desktop with a display.
6. **Test gaps**: mixed encryption inside one game (the engine cannot load such a game anyway), a save that fails to write.

## Not verified
Stock RGSS (`stock640`) in a real player; real mkxp-z with any Hires pack; XP translation on a real Japanese game; OCR on decorative lettering; MZ in a visible GPU window.

## Next step
Take item 1: run `tools/e2e_mz.js` on the original and upscaled MZ game (about 15 min on software GL; `ONLY=shop,menu` limits it) and look at the screenshots.

## Chat
Base name: rpgm-mz-upscaler-27
Run: 4
