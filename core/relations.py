"""关系值：好感不是数字游戏，只由真实事件驱动。

规则（先做启发式，够用再谈模型打分）：
- 每说一句：熟悉度 +0.01（只涨不跌）
- 她回应了：好感 +0.3
- 被夸：好感 +2
- 被敷衍（"哦""嗯""。"）：好感 -0.5
- 被凶（"闭嘴""滚""烦"）：好感 -1.5
- 长期没互动：不回涨，也不掉（人不会因为不说话就变仇人）
"""

from __future__ import annotations

import sqlite3

from memory import store

PRAISE = ("喜欢", "爱你", "聪明", "好孩子", "厉害", "谢谢", "可爱", "真棒", "牛")
DISMISS_SHORT = {"哦", "嗯", "。", "？", "啊", "行", "好", "随便", "随你"}
HARSH = ("闭嘴", "滚", "烦", "别说了", "傻", "蠢", "讨厌")

SELF_NAME = "阿鱼"


def _self(con: sqlite3.Connection) -> int:
    return store.ensure_entity(con, SELF_NAME)


def label_of(affinity: float, familiarity: float) -> str:
    if affinity >= 30:
        return "很在意"
    if affinity >= 10:
        return "挺喜欢"
    if affinity <= -10:
        return "不太对付"
    if familiarity > 0.6:
        return "老熟人"
    return "还行"


def touch(con: sqlite3.Connection, name: str, *, replied: bool = False, text: str = "") -> dict:
    """根据一次互动更新关系。返回更新后的关系。

    熟悉度和好感是两回事：
    - 熟悉度：聊得多就涨，跟喜不喜欢无关
    - 好感：只由「被怎么对待」决定 —— 被夸会涨、被凶会掉；
      「回了一句话」不再加好感（那只是聊得多，不代表更喜欢）
    好感真正的归属由她自己 feel 决定，这里只做被动的加减。
    """
    if not name or name in ("某人", "群里", SELF_NAME):
        return {}
    src, dst = _self(con), store.ensure_entity(con, name)
    row = con.execute("SELECT * FROM relations WHERE src_id=? AND dst_id=?", (src, dst)).fetchone()
    aff = row["affinity"] if row else 0.0
    fam = row["familiarity"] if row else 0.0

    t = (text or "").strip()
    d_aff = 0.0
    if any(w in t for w in PRAISE):
        d_aff += 2.0
    if t in DISMISS_SHORT:
        d_aff -= 0.5
    if any(w in t for w in HARSH):
        d_aff -= 1.5

    aff = max(-100.0, min(100.0, aff + d_aff))
    fam = min(1.0, fam + 0.01)
    con.execute(
        """INSERT INTO relations(src_id,dst_id,affinity,trust,familiarity,label,updated_at)
           VALUES (?,?,?,?,?,?,?)
           ON CONFLICT(src_id,dst_id) DO UPDATE SET
             affinity=excluded.affinity, familiarity=excluded.familiarity,
             label=excluded.label, updated_at=excluded.updated_at""",
        (src, dst, aff, 0.5, fam, label_of(aff, fam), store.now_iso()),
    )
    return {"name": name, "affinity": round(aff, 1), "familiarity": round(fam, 2),
            "label": label_of(aff, fam), "delta": round(d_aff, 2)}


def adjust(con: sqlite3.Connection, name: str, *, delta: float | None = None,
           set_to: float | None = None) -> dict:
    """她自己调对某个人的好感 —— 不靠启发式，是她自己的判断。

    delta：加/减多少；set_to：直接定成多少。两个都不给就等于没变。
    """
    if not name or name in ("某人", "群里", SELF_NAME, "阿鱼"):
        return {}
    src, dst = _self(con), store.ensure_entity(con, name)
    row = con.execute("SELECT * FROM relations WHERE src_id=? AND dst_id=?",
                      (src, dst)).fetchone()
    old = float(row["affinity"]) if row else 0.0
    fam = float(row["familiarity"]) if row else 0.0
    aff = float(set_to) if set_to is not None else old + float(delta or 0.0)
    aff = max(-100.0, min(100.0, aff))
    con.execute(
        """INSERT INTO relations(src_id,dst_id,affinity,trust,familiarity,label,updated_at)
           VALUES (?,?,?,?,?,?,?)
           ON CONFLICT(src_id,dst_id) DO UPDATE SET
             affinity=excluded.affinity, label=excluded.label, updated_at=excluded.updated_at""",
        (src, dst, aff, 0.5, fam, label_of(aff, fam), store.now_iso()),
    )
    return {"name": name, "old": round(old, 1), "affinity": round(aff, 1),
            "label": label_of(aff, fam), "delta": round(aff - old, 1)}


def snapshot(con: sqlite3.Connection, limit: int = 8) -> list[dict]:
    rows = con.execute(
        """SELECT e.name, r.affinity, r.familiarity, r.label
           FROM relations r JOIN entities e ON e.id = r.dst_id
           ORDER BY r.affinity DESC, r.familiarity DESC LIMIT ?""", (limit,)).fetchall()
    return [dict(r) for r in rows]
