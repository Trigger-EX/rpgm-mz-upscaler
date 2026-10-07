# Handoff

## Goal
RPGM Hub (Linux, PySide6 + CLI): upscale RPG Maker MV/MZ/VX Ace/VX games to 1080p, edit saves, translate whole Japanese games to English offline (data text by default, OCR image overlay optional).

## Decisions and constraints
- Work only on branch `ccr-3bb5b40d-4u069j`. Import name `rpgm_upscaler`.
- Models run on ctranslate2: NLLB-200 600M (`translate install nllb`, preferred) with Argos ja-en fallback. Romaji only for names.
- Translation cache keys carry `PIPELINE` (translate/service.py); bump it when protection or splitting rules change.
- `translate-game GAME -o OUT` writes a copy; `OUT/.translation/report.tsv` and `memory.tsv` (reuse with `--memory`). Names that scripts or plugins compare against are kept.
- OCR = Tesseract `jpn` + OpenCV (`--ocr`). Short readings must occur in the game's own text; `plausible_text` filters art noise.
- mkxp-z exe must be named `Game`; `"RTP": [path]` in mkxp.json.
- `CLAUDE.md` Autonomy rules and Stop hook `.claude/hooks/keep-going.sh`: end replies with `STATUS: DONE` or `STATUS: NEEDS_INPUT - <question>`.

## Current state
- Full suite: 152 passed, 6 skipped (run it through a Haiku agent). MV real-engine e2e 13/13 (one screenshot check is load-flaky).
- Real Japanese MZ game (`samples/elweed/game`, git-ignored): translated (4352 strings, about 1 h with NLLB on 4 cores), upscaled, and title/menu/options compared with the original in Chromium.
- Patch scales bare-number `*Width/*Height/*Spacing/*Padding` methods and `const ww = N` locals in rect methods.
- Everything is committed and pushed.

## Remaining ranked items (most valuable first)
1. **Saves, map change** (`saves/rgss.py`, `saves/mvmz.py`): only the map id is rewritten, so the old map's events stay. Set the game's transfer flags (`_transferring/_newMapId/_newX/_newY`; Ace/VX `@transferring/@new_map_id...`) and verify with `tools/e2e_saves.py`.
2. **Translation consistency**: translate actor/enemy names first and reuse them in dialogue; per-speaker context; short sound-effect lines ("むにゃ") get invented text; add 357 plugin-command text, note tags, Ace `Scripts` vocab and `js/` literals; an update-existing-output mode so `images.json` resume works.
3. **Translation speed**: NLLB beam 4 is slow; add a `--fast` (beam 1-2) option and measure quality.
4. **ncnn upscaling** (`core/engines.py`, `core/runner.py`): one process per image, always x4 then downscale. Use directory batch mode, the smallest sufficient model scale, and a worker count budgeted by image size; catch `BrokenProcessPool`.
5. **Archives** (`rgss/archive.py`, `rgss/pipeline.py`, `hubcli.py`): whole archive read into RAM (use mmap); `analyze/plan/scripts` extract everything just to read two files; cancelled extraction continues planning; case-colliding names and symlink targets; RTP auto-detection (patch.py).
6. **Planner/patch details**: read PNG sizes from the first bytes of encrypted files; `tex_multiplier` assumes 768 px tilesets; validate a bogus `encryptionKey` against one image and fall back to `recover_key`; `--plain-images` with mixed encryption; MV outline width 4 not 3; runtime resolution-option plugins (MUSH) only warn, add `--orig WxH`.
7. **GUI**: hard-coded colours and `SmoothTransformation` preview (pixel art, HiDPI); a second preview click is dropped; saves tab waits on the UI thread; `hub.open_save_file` loads twice and can prompt twice; a failed edit leaves the bad value in the cell; `tr()` readiness; drag and drop; settings only saved by the Upscale tab.
8. **Packaging**: PySide6 upper bound, `pyproject` description (MV/MZ only), AppImage/PyInstaller, entry-point aliases.
9. **Test gaps**: cancel and close during a run, a failed run, QMessageBox stubs in GUI tests (a dialog hangs them), `wait_for` into conftest, mixed encryption, non-ASCII paths, a real `.rgss3a`, `BrokenProcessPool`, Marshal dict keys `1`/`True`/`1.0` merging.
10. **XP** (`.rxdata`) is unsupported.

## Not verified
MZ battle, shop and event screens and MZ audio; VX (not Ace); real `.rgss3a`; stock RGSS (`stock640`); OCR on decorative lettering.

## Next step
Take item 1 (save map transfer) or item 2 (name pre-pass), each with a regression test.

## Chat
Base name: rpgm-mz-upscaler-27
Run: 2
