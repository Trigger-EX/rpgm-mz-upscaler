"""Plan an upscale of a VX / VX Ace game. Jobs are emitted for the shared runner."""
from __future__ import annotations

from pathlib import PurePosixPath

from PIL import Image

from ..core import scaling
from ..core.planner import Job, Plan
from ..core.settings import Options
from . import categories as cat
from .patch import make_hook
from .project import RgssProject

MODES = ("hires", "stock640")


def build_plan(project: RgssProject, opts: Options, mode: str = "hires") -> Plan:
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    if mode == "stock640" and project.engine == "XP":
        raise ValueError("RPG Maker XP already runs at 640x480; use the hires mode")
    n = scaling.parse_scale(opts.scale, project.screen, tuple(opts.target))
    sp = scaling.ScalePlan(n=n, orig=project.screen, ui_orig=project.screen, target=tuple(opts.target), tile_orig=project.tile_size,
                           ui_fill=opts.ui_fill, anchor=opts.anchor)
    sp.warnings = sp.check()
    plan = Plan(project, opts, sp, warnings=list(project.warnings) + list(sp.warnings), mode=mode)
    plan.patch_hook = make_hook(mode)
    base = project.base
    if mode == "stock640":
        plan.warnings.append("stock640: assets are not upscaled (stock RGSS cannot draw them); only the screen is set to 640x480.")
        plan.copies = sum(1 for f in base.rglob("*") if f.is_file())
        return plan
    for f in sorted(base.rglob("*")):
        if not f.is_file():
            continue
        rel = PurePosixPath(f.relative_to(base).as_posix())
        c = cat.category(rel)
        if c is None or c in opts.skip:
            plan.copies += 1
            continue
        try:
            with Image.open(f) as im:
                w, h = im.size
        except Exception:  # noqa: BLE001
            plan.warnings.append(f"{rel}: unreadable image; copied unchanged")
            plan.copies += 1
            continue
        stem = f.stem
        if cat.is_windowskin(c, stem) and not opts.scale_windowskin:
            plan.skipped_windowskins.append(rel.as_posix())
            plan.copies += 1
            continue
        if c not in (cat.XP_FOLDERS if project.engine == "XP" else cat.FOLDERS):
            msg = f"Graphics/{rel.parts[1]}: unknown folder scaled by x{n:g}"
            if msg not in plan.warnings:
                plan.warnings.append(msg)
        cell = cat.cell_size(c, stem, w, h, project.tile_kinds, project.engine)
        out_w = w // cell[0] * scaling.scaled(cell[0], n) if cell else scaling.scaled(w, n)
        out_h = h // cell[1] * scaling.scaled(cell[1], n) if cell else scaling.scaled(h, n)
        if (out_w, out_h) != (scaling.scaled(w, n), scaling.scaled(h, n)):
            plan.warnings.append(f"{rel}: x{n:g} gives {out_w}x{out_h} instead of {scaling.scaled(w, n)}x{scaling.scaled(h, n)} "
                                 "(cell size is not a multiple of 8); mkxp-z may show a seam")
        if max(out_w, out_h) > 16384:
            plan.warnings.append(f"{rel}: scaled size {out_w}x{out_h} exceeds 16384px; copied unchanged")
            plan.copies += 1
            continue
        dst = PurePosixPath("Hires") / rel.with_suffix(".png")
        plan.jobs.append(Job("image", rel.as_posix(), dst.as_posix(), c, "exact", cell=cell,
                             resampler=opts.resamplers.get(c, "default"), keep_source=True, cost=out_w * out_h))
    return plan
