# Handoff

## Goal
RPGM Hub (Linux, PySide6 + CLI): upscale RPG Maker MV/MZ/VX Ace/VX games to 1920x1080, edit saves, offline JA->EN translation of names.

## Decisions and constraints
- User: commit straight to `main`, never create branches. Import name stays `rpgm_upscaler`.
- MV/MZ: patched engine copy, one factor N (multiple of 1/8). VX/Ace: stock engine cannot exceed 640x480, so default is an mkxp-z Hires pack (`Hires/Graphics/**` + `mkxp.json`, keys verified in mkxp-z source); `--mode stock640` only sets 640x480.
- Own byte-exact Ruby Marshal codec (rubymarshal fails round-trips); LZString 1.3.x port (matches MV's lib); translation = overrides -> glossary/romaji -> optional Argos model via ctranslate2+sentencepiece.
- Sandbox: LD_LIBRARY_PATH stub libs (scratchpad/stubs) needed for Qt tests; never `pkill -f` with a pattern in the same command.
- Real-engine checks: corescript clone at /home/user/rpgtkoolmv/corescript, mkxp-z at /home/user/mkxp-z/mkxp-z.

## Current state
All pushed to `main` (last feature commit 9434926). 114 tests pass, 7 skipped (optional e2e/real model); GUI tests pass with stubs. Docs: README.md, docs/plan.md, docs/plan-hub.md.

## Open questions
- MZ untested against a real engine; no real VX/Ace game, `.rgss3a` or Argos model could be tried here.
- XP unsupported. Remote branch `ccr-8563ced6-1lhmz8` is merged but still on GitHub.

## Next step
Have the user try the hub on real games; fix what breaks (MZ first), then consider XP and RTP upscaling.

## Chat
Base name: rpgm-mz-upscaler-27
Run: 1
