"""连续思考测试：每轮带上上一轮的念头 + 工具回来后先冒一句。

    python tests/test_recall_thought.py

要验的四件事：
1. 老库（没有 outcome 列）能自动补列，旧念头不丢
2. 上一轮办完的念头，下一轮 think 看得见（这是「不再反复念叨同一件事」的根）
3. 工具结果回来后，act 会多跑一次「只是冒一句」的思考，且它的话进了决策 prompt
4. 反应次数有上限，不会一次行动里无限加调用
"""

from __future__ import annotations

import os
import pathlib
import sys
import tempfile

TMP = pathlib.Path(tempfile.mkdtemp(prefix="inhabit-recall-"))
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


def _db(with_outcome: bool = True):
    """建一个库；with_outcome=False 时模拟老库（thoughts 没有 outcome 列）。"""
    import sqlite3
    from memory import store

    path = TMP / f"t{ok}{fail}{int(with_outcome)}.db"
    if path.exists():
        path.unlink()
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    schema = (ROOT / "memory" / "schema.sql").read_text(encoding="utf-8")
    if not with_outcome:
        # 老库：把 outcome 列抠掉，模拟升级前的样子
        schema = schema.replace(
            "    outcome    TEXT,                               "
            "-- 这个念头最后落到哪儿（说了什么/查到什么/没做成）\n", "")
    con.executescript(schema)
    store._migrate(con)
    con.commit()
    return con


# ---------------------------------------------------------------- 1. 老库补列
def test_migrate_adds_outcome():
    con = _db(with_outcome=False)
    cols = {r[1] for r in con.execute("PRAGMA table_info(thoughts)")}
    assert "outcome" in cols, f"补列失败：{cols}"
    con.close()


def test_migrate_keeps_old_rows():
    """补列不能把旧数据洗掉 —— 这是升级时最容易出事的地方。"""
    from memory import store
    con = _db(with_outcome=False)
    # 老库里已有的一条念头（模拟升级前留下的）
    con.execute("INSERT INTO thoughts(ts, kind, content, urge, status, created_at,"
                " updated_at) VALUES ('2026-01-01T00:00:00','reply','旧念头',0.5,"
                "'pending','2026-01-01T00:00:00','2026-01-01T00:00:00')")
    con.commit()
    store._migrate(con)
    rows = store.open_thoughts(con, 5)
    assert len(rows) == 1, rows
    assert rows[0]["content"] == "旧念头"
    assert rows[0]["outcome"] is None
    con.close()


# ---------------------------------------------------------------- 2. 上一轮可见
def test_recent_thoughts_hides_pending():
    """只取「已收场」的 —— 还没做的由 open_thoughts 那条线给，两边不能混。"""
    from memory import store
    con = _db()
    a = store.add_thought(con, "还没做的", urge=0.9)
    b = store.add_thought(con, "已经做完的", urge=0.5)
    store.set_thought_status(con, b, "done", outcome="说了「我先去吃饭」")
    con.commit()
    got = store.recent_thoughts(con, 5)
    ids = [r["id"] for r in got]
    assert b in ids, f"办完的念头没取到：{ids}"
    assert a not in ids, f"还没做的念头不该进「上一轮」：{ids}"
    assert got[0]["outcome"] == "说了「我先去吃饭」"
    con.close()


def test_outcome_survives_status_change():
    from memory import store
    con = _db()
    tid = store.add_thought(con, "想查一件事", urge=0.6)
    store.set_thought_status(con, tid, "done", outcome="没搜到")
    store.set_thought_status(con, tid, "done")      # 再关一次，不该抹掉结局
    con.commit()
    row = con.execute("SELECT outcome FROM thoughts WHERE id=?", (tid,)).fetchone()
    assert row["outcome"] == "没搜到", row["outcome"]
    con.close()


def test_last_thoughts_text_format():
    """Self 上露出的那段文字：要带「后来」，不带编号噪声。"""
    from core.self import Self
    from memory import store
    con = _db()
    tid = store.add_thought(con, "想弄清楚格斗游戏", urge=0.7)
    store.set_thought_status(con, tid, "done", outcome="搜到 3 条")
    con.commit()
    bot = Self(con, bot_name="阿鱼")
    text = bot.last_thoughts_text(2)
    assert "想弄清楚格斗游戏" in text, text
    assert "后来：搜到 3 条" in text, text
    con.close()


def test_empty_when_nothing_happened():
    from core.self import Self
    con = _db()
    bot = Self(con, bot_name="阿鱼")
    assert bot.last_thoughts_text(2) == ""
    con.close()


# ---------------------------------------------------------------- 3. 工具后反应
def _fake_brain(reply="这跟我以为的不一样"):
    class Fake:
        def __init__(self):
            self.calls = []

        def speak(self, *, system, user, avoid=()):
            self.calls.append({"system": system, "user": user})
            if "你心里第一句是什么" in user:
                return reply
            return '{"action":"wait","reason":"看完了"}'

    return Fake()


def test_react_returns_one_line():
    from core.act import react
    from core.self import Self
    con = _db()
    bot = Self(con, bot_name="阿鱼")
    brain = _fake_brain("  这跟我以为的不一样  ")
    line = react(bot, brain, what="搜了一下", content="- 标题\n  摘要", thought=None)
    assert line == "这跟我以为的不一样", repr(line)
    con.close()


def test_react_failure_is_silent():
    """反应这一层挂了，不能把整个动作带崩。"""
    from core.act import react
    from core.self import Self
    con = _db()
    bot = Self(con, bot_name="阿鱼")

    class Boom:
        def speak(self, **kw):
            raise RuntimeError("模型挂了")

    assert react(bot, Boom(), what="搜了一下", content="x") == ""
    con.close()


def test_react_strips_quotes():
    from core.act import react
    from core.self import Self
    con = _db()
    bot = Self(con, bot_name="阿鱼")
    line = react(bot, _fake_brain("「有点意思」"), what="看了", content="x")
    assert line == "有点意思", repr(line)
    con.close()


def test_act_prompt_gets_reaction():
    """走一遍真的 act：search_web 之后，反应那句话必须出现在后续 prompt 里。"""
    from core.act import act
    from core.mind import Thought
    from core.self import Self

    con = _db()
    bot = Self(con, bot_name="阿鱼")

    rows = [{"title": "格斗游戏是什么", "snippet": "边狱巴士的格斗游戏叫…",
             "url": "https://example.com/a"}]

    class Brain:
        def __init__(self):
            self.prompts = []
            self.n = 0

        def speak(self, *, system, user, avoid=()):
            self.prompts.append(user)
            if "你心里第一句是什么" in user:
                return "原来真有格斗游戏"
            self.n += 1
            if self.n == 1:
                return '{"action":"search_web","query":"边狱巴士 格斗游戏"}'
            return '{"action":"wait","reason":"知道了"}'

    import core.web as webmod
    old_search = webmod.search
    webmod.search = lambda q, limit=5: rows
    try:
        brain = Brain()
        r = act(bot, brain, Thought(content="想知道格斗游戏是什么", plan="search_web"),
                scene="qq_group_1", max_steps=3)
        assert any("原来真有格斗游戏" in p for p in brain.prompts), \
            f"反应没进 prompt：{brain.prompts}"
        assert r["outcome"], "outcome 是空的"
    finally:
        webmod.search = old_search
        con.close()


def test_outcome_reads_naturally():
    from core.act import _outcome
    assert _outcome(["我先去吃饭"], []) == "说了「我先去吃饭」"
    got = _outcome([], ["搜到 3 条：x", "说了：好"])
    assert "搜到 3 条" in got
    assert _outcome([], []) == ""
    # 只说一遍，别复读
    got = _outcome([], ["搜到 2 条：a", "搜到 2 条：a"])
    assert got.count("搜到 2 条") == 1, got


def test_outcome_on_wait():
    from core.act import _outcome
    assert "没说" in _outcome([], ["没说：不想说"])


# ---------------------------------------------------------------- 4. 调用上限
def test_react_cap():
    """_MAX_REACT 必须是个小数字 —— 它直接乘在模型账单上。"""
    from core import act as A
    assert 1 <= A._MAX_REACT <= 3, A._MAX_REACT


def test_react_not_used_for_say():
    """只在「工具回来了」时补反应；单纯 say 不该多花钱。"""
    import inspect
    from core import act as A
    src = inspect.getsource(A.act)
    # react(...) 只在工具分支里出现，say / wait / sleep 这些不该有
    assert src.count("= react(") == 3, f"反应调用点数量不对：{src.count('= react(')}"
    for tool in ("search_web", "fetch", "browse_bili"):
        seg = src.split(f'if action == "{tool}":')[1].split("if action ==")[0]
        assert "react(" in seg, f"{tool} 分支没有补反应"


def main():
    check("老库自动补 outcome 列", test_migrate_adds_outcome)
    check("补列不丢旧数据", test_migrate_keeps_old_rows)
    check("上一轮只取已收场念头", test_recent_thoughts_hides_pending)
    check("重复关闭不抹掉结局", test_outcome_survives_status_change)
    check("带「后来」的文字格式", test_last_thoughts_text_format)
    check("什么都没发生时为空", test_empty_when_nothing_happened)
    check("反应只取一行", test_react_returns_one_line)
    check("反应失败静默", test_react_failure_is_silent)
    check("反应去掉引号", test_react_strips_quotes)
    check("反应进了下一轮 prompt", test_act_prompt_gets_reaction)
    check("outcome 读起来像话", test_outcome_reads_naturally)
    check("wait 也有 outcome", test_outcome_on_wait)
    check("反应次数有上限", test_react_cap)
    check("say 不多花调用", test_react_not_used_for_say)
    print(f"\n{ok} 通过 / {fail} 失败")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
