# Handoff

## Goal
RPGM Hub (Linux, PySide6 + CLI): upscale RPG Maker MV/MZ/VX Ace/VX games to 1920x1080, edit saves, translate a whole Japanese game to English offline (data text by default, image text by OCR on request).

## Decisions and constraints
- Work is on branch `ccr-3bb5b40d-4u069j` (session-pinned; an older note said commit to main). Import name stays `rpgm_upscaler`.
- MV/MZ: patched engine copy (injected `UpscalerPatch` plugin), one factor N (multiple of 1/8). VX/Ace: mkxp-z Hires pack (`Hires/Graphics/**` + `mkxp.json`); the exe must be named `Game`; `"RTP": [path]` for RTP games.
- Own byte-exact Ruby Marshal codec; LZString port; translation = overrides -> glossary (romaji only for names) -> neural model. Models run on ctranslate2+sentencepiece: NLLB-200 600M int8 (preferred, `translate install nllb`, CC-BY-NC) and Argos ja->en 1.1 (fallback). Argos alone returns unknown-token debris for short colloquial lines.
- Translation never edits the source game: `translate-game GAME -o OUT` writes a copy; `.translation/report.tsv` lists every string, `.translation/memory.tsv` is an editable translation memory (`--memory`). Names that scripts/plugins compare against are kept ("kept: ..." rows). Scripts, plugin commands, notes and file names are never touched.
- OCR (`--ocr`): Tesseract (`jpn`, `jpn_vert`) + OpenCV: detect, erase (flat fill or inpaint), overlay English. `apt install tesseract-ocr tesseract-ocr-jpn tesseract-ocr-jpn-vert`.
- Sandbox notes: `apt install libegl1 libgl1 libxkbcommon0 libfontconfig1 libdbus-1-3` for the GUI tests; GitHub archive tarballs are 403 (clone with git instead); huggingface.co, argos-net.com and rpgmakerweb.tkool.jp are reachable. mkxp-z build recipe and the headless browser harness are described in the session history / README; the harness was a throwaway script (`boot.js`).

## Current state
All pushed to the branch. 140+ tests pass (6 opt-in e2e skipped). Verified against real things this session: MV corescript e2e, real mkxp-z + Crysalis (Ace) + official RTP, real MZ 1.9/1.10 games boot patched (placeholder assets), real Argos and NLLB models, 131 real Ace data files round-trip byte-exactly.
Fixed this session from real data/audits: MZ 1.9+ getter abort in the patch, MZ timer bitmap, Marshal floats with Ruby 1.9 mantissa bytes, VX string write encoding, dialogue-only files not written, kana running text romanised, failed upscale job dropping the asset, Ace player position (int @real_x), atomic save writes, case-insensitive img folders, cancel before the plan exists, translator thread-safety.

## Open questions / next steps (from the four read-only audits, ranked)
1. Plugin-defined resolutions (Community_Basic, YEP, MUSH resolution options) are ignored when computing the original size; add plugin-parameter detection + `--orig WxH`.
2. Only 5 MV `windowWidth`s are scaled; generically wrap `Window_*/Scene_*` methods returning int literals (MV: Command 240, Options 400, ShopBuy 456...; MZ: Options 400, Name 600...).
3. Saves: a map change only rewrites the map id (set `_transferring`/`_newMapId` instead); archives are read fully into RAM (mmap + numpy keystream); `analyze/plan/scripts` extract whole archives; RTP auto-detection; XP unsupported.
4. GUI: closing while workers run (wait for all, one job registry), model load on UI thread at startup (partly fixed), HiDPI/theme, `tr()` readiness; run.sh never reinstalls changed requirements; libEGL error text.
5. Translation: name/glossary pre-pass for consistency, per-speaker handling, update-existing-output mode so `images.json` resume works, 357/note-tag/Ace Scripts vocab text, MZ/MV text in `js/` literals, quality checks on model output (repetition loops).
6. ncnn upscaling: batch mode and smallest sufficient model scale; worker count by image size.
7. MZ and any real Japanese game have not been run end to end with visible output (only booted with placeholders); President Chainsaw's resolution plugin untested.

## Chat
Base name: rpgm-mz-upscaler-27
Run: 1
