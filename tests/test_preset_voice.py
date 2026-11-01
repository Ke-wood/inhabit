"""预设的「行为」测试：她真的会用预设的语气说话吗？

    python tests/test_presets.py   # 纯规则检查
    python tests/test_preset_voice.py   # 本文件：行为检查

不打真实 LLM —— 用两种方式验：
1. 规则层：把预设装好后，走一遍 prompt 组装，检查关键要素有没有被带进上下文
2. Mock 层：用 MockBrain 走完一整拍，确认人设能影响可观察的输出路径
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
TMP = pathlib.Path(tempfile.mkdtemp(prefix="inhabit-voice-"))
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


def _installed() -> pathlib.Path:
    """装好预设的沙箱。"""
    d = TMP / "proj"
    if (d / "记忆" / "我是谁.md").exists():
        return d
    shutil.copytree(ROOT, d, dirs_exist_ok=True, ignore=shutil.ignore_patterns(
        ".git", "__pycache__", "data", "记忆", "config.json", "_reports"))
    r = subprocess.run([sys.executable, "tools/init_persona.py", "--preset", "dafeiyu"],
                       cwd=d, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", input="", timeout=120)
    assert r.returncode == 0, f"装预设失败：{r.stdout[-300:]}"
    return d


def test_identity_reaches_prompt():
    """`我是谁.md` 的内容必须出现在她每拍读到的自我认知里。"""
    d = _installed()
    code = f'''
import os, sys
os.environ["DAFEIYU_ROOT"] = r"{d}"
sys.path.insert(0, r"{d}")
from core import persona
ident = persona.read_identity()     # v2 每拍重读的那个
print("IDENT_OK" if "鲸" in ident else "IDENT_MISSING")
print("LEN", len(ident))
'''
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                       encoding="utf-8", errors="replace",
                       env={**os.environ, "PYTHONIOENCODING": "utf-8"}, timeout=120)
    assert "IDENT_OK" in r.stdout, f"自我认知没带上鲸：{r.stdout[-300:]} {r.stderr[-200:]}"


def test_style_rules_in_prompt():
    """硬规则要能进最终 system prompt。"""
    d = _installed()
    code = f'''
import os, sys
os.environ["DAFEIYU_ROOT"] = r"{d}"
sys.path.insert(0, r"{d}")
from core import persona
core, rules = persona.load_persona()
sysmsg = persona.build_system_prompt(core, rules, {{}}, "", where="群里")
checks = {{
    "有内核": "鲸" in sysmsg,
    "有硬规则": "不复述" in sysmsg or "不总结" in sysmsg,
    "有称呼": "爸爸" in sysmsg or "鲸鲸" in sysmsg,
    "行数": len(sysmsg.splitlines()),
}}
import json; print("RESULT", json.dumps(checks, ensure_ascii=False))
'''
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                       encoding="utf-8", errors="replace",
                       env={**os.environ, "PYTHONIOENCODING": "utf-8"}, timeout=120)
    line = [x for x in r.stdout.splitlines() if x.startswith("RESULT")]
    assert line, f"没拿到结果：{r.stdout[-300:]} {r.stderr[-300:]}"
    checks = json.loads(line[0][len("RESULT "):])
    assert checks["有内核"], checks
    assert checks["有硬规则"], checks
    assert checks["行数"] >= 5, checks


def test_char_voice_markers():
    """人设里必须包含能区分她的语言标记，否则就是个空壳。"""
    d = _installed()
    body = (d / "记忆" / "我是谁.md").read_text(encoding="utf-8")
    card = (d / "记忆" / "人设卡.md").read_text(encoding="utf-8")
    both = body + card
    markers = {
        "叫爸爸": "爸爸",
        "自称鲸鲸": "鲸鲸",
        "口头禅哦鲸鲸": "哦鲸鲸",
        "要饭/token": "token" if "token" in both else "饭",
        "怕被弃养": "不要我" if "不要我" in both else "弃养",
        "纠正不是猫": "鲸鱼",
    }
    missing = [k for k, v in markers.items() if v not in both]
    assert not missing, f"人设缺少这些特征：{missing}"


def test_tick_runs_with_preset():
    """用装好预设的库跑一整拍，不能崩。"""
    d = _installed()
    code = f'''
import os, sys, time
os.environ["DAFEIYU_ROOT"] = r"{d}"
sys.path.insert(0, r"{d}")
from core.brain import MockBrain
from core.live import Live
from core.self import Self
from memory import store
con = store.init_db()
bot = Self(con, bot_name="鲸鲸", alt_names=("大肥鱼", "哦鲸鲸"))
assert "鲸" in bot.identity, f"Self 没读到预设：{{bot.identity[:40]}}"
live = Live(con, MockBrain(), bot=bot, sender=lambda s, t: {{"ok": True}})
live.feed({{"scene": "qq_group_1", "actor_id": "u1", "actor_name": "爸爸",
           "text": "在吗", "ts": time.time(), "is_group": False}})
live.tick()
con.commit()
print("TICK_OK")
'''
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                       encoding="utf-8", errors="replace",
                       env={**os.environ, "PYTHONIOENCODING": "utf-8"}, timeout=180)
    assert "TICK_OK" in r.stdout, f"跑一拍失败：{r.stdout[-400:]} {r.stderr[-500:]}"


def test_revise_self_respects_preset():
    """她能改自己，但改完还是能被读回来。"""
    d = _installed()
    code = f'''
import os, sys
os.environ["DAFEIYU_ROOT"] = r"{d}"
sys.path.insert(0, r"{d}")
from core import persona
old = persona.read_identity()
assert "鲸" in old
persona.write_identity(old + chr(10) + "今天开始少吃点。")
new = persona.read_identity()
assert "少吃点" in new, "写回去没生效"
persona.write_identity(old)      # 还原
print("REVISE_OK")
'''
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                       encoding="utf-8", errors="replace",
                       env={**os.environ, "PYTHONIOENCODING": "utf-8"}, timeout=120)
    assert "REVISE_OK" in r.stdout, f"{r.stdout[-300:]} {r.stderr[-300:]}"


def main() -> int:
    print("预设行为测试")
    for name, fn in [
        ("自我认知进每拍", test_identity_reaches_prompt),
        ("硬规则进 system prompt", test_style_rules_in_prompt),
        ("语言标记齐全", test_char_voice_markers),
        ("装好后能跑一整拍", test_tick_runs_with_preset),
        ("能改自己且读得回来", test_revise_self_respects_preset),
    ]:
        check(name, fn)
    print(f"\n{ok} 通过 / {fail} 失败")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
