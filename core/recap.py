"""醒来只看最近一段，想看得更多再往前翻。

不是把几个小时的聊天一次性倒给她——那样她会淹掉，也不像人。
"""

from __future__ import annotations

import json
import pathlib
import sqlite3

from core.paths import ROOT
CURSOR = ROOT / "data" / "recap.json"

DEFAULT_WINDOW = 30


def _load() -> dict:
    if not CURSOR.exists():
        return {}
    try:
        return json.loads(CURSOR.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _save(d: dict) -> None:
    CURSOR.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")


def rows_between(con: sqlite3.Connection, scene: str, since_ts: str) -> list[dict]:
    return [dict(r) for r in con.execute(
        """SELECT id, ts, actor_name, text FROM events
           WHERE scene=? AND kind='message' AND ts > ? ORDER BY ts, id""",
        (scene, since_ts))]


def take_window(items: list[dict], n: int = DEFAULT_WINDOW) -> tuple[list[dict], int]:
    """取最近 n 条，返回 (窗口, 还有多少更早的)。"""
    if len(items) <= n:
        return items, 0
    return items[-n:], len(items) - n


def remember_cursor(scene: str, oldest_shown_ts: str, remaining: int) -> None:
    d = _load()
    d[scene] = {"before_ts": oldest_shown_ts, "remaining": remaining}
    _save(d)


def remaining(scene: str) -> int:
    return int(_load().get(scene, {}).get("remaining", 0))


def more(con: sqlite3.Connection, scene: str, n: int = DEFAULT_WINDOW) -> list[dict]:
    """往前再翻 n 条，并推进游标。"""
    cur = _load().get(scene)
    if not cur:
        return []
    before = cur.get("before_ts") or "9999"
    rows = [dict(r) for r in con.execute(
        """SELECT id, ts, actor_name, text FROM events
           WHERE scene=? AND kind='message' AND ts < ? ORDER BY ts DESC, id DESC LIMIT ?""",
        (scene, before, n))]
    rows.reverse()
    if rows:
        left = max(0, int(cur.get("remaining", 0)) - len(rows))
        remember_cursor(scene, rows[0]["ts"], left)
    return rows


def format_lines(rows: list[dict]) -> list[str]:
    return [f"{r['actor_name'] or '某人'}：{r['text'][:40]}" for r in rows]
