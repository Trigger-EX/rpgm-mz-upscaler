#!/usr/bin/env bash
# End-to-end check: build a sample MV game from the real engine, upscale it, boot both in headless Chromium.
# usage: tools/run_e2e.sh <corescript-checkout> <workdir> [font.ttf] [extra upscaler args...]
set -eu
CORE=$1; WORK=$2; FONT=${3:-}; shift 3 || shift $#
HERE=$(cd "$(dirname "$0")" && pwd); ROOT=$(dirname "$HERE")
PY=${PY:-$ROOT/.venv/bin/python}
export NODE_PATH=${NODE_PATH:-/opt/node22/lib/node_modules}
export PLAYWRIGHT_MODULE=${PLAYWRIGHT_MODULE:-/opt/node22/lib/node_modules/playwright}
rm -rf "$WORK"; mkdir -p "$WORK"
$PY "$HERE/make_sample_game.py" --corescript "$CORE" --out "$WORK/sample" ${FONT:+--font "$FONT"} ${ENCRYPT:+--encrypt}
$PY -m rpgm_upscaler.cli run "$WORK/sample" -o "$WORK/out" --no-movies "$@" 2>&1 | tr '\r' '\n' | tail -1
serve() { (cd "$1" && exec python3 -m http.server "$2" >/dev/null 2>&1) & echo $!; }
P1=$(serve "$WORK/sample" 8801); P2=$(serve "$WORK/out" 8802)
trap 'kill $P1 $P2 2>/dev/null' EXIT
sleep 1
node "$HERE/e2e_browser.js" http://localhost:8801/index.html "$WORK/shots_orig" 816 624 > "$WORK/report_orig.txt" 2>&1 || true
node "$HERE/e2e_browser.js" http://localhost:8802/index.html "$WORK/shots_up" 1920 1080 > "$WORK/report_up.txt" 2>&1 || true
for f in orig up; do echo "== $f"; grep -E "TIMEOUT|HARNESS|pageerror|GL_INVALID|UpscalerPatch|\"error|http 4" "$WORK/report_$f.txt" | head; done
echo; "$PY" "$HERE/check_e2e.py" "$WORK"
