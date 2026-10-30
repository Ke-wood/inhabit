"""睡觉。

规则：
- 睡前她自己决定几点醒。
- 睡着期间一律不回复、不主动说话、不调模型；消息照常落库。
- 到点自己醒；醒来要看一眼睡着期间发生了什么。
- 人工叫醒：`python tools/sleep.py wake`
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timedelta

from core.brain import Brain
from memory import store

KEYS = ("asleep", "sleep_started", "sleep_until", "wake_reason", "sleep_decided_on")

DECIDE_SYSTEM = """现在是深夜。你在决定要不要睡、睡到几点。

只输出 JSON，不要解释：
{"sleep": true, "wake": "07:30", "reason": "困了"}

- 想睡就 sleep=true，wake 是明天几点（24 小时制 HH:MM）
- 不想睡就 sleep=false
- 别睡超过 10 小时
- 说话随意一点，reason 一句话"""


def _get(con: sqlite3.Connection) -> dict:
    return store.get_state(con)


def asleep(con: sqlite3.Connection, now: datetime | None = None) -> bool:
    now = now or datetime.now()
    st = _get(con)
    if st.get("asleep") != "1":
        return False
    until = st.get("sleep_until")
    if not until:
        return False
    try:
        return now < datetime.fromisoformat(until)
    except ValueError:
        return False


def until(con: sqlite3.Connection) -> datetime | None:
    v = _get(con).get("sleep_until")
    try:
        return datetime.fromisoformat(v) if v else None
    except ValueError:
        return None


def go_to_sleep(con: sqlite3.Connection, wake_at: datetime, reason: str = "") -> None:
    store.set_state(con, "asleep", "1")
    store.set_state(con, "sleep_started", datetime.now().strftime("%Y-%m-%dT%H:%M:%S"))
    store.set_state(con, "sleep_until", wake_at.strftime("%Y-%m-%dT%H:%M:%S"))
    store.set_state(con, "wake_reason", reason)
    con.commit()


def wake(con: sqlite3.Connection, reason: str = "自己醒了") -> dict:
    st = _get(con)
    started = st.get("sleep_started")
    store.set_state(con, "asleep", "0")
    store.set_state(con, "wake_reason", reason)
    con.commit()

    since = started or (datetime.now() - timedelta(hours=8)).strftime("%Y-%m-%dT%H:%M:%S")
    rows = con.execute(
        """SELECT actor_name, text, ts, scene FROM events
           WHERE kind='message' AND ts > ? ORDER BY ts, id""", (since,)).fetchall()
    return {
        "slept_since": started,
        "messages": [dict(r) for r in rows],
        "count": len(rows),
    }


def decide(con: sqlite3.Connection, brain: Brain, now: datetime, hint: str = "",
           force: bool = False) -> dict | None:
    """让她自己决定睡不睡、几点醒。同一次夜深只问一次，最多三小时一次。"""
    st = _get(con)
    last = st.get("sleep_decided_on")
    if last and not force:
        try:
            if (now - datetime.fromisoformat(last)).total_seconds() < 3 * 3600:
                return None
        except ValueError:
            pass
    store.set_state(con, "sleep_decided_on", now.strftime("%Y-%m-%dT%H:%M:%S"))
    con.commit()

    extra = f"\n{hint}" if hint else ""
    raw = brain.speak(
        system=DECIDE_SYSTEM,
        user=f"现在是 {now.strftime('%H:%M')}。今天聊了不少，你也累了。睡不睡？{extra}",
        avoid=[])
    m = re.search(r"\{.*\}", raw or "", re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    if not d.get("sleep"):
        return {"sleep": False}
    try:
        hh, mm = str(d.get("wake", "08:00")).split(":")
        wake_dt = (now + timedelta(days=1)).replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
        if wake_dt <= now:
            wake_dt += timedelta(days=1)
        if wake_dt > now + timedelta(hours=10):
            wake_dt = now + timedelta(hours=10)
    except (ValueError, TypeError):
        wake_dt = (now + timedelta(days=1)).replace(hour=8, minute=0, second=0, microsecond=0)
    return {"sleep": True, "wake_at": wake_dt, "reason": str(d.get("reason") or "")[:40]}
