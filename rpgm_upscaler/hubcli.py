"""CLI handlers for the non-upscaling commands (detect, saves ...)."""
from __future__ import annotations

import json
import sys

from .detect import detect_engine
from .saves.database import load_names, strip_codes
from .saves.files import find_saves, open_save
from .saves.model import INVENTORY_KINDS, SaveError


def _add_english(d: dict) -> None:
    """Translate every Japanese label in a dump (offline) and store it under d["english"]."""
    from .translate.service import Translator
    labels = set()
    for key in ("switch_names", "variable_names"):
        labels.update(d.get(key, {}).values())
    for a in d["party"]:
        labels.add(a.get("db_name", ""))
    for kind in d["inventory"].values():
        labels.update(v.get("name", "") for v in kind.values())
    if d.get("position"):
        labels.add(d["position"].get("map_name", ""))
    labels = sorted(x for x in labels if x)
    t = Translator()
    d["english"] = {r_src: r.text for r_src, r in zip(labels, t.translate_many(labels)) if r.translated}


def _neutral(doc, names=None) -> dict:
    def label(kind, i):
        return strip_codes(names.inventory(kind).get(i, "")) if names else ""

    out = {"engine": doc.engine, "file": str(doc.path), "readonly": doc.readonly, "playtime": doc.playtime(), "gold": doc.gold(),
           "position": dict(zip(("map", "x", "y"), doc.position() or ())) or None}
    if names and out["position"]:
        out["position"]["map_name"] = strip_codes(names.maps.get(out["position"]["map"], ""))
    out["switches"] = {i: doc.get_switch(i) for i in range(1, doc.switch_count() + 1)}
    out["variables"] = {i: doc.get_variable(i) for i in range(1, doc.variable_count() + 1)}
    for d, key, fn in ((out["switches"], "switch_names", lambda i: names.switch(i)),
                       (out["variables"], "variable_names", lambda i: names.variable(i))):
        if names:
            out[key] = {i: strip_codes(fn(i)) for i in d if fn(i)}
    party = []
    for aid in doc.party_ids():
        a = doc.actor(aid)
        if a:
            row = {"id": a.id, "name": a.name, "level": a.level, "exp": a.exp, "hp": a.hp, "mp": a.mp}
            if names and aid in names.actors:
                row["db_name"] = strip_codes(names.actors[aid])
            party.append(row)
    out["party"] = party
    out["inventory"] = {k: {i: {"count": c, **({"name": label(k, i)} if names else {})} for i, c in doc.inventory(k).items()}
                        for k in INVENTORY_KINDS}
    return out


def _print_dump(d: dict) -> None:
    print(f"{d['file']}  [{d['engine']}]" + (f"  READ-ONLY: {d['readonly']}" if d["readonly"] else ""))
    print(f"playtime {d['playtime']}   gold {d['gold']}   position {d['position']}")
    for a in d["party"]:
        print(f"  actor {a['id']:>3} {a['name']:<14} Lv{a['level']} exp {a['exp']} hp {a['hp']} mp {a['mp']}")
    for kind, items in d["inventory"].items():
        for i, v in items.items():
            print(f"  {kind[:-1]:<7} {i:>3} x{v['count']:<3} {v.get('name', '')}")
    en = d.get("english", {})
    sn = {k: (f"{v} [{en[v]}]" if v in en else v) for k, v in d.get("switch_names", {}).items()}
    vn = {k: (f"{v} [{en[v]}]" if v in en else v) for k, v in d.get("variable_names", {}).items()}
    on = [f"{i}{(':' + sn[i]) if i in sn else ''}" for i, v in d["switches"].items() if v]
    print("switches ON:", ", ".join(on) or "-")
    for i, v in d["variables"].items():
        if v not in (0, None, ""):
            print(f"  var {i:>3} = {v!r}  {vn.get(i, '')}")


def _parse_pair(text: str, what: str) -> tuple[str, str]:
    if "=" not in text:
        raise SaveError(f"bad {what} {text!r}; expected KEY=VALUE")
    k, v = text.split("=", 1)
    return k.strip(), v.strip()


def _value(text: str):
    for conv in (int, float):
        try:
            return conv(text)
        except ValueError:
            pass
    return text


def _translate_cmd(args) -> int:
    from .translate import argos
    from .translate.service import Translator
    items = list(args.items)
    if items and items[0] == "status":
        st = Translator().status()
        print(json.dumps(st, ensure_ascii=False, indent=2))
        return 0
    if items and items[0] == "install":
        last = [0]

        def prog(done, total):
            if total and done * 100 // total >= last[0] + 5:
                last[0] = done * 100 // total
                print(f"\rdownloading {last[0]}%", end="", file=sys.stderr)
        print("model installed at", argos.install(progress=prog))
        return 0
    if items and items[0] == "import":
        if len(items) < 2:
            print("usage: translate import PATH.argosmodel", file=sys.stderr)
            return 2
        print("model installed at", argos.import_package(items[1]))
        return 0
    texts = list(items)
    if args.file:
        texts += [ln.rstrip("\n") for ln in open(args.file, encoding="utf-8") if ln.strip()]
    if not texts:
        print("nothing to translate (give TEXT..., --file, or install|import|status)", file=sys.stderr)
        return 2
    res = Translator().translate_many(texts)
    if args.json:
        print(json.dumps([{"ja": t, "en": r.text, "source": r.source, "confidence": r.confidence} for t, r in zip(texts, res)],
                         ensure_ascii=False, indent=2))
    else:
        for t, r in zip(texts, res):
            print(f"{t}\t{r.text}\t[{r.source}]")
    return 0


def run_hub_command(args) -> int:
    try:
        if args.cmd == "translate":
            return _translate_cmd(args)
        if args.cmd == "detect":
            info = detect_engine(args.path)
            if info is None:
                print("not an RPG Maker project (no index.html / Game.ini found)", file=sys.stderr)
                return 2
            print(f"{info.engine}\t{info.label}\t{info.root}" + (f"\tarchive={info.archive.name}" if info.archive else ""))
            return 0
        if args.saves_cmd == "list":
            files = find_saves(args.game)
            for f in files:
                try:
                    d = open_save(f)
                    print(f"{f}\t{d.engine}\t{d.playtime() or '-'}\tgold={d.gold()}" + ("\tREAD-ONLY" if d.readonly else ""))
                except SaveError as e:
                    print(f"{f}\tunreadable: {e}")
            return 0 if files else 1
        doc = open_save(args.save)
        if args.saves_cmd == "dump":
            d = _neutral(doc, load_names(args.save) if (args.names or args.translate) else None)
            if args.translate:
                _add_english(d)
            print(json.dumps(d, ensure_ascii=False, indent=2) if args.json else "", end="" if args.json else "")
            if args.json:
                print()
            else:
                _print_dump(d)
            return 0
        for s in args.switch:
            k, v = _parse_pair(s, "--switch")
            doc.set_switch(int(k), v.lower() in ("1", "on", "true", "yes"))
        for s in args.var:
            k, v = _parse_pair(s, "--var")
            doc.set_variable(int(k), _value(v))
        if args.gold is not None:
            doc.set_gold(args.gold)
        for s in args.item:
            k, v = _parse_pair(s, "--item")
            kind, _, iid = k.rpartition(":")
            doc.set_item(kind or "items", int(iid), int(v))
        for s in args.actor:
            k, v = _parse_pair(s, "--actor")
            aid, _, field = k.partition(":")
            doc.set_actor(int(aid), **{field: v if field == "name" else int(v)})
        if args.map is not None or args.pos:
            x = y = None
            if args.pos:
                x, y = (int(t) for t in args.pos.split(","))
            doc.set_position(args.map, x, y)
        doc.save()
        print(f"saved {doc.path} (backup kept)")
        return 0
    except (SaveError, ValueError, argos_error()) as e:
        print("error:", e, file=sys.stderr)
        return 2


def argos_error():
    from .translate.argos import ArgosError
    return ArgosError
