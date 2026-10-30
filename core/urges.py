"""想说话的冲动。

人不是定时器。心里攒着一件事，越想越想说；有些念头过几个小时就淡了，
有些憋一整天反而更想说。所以这里不排时间表，只排队"想说的由头"。
"""

from __future__ import annotations

import math
import sqlite3
from datetime import datetime, timedelta

from memory import store

HALF_LIFE_H = 6.0        # 冲动半衰期：6 小时后强度减半
MEAN_INTERVAL_MIN = 12   # 强度 1.0 时，平均 12 分钟会憋不住一次
KINDS = ("video", "followup", "person", "boredom", "thought", "share")


def add(con: sqlite3.Connection, kind: str, *, note: str, about: str = "",
        strength: float = 0.6, scene: str | None = None,
        ttl_hours: float | None = None) -> int:
    now = datetime.now()
    expires = (now + timedelta(hours=ttl_hours)).strftime("%Y-%m-%dT%H:%M:%S") if ttl_hours else None
    cur = con.execute(
        """INSERT INTO urges(kind, about, note, strength, scene, created_at, expires_at)
           VALUES (?,?,?,?,?,?,?)""",
        (kind, about, note, max(0.0, min(1.0, strength)), scene,
         now.strftime("%Y-%m-%dT%H:%M:%S"), expires),
    )
    return int(cur.lastrowid)


def _decayed(strength: float, created_at: str, now: datetime) -> float:
    try:
        age_h = (now - datetime.fromisoformat(created_at)).total_seconds() / 3600
    except ValueError:
        return 0.0
    return strength * math.exp(-age_h / HALF_LIFE_H * math.log(2))


def top(con: sqlite3.Connection, now: datetime | None = None) -> dict | None:
    now = now or datetime.now()
    rows = con.execute(
        """SELECT * FROM urges WHERE used_at IS NULL
           AND (expires_at IS NULL OR expires_at > ?)
           ORDER BY created_at DESC LIMIT 40""",
        (now.strftime("%Y-%m-%dT%H:%M:%S"),),
    ).fetchall()
    best, best_s = None, 0.0
    for r in rows:
        s = _decayed(float(r["strength"]), r["created_at"], now)
        if s > best_s:
            best, best_s = dict(r), s
    if not best or best_s < 0.08:
        return None
    best["now_strength"] = round(best_s, 3)
    return best


def mark_used(con: sqlite3.Connection, urge_id: int) -> None:
    con.execute("UPDATE urges SET used_at=? WHERE id=?",
                (datetime.now().strftime("%Y-%m-%dT%H:%M:%S"), urge_id))


def prune(con: sqlite3.Connection, days: int = 3) -> int:
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S")
    return con.execute("DELETE FROM urges WHERE created_at < ? AND (used_at IS NOT NULL OR expires_at < ?)",
                       (cutoff, cutoff)).rowcount


def has_recent(con: sqlite3.Connection, kind: str, hours: float = 2.0) -> bool:
    since = (datetime.now() - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%S")
    return con.execute("SELECT 1 FROM urges WHERE kind=? AND created_at > ? LIMIT 1",
                       (kind, since)).fetchone() is not None


# ------------------------------------------------------------------ 造念头
def from_video(con: sqlite3.Connection, title: str, reaction: str) -> None:
    add(con, "video", note=f"刚看了《{title[:20]}》，想说：{reaction[:30]}", strength=0.8,
        ttl_hours=8)


def from_fact(con: sqlite3.Connection, who: str, fact: str) -> None:
    """带"约定感"的事实会变成想追问的念头。"""
    if any(w in fact for w in ("明天", "回头", "答应", "约", "下次", "等", "再说")):
        add(con, "followup", about=who, note=f"{who}说过：{fact[:30]}，想问一句",
            strength=0.55, ttl_hours=48)


def from_person(con: sqlite3.Connection, name: str, hours: float) -> None:
    add(con, "person", about=name, note=f"{name}好一阵没说话了（{hours:.0f}小时），有点想问问",
        strength=0.5, ttl_hours=12)


def from_boredom(con: sqlite3.Connection) -> None:
    add(con, "boredom", note="没事干，有点闷", strength=0.35, ttl_hours=4)


def from_share(con: sqlite3.Connection, note: str, *, about: str = "",
               to: str | None = None, strength: float = 0.75) -> None:
    """分享欲：看到点东西、想到点东西，想找个人说说。

    不是提问，也不是求助，就是"哎你看"。to 为空表示当前会话。
    """
    add(con, "share", note=note, about=about, strength=strength, scene=to, ttl_hours=12)
