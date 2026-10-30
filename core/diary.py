"""日记：每晚把今天写成第一人称的一段。

不是日报，不总结收获。写不出来就可以写"今天没什么意思"。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime

from core.brain import Brain
from memory import store

SYSTEM = """你在写自己的日记。第一人称，三到五句，像人随手记下的。

只写今天真的看到、听到的事。可以冷淡，可以只写一句，可以抱怨。
不要总结收获，不要励志，不要分点，不要提到"作为AI"。
不要编造没发生过的事。"""


def _today_prefix(now: datetime) -> str:
    return now.strftime("%Y-%m-%d")


def already_written(con: sqlite3.Connection, now: datetime) -> bool:
    row = con.execute(
        "SELECT id FROM reflections WHERE period='day' AND ts LIKE ? LIMIT 1",
        (_today_prefix(now) + "%",),
    ).fetchone()
    return row is not None


def should_write(con: sqlite3.Connection, now: datetime) -> bool:
    """深夜,或者第二天凌晨补写。"""
    if now.hour not in (23, 0, 1):
        return False
    return not already_written(con, now)


def write(con: sqlite3.Connection, brain: Brain, now: datetime) -> str | None:
    day = _today_prefix(now)
    rows = con.execute(
        """SELECT id, actor_name, text FROM events
           WHERE kind='message' AND ts LIKE ? AND actor_name NOT IN ('阿鱼','群里')
           ORDER BY importance DESC, ts DESC LIMIT 14""",
        (day + "%",),
    ).fetchall()
    if len(rows) < 3:
        return None

    facts = con.execute(
        """SELECT e.name, f.predicate, f.object_text FROM facts f
           JOIN entities e ON e.id=f.subject_id
           WHERE f.created_at LIKE ? LIMIT 10""", (day + "%",)
    ).fetchall()

    lines = [f"{r['actor_name']}：{r['text']}" for r in rows]
    extra = ""
    if facts:
        extra = "\n\n今天新记住的：" + "；".join(f"{f['name']}{f['object_text']}" for f in facts)

    text = brain.speak(system=SYSTEM, user="今天群里/私聊里发生的事：\n" + "\n".join(lines) + extra + "\n\n写今天的日记。")
    text = (text or "").strip()
    if not text:
        return None
    store.add_reflection(con, text, period="day",
                         derived_from=[r["id"] for r in rows],
                         mood=store.get_state(con).get("mood"))
    con.commit()
    return text
