"""Turn a Project + Options into a Plan (pure data, no writes)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from PIL import Image

from . import categories as cat_mod
from . import crypto, scaling, video
from .project import Project
from .settings import Options

MAX_SIDE_WARN, MAX_SIDE_REFUSE = 8192, 16384


@dataclass
class Job:
    kind: str                      # image | video
    src: str                       # relative to project.base (posix)
    dst: str
    category: str
    policy: str = "exact"
    cell: tuple[int, int] | None = None
    target: tuple[int, int] | None = None   # None = whole-image uses factor
    resampler: str = "default"
    encrypted: bool = False
    out_encrypted: bool = False
    passthrough: bool = False      # decrypt only, no resize (plain-image mode)
    cost: int = 1                  # pixels, for ETA

    @property
    def name(self) -> str:
        return self.src


@dataclass
class Plan:
    project: Project
    options: Options
    scale: scaling.ScalePlan
    jobs: list[Job] = field(default_factory=list)
    copies: int = 0
    warnings: list[str] = field(default_factory=list)
    skipped_windowskins: list[str] = field(default_factory=list)

    def summary(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for j in self.jobs:
            out[j.category] = out.get(j.category, 0) + 1
        return out


def _image_dims(path: Path, encrypted: bool, key: bytes | None) -> tuple[int, int] | None:
    try:
        if encrypted:
            if key is None:
                return None
            data = crypto.decrypt(path.read_bytes(), key)
            import io
            return Image.open(io.BytesIO(data)).size
        with Image.open(path) as im:
            return im.size
    except Exception:  # noqa: BLE001
        return None


def make_scale_plan(project: Project, opts: Options) -> scaling.ScalePlan:
    n = scaling.parse_scale(opts.scale, project.screen, tuple(opts.target))
    sp = scaling.ScalePlan(n=n, orig=project.screen, ui_orig=project.ui_area, target=tuple(opts.target),
                           tile_orig=project.tile_size, ui_fill=opts.ui_fill, anchor=opts.anchor)
    sp.warnings = sp.check()
    return sp


def build_plan(project: Project, opts: Options) -> Plan:
    sp = make_scale_plan(project, opts)
    plan = Plan(project, opts, sp, warnings=list(project.warnings) + list(sp.warnings))
    tile_kinds = cat_mod.tileset_kinds(project.tileset_names)
    base = project.base
    key = project.key

    def keep(relpos: PurePosixPath, enc: bool, cname: str) -> None:
        """Leave an image unscaled; in plain-image mode encrypted files must still be decrypted."""
        if enc and key is not None and not opts.reencrypt:
            plan.jobs.append(Job("image", relpos.as_posix(), relpos.with_suffix(".png").as_posix(), cname,
                                 "copy", encrypted=True, passthrough=True))
        else:
            plan.copies += 1

    for f in sorted(base.rglob("*")):
        if not f.is_file():
            continue
        rel = f.relative_to(base)
        relpos = PurePosixPath(rel.as_posix())
        if not project.web:
            web_rel = relpos
        elif rel.parts[0] == project.web and len(rel.parts) > 1:
            web_rel = PurePosixPath(*rel.parts[1:])
        else:
            plan.copies += 1
            continue
        ext = f.suffix.lower()
        if ext in video.VIDEO_EXTS and web_rel.parts[0] == "movies":
            if opts.movies and video.available():
                plan.jobs.append(Job("video", relpos.as_posix(), relpos.as_posix(), "movies", "exact"))
            else:
                plan.copies += 1
            continue
        c = cat_mod.category_for(web_rel)
        enc = ext in crypto.ENCRYPTED_IMAGE_EXTS
        if c is None or not (ext == ".png" or enc):
            plan.copies += 1
            continue
        if c.name in opts.skip or c.policy == cat_mod.COPY:
            keep(relpos, enc, c.name)
            continue
        if enc and key is None:
            plan.warnings.append(f"{relpos}: encrypted but no key; copied unchanged")
            plan.copies += 1
            continue
        dims = _image_dims(f, enc, key)
        if dims is None:
            plan.warnings.append(f"{relpos}: unreadable image; copied unchanged")
            plan.copies += 1
            continue
        w, h = dims
        stem = f.stem
        if cat_mod.is_windowskin(c.name, stem, w, h) and not opts.scale_windowskin:
            plan.skipped_windowskins.append(relpos.as_posix())
            keep(relpos, enc, c.name)
            continue
        target = None
        cell = None
        if c.policy == cat_mod.COVER:
            target = scaling.cover_size(w, h, sp.n, sp.target)
        else:
            cell = cat_mod.cell_size(c.name, stem, w, h, tile_kinds)
        out_w = target[0] if target else (w // cell[0] * scaling.scaled(cell[0], sp.n) if cell else scaling.scaled(w, sp.n))
        out_h = target[1] if target else (h // cell[1] * scaling.scaled(cell[1], sp.n) if cell else scaling.scaled(h, sp.n))
        if max(out_w, out_h) > MAX_SIDE_REFUSE:
            plan.warnings.append(f"{relpos}: scaled size {out_w}x{out_h} exceeds {MAX_SIDE_REFUSE}px; copied unchanged")
            keep(relpos, enc, c.name)
            continue
        if max(out_w, out_h) > MAX_SIDE_WARN:
            plan.warnings.append(f"{relpos}: scaled size {out_w}x{out_h} may exceed the GPU texture limit")
        out_enc = enc and opts.reencrypt
        dst = relpos
        if enc and not out_enc:
            dst = relpos.with_suffix(".png")
        plan.jobs.append(Job("image", relpos.as_posix(), dst.as_posix(), c.name, c.policy, cell=cell, target=target,
                             resampler=opts.resamplers.get(c.name, "default"), encrypted=enc,
                             out_encrypted=out_enc, cost=out_w * out_h))
        if not c.known:
            msg = f"img/{c.name}: unknown (plugin) folder scaled by x{sp.n:g}; layout may need manual fixes"
            if msg not in plan.warnings:
                plan.warnings.append(msg)
    if project.has_encrypted_audio:
        plan.warnings.append("Encrypted audio is copied unchanged.")
    return plan
