"""Translate a whole RPG Maker game into English, into a separate copy: dialogue, database, system text and plugin text by
default; Japanese lettering inside images on request (OCR + overlay). MV, MZ, VX Ace and VX games are supported."""
from __future__ import annotations

import csv
import json
import os
import re
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from PIL import Image

from ..core import crypto, imageops
from ..core.patcher import format_plugins_js, parse_plugins_js
from ..core.project import ProjectError, load_project, read_json
from ..detect import EngineInfo, ci_child, detect_engine
from ..rgss import marshal as m
from ..rgss.archive import open_archive
from . import gametext as gt
from .detect import is_japanese
from .pluginparams import collect_plugin_params

Progress = Callable[[str, int, int], None]


class GameTranslateError(Exception):
    pass


@dataclass
class Options:
    dialogue: bool = True
    database: bool = True
    system: bool = True
    plugin_params: bool = True
    ocr: bool = False
    ocr_scope: str = "likely"           # likely | all
    ocr_min_conf: float = 55.0
    font: str | None = None
    wrap_chars: int | None = None
    keep_referenced: bool = True        # leave names that scripts/plugins compare against untouched
    memory: str | None = None           # TSV (japanese<TAB>english) of corrections that win over the model
    copy_mode: str = "copy"             # copy | link
    overwrite: bool = False
    workers: int = 0                    # image workers; 0 = auto


@dataclass
class Result:
    engine: str = ""
    out: Path | None = None
    files_changed: int = 0
    strings: int = 0
    translated: int = 0
    images_scanned: int = 0
    images_changed: int = 0
    regions: int = 0
    cancelled: bool = False
    skipped: int = 0
    warnings: list[str] = field(default_factory=list)
    rows: list[dict] = field(default_factory=list)

    @property
    def untranslated(self) -> int:
        return self.strings - self.translated


# ---------------------------------------------------------------------------------------------------------------------
# helpers


def _validate_out(src: Path, out: Path, overwrite: bool) -> Path:
    src, out = src.resolve(), out.expanduser().resolve()
    if out == src or src in out.parents or out in src.parents:
        raise GameTranslateError("The output folder must be separate from the game (not equal to, inside or containing it).")
    if out.exists() and any(out.iterdir()) and not overwrite:
        raise GameTranslateError(f"{out} is not empty. Choose another folder or allow overwriting.")
    return out


def _mirror(src: Path, out: Path, mode: str, skip: Callable[[Path], bool], progress: Progress | None, cancel) -> None:
    files = [p for p in src.rglob("*") if p.is_file() and not skip(p)]
    for i, p in enumerate(files):
        if cancel is not None and cancel.is_set():
            return
        dst = out / p.relative_to(src)
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists():
            dst.unlink()
        if mode == "link":
            try:
                os.link(p, dst)
            except OSError:
                shutil.copy2(p, dst)
        else:
            shutil.copy2(p, dst)
        if progress and i % 50 == 0:
            progress("copy", i, len(files))
    if progress:
        progress("copy", len(files), len(files))


def _atomic_write(path: Path, data: bytes) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)          # new inode: never writes through a hard link into the original game


def _wrap_for_game(info: EngineInfo, proj, opts: Options) -> gt.Wrap:
    if opts.wrap_chars:
        n = opts.wrap_chars
        return gt.Wrap(chars=n, face_chars=max(16, int(n * 0.78)), wide_chars=int(n * 1.2))
    if info.is_html5:
        font = 28 if info.engine == "MV" else (proj.font_size or 26)
        return gt.wrap_for(info.engine, proj.ui_area[0] if info.engine == "MZ" else proj.screen[0], font)
    return gt.wrap_for(info.engine, 544, 24 if info.engine == "ACE" else 20)


def _data_files(info: EngineInfo, out: Path) -> list[Path]:
    d = (out / info.web if info.web else out) / "data" if info.is_html5 else (ci_child(out, "Data") or out / "Data")
    if not d.is_dir():
        return []
    exts = {".json"} if info.is_html5 else {".rvdata2", ".rvdata"}
    return sorted(p for p in d.iterdir() if p.is_file() and p.suffix.lower() in exts)


_JA_LITERAL = re.compile(r"""(["'`])((?:(?!\1)[^\n\\]|\\.)*?[\u3040-\u30ff\u3400-\u9fff][^\n]*?)\1""")
_ID_KINDS = {"name", "term", "title", "map"}


def _script_literals(info: EngineInfo, out: Path, loaded: list[tuple[Path, object]]) -> set[str]:
    """Japanese string literals that scripts, plugin code and script commands mention. A name that code compares against
    (`$gameParty.members()[0].name() === "アリス"`) breaks if it is translated, so such names are kept as they are."""
    texts: list[str] = []
    root = out / info.web if (info.is_html5 and info.web) else out
    if info.is_html5:
        for p in (root / "js").rglob("*.js"):
            if p.name == "plugins.js" or p.name.startswith(("rpg_", "rmmz_")) or "libs" in p.parts:
                continue
            try:
                texts.append(p.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                pass

        def cmds(o):
            if isinstance(o, dict):
                if "code" in o and "parameters" in o:
                    yield o["code"], o["parameters"]
                for v in o.values():
                    yield from cmds(v)
            elif isinstance(o, list):
                for v in o:
                    yield from cmds(v)
        for _f, data in loaded:
            for code, ps in cmds(data):
                if code in (355, 655, 356, 357) or (code == 111 and ps and ps[0] == 12):
                    texts += [x for x in ps if isinstance(x, str)] + [json.dumps(x, ensure_ascii=False) for x in ps if isinstance(x, dict)]
    else:
        from ..rgss import scripts as sc
        sf = next((f for f, _ in loaded if f.name.lower().startswith("scripts.")), None)
        sp = ci_child(out / "Data", "Scripts.rvdata2") or ci_child(out / "Data", "Scripts.rvdata") or sf
        if sp is not None and sp.is_file():
            try:
                arr = sc.load(sp.read_bytes())
                texts += [sc.source(e) for e in arr]
            except Exception:  # noqa: BLE001
                pass

        def rcmds(o, seen=None):
            seen = seen if seen is not None else set()
            if id(o) in seen:
                return
            seen.add(id(o))
            if isinstance(o, m.RObject):
                if o.cls == "RPG::EventCommand" and o.ivars.get("@code") in (355, 655, 111):
                    for x in o.ivars.get("@parameters") or []:
                        if isinstance(x, m.RString):
                            texts.append(x.text)
                for v in o.ivars.values():
                    yield from rcmds(v, seen)
            elif isinstance(o, (list, tuple)):
                for v in o:
                    yield from rcmds(v, seen)
            elif isinstance(o, dict):
                for v in o.values():
                    yield from rcmds(v, seen)
        for _f, data in loaded:
            list(rcmds(data))
    found: set[str] = set()
    for t in texts:
        for mt in _JA_LITERAL.finditer(t):
            found.add(mt.group(2))
    return found


def _load_memory(path: str) -> dict[str, str]:
    tm: dict[str, str] = {}
    with open(path, encoding="utf-8", newline="") as f:
        for row in csv.reader(f, delimiter="\t"):
            if len(row) >= 2 and row[0] and row[0] != "japanese" and row[1]:
                tm[row[0].replace("\\n", "\n")] = row[1].replace("\\n", "\n")
    return tm


# ---------------------------------------------------------------------------------------------------------------------
# main entry


def translate_game(src: str | Path, out: str | Path, translator, opts: Options | None = None, progress: Progress | None = None,
                   cancel: threading.Event | None = None, ocr_backend=None) -> Result:
    opts = opts or Options()
    src = Path(src).expanduser().resolve()
    info = detect_engine(src)
    if info is None:
        raise GameTranslateError(f"{src} is not an RPG Maker game folder I recognise.")
    if info.engine == "XP":
        raise GameTranslateError("RPG Maker XP games are not supported yet.")
    out = _validate_out(info.root, Path(out), opts.overwrite)
    res = Result(engine=info.engine, out=out)
    out.mkdir(parents=True, exist_ok=True)

    proj = None
    if info.is_html5:
        try:
            proj = load_project(info.root)
        except ProjectError as e:
            raise GameTranslateError(str(e)) from e
    # 1. mirror the game (an encrypted RGSS archive is unpacked instead of copied, so the translated loose files take priority)
    archive = info.archive
    _mirror(info.root, out, opts.copy_mode, lambda p: archive is not None and p == archive, progress, cancel)
    if cancel is not None and cancel.is_set():
        res.cancelled = True
        return res
    if archive is not None:
        open_archive(archive).extract_all(out, lambda d, t, n: progress and progress("copy", d, t), cancel)

    # 2. collect every visible string
    wrap = _wrap_for_game(info, proj, opts)
    col = gt.Collected()
    loaded: list[tuple[Path, object]] = []
    spans: dict[Path, tuple[int, int]] = {}      # which units each data file contributed
    files = _data_files(info, out)
    for i, f in enumerate(files):
        if progress:
            progress("scan", i, len(files))
        first = len(col.units)
        try:
            if info.is_html5:
                data = read_json(f)
                gt.collect_json(f.name, data, wrap, col, info.engine, do_events=opts.dialogue, do_db=opts.database, do_system=opts.system)
            else:
                data = m.loads(f.read_bytes())
                gt.collect_marshal(f.name, data, wrap, col, info.engine, do_events=opts.dialogue, do_db=opts.database, do_system=opts.system)
        except (ValueError, m.MarshalError) as e:
            res.warnings.append(f"{f.name}: skipped ({e})")
            continue
        loaded.append((f, data))
        spans[f] = (first, len(col.units))
    plugins_state = None
    if info.is_html5 and opts.plugin_params:
        pj = (out / info.web / "js" / "plugins.js") if info.web else out / "js" / "plugins.js"
        if pj.is_file():
            try:
                prefix, entries = parse_plugins_js(pj.read_text(encoding="utf-8"))
                collect_plugin_params(entries, out / info.web if info.web else out, col)
                plugins_state = (pj, prefix, entries)
            except (ValueError, OSError) as e:
                res.warnings.append(f"plugins.js: skipped ({e})")

    # 3. translate
    if opts.memory:
        translator.overrides.update(_load_memory(opts.memory))
    literals = _script_literals(info, out, loaded) if opts.keep_referenced else set()
    for u in col.units:
        if u.kind in _ID_KINDS and u.ja in literals:
            u.skip_reason = "kept: a script or plugin refers to this exact text"
            res.skipped += 1
            res.rows.append({"kind": u.kind, "where": u.where, "ja": u.ja, "en": "", "source": u.skip_reason})
    col.units = [u for u in col.units if not u.skip_reason]
    res.strings = len(col.units)
    # names may be transliterated (romaji) as a fallback; running text and UI words must go to the model instead
    groups = [([u for u in col.units if u.kind == "name"], True), ([u for u in col.units if u.kind != "name"], False)]
    total, base = res.strings, 0
    for units, romaji in groups:
        if not units:
            continue
        results = translator.translate_many([u.ja for u in units], cancel=cancel, romaji=romaji,
                                            progress=(lambda d, t, b=base: progress("translate", b + d, total)) if progress else None)
        if cancel is not None and cancel.is_set():
            res.cancelled = True
            return res
        for u, r in zip(units, results):
            if r.translated:
                u.en = r.text
                res.translated += 1
            res.rows.append({"kind": u.kind, "where": u.where, "ja": u.ja, "en": r.text if r.translated else "", "source": r.source})
        base += len(units)
    st = translator.status() if hasattr(translator, "status") else {}
    if st.get("model") != "ready" and res.untranslated:
        res.warnings.append("No translation model is installed, so only text the built-in dictionary covers was translated. "
                            "Install the model (Translation tab or `translate install`) and run again.")

    # 4. write back what changed
    col.commit()
    for f, data in loaded:
        a, b = spans[f]
        if not any(u.changed for u in col.units[a:b]):
            continue
        if info.is_html5:
            _atomic_write(f, json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        else:
            _atomic_write(f, m.dumps(data))
        res.files_changed += 1
    if plugins_state is not None and any(u.changed and u.kind == "plugin" for u in col.units):
        pj, prefix, entries = plugins_state
        _atomic_write(pj, format_plugins_js(prefix, entries).encode("utf-8"))
        res.files_changed += 1
    if info.is_html5:                                   # MV and MZ both keep a locale in System.json
        _set_locale(out / info.web / "data" / "System.json" if info.web else out / "data" / "System.json")

    # 5. images
    if opts.ocr:
        _translate_images(info, proj, out, translator, opts, res, progress, cancel, ocr_backend)

    _write_report(out, res)
    return res


def _set_locale(system_json: Path) -> None:
    try:
        d = read_json(system_json)
        if d.get("locale") and d["locale"] != "en_US":
            d["locale"] = "en_US"
            _atomic_write(system_json, json.dumps(d, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    except (OSError, ValueError):
        pass


def _write_report(out: Path, res: Result) -> None:
    d = out / ".translation"
    d.mkdir(exist_ok=True)
    with open(d / "report.tsv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter="\t", lineterminator="\n")
        w.writerow(["kind", "where", "japanese", "english", "source"])
        for r in res.rows:
            w.writerow([r["kind"], r["where"], r["ja"].replace("\n", "\\n"), r["en"].replace("\n", "\\n"), r["source"]])
    seen: dict[str, str] = {}
    for r in res.rows:
        if r["en"] and r["kind"] != "image":
            seen.setdefault(r["ja"], r["en"])
    with open(d / "memory.tsv", "w", encoding="utf-8", newline="") as f:     # edit the English column, then rerun with --memory
        w = csv.writer(f, delimiter="\t", lineterminator="\n")
        w.writerow(["japanese", "english"])
        for ja, en in seen.items():
            w.writerow([ja.replace("\n", "\\n"), en.replace("\n", "\\n")])
    (d / "summary.json").write_text(json.dumps({"engine": res.engine, "strings": res.strings, "translated": res.translated,
                                                "files_changed": res.files_changed, "images_scanned": res.images_scanned,
                                                "images_changed": res.images_changed, "warnings": res.warnings}, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------------------------------------------------
# images


def _save_like(img: Image.Image, path: Path, key: bytes | None) -> None:
    ext = path.suffix.lower()
    if ext in crypto.ENCRYPTED_IMAGE_EXTS:
        imageops.save_image(img, path, key, encrypt=True)
    elif ext in (".jpg", ".jpeg"):
        tmp = path.with_name(path.name + ".tmp")
        img.convert("RGB").save(tmp, "JPEG", quality=95)
        tmp.replace(path)
    elif ext == ".bmp":
        tmp = path.with_name(path.name + ".tmp")
        img.convert("RGB").save(tmp, "BMP")
        tmp.replace(path)
    else:
        imageops.save_image(img, path)


def _translate_images(info, proj, out: Path, translator, opts: Options, res: Result, progress, cancel, backend) -> None:
    from . import ocr
    if backend is None:
        try:
            backend = ocr.Tesseract(min_conf=opts.ocr_min_conf)
        except ocr.OcrError as e:
            res.warnings.append(f"Image translation skipped: {e}")
            return
    font = ocr.find_font(opts.font)
    root = out / info.web if (info.is_html5 and info.web) else out
    key = proj.key if proj is not None else None
    images = ocr.iter_images(root, "html5" if info.is_html5 else "rgss", opts.ocr_scope)
    man_path = out / ".translation" / "images.json"
    man_path.parent.mkdir(exist_ok=True)
    try:
        done = json.loads(man_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        done = {}
    lock = threading.Lock()
    counter = {"n": 0}

    def sig(p: Path) -> list[int]:
        s = p.stat()
        return [s.st_size, s.st_mtime_ns]

    def work(p: Path) -> None:
        if cancel is not None and cancel.is_set():
            return
        rel = p.relative_to(out).as_posix()
        if done.get(rel) == sig(p):
            return
        try:
            img = imageops.load_image(p, key)
            regions = backend.detect(img)
        except Exception as e:  # noqa: BLE001  (one unreadable image must not stop the run)
            with lock:
                res.warnings.append(f"{rel}: {e}")
            return
        with lock:
            res.images_scanned += 1
        if regions:
            with lock:
                trs = translator.translate_many([r.text for r in regions])
            use = []
            for r, t in zip(regions, trs):
                if t.translated and t.text.strip():
                    r.en = t.text.strip()
                    use.append(r)
                    with lock:
                        res.rows.append({"kind": "image", "where": f"{rel}@{r.box}", "ja": r.text, "en": r.en, "source": t.source})
            if use:
                _save_like(ocr.overlay_translation(img, use, font), p, key)
                with lock:
                    res.images_changed += 1
                    res.regions += len(use)
        with lock:
            done[rel] = sig(p)
            counter["n"] += 1
            if progress:
                progress("images", counter["n"], len(images))
            if counter["n"] % 25 == 0:
                man_path.write_text(json.dumps(done), encoding="utf-8")

    workers = opts.workers or min(4, os.cpu_count() or 1)
    if progress:
        progress("images", 0, len(images))
    with ThreadPoolExecutor(workers) as ex:
        list(ex.map(work, images))
    man_path.write_text(json.dumps(done), encoding="utf-8")
    if cancel is not None and cancel.is_set():
        res.cancelled = True
