"""任务与共同经历：随时能写、随时能读，不用等后台整理。

问题
----
原来的链路是：

    对话 → （等 30 分钟）→ janitor 抽事实 → 库 → 下一轮上下文

「共同经历」（他给我做过什么、我们一起经历过什么）尤其吃亏：
它只占每轮配额里的 1 条，还要跟一堆别的分类抢位置。
于是出现「明明昨天一起做过某件事，今天完全想不起来」。

这里的做法
----------
1. **即时写**：说完话就抽，不再等 janitor。轻量、只在有触发词时才跑。
2. **任务单独一条线**：答应过的事（「我明天提醒你」）跟「悬而未决的好奇」
   不是一回事 —— 前者是承诺，后者是疑问。分开存，分开提醒。
3. **共同经历给足配额**，且不按 id 排（新写的反而该先看到）。

存储
----
- 任务：`open_loops` 表里加 `kind='task'` 区分（复用现有表，不造新表）
- 共同经历：`facts` 表的 `predicate='共同经历'`（复用现有分类）
"""

from __future__ import annotations

import re
import time

from memory import store

# 触发词：出现这些词，说明这句话里可能有「答应过的事」或「一起做过的事」
TASK_HINTS = ("我明天", "我待会", "我等下", "我一会儿", "回头我", "我帮你",
              "记得提醒我", "别忘了", "答应你", "说好了", "下次", "改天",
              "我这就", "我马上", "保证")
SHARED_HINTS = ("我们一起", "我和你", "咱们", "我们俩", "你给我", "我给你",
                "你帮我", "我帮你", "一起看", "一起吃", "一起去", "陪我看",
                "陪我", "陪你")

# 太短或太模糊的，不值得记
_TOO_VAGUE = re.compile(r"^(那|这|它|他|她|哦|嗯|啊|好)+[。！？…]*$")


def looks_like_task(text: str) -> bool:
    t = (text or "").strip()
    if len(t) < 4 or _TOO_VAGUE.match(t):
        return False
    return any(h in t for h in TASK_HINTS)


def looks_like_shared(text: str) -> bool:
    t = (text or "").strip()
    if len(t) < 4 or _TOO_VAGUE.match(t):
        return False
    return any(h in t for h in SHARED_HINTS)


def _clip(s: str, n: int = 40) -> str:
    s = re.sub(r"\s+", " ", (s or "").strip())
    return s[:n]


# ------------------------------------------------------------------ 任务
def add_task(con, text: str, *, who: str | None = None, when: str = "") -> int:
    """记一件「答应过、还没做」的事。

    who：对谁的承诺；when：什么时候（自由文本，比如「明天」「今晚」）。
    """
    body = _clip(text, 50)
    if not body:
        return 0
    label = f"{body}" + (f"（{when}）" if when else "")
    return store.add_loop(con, label, target=who, strength=0.75, source="task")


def open_tasks(con, limit: int = 5) -> list[dict]:
    """还没做完的任务。和「悬着的疑问」分开拿。"""
    try:
        rows = con.execute(
            "SELECT * FROM open_loops WHERE closed_at IS NULL AND source='task' "
            "ORDER BY updated_at DESC, id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def tasks_text(con, limit: int = 5) -> str:
    rows = open_tasks(con, limit)
    if not rows:
        return ""
    return "\n".join(f"- #{r['id']} {r['text']}" for r in rows)


# ------------------------------------------------------------------ 共同经历
def add_shared(con, text: str, *, who: str, evidence: int | None = None) -> int:
    """记一条「我们一起经历过的事」。第一人称写。"""
    body = _clip(text, 60)
    if not body:
        return 0
    eid = store.ensure_entity(con, who) if who else None
    return store.add_fact(con, eid, "共同经历", body, confidence=0.85,
                          evidence=[evidence] if evidence else [])


def shared_text(con, who: str, limit: int = 6) -> str:
    """某个人的共同经历，最新的在前。"""
    try:
        rows = con.execute(
            "SELECT f.object_text FROM facts f JOIN entities e ON e.id = f.subject_id "
            "WHERE e.name=? AND f.predicate='共同经历' AND f.disputed=0 "
            "AND f.superseded_by IS NULL ORDER BY f.id DESC LIMIT ?",
            (who, limit)).fetchall()
        return "\n".join(f"- {r['object_text']}" for r in rows)
    except Exception:
        return ""


# ------------------------------------------------------------------ 即时抽取
def capture(con, text: str, *, who: str = "", ts: float | None = None) -> dict:
    """一句话进来，顺手看有没有值得立刻记下的任务/共同经历。

    不调 LLM —— 只靠词面特征。宁可漏，不可误记一堆废话。
    返回 {"task": id|0, "shared": id|0}
    """
    out = {"task": 0, "shared": 0}
    t = (text or "").strip()
    if not t:
        return out
    try:
        if looks_like_task(t):
            out["task"] = add_task(con, t, who=who or None)
        if who and looks_like_shared(t):
            out["shared"] = add_shared(con, t, who=who)
        if out["task"] or out["shared"]:
            con.commit()
    except Exception:
        pass
    return out


def stats(con) -> dict:
    """面板/测试用。"""
    def _n(sql, *args):
        try:
            return int(con.execute(sql, args).fetchone()[0])
        except Exception:
            return 0
    return {
        "tasks_open": _n("SELECT count(*) FROM open_loops "
                         "WHERE closed_at IS NULL AND source='task'"),
        "shared": _n("SELECT count(*) FROM facts WHERE predicate='共同经历' AND disputed=0"),
        "loops_open": _n("SELECT count(*) FROM open_loops "
                         "WHERE closed_at IS NULL AND (source IS NULL OR source!='task')"),
    }
