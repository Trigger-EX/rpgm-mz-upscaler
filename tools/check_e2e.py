#!/usr/bin/env python3
"""Assert the upscaled game behaves like the original. usage: check_e2e.py <workdir> [WxH]"""
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image


def orange_bbox(path):
    a = np.asarray(Image.open(path).convert("RGB")).astype(int)
    m = (abs(a[..., 0] - 255) < 3) & (abs(a[..., 1] - 128) < 3) & (a[..., 2] < 3)
    ys, xs = np.where(m)
    return xs.min(), ys.min(), xs.max(), ys.max()


def main() -> int:
    work = Path(sys.argv[1])
    tw, th = (int(v) for v in (sys.argv[2] if len(sys.argv) > 2 else "1920x1080").split("x"))
    orig = json.loads((work / "shots_orig/report.json").read_text())
    up = json.loads((work / "shots_up/report.json").read_text())
    fails = []

    def check(cond, msg):
        print(("ok   " if cond else "FAIL ") + msg)
        if not cond:
            fails.append(msg)

    for name, rep in (("original", orig), ("upscaled", up)):
        bad = [k for k, v in rep["steps"].items() if isinstance(v, str)]
        check(not bad, f"{name}: every scene reached ({bad or 'none timed out'})")
        check(not [l for l in rep["logs"] if l.startswith("pageerror")], f"{name}: no uncaught JS errors")
    check(not [l for l in up["logs"] if "GL_INVALID" in l or "UpscalerPatch] aborted" in l], "upscaled: no WebGL errors / patch abort")
    check(any("[UpscalerPatch] loaded" in l for l in up["logs"]), "upscaled: patch plugin loaded")
    new_errors = [l for l in up["logs"] if l.startswith(("error", "warning")) and l.split("  (x")[0] not in
                  {o.split("  (x")[0] for o in orig["logs"]}]
    check(not new_errors, f"upscaled: no console errors/warnings the original lacks {new_errors[:3]}")

    if any(isinstance(up["steps"].get(k), str) or k not in up["steps"] for k in ("map", "battle")):
        print("\nRESULT: FAIL (scenes not reached)")
        return 1
    g = up["steps"]["map"]["graphics"]
    check(g[0] == tw and g[1] == th, f"screen is {g[0]}x{g[1]}")
    n = up["steps"]["map"]["tile"] / 48
    check(abs(n * 48 - up["steps"]["map"]["tile"]) < 1e-9 and n > 1, f"tile size {up['steps']['map']['tile']} (x{n:g})")

    ox, oy = (tw - 816 * n) / 2, (th - 624 * n) / 2
    bo = orange_bbox(work / "shots_orig/4_picture.png")
    bu = orange_bbox(work / "shots_up/4_picture.png")
    ex, ey = bo[0] * n + ox, bo[1] * n + oy
    check(abs(bu[0] - ex) <= 8 and abs(bu[1] - ey) <= 8, f"picture at ({bu[0]},{bu[1]}) expected ~({ex:.0f},{ey:.0f})")
    ew, eh = (bo[2] - bo[0]) * n, (bo[3] - bo[1]) * n
    check(abs((bu[2] - bu[0]) - ew) <= 8 and abs((bu[3] - bu[1]) - eh) <= 8, "picture scaled by the world factor")
    def yellow(path):
        a = np.asarray(Image.open(path).convert("RGB")).astype(int)
        ys, xs = np.where((a[..., 0] > 230) & (a[..., 1] > 230) & (a[..., 2] < 40))
        return (xs.min(), ys.min(), xs.max(), ys.max()) if len(xs) else None

    yo, yu = yellow(work / "shots_orig/4b_animation.png"), yellow(work / "shots_up/4b_animation.png")
    if yo and yu:
        check(abs((yu[2] - yu[0]) - (yo[2] - yo[0]) * n) <= 6, f"animation cell scaled by the world factor ({yu[2]-yu[0]} vs {(yo[2]-yo[0])*n:.0f})")
        cx, cy = (yu[0] + yu[2]) / 2, (yu[1] + yu[3]) / 2
        ecx, ecy = (yo[0] + yo[2]) / 2 * n + ox, (yo[1] + yo[3]) / 2 * n + oy
        check(abs(cx - ecx) <= 8 and abs(cy - ecy) <= 8, f"animation centred at ({cx:.0f},{cy:.0f}) expected ~({ecx:.0f},{ecy:.0f})")
    else:
        check(False, "animation visible in both runs")
    for f in ("1_title", "4b_animation", "4c_balloon", "5_menu", "6_battle"):
        check((work / f"shots_up/{f}.png").exists(), f"screenshot {f} exists")
    print("\nRESULT:", "FAIL" if fails else "PASS")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
