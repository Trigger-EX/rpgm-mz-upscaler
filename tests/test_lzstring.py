import json
import shutil
import subprocess
from pathlib import Path

import pytest

from rpgm_upscaler.saves import lzstring as lz

SAMPLES = ["a", "hello hello hello", "日本語のテキスト😀 ok", json.dumps({"k": list(range(300)), "s": "ソード" * 20}),
           "x" * 5000, "".join(chr(i) for i in range(0, 700)), "😀 \u0000 end"]
import os
CORE = Path(os.environ.get("RPGM_CORESCRIPT", "/nonexistent")) / "js/libs/lz-string.js"


@pytest.mark.parametrize("text", SAMPLES)
def test_roundtrip(text):
    assert lz.decompress_from_base64(lz.compress_to_base64(text)) == text


def test_vectors_from_real_library():
    """Vectors produced by the lz-string 1.3.x file RPG Maker MV ships (js/libs/lz-string.js)."""
    vectors = json.loads((Path(__file__).parent / "fixtures" / "lzstring_vectors.json").read_text())
    for text, b64 in vectors:
        assert lz.compress_to_base64(text) == b64
        assert lz.decompress_from_base64(b64) == text


def test_garbage_rejected():
    with pytest.raises(ValueError):
        lz.decompress_from_base64("!!!")
    with pytest.raises(ValueError):
        lz.decompress_from_base64("")


@pytest.mark.skipif(not (shutil.which("node") and CORE.exists()), reason="needs node and the corescript lz-string.js")
def test_interop_with_real_lzstring_js(tmp_path):
    f = tmp_path / "in.json"
    f.write_text(json.dumps(SAMPLES))
    js = (f"const fs=require('fs');const vm=require('vm');vm.runInThisContext(fs.readFileSync('{CORE}','utf8'));"
          f"const S=JSON.parse(fs.readFileSync('{f}','utf8'));"
          "console.log(JSON.stringify(S.map(s=>[LZString.compressToBase64(s), LZString.decompressFromBase64(LZString.compressToBase64(s))===s])));")
    out = json.loads(subprocess.run(["node", "-e", js], capture_output=True, text=True, check=True).stdout)
    for text, (js_c, ok) in zip(SAMPLES, out):
        assert ok
        assert lz.compress_to_base64(text) == js_c            # identical bytes to the real library
        assert lz.decompress_from_base64(js_c) == text
