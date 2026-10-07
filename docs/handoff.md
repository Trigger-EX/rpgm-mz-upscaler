# Handoff

## Goal
RPGM Hub (Linux, PySide6 + CLI): upscale RPG Maker MV/MZ/VX Ace/VX games to 1080p, edit saves, translate whole Japanese games to English offline (data text by default, OCR image overlay optional).

## Decisions and constraints
- Work only on branch `ccr-3bb5b40d-4u069j`. Import name `rpgm_upscaler`.
- Models run on ctranslate2: NLLB-200 600M (`translate install nllb`, preferred) with Argos ja-en as fallback. Romaji only for names.
- `translate-game GAME -o OUT` writes a copy; `OUT/.translation/report.tsv` and `memory.tsv` (reuse with `--memory`). Names that scripts or plugins compare against are kept.
- OCR = Tesseract `jpn` + OpenCV (`--ocr`). mkxp-z exe must be named `Game`; `"RTP": [path]` in mkxp.json.
- `CLAUDE.md` Autonomy rules and Stop hook `.claude/hooks/keep-going.sh`: end replies with `STATUS: DONE` or `STATUS: NEEDS_INPUT - <question>`.

## Current state
- 140 tests pass, 6 opt-in e2e skipped (full runs go to a Haiku test-runner).
- Pushed: translator, GUI "Translate game" tab, audit fixes (plugin-defined resolution, runner keeps originals, MZ patch, Marshal floats, Ace position, atomic saves).
- Uncommitted before this checkpoint: vectorised RGSS cipher (equivalence-tested, speed gain unmeasured), hooks, CLAUDE.md.
- Test games in git-ignored `samples/`: crysalis (Ace), mzproj and delusion (MZ), chainsaw and omori (MV), elweed (real Japanese MZ 1.3.0, 4352 strings, encrypted images).
- A background `translate-game` run on elweed writes `/home/user/elweed_out`; log `/tmp/elweed_tr.log`.

## Open questions
- Translation quality on real Japanese text is unreviewed. MZ visuals unverified.
- Not done: name pre-pass, unscaled MV window widths, save map transfer, RTP detection, XP.

## Next step
Read `/home/user/elweed_out/.translation/report.tsv` and fix what looks wrong. Then upscale elweed (encrypted MZ images) and boot it. Then take the window-width literals item from the audit list.

## Chat
Base name: rpgm-mz-upscaler-27
Run: 2
