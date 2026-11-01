"""端到端：她真的会因为被叫「大肥鱼」而反应，也真的会立刻记住任务。

    python tests/test_e2e_react.py

不打真 LLM —— 检查的是 prompt 里有没有那些话，
以及从「收到消息」到「下一步动作」这条链路是否真的接上了。
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
import tempfile
import time

TMP = pathlib.Path(tempfile.mkdtemp(prefix="inhabit-react-"))
os.environ["DAFEIYU_ROOT"] = str(TMP)
ROOT = pathlib.Path(__file__).resolve().parent.parent
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


def _env():
    """装好预设的环境（库在临时目录）。"""
    mem = TMP / "记忆"
    if not (mem / "我是谁.md").exists():
        import shutil
        shutil.copytree(ROOT / "presets" / "dafeiyu" / "persona", mem, dirs_exist_ok=True)
        (TMP / "config.json").write_text(json.dumps({
            "active": "鲸鲸",
            "profiles": {"鲸鲸": {"dir": "data", "memory": "记忆",
                                  "persona": "我是谁.md"}},
            "identity": {"names": ["鲸鲸", "大肥鱼", "哦鲸鲸", "本鱼"]},
        }, ensure_ascii=False), encoding="utf-8")
        from core import config as cfg
        cfg._cache.clear()
        cfg._mtime = 0.0
    from memory import store
    return store.init_db(TMP / "feiyu.db")   # 固定路径：多次调用拿同一个库


def test_wrong_name_hits_prompt():
    """叫她「大肥鱼」，这一拍的 prompt 里必须出现「叫错名字」。"""
    from core.act import _address_block
    from core import address
    con = _env()
    # 「大肥鱼」在别名里，但也在禁称里 —— 禁称优先，这就是「不许叫我大肥鱼」
    (TMP / "记忆" / "称呼.md").write_text(
        "# 称呼\n\n## 可以这么叫我\n\n- 鲸鲸\n\n"
        "## 别这么叫我\n\n- 大肥鱼\n- 死鱼\n", encoding="utf-8")
    hit = address.detect("大肥鱼你给我出来",
                         bot_names=("鲸鲸", "大肥鱼", "哦鲸鲸"))
    assert hit and hit["hit"] == "大肥鱼", f"禁称没优先：{hit}"

    hit["count"] = address.bump_wrong(con, "大肥鱼")
    block = _address_block(hit)
    assert "叫错你名字" in block, block
    desc = address.describe(hit, hit["count"])
    assert "大肥鱼" in desc, f"描述里没体现被叫的名字：{desc}"


def test_escalation_in_prompt():
    """连续被叫，prompt 里的语气要变。"""
    from core import address
    con = _env()
    (TMP / "记忆" / "称呼.md").write_text(
        "# 称呼\n\n## 别这么叫我\n\n- 死鱼\n", encoding="utf-8")
    h1 = {"hit": "死鱼", "count": 1}
    h2 = {"hit": "死鱼", "count": 3}
    d1, d2 = address.describe(h1, 1), address.describe(h2, 3)
    assert d1 != d2, (d1, d2)
    assert "第 3 次" in d2, d2


def test_live_feed_records_address():
    """Live.feed 收到禁称，要记次数。"""
    from core.brain import MockBrain
    from core.live import Live
    from core.self import Self
    con = _env()
    (TMP / "记忆" / "称呼.md").write_text(
        "# 称呼\n\n## 别这么叫我\n\n- 死鱼\n", encoding="utf-8")
    bot = Self(con, bot_name="鲸鲸")
    live = Live(con, MockBrain(), bot=bot, sender=lambda s, t: {"ok": True})
    live.feed({"scene": "qq_private_1", "actor_id": "u1", "actor_name": "爸爸",
               "text": "死鱼", "ts": time.time(), "is_group": False})
    item = live.pending[-1]
    assert item.get("address"), f"没记录叫错：{item}"
    assert item["address"]["hit"] == "死鱼"


def test_feed_captures_task_realtime():
    """答应的事，feed 完立刻在库里。"""
    from core.brain import MockBrain
    from core.live import Live
    from core.self import Self
    from core import tasks
    con = _env()
    bot = Self(con, bot_name="鲸鲸")
    live = Live(con, MockBrain(), bot=bot, sender=lambda s, t: {"ok": True})
    before = len(tasks.open_tasks(con, 50))
    live.feed({"scene": "qq_private_1", "actor_id": "u1", "actor_name": "爸爸",
               "text": "我明天提醒你把那份文档发出去", "ts": time.time(),
               "is_group": False})
    after = tasks.open_tasks(live.con, 50)
    assert len(after) > before, f"任务没实时写入：before={before} after={len(after)}"


def test_shared_experience_realtime():
    from core.brain import MockBrain
    from core.live import Live
    from core.self import Self
    from core import tasks
    con = _env()
    bot = Self(con, bot_name="鲸鲸")
    live = Live(con, MockBrain(), bot=bot, sender=lambda s, t: {"ok": True})
    live.feed({"scene": "qq_private_1", "actor_id": "u1", "actor_name": "爸爸",
               "text": "我们一起看了那个纪录片", "ts": time.time(), "is_group": False})
    from memory import store
    txt = tasks.shared_text(live.con, "爸爸")
    assert "纪录片" in txt, f"共同经历没实时写入：{txt!r}"


def test_task_reaches_act_prompt():
    """任务要出现在 act 的初始 prompt 里。"""
    from core import tasks
    from core.act import _initial_prompt
    from core.mind import Thought
    from core.self import Self
    con = _env()
    # 清掉前面测试留下的，保证这条一定是最新的
    con.execute("DELETE FROM open_loops WHERE source='task'")
    con.commit()
    tid = tasks.add_task(con, "把文档发给他", who="爸爸", when="明天")
    con.commit()
    bot = Self(con, bot_name="鲸鲸")
    prompt_bot = bot
    thought = Thought(content="想起还有件事", urge=0.6, plan="reply")
    prompt = _initial_prompt(bot, thought, "qq_private_1")
    assert "答应过" in prompt, prompt[:400]
    assert "把文档发给他" in prompt, "任务内容没进 prompt"


def test_task_reaches_think_prompt():
    """任务也要进 think 的上下文。"""
    from core import tasks
    from core.self import Self
    con = _env()
    tasks.add_task(con, "答应要问她考试结果", who="爸爸")
    con.commit()
    bot = Self(con, bot_name="鲸鲸")
    ctx = bot.context("qq_private_1")
    assert "答应要问她考试结果" in (ctx.get("tasks") or ""), ctx.get("tasks")


def main() -> int:
    print("端到端：称呼反应 + 实时记忆")
    for name, fn in [
        ("禁称进 prompt", test_wrong_name_hits_prompt),
        ("语气递进", test_escalation_in_prompt),
        ("feed 记录叫错", test_live_feed_records_address),
        ("feed 实时捕获任务", test_feed_captures_task_realtime),
        ("feed 实时捕获共同经历", test_shared_experience_realtime),
        ("任务进 act prompt", test_task_reaches_act_prompt),
        ("任务进 think 上下文", test_task_reaches_think_prompt),
    ]:
        check(name, fn)
    print(f"\n{ok} 通过 / {fail} 失败")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
