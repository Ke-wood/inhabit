"""称呼 + 任务/共同经历的实时性测试。

    python tests/test_address_tasks.py

核心验收：**写进去必须立刻能读到**，不能等后台 janitor。
"""

from __future__ import annotations

import os
import pathlib
import shutil
import sys
import tempfile
import time

TMP = pathlib.Path(tempfile.mkdtemp(prefix="inhabit-at-"))
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


def _fresh_db():
    """全新的库 + 记忆目录（带称呼规则）。"""
    from memory import store
    db = TMP / f"t{time.time_ns()}.db"
    con = store.init_db(db)
    mem = TMP / "记忆"
    mem.mkdir(parents=True, exist_ok=True)
    (mem / "称呼.md").write_text(
        "# 称呼\n\n## 可以这么叫我\n\n- 鲸鲸\n- 小鲸鱼\n\n"
        "## 别这么叫我\n\n- 大肥鱼\n- 肥鱼\n- 死鱼\n\n"
        "## 被叫错了，我怎么办\n\n先纠正，别翻脸。\n",
        encoding="utf-8")
    (TMP / "config.json").write_text(
        '{"active":"鲸鲸","profiles":{"鲸鲸":'
        '{"dir":"data","memory":"记忆","persona":"我是谁.md"}}}', encoding="utf-8")
    from core import config as cfg
    cfg._cache.clear()
    cfg._mtime = 0.0
    return con


# ------------------------------------------------------------------ 称呼
def test_detect():
    from core import address
    _fresh_db()
    hit = address.detect("大肥鱼你在吗", bot_names=("鲸鲸", "小鲸鱼"))
    assert hit and hit["hit"] == "大肥鱼", hit
    assert address.detect("鲸鲸你在吗", bot_names=("鲸鲸",)) is None
    assert address.detect("今天天气不错", bot_names=("鲸鲸",)) is None


def test_liked_not_flagged():
    """正名和别名不能被当成「叫错」。"""
    from core import address
    _fresh_db()
    for name in ("鲸鲸", "小鲸鱼"):
        assert address.detect(f"{name}辛苦了", bot_names=("鲸鲸", "小鲸鱼")) is None, name


def test_count_escalates():
    from core import address
    con = _fresh_db()
    for i in (1, 2, 3):
        n = address.bump_wrong(con, "大肥鱼")
        assert n == i, n
    hit = {"hit": "大肥鱼"}
    first = address.describe(hit, 1)
    third = address.describe(hit, 3)
    assert "第一次" in first, first
    assert "第 3 次" in third, third
    assert first != third, "三档语气没有区别"


def test_no_rules_file():
    """没有称呼文件时不能崩。"""
    from core import address
    con = _fresh_db()
    (TMP / "记忆" / "称呼.md").unlink()
    assert address.detect("大肥鱼", bot_names=("鲸鲸",)) is None
    assert address.load()["hated"] == []


# ------------------------------------------------------------------ 任务
def test_task_detected():
    from core import tasks
    con = _fresh_db()
    assert tasks.looks_like_task("我明天提醒你吃药")
    assert tasks.looks_like_task("记得提醒我交作业")
    assert not tasks.looks_like_task("嗯")
    assert not tasks.looks_like_task("今天天气不错")


def test_task_realtime():
    """写完立刻读得到 —— 这是核心要求。"""
    from core import tasks
    con = _fresh_db()
    tid = tasks.add_task(con, "明天叫她起床", who="爸爸", when="明早七点")
    assert tid > 0, tid
    rows = tasks.open_tasks(con)
    assert rows and rows[0]["id"] == tid, rows
    assert "明天叫她起床" in rows[0]["text"]
    assert "明早七点" in rows[0]["text"]
    txt = tasks.tasks_text(con)
    assert f"#{tid}" in txt, txt


def test_task_separate_from_loops():
    """任务和「悬着的疑问」不能混。"""
    from core import tasks
    from memory import store
    con = _fresh_db()
    t_id = tasks.add_task(con, "答应帮她查资料", who="某人")
    l_id = store.add_loop(con, "那个游戏到底怎么玩")
    tx = tasks.tasks_text(con)
    assert f"#{t_id}" in tx, tx
    assert f"#{l_id}" not in tx, "疑问混进了任务列表"
    loops = [r["id"] for r in store.loops_with_ids(con, 10)]
    assert l_id in loops, loops


def test_capture_writes_both():
    from core import tasks
    con = _fresh_db()
    r = tasks.capture(con, "我明天帮你看看那个软件", who="爸爸")
    assert r["task"] > 0, r
    assert tasks.open_tasks(con), "任务没写进去"


# ------------------------------------------------------------------ 共同经历
def test_shared_realtime():
    from core import tasks
    con = _fresh_db()
    sid = tasks.add_shared(con, "一起看了那个修自行车的视频", who="爸爸")
    assert sid > 0, sid
    txt = tasks.shared_text(con, "爸爸")
    assert "修自行车" in txt, txt


def test_shared_detected():
    from core import tasks
    assert tasks.looks_like_shared("我们一起看了个视频")
    assert tasks.looks_like_shared("你帮我改的代码")
    assert not tasks.looks_like_shared("嗯")
    con = _fresh_db()
    r = tasks.capture(con, "我们一起看了那个视频", who="爸爸")
    assert r["shared"] > 0, r


def test_shared_quota_reaches_prompt():
    """共同经历要真的进上下文，且不会被别的分类挤掉。"""
    from core.self import Self
    from core import tasks
    con = _fresh_db()
    sid = __import__("memory.store", fromlist=["store"]).ensure_entity(con, "爸爸")
    from memory import store
    store.add_fact(con, sid, "身份", "养我的那个人")
    store.add_fact(con, sid, "喜好", "喜欢熬夜")
    for i in range(3):
        tasks.add_shared(con, f"一起做过第{i}件事", who="爸爸")
    con.commit()
    bot = Self(con, bot_name="鲸鲸")
    from core.loop import Msg
    bot.observe(Msg("u1", "爸爸", "在吗", time.time()), "qq_private_1")
    txt = bot.facts_here("qq_private_1", limit=20)
    n = txt.count("一起做过")
    assert n >= 2, f"共同经历只进了 {n} 条：\n{txt}"


def test_stats():
    from core import tasks
    con = _fresh_db()
    tasks.add_task(con, "答应的事", who="某人")
    tasks.add_shared(con, "一起看过东西", who="某人")
    s = tasks.stats(con)
    assert s["tasks_open"] >= 1, s
    assert s["shared"] >= 1, s


def main() -> int:
    print("称呼 + 任务/共同经历")
    for name, fn in [
        ("识别被叫错名字", test_detect),
        ("正名不算叫错", test_liked_not_flagged),
        ("三次语气递进", test_count_escalates),
        ("没有规则文件不崩", test_no_rules_file),
        ("任务触发词", test_task_detected),
        ("任务实时可读", test_task_realtime),
        ("任务与疑问分开", test_task_separate_from_loops),
        ("一句话同时抽任务", test_capture_writes_both),
        ("共同经历实时可读", test_shared_realtime),
        ("共同经历触发词", test_shared_detected),
        ("共同经历进上下文", test_shared_quota_reaches_prompt),
        ("统计", test_stats),
    ]:
        check(name, fn)
    print(f"\n{ok} 通过 / {fail} 失败")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
