#!/usr/bin/env python3
"""Build a small, bootable RPG Maker MV project for end-to-end testing.

The engine scripts are NOT part of this repository. Point --corescript at a checkout of the MIT-licensed
https://github.com/rpgtkoolmv/corescript ; its split sources are concatenated into js/rpg_*.js.
Art is generated with Pillow (numbered coloured tiles, so scaling mistakes are visible by eye).

    python tools/make_sample_game.py --corescript ~/corescript --out /tmp/samplegame [--encrypt]
"""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from PIL import Image, ImageDraw

KEY = bytes.fromhex("00112233445566778899aabbccddeeff")
BGM = {"name": "", "pan": 0, "pitch": 100, "volume": 90}
SE = {"name": "", "pan": 0, "pitch": 100, "volume": 90}


def label(draw, xy, text, fill=(255, 255, 255, 255)):
    draw.text(xy, text, fill=fill)


def numbered_grid(w, h, cell, base_seed=0, text=True):
    img = Image.new("RGBA", (w, h))
    d = ImageDraw.Draw(img)
    cols = w // cell
    for i in range((w // cell) * (h // cell)):
        c, r = i % cols, i // cols
        col = ((c * 41 + base_seed * 17) % 200 + 40, (r * 57 + base_seed * 31) % 200 + 40, ((c + r) * 23 + base_seed * 7) % 200 + 40, 255)
        d.rectangle([c * cell, r * cell, (c + 1) * cell - 1, (r + 1) * cell - 1], fill=col, outline=(0, 0, 0, 255))
        if text and cell >= 24:
            label(d, (c * cell + 4, r * cell + 4), str(i))
    return img


def character_sheet():
    img = Image.new("RGBA", (576, 384), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    for r in range(8):
        for c in range(12):
            x, y = c * 48, r * 48
            col = ((r * 30) % 255, (c * 20) % 255, 160, 255)
            d.ellipse([x + 10, y + 4, x + 38, y + 32], fill=col, outline=(0, 0, 0, 255))
            d.rectangle([x + 14, y + 30, x + 34, y + 46], fill=col, outline=(0, 0, 0, 255))
            label(d, (x + 18, y + 12), f"{c % 3}")
    return img


def build_images(web: Path) -> None:
    img = web / "img"
    def save(rel, im):
        p = img / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        im.save(p)
    save("tilesets/Test_A5.png", numbered_grid(384, 768, 48, 1))
    save("tilesets/Test_B.png", numbered_grid(768, 768, 48, 2))
    save("characters/Actor1.png", character_sheet())
    save("faces/Actor1.png", numbered_grid(576, 288, 144, 3))
    sv = numbered_grid(576, 384, 64, 4)
    save("sv_actors/Actor1_1.png", sv)
    e = Image.new("RGBA", (120, 90), (0, 0, 0, 0))
    ImageDraw.Draw(e).ellipse([2, 2, 117, 87], fill=(60, 200, 90, 255), outline=(0, 0, 0, 255), width=3)
    label(ImageDraw.Draw(e), (45, 38), "SLIME")
    save("enemies/Slime.png", e)
    for name, seed in (("Grass", 5),):
        bb = Image.new("RGBA", (1000, 740), (90, 160, 90, 255))
        d = ImageDraw.Draw(bb)
        for i in range(0, 1000, 100):
            d.line([i, 0, i, 740], fill=(0, 80, 0, 255), width=3)
            label(d, (i + 4, 4), str(i))
        for j in range(0, 740, 100):
            d.line([0, j, 1000, j], fill=(0, 80, 0, 255), width=3)
        save(f"battlebacks1/{name}.png", bb)
        b2 = Image.new("RGBA", (1000, 740), (0, 0, 0, 0))
        ImageDraw.Draw(b2).rectangle([0, 0, 1000, 160], fill=(120, 180, 255, 255))
        save(f"battlebacks2/{name}.png", b2)
    t = Image.new("RGBA", (816, 624), (30, 30, 90, 255))
    d = ImageDraw.Draw(t)
    for i in range(0, 816, 102):
        d.line([i, 0, i, 624], fill=(255, 255, 255, 80))
    for j in range(0, 624, 104):
        d.line([0, j, 816, j], fill=(255, 255, 255, 80))
    d.rectangle([0, 0, 815, 623], outline=(255, 255, 0, 255), width=4)
    label(d, (20, 20), "TITLE 816x624")
    save("titles1/Title.png", t)
    p = Image.new("RGBA", (200, 100), (255, 128, 0, 255))
    ImageDraw.Draw(p).rectangle([0, 0, 199, 99], outline=(255, 255, 255, 255), width=4)
    label(ImageDraw.Draw(p), (60, 45), "PICTURE 200x100")
    save("pictures/Pic.png", p)
    # animation sheet: 5 columns of 192 cells
    a = Image.new("RGBA", (960, 192), (0, 0, 0, 0))
    ad = ImageDraw.Draw(a)
    for c in range(5):
        ad.ellipse([c * 192 + 40, 40, c * 192 + 152, 152], outline=(255, 255, 0, 255), width=6)
    save("animations/Test.png", a)
    # system sheets
    win = Image.new("RGBA", (192, 192), (0, 0, 60, 200))
    wd = ImageDraw.Draw(win)
    wd.rectangle([0, 0, 95, 95], fill=(10, 10, 80, 220), outline=(255, 255, 255, 255))
    wd.rectangle([96, 0, 191, 95], outline=(255, 255, 255, 255))
    wd.rectangle([96, 96, 191, 191], fill=(255, 255, 255, 255))
    for i in range(32):  # text colour palette sampled at (96+(n%8)*12+6, 144+(n//8)*12+6)
        x, y = 96 + (i % 8) * 12, 144 + (i // 8) * 12
        wd.rectangle([x, y, x + 11, y + 11], fill=(255 - i * 6, 255 - i * 3, 255, 255) if i else (255, 255, 255, 255))
    save("system/Window.png", win)
    save("system/IconSet.png", numbered_grid(512, 192, 32, 6))
    save("system/Balloon.png", numbered_grid(384, 720, 48, 7, text=False))
    save("system/Shadow1.png", Image.new("RGBA", (48, 48), (0, 0, 0, 128)))
    save("system/Shadow2.png", Image.new("RGBA", (48, 48), (0, 0, 0, 128)))
    dm = Image.new("RGBA", (480, 240), (0, 0, 0, 0))
    dd = ImageDraw.Draw(dm)
    for r in range(5):
        for c in range(10):
            label(dd, (c * 48 + 18, r * 48 + 18), str(c), fill=(255, 255, 255, 255))
    save("system/Damage.png", dm)
    save("system/States.png", numbered_grid(768, 960, 96, 8, text=False))
    for n in (1, 2, 3):
        save(f"system/Weapons{n}.png", numbered_grid(576, 384, 96, 9, text=False))
    save("system/ButtonSet.png", numbered_grid(576, 96, 48, 10, text=False))
    save("system/Loading.png", Image.new("RGBA", (200, 40), (255, 255, 255, 255)))
    save("system/GameOver.png", Image.new("RGBA", (816, 624), (60, 0, 0, 255)))


def d(**kw):
    return kw


def build_data(web: Path, encrypted: bool) -> None:
    data = web / "data"
    data.mkdir(parents=True, exist_ok=True)

    def put(name, obj):
        (data / f"{name}.json").write_text(json.dumps(obj))

    params = [[50 + i * 10 for i in range(100)], [10 + i for i in range(100)], [8 + i for i in range(100)],
              [8 + i for i in range(100)], [8 + i for i in range(100)], [8 + i for i in range(100)],
              [8 + i for i in range(100)], [8 + i for i in range(100)]]
    put("Actors", [None, d(id=1, battlerName="Actor1_1", characterIndex=0, characterName="Actor1", classId=1,
                           equips=[0, 0, 0, 0, 0], faceIndex=0, faceName="Actor1", traits=[], initialLevel=1,
                           maxLevel=99, name="Hero", nickname="", note="", profile="")])
    put("Classes", [None, d(id=1, expParams=[30, 20, 30, 30], traits=[], learnings=[], name="Fighter", note="", params=params)])
    put("Skills", [None, d(id=1, animationId=1, damage=d(critical=False, elementId=0, formula="a.atk*4-b.def*2", type=1, variance=20),
                           description="", effects=[], hitType=1, iconIndex=76, message1="", message2="", mpCost=0,
                           name="Attack", note="", occasion=1, repeats=1, requiredWtypeId1=0, requiredWtypeId2=0,
                           scope=1, speed=0, stypeId=0, successRate=100, tpCost=0, tpGain=10)])
    for n in ("Items", "Weapons", "Armors", "CommonEvents"):
        put(n, [None])
    put("Enemies", [None, d(id=1, actions=[d(conditionParam1=0, conditionParam2=0, conditionType=0, rating=5, skillId=1)],
                            battlerHue=0, battlerName="Slime", dropItems=[d(dataId=1, denominator=1, kind=0)] * 3,
                            exp=1, traits=[d(code=22, dataId=0, value=1), d(code=31, dataId=1, value=0), d(code=23, dataId=0, value=1)],
                            gold=1, name="Slime", note="", params=[100, 0, 10, 10, 10, 10, 10, 10])])
    cond = d(actorHp=50, actorId=1, actorValid=False, enemyHp=50, enemyIndex=0, enemyValid=False, switchId=1,
             switchValid=False, turnA=0, turnB=0, turnEnding=False, turnValid=False)
    put("Troops", [None, d(id=1, members=[d(enemyId=1, x=408, y=300, hidden=False)], name="Slime",
                           pages=[d(conditions=cond, list=[d(code=0, indent=0, parameters=[])], span=0)])])
    put("States", [None, d(id=1, autoRemovalTiming=0, chanceByDamage=100, description="", iconIndex=1, maxTurns=1,
                           message1="", message2="", message3="", message4="", messageType=1, minTurns=1, motion=3,
                           name="Death", note="", overlay=0, priority=100, releaseByDamage=False, removeAtBattleEnd=False,
                           removeByDamage=False, removeByRestriction=False, removeByWalking=False, restriction=4,
                           stepsToRemove=100, traits=[d(code=23, dataId=9, value=0)])])
    put("Animations", [None, d(id=1, animation1Hue=0, animation1Name="Test", animation2Hue=0, animation2Name="",
                               frames=[[[i % 5, 0, 0, 100, 0, 0, 255, 0]] for i in range(30)],
                               name="Test", position=1, timings=[])])
    put("Tilesets", [None, d(id=1, flags=[0] * 8192, mode=1, name="Test", note="",
                             tilesetNames=["", "", "", "", "Test_A5", "Test_B", "", "", ""])])
    put("MapInfos", [None, d(id=1, expanded=False, name="Map001", order=1, parentId=0, scrollX=0, scrollY=0)])
    W, H = 25, 19
    layer0, layer1 = [], []
    for y in range(H):
        for x in range(W):
            layer0.append(1536 + ((x + y) % 2))      # A5 ground checker
            layer1.append(1 + ((x * 3 + y * 5) % 11) if (x % 4 == 1 and y % 3 == 1) else 0)  # B objects
    mapdata = layer0 + layer1 + [0] * (W * H * 4)
    ev_page = d(conditions=d(actorId=1, actorValid=False, itemId=1, itemValid=False, selfSwitchCh="A", selfSwitchValid=False,
                             switch1Id=1, switch1Valid=False, switch2Id=1, switch2Valid=False, variableId=1,
                             variableValid=False, variableValue=0),
                directionFix=False, image=d(characterIndex=0, characterName="Actor1", direction=2, pattern=1, tileId=0),
                list=[d(code=101, indent=0, parameters=["Actor1", 0, 0, 2]),
                      d(code=401, indent=0, parameters=["Hello! This tests face + text scaling."]),
                      d(code=0, indent=0, parameters=[])],
                moveFrequency=3, moveRoute=d(list=[d(code=0, parameters=[])], repeat=True, skippable=False, wait=False),
                moveSpeed=3, moveType=0, priorityType=1, stepAnime=False, through=False, trigger=0, walkAnime=True)
    put("Map001", d(autoplayBgm=False, autoplayBgs=False, battleback1Name="Grass", battleback2Name="Grass", bgm=BGM, bgs=BGM,
                    disableDashing=False, displayName="", encounterList=[], encounterStep=30, height=H, note="",
                    parallaxLoopX=False, parallaxLoopY=False, parallaxName="", parallaxShow=True, parallaxSx=0,
                    parallaxSy=0, scrollType=0, specifyBattleback=False, tilesetId=1, width=W, data=mapdata,
                    events=[None, d(id=1, name="NPC", note="", pages=[ev_page], x=14, y=9)]))
    basic = ["Level", "Lv", "HP", "HP", "MP", "MP", "TP", "TP", "EXP"]
    put("System", d(
        airship=d(bgm=BGM, characterIndex=0, characterName="", startMapId=0, startX=0, startY=0),
        armorTypes=["", "General Armor"], attackMotions=[d(type=0, weaponImageId=0)] * 3,
        battleBgm=BGM, battleback1Name="Grass", battleback2Name="Grass", battlerHue=0, battlerName="",
        boat=d(bgm=BGM, characterIndex=0, characterName="", startMapId=0, startX=0, startY=0),
        currencyUnit="G", defeatMe=BGM, editMapId=1, elements=["", "Physical"],
        equipTypes=["", "Weapon", "Shield", "Head", "Body", "Accessory"], gameTitle="Sample Game", gameoverMe=BGM,
        locale="en_US", magicSkills=[1], menuCommands=[True] * 6, optDisplayTp=True, optDrawTitle=True, optExtraExp=False,
        optFloorDeath=False, optFollowers=True, optSlipDeath=False, optTransparent=False, partyMembers=[1],
        ship=d(bgm=BGM, characterIndex=0, characterName="", startMapId=0, startX=0, startY=0),
        skillTypes=["", "Magic"], sounds=[SE] * 24, startMapId=1, startX=12, startY=9, switches=["", "S1"],
        terms=d(basic=basic, commands=["Fight", "Escape", "Attack", "Guard", "Item", "Skill", "Equip", "Status",
                                       "Formation", "Save", "Game End", "Options", "Weapon", "Armor", "Key Item",
                                       "Equip", "Optimize", "Clear", "New Game", "Continue", None, "To Title", "Cancel",
                                       None, "Buy", "Sell"],
                params=["Max HP", "Max MP", "Attack", "Defense", "M.Attack", "M.Defense", "Agility", "Luck", "Hit", "Evasion"],
                messages=d(alwaysDash="Always Dash", commandRemember="Command Remember", bgmVolume="BGM Volume",
                           bgsVolume="BGS Volume", meVolume="ME Volume", seVolume="SE Volume", possession="Possession",
                           expTotal="Current %1", expNext="To Next %1", saveMessage="Save to which file?",
                           loadMessage="Load which file?", file="File", partyName="%1's Party", emerge="%1 emerged!",
                           preemptive="%1 got the upper hand!", surprise="%1 was surprised!", escapeStart="%1 has started to escape!",
                           escapeFailure="However, it was unable to escape!", victory="%1 was victorious!",
                           defeat="%1 was defeated.", obtainExp="%1 EXP received!", obtainGold="%1\\G found!",
                           obtainItem="%1 found!", levelUp="%1 is now %2 %3!", obtainSkill="%1 learned!",
                           useItem="%1 uses %2!", criticalToEnemy="An excellent hit!!", criticalToActor="A painful blow!!",
                           actorDamage="%1 took %2 damage!", actorRecovery="%1 recovered %2 %3!", actorGain="%1 gained %2 %3!",
                           actorLoss="%1 lost %2 %3!", actorDrain="%1 was drained of %2 %3!", actorNoDamage="%1 took no damage!",
                           actorNoHit="Miss! %1 took no damage!", enemyDamage="%1 did %2 damage!",
                           enemyRecovery="%1 recovered %2 %3!", enemyGain="%1 gained %2 %3!", enemyLoss="%1 lost %2 %3!",
                           enemyDrain="%1 drained %2 %3!", enemyNoDamage="%1 took no damage!", enemyNoHit="Miss! %1 took no damage!",
                           evasion="%1 evaded the attack!", magicEvasion="%1 nullified the magic!",
                           magicReflection="%1 reflected the magic!", counterAttack="%1 counterattacked!",
                           substitute="%1 protected %2!", buffAdd="%1's %2 went up!", debuffAdd="%1's %2 went down!",
                           buffRemove="%1's %2 returned to normal!", actionFailure="There was no effect on %1!")),
        testBattlers=[], testTroopId=1, title1Name="Title", title2Name="", titleBgm=BGM, variables=["", "V1"], versionId=1,
        victoryMe=BGM, weaponTypes=["", "Dagger"], windowTone=[0, 0, 0, 0],
        hasEncryptedImages=encrypted, hasEncryptedAudio=False, **({"encryptionKey": KEY.hex()} if encrypted else {})))


def build_engine(core: Path, web: Path) -> None:
    (web / "js/libs").mkdir(parents=True, exist_ok=True)
    for name in ("rpg_core", "rpg_managers", "rpg_objects", "rpg_scenes", "rpg_sprites", "rpg_windows"):
        order = json.loads((core / f"{name}.json").read_text())
        (web / "js" / f"{name}.js").write_text("\n".join((core / f).read_text(encoding="utf-8") for f in order), encoding="utf-8")
    for f in (core / "js/libs").glob("*.js"):
        shutil.copy2(f, web / "js/libs" / f.name)
    shutil.copy2(core / "js/main.js", web / "js/main.js")
    shutil.copytree(core / "template/fonts", web / "fonts", dirs_exist_ok=True)
    (web / "index.html").write_text((core / "template/index.html").read_text(encoding="utf-8").replace("<title></title>", "<title>Sample Game</title>"))
    (web / "js/plugins").mkdir(exist_ok=True)
    (web / "js/plugins.js").write_text('// Generated by RPG Maker.\n// Do not edit this file directly.\nvar $plugins =\n[\n];\n')
    (web / "css").mkdir(exist_ok=True)
    for sub in ("bgm", "bgs", "me", "se"):
        (web / "audio" / sub).mkdir(parents=True, exist_ok=True)
    (web / "movies").mkdir(exist_ok=True)


def encrypt_images(web: Path) -> None:
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from rpgm_upscaler.core import crypto
    plain = {"system/Window.png", "system/Loading.png"}   # the MV engine loads these unencrypted
    for f in list((web / "img").rglob("*.png")):
        if f.relative_to(web / "img").as_posix() in plain:
            continue
        f.with_suffix(".rpgmvp").write_bytes(crypto.encrypt(f.read_bytes(), KEY))
        f.unlink()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corescript", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--encrypt", action="store_true")
    ap.add_argument("--font", type=Path, help="a .ttf to install as fonts/mplus-1m-regular.ttf "
                    "(the engine refuses to boot without its game font)")
    a = ap.parse_args()
    if a.out.exists():
        raise SystemExit(f"{a.out} exists")
    a.out.mkdir(parents=True)
    build_engine(a.corescript, a.out)
    font = a.font or next(iter(sorted(Path("/usr/share/fonts").rglob("*.ttf"))), None)
    if font is None or not Path(font).exists():
        raise SystemExit("no .ttf found; pass --font")
    shutil.copy2(font, a.out / "fonts" / "mplus-1m-regular.ttf")
    build_images(a.out)
    build_data(a.out, a.encrypt)
    (a.out / "package.json").write_text(json.dumps({"name": "sample", "main": "index.html",
                                                    "window": {"title": "", "width": 816, "height": 624}}, indent=2))
    if a.encrypt:
        encrypt_images(a.out)
    print("sample game written to", a.out)


if __name__ == "__main__":
    main()
