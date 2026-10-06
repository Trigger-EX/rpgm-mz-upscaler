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
Work is on branch `ccr-3bb5b40d-4u069j` (the session was pinned to it; earlier instruction was main). 121 tests pass, 6 skipped (optional e2e).
Verified this session with network: MV corescript e2e (13 pass), real Argos ja_en 1.1 model (fixed `find_installed`: the real package unpacks as plain `ja_en`), real mkxp-z built from source runs a generated Ace game; stock 544x416 stays logical, the Hires texture is drawn at the 2.5x window (new `tests/test_e2e_mkxpz.py`).
Sandbox build recipe for mkxp-z: apt libs + SDL_sound v2.0.4 from git (plus `sdl2_sound.pc` symlink), `meson setup build -Dmri_version=3.2 -Dangle=disabled -Ddefault_system=enabled --wrap-mode=nofallback`; github tarballs are 403, so clone subproject sources with git (spirv-headers, theoraplay); `sed` src/meson.build:836 to `_TTF_Font`. Exe must be named `Game`; add `rubyLoadpath` if zlib is missing.
GUI: apt `libegl1 libgl1 libxkbcommon0 libfontconfig1 libdbus-1-3` make the hub start headless.

## Open questions
- MZ untested against a real engine: its core scripts are proprietary and not public (not cloned). Needs an MZ game's `js/` folder from the user.
- Still no real VX/Ace commercial game, `.rgss3a`, or stock RGSS (stock640 mode) run. XP unsupported.
- Remote branch `ccr-8563ced6-1lhmz8` is merged but still on GitHub.

## Next step
Have the user try the hub on real games (MZ first); then consider XP and RTP upscaling.

## Chat
Base name: rpgm-mz-upscaler-27
Run: 1
