"""注意力判断测试：该沉默时沉默，该说话时说话。"""
from __future__ import annotations

import os
import pathlib
import shutil
import sys
import tempfile
import time

TMP = pathlib.Path(tempfile.mkdtemp(prefix="inhabit-att-"))
os.environ["DAFEIYU_ROOT"] = str(TMP)
ROOT = pathlib.Path(r"D:\plan\dafeiyu v1")
sys.path.insert(0, str(ROOT))

import json

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


from core import attention as A


def test_mentioned():
    v = A.judge(A.Signals(mentioned=True))
    assert v.should_think and v.reason == "在叫她", v


def test_quiet_hour_silent():
    v = A.judge(A.Signals(quiet_hour=True, has_message=False))
    assert not v.should_think, v


def test_quiet_hour_but_message():
    """深夜有人说话，还是该看。"""
    v = A.judge(A.Signals(quiet_hour=True, has_message=True, mentioned=True))
    assert v.should_think, v


def test_no_message_no_novelty():
    """什么都没发生、刚说过话 —— 不该主动。"""
    v = A.judge(A.Signals(silence_sec=10, energy=0.9, novelty=0.0))
    assert not v.should_think, v


def test_long_silence_trigger():
    """憋久了可以说。"""
    v = A.judge(A.Signals(silence_sec=1200, energy=0.8, novelty=0.0))
    assert v.should_think, v


def test_message_triggers():
    v = A.judge(A.Signals(silence_sec=5, energy=0.5, has_message=True))
    assert v.should_think, v


def test_low_energy_silent():
    v = A.judge(A.Signals(energy=0.05, has_message=False))
    assert not v.should_think, v


def test_low_energy_but_message():
    v = A.judge(A.Signals(energy=0.05, has_message=True))
    assert v.should_think, v


def test_higher_silence_higher_score():
    lo = A.judge(A.Signals(silence_sec=60, energy=0.8, has_message=True))
    hi = A.judge(A.Signals(silence_sec=1200, energy=0.8, has_message=True))
    assert hi.score >= lo.score, (lo.score, hi.score)


def test_explain_readable():
    s = A.Signals(silence_sec=123, energy=0.42, has_message=True)
    txt = A.explain(s)
    assert "123" in txt and "0.42" in txt, txt


# ---------------------------------------------------------------- 端到端
def _setup():
    shutil.copytree(ROOT / "memory_template", TMP / "记忆", dirs_exist_ok=True)
    (TMP / "config.json").write_text(json.dumps({
        "active": "阿鱼",
        "profiles": {"阿鱼": {"dir": "data", "memory": "记忆", "persona": "我是谁.md"}},
        "channels": {"qq": {"kind": "onebot", "enabled": True, "prefixes": ["qq_"]}},
    }, ensure_ascii=False), encoding="utf-8")
    from core import config
    config._cache.clear()
    config._mtime = 0.0


def test_idle_silent_when_boring():
    """空转时不该每拍都调模型。"""
    _setup()
    from core.brain import MockBrain
    from core.live import Live
    from core.self import Self
    from memory import store

    con = store.init_db()
    bot = Self(con, bot_name="阿鱼")
    calls = []

    class CountingBrain(MockBrain):
        def speak(self, **kw):
            calls.append(1)
            return super().speak(**kw)

    live = Live(con, CountingBrain(), bot=bot, sender=lambda s, t: {"ok": True})
    # 把空闲间隔压到 0，让时间闸一定放行
    live._t_idle = 0.0
    live._t_min = 0.0
    live.last_think = 0.0
    live.last_active = time.time()
    live.last_speak = time.time()
    thoughts = 0
    for _ in range(5):
        r = live.tick()
        if r and r.get("thought"):
            thoughts += 1
        time.sleep(0.01)
    assert thoughts == 0, f"空转时不该想，却想了 {thoughts} 次"


print("注意力判断")
for name, fn in [
    ("被叫一定想", test_mentioned),
    ("深夜不主动", test_quiet_hour_silent),
    ("深夜有人说话要想", test_quiet_hour_but_message),
    ("没事发生不想", test_no_message_no_novelty),
    ("憋久了要想", test_long_silence_trigger),
    ("有人说话要想", test_message_triggers),
    ("没精力不主动", test_low_energy_silent),
    ("没精力但有人叫要想", test_low_energy_but_message),
    ("沉默越久分越高", test_higher_silence_higher_score),
    ("解释可读", test_explain_readable),
    ("端到端：空转时沉默", test_idle_silent_when_boring),
]:
    check(name, fn)

print(f"\n{ok} 通过 / {fail} 失败")
sys.exit(0 if fail == 0 else 1)
