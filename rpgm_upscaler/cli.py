"""Command line interface: analyze | plan | run."""
from __future__ import annotations

import argparse
import json
import sys

from .core import engines, video
from .core.planner import build_plan
from .core.project import ProjectError, load_project
from .core.runner import RunError, Runner
from .core.settings import Options


def _opts(a: argparse.Namespace) -> Options:
    w, h = (int(x) for x in a.target.lower().split("x"))
    res = {}
    for item in a.resampler or []:
        k, _, v = item.partition("=")
        res[k] = v
    return Options(target=(w, h), scale=a.scale, engine=a.engine, engine_path=a.engine_path or "",
                   model=a.model or "", resamplers=res, skip=a.skip or [], movies=not a.no_movies,
                   patch=not a.no_patch, reencrypt=not a.plain_images, ui_fill=a.ui_fill,
                   anchor=a.anchor, workers=a.workers, resume=not a.overwrite,
                   scale_windowskin=a.scale_windowskin)


def _add_opts(p: argparse.ArgumentParser) -> None:
    p.add_argument("game")
    p.add_argument("--target", default="1920x1080")
    p.add_argument("--scale", default="fit", help="fit | number (rounded down to a multiple of 1/8)")
    p.add_argument("--engine", default="lanczos", choices=[*engines.PILLOW_ENGINES, *engines.NCNN_ENGINES])
    p.add_argument("--engine-path")
    p.add_argument("--model")
    p.add_argument("--resampler", action="append", metavar="CATEGORY=MODE")
    p.add_argument("--skip", action="append", metavar="CATEGORY")
    p.add_argument("--no-movies", action="store_true")
    p.add_argument("--no-patch", action="store_true")
    p.add_argument("--plain-images", action="store_true", help="write decrypted PNGs instead of re-encrypting")
    p.add_argument("--scale-windowskin", action="store_true")
    p.add_argument("--ui-fill", action="store_true")
    p.add_argument("--anchor", default="center", choices=["center", "topleft"])
    p.add_argument("--workers", type=int, default=0)
    p.add_argument("--overwrite", action="store_true")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="rpgm-upscaler-cli", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a_an = sub.add_parser("analyze")
    a_an.add_argument("game")
    a_pl = sub.add_parser("plan")
    _add_opts(a_pl)
    a_pl.add_argument("--json", action="store_true")
    a_run = sub.add_parser("run")
    _add_opts(a_run)
    a_run.add_argument("-o", "--output", required=True)
    args = ap.parse_args(argv)
    try:
        project = load_project(args.game)
        if args.cmd == "analyze":
            print(f"engine: {project.engine} {project.version}\nscreen: {project.screen[0]}x{project.screen[1]}\n"
                  f"ui area: {project.ui_area[0]}x{project.ui_area[1]}\ntile size: {project.tile_size}\n"
                  f"encrypted images: {project.has_encrypted_images} (key {'yes' if project.key else 'no'})\n"
                  f"plugins: {len(project.plugins)}\nffmpeg: {video.available()}")
            for w in project.warnings:
                print("warning:", w)
            return 0
        opts = _opts(args)
        plan = build_plan(project, opts)
        if args.cmd == "plan":
            if args.json:
                print(json.dumps({"scale": plan.scale.n, "tile": plan.scale.tile, "ui_area": plan.scale.ui_area,
                                  "summary": plan.summary(), "copies": plan.copies, "warnings": plan.warnings,
                                  "jobs": len(plan.jobs)}))
            else:
                print(f"scale x{plan.scale.n:g}  tile {plan.scale.tile}  UI {plan.scale.ui_area}  offset {plan.scale.offset}")
                for k, v in sorted(plan.summary().items()):
                    print(f"  {k:16s} {v}")
                print(f"  copied as-is     {plan.copies}")
                for w in plan.warnings:
                    print("warning:", w)
            return 0
        runner = Runner(plan, args.output, on_progress=lambda d, t, n: print(f"\r{d}/{t} {n[:60]:60s}", end="", file=sys.stderr),
                        on_log=lambda lvl, msg: lvl in ("error", "warning") and print(f"\n{lvl}: {msg}", file=sys.stderr))
        res = runner.run()
        print(f"\nupscaled {res.ok}, resumed {res.skipped}, failed {len(res.failed)}")
        return 0 if res.success else 1
    except (ProjectError, RunError) as e:
        print("error:", e, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
