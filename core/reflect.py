"""把聊天沉淀成「关于某个人的事实」。

这是跨会话共享记忆的关键：事件带 scene（群里/私聊），事实不带，
所以私聊里知道的事，群里也想得起来。
"""

from __future__ import annotations

import json
import re
import sqlite3

from core.brain import Brain
from memory import store

SYSTEM = """你要把一段聊天记录沉淀成「自己记得的事」。

只提取「以后还用得上」的稳定事实，按类归：
- 身份：是谁、多大、做什么的
- 经历：他发生过的事、遭遇
- 喜好：喜欢/讨厌什么、习惯
- 关系：跟谁什么关系、怎么称呼
- 观点：稳定的看法、立场
- 共同经历：他为你做过的事、你们一起经历过的事。写成「给我做了X」「和我一起X」

不要提取：临时情绪、问句、玩笑话、天气。
每条 25 字以内。主语用对方的称呼（他叫什么就写什么）。
**提到你自己时用「我」**，比如「给我调了五分钟」「说我是人」。
不要写她自己的名字，也不要用「对方」「聊天对象」「机器人」这类代替。
只输出 JSON 数组，不要解释，不要代码块：

[{"who":"示例甲","cat":"经历","fact":"最近在准备一个重要考试"}]

没有值得记的就输出 []"""

MAX_PER_ROUND = 8
CATS = {"身份", "经历", "喜好", "关系", "观点", "共同经历"}


def _parse(raw: str) -> list[dict]:
    raw = (raw or "").strip()
    raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.M).strip()
    m = re.search(r"\[.*\]", raw, re.S)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return []
    out = []
    for d in data if isinstance(data, list) else []:
        if isinstance(d, dict) and d.get("who") and d.get("fact"):
            cat = str(d.get("cat") or "其他")
            out.append({"who": str(d["who"])[:20], "cat": cat if cat in CATS else "其他",
                        "fact": str(d["fact"])[:60]})
    return out[:MAX_PER_ROUND]


def extract(con: sqlite3.Connection, brain: Brain, *, since_id: int = 0,
            limit: int = 60) -> tuple[list[dict], int]:
    """从 since_id 之后的消息里抽事实。返回 (新事实列表, 本次处理到的最大 id)。"""
    rows = con.execute(
        """SELECT id, actor_name, text, scene FROM events
           WHERE id > ? AND kind = 'message' AND actor_name IS NOT NULL
             AND actor_name NOT IN ('阿鱼', '群里', '某人', '对方')
           ORDER BY id LIMIT ?""",
        (since_id, limit),
    ).fetchall()
    if not rows:
        return [], since_id

    lines = [f"{r['actor_name']}：{r['text']}" for r in rows]
    raw = brain.speak(system=SYSTEM, user="聊天记录：\n" + "\n".join(lines), avoid=[])
    facts = _parse(raw)

    added = []
    for f in facts:
        sid = store.ensure_entity(con, f["who"])
        dup = con.execute(
            "SELECT id FROM facts WHERE subject_id=? AND object_text=?", (sid, f["fact"])
        ).fetchone()
        if dup:
            continue
        store.add_fact(con, sid, f["cat"], f["fact"], confidence=0.7)
        added.append(f)
    store.dedupe_facts(con)
    con.commit()
    return added, max(r["id"] for r in rows)
