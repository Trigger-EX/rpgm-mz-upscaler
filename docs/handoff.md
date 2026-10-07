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
- Full suite passes (run it through a Haiku agent); MV real-engine e2e 13/13 (one screenshot check is load-flaky).
- Real Japanese MZ game (`samples/elweed/game`, git-ignored) translated (4352 strings) and upscaled; title, menu and options compared with the original in Chromium. Findings fixed: plugin control codes with text arguments, `%1`/`\N[n]` placeholders, repeated words, undecodable file names, MZ local size literals.
- Upscale patch scales every bare-number `*Width/*Height/*Spacing/*Padding` method and `const ww = N` locals in rect methods.
- Everything is pushed except the last OCR layout/filter commit if the next session finds it uncommitted.

## Open questions
- NLLB is slow (about 1 h for 4300 strings on 4 cores) and sometimes invents words; a name pre-pass and per-speaker context are untried.
- Not verified: MZ battle, shop and event screens; VX (not Ace); real `.rgss3a`; stock RGSS; OCR on art with decorative lettering.
- Not done: save map transfer, RTP detection, XP, ncnn batching.

## Next step
Commit and push any pending OCR changes, then take the ranked audit items in the README and this file (name pre-pass, map transfer, ncnn batching).

## Chat
Base name: rpgm-mz-upscaler-27
Run: 2
