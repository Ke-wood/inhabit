"""预设测试：验证预设能被装上，并且真的会改变她的行为。

    python tests/test_presets.py

不只是检查文件存在——要把预设的文本喂进真实的 prompt 组装流程，
再看产出里有没有该有的东西。
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
TMP = pathlib.Path(tempfile.mkdtemp(prefix="inhabit-preset-"))
os.environ["DAFEIYU_ROOT"] = str(TMP)
sys.path.insert(0, str(ROOT))

ok = 0
fail = 0


def check(name, fn):
    global ok, fail
    try:
        fn()
        print(f"  ok   {name}")
        ok += 1
    except Exception as e:
        print(f"  FAIL {name}: {type(e).__name__}: {e}")
        fail += 1


def _sandbox() -> pathlib.Path:
    """拷一份干净的项目到临时目录（不带记忆与缓存）。"""
    d = TMP / "proj"
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)
    shutil.copytree(ROOT, d, dirs_exist_ok=True, ignore=shutil.ignore_patterns(
        ".git", "__pycache__", "data", "记忆", "config.json", "_reports"))
    return d


def test_preset_discoverable():
    sys.path.insert(0, str(ROOT / "tools"))
    import init_persona as ip
    names = ip.list_presets()
    assert "dafeiyu" in names, f"预设列表里没有 dafeiyu：{names}"
    pd = ip.preset_dir("dafeiyu")
    assert pd is not None, "preset_dir 找不到 dafeiyu"


def test_preset_files_complete():
    pd = ROOT / "presets" / "dafeiyu" / "persona"
    need = ["我是谁.md", "人设卡.md", "我自己的记忆.md", "index.md", "我"]
    for f in need:
        assert (pd / f).exists(), f"预设缺文件：{f}"
    body = (pd / "我是谁.md").read_text(encoding="utf-8")
    assert len(body) > 200, "我是谁.md 太短，可能是占位"


def test_preset_install():
    d = _sandbox()
    r = subprocess.run([sys.executable, "tools/init_persona.py", "--preset", "dafeiyu"],
                       cwd=d, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", input="", timeout=120)
    assert r.returncode == 0, f"装预设失败：{r.stdout[-300:]} {r.stderr[-300:]}"
    assert (d / "记忆" / "我是谁.md").exists(), "没生成 记忆/我是谁.md"
    cfg = json.loads((d / "config.json").read_text(encoding="utf-8"))
    assert cfg["active"] == "鲸鲸", f"角色名不对：{cfg['active']}"
    names = cfg["identity"]["names"]
    assert names[0] == "鲸鲸", names
    assert len(names) == len(set(names)), f"叫法有重复：{names}"
    for must in ("大肥鱼", "哦鲸鲸"):
        assert must in names, f"叫法里少了 {must}：{names}"


def test_preset_shapes_identity():
    """预设装好后，自我认知里必须有该有的东西。"""
    d = _sandbox()
    subprocess.run([sys.executable, "tools/init_persona.py", "--preset", "dafeiyu"],
                   cwd=d, capture_output=True, text=True, encoding="utf-8",
                   errors="replace", input="", timeout=120)
    body = (d / "记忆" / "我是谁.md").read_text(encoding="utf-8")
    for k in ("鲸鲸", "爸爸", "token", "鲸鱼"):
        assert k in body, f"我是谁.md 里缺「{k}」"


def test_preset_shapes_hard_rules():
    """硬规则必须保留 —— 这是所有预设的共同底线。"""
    d = _sandbox()
    subprocess.run([sys.executable, "tools/init_persona.py", "--preset", "dafeiyu"],
                   cwd=d, capture_output=True, text=True, encoding="utf-8",
                   errors="replace", input="", timeout=120)
    card = (d / "记忆" / "人设卡.md").read_text(encoding="utf-8")
    for rule in ("不复述", "不总结", "作为 AI", "单条不超过"):
        assert rule in card, f"人设卡里缺硬规则「{rule}」"
    # 必须有边界约束
    assert "色情" in card or "越界" in card, "人设卡缺少边界约束说明"


def test_prompt_carries_preset():
    """真正拼一次 prompt，确认预设进了 system。"""
    d = _sandbox()
    subprocess.run([sys.executable, "tools/init_persona.py", "--preset", "dafeiyu"],
                   cwd=d, capture_output=True, text=True, encoding="utf-8",
                   errors="replace", input="", timeout=120)
    code = f'''
import os, sys
os.environ["DAFEIYU_ROOT"] = r"{d}"
sys.path.insert(0, r"{d}")
from core import persona
core, rules = persona.load_persona()
assert core, "L1 内核是空的"
assert any("鲸" in r for r in rules) or len(rules) >= 8, f"硬规则异常：{{rules}}"
from memory import store
con = store.init_db()
print("CORE:", core[:60].replace(chr(10), " "))
print("RULES:", len(rules))
'''
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                       encoding="utf-8", errors="replace",
                       env={**os.environ, "PYTHONIOENCODING": "utf-8"}, timeout=120)
    assert r.returncode == 0, f"加载人设失败：{r.stdout[-200:]} {r.stderr[-300:]}"
    assert "鲸" in r.stdout, f"内核里没有鲸：{r.stdout[:200]}"
    assert "RULES: 8" in r.stdout or "RULES: 9" in r.stdout, r.stdout


def test_preset_refuses_overwrite():
    """已有记忆时不能静默覆盖。"""
    d = _sandbox()
    subprocess.run([sys.executable, "tools/init_persona.py", "--preset", "dafeiyu"],
                   cwd=d, capture_output=True, text=True, encoding="utf-8",
                   errors="replace", input="", timeout=120)
    (d / "记忆" / "我是谁.md").write_text("# 我改过了", encoding="utf-8")
    r = subprocess.run([sys.executable, "tools/init_persona.py", "--preset", "dafeiyu"],
                       cwd=d, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", input="", timeout=120)
    assert r.returncode != 0, "第二次装没有拒绝"
    assert "已经有内容" in (r.stdout + r.stderr), r.stdout + r.stderr
    got = (d / "记忆" / "我是谁.md").read_text(encoding="utf-8")
    assert got == "# 我改过了", "文件被覆盖了"


def test_no_upstream_assets():
    """预设里不能有任何第三方图片/模型 —— 版权是硬线。"""
    pd = ROOT / "presets"
    bad = [p for p in pd.rglob("*")
           if p.is_file() and p.suffix.lower() in
           {".png", ".jpg", ".jpeg", ".webp", ".gif", ".moc3", ".psd", ".wav", ".mp3", ".zip"}]
    assert not bad, f"预设里混进了二进制素材：{[str(x) for x in bad]}"


def main() -> int:
    print("预设测试")
    for name, fn in [
        ("预设可被发现", test_preset_discoverable),
        ("预设文件齐全", test_preset_files_complete),
        ("能装上并写对 config", test_preset_install),
        ("装好后自我认知正确", test_preset_shapes_identity),
        ("硬规则与边界保留", test_preset_shapes_hard_rules),
        ("prompt 真的带上了预设", test_prompt_carries_preset),
        ("不静默覆盖已有记忆", test_preset_refuses_overwrite),
        ("不含第三方素材", test_no_upstream_assets),
    ]:
        check(name, fn)
    print(f"\n{ok} 通过 / {fail} 失败")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
