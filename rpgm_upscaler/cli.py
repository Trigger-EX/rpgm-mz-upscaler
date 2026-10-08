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
                   scale_windowskin=a.scale_windowskin, bundle_player=not a.no_player, allow_no_player=a.allow_no_player,
                   mkxp_path=a.mkxp_path or "",
                   orig=tuple(int(x) for x in a.orig.lower().split("x")) if a.orig else None)


def _add_opts(p: argparse.ArgumentParser) -> None:
    p.add_argument("game")
    p.add_argument("--target", default="1920x1080")
    p.add_argument("--orig", metavar="WxH", help="resolution the game was made for, when it cannot be detected (e.g. a plugin lets the player pick one)")
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
    p.add_argument("--no-player", action="store_true", help="VX/Ace/XP hires: do not copy the mkxp-z player into the export")
    p.add_argument("--allow-no-player", action="store_true", help="VX/Ace/XP hires: upscale even though mkxp-z is not installed")
    p.add_argument("--mkxp-path", metavar="DIR", help="folder that already holds mkxp-z (default: the downloaded copy, or $RPGM_MKXPZ_DIR)")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="rpgm-upscaler-cli", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a_an = sub.add_parser("analyze")
    a_an.add_argument("game")
    a_pl = sub.add_parser("plan")
    _add_opts(a_pl)
    a_pl.add_argument("--mode", choices=["hires", "stock640"], default="hires", help="VX/Ace only (see README)")
    a_pl.add_argument("--json", action="store_true")
    a_run = sub.add_parser("run")
    _add_opts(a_run)
    a_run.add_argument("-o", "--output", required=True)
    a_run.add_argument("--mode", choices=["hires", "stock640"], default="hires", help="VX/Ace only (see README)")
    a_un = sub.add_parser("unpack", help="extract an encrypted RGSS archive (.rgss3a/.rgss2a/.rgssad)")
    a_un.add_argument("game")
    a_un.add_argument("-o", "--output")
    a_sc = sub.add_parser("scripts", help="list or extract the Ruby scripts of a VX / VX Ace project")
    a_sc.add_argument("game")
    a_sc.add_argument("--extract", metavar="DIR")
    a_det = sub.add_parser("detect", help="identify the RPG Maker engine of a folder or file")
    a_det.add_argument("path")
    a_sv = sub.add_parser("saves", help="inspect and edit save files (MV, MZ, VX, VX Ace)")
    ssub = a_sv.add_subparsers(dest="saves_cmd", required=True)
    s_ls = ssub.add_parser("list")
    s_ls.add_argument("game")
    s_dump = ssub.add_parser("dump")
    s_dump.add_argument("save")
    s_dump.add_argument("--json", action="store_true")
    s_dump.add_argument("--names", action="store_true", help="show database names (switches, variables, items ...)")
    s_dump.add_argument("--translate", action="store_true", help="also translate Japanese names to English (offline)")
    s_set = ssub.add_parser("set", help="edit a save in place (a .bak backup is always made)")
    s_set.add_argument("save")
    s_set.add_argument("--switch", action="append", default=[], metavar="ID=on|off")
    s_set.add_argument("--var", action="append", default=[], metavar="ID=VALUE")
    s_set.add_argument("--gold", type=int)
    s_set.add_argument("--item", action="append", default=[], metavar="[items|weapons|armors:]ID=COUNT")
    s_set.add_argument("--actor", action="append", default=[], metavar="ID:level|exp|hp|mp|name=VALUE")
    s_set.add_argument("--map", type=int)
    s_set.add_argument("--pos", metavar="X,Y")
    a_mk = sub.add_parser("mkxp", help="the mkxp-z player for VX / VX Ace / XP hires exports: status | install")
    a_mk.add_argument("action", choices=["status", "install"])
    a_fx = sub.add_parser("fix-export", help="re-apply the fonts, preloads and mkxp-z player of a hires export that is already upscaled (no re-upscaling)")
    a_fx.add_argument("folder")
    a_fx.add_argument("--source", metavar="ORIGINAL", help="the original game folder: files it has that the export lacks (e.g. Audio/) are copied in")
    a_fx.add_argument("--mkxp-path", metavar="DIR", help="folder that already holds mkxp-z (default: the downloaded copy)")
    a_tr = sub.add_parser("translate", help="offline Japanese -> English: TEXT... | --file F | install | import PATH | status")
    a_tr.add_argument("items", nargs="*")
    a_tr.add_argument("--file", help="translate each line of a UTF-8 text file")
    a_tr.add_argument("--json", action="store_true")
    a_tg = sub.add_parser("translate-game", help="translate a whole game (dialogue, database, system and plugin text) into a new folder; "
                                                   "optionally OCR and overlay Japanese lettering in images")
    a_tg.add_argument("game")
    a_tg.add_argument("-o", "--output", required=True, help="new folder for the translated copy (the original is never touched)")
    a_tg.add_argument("--no-dialogue", action="store_true", help="skip event text (messages, choices, scrolling text)")
    a_tg.add_argument("--no-database", action="store_true", help="skip actors, items, skills, states, enemies, classes ...")
    a_tg.add_argument("--no-system", action="store_true", help="skip game title, terms and map names")
    a_tg.add_argument("--no-plugin-params", action="store_true", help="skip text in plugin parameters (MV/MZ)")
    a_tg.add_argument("--ocr", action="store_true", help="also find Japanese text in images (OpenCV + Tesseract) and overlay English")
    a_tg.add_argument("--ocr-scope", choices=["likely", "all"], default="likely",
                      help="likely: pictures, titles, system (default); all: every image file")
    a_tg.add_argument("--ocr-min-conf", type=float, default=60.0, help="minimum OCR confidence 0-100 (default 60)")
    a_tg.add_argument("--font", help="TTF/OTF font for overlaid English (default: a system sans-serif)")
    a_tg.add_argument("--wrap-chars", type=int, help="characters per message line (default: estimated from the engine)")
    a_tg.add_argument("--memory", metavar="TSV", help="your corrections (japanese<TAB>english, e.g. an edited .translation/memory.tsv) that win over the model")
    a_tg.add_argument("--no-keep-referenced", action="store_true", help="also translate names that scripts or plugins compare against (may break the game)")
    a_tg.add_argument("--link", action="store_true", help="hard-link unchanged files instead of copying them (saves disk space)")
    a_tg.add_argument("--resume", action="store_true", help="continue an earlier run in the same output folder: text is redone (cached), images already handled are kept")
    a_tg.add_argument("--overwrite", action="store_true", help="allow a non-empty output folder")
    a_tg.add_argument("--fast", action="store_true", help="greedy decoding (beam 1): several times faster, slightly rougher wording")
    a_tg.add_argument("--beam", type=int, choices=[1, 2, 3, 4, 5], help="model search width (default 4; --fast is 1)")
    a_tg.add_argument("--workers", type=int, default=0, help="parallel image workers (default: auto)")
    args = ap.parse_args(argv)
    if args.cmd == "fix-export":
        from .rgss import patch
        try:
            for line in patch.refresh_export(args.folder, args.mkxp_path or "", args.source):
                print(line)
        except (ValueError, OSError) as e:
            print("error:", e, file=sys.stderr)
            return 2
        return 0
    if args.cmd == "mkxp":
        from .rgss import mkxp
        if args.action == "install":
            last = [-1]

            def show(done: int, total: int) -> None:
                pct = done * 100 // total if total else done >> 20          # percent, or MB when the size is unknown
                if pct != last[0]:
                    last[0] = pct
                    print(f"\rdownloading {pct}{'%' if total else ' MB'}", end="", file=sys.stderr)
            try:
                where = mkxp.install(show)
            except mkxp.MkxpError as e:
                print("\nerror:", e, file=sys.stderr)
                return 2
            print(f"\ninstalled in {where}")
            return 0
        where = mkxp.locate()
        print(f"installed: {where} {mkxp.version(where)}" if where else "not installed. " + mkxp.HELP)
        return 0 if where else 1
    if args.cmd in ("detect", "saves", "translate", "translate-game", "unpack", "scripts"):
        from .hubcli import run_hub_command
        return run_hub_command(args)
    if args.cmd in ("analyze", "plan", "run"):
        from .detect import detect_engine
        info = detect_engine(args.game)
        if info is not None and not info.is_html5:
            from .hubcli import run_rgss_command
            return run_rgss_command(args, info, _opts(args) if args.cmd != "analyze" else None)
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
