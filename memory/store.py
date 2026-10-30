"""阿鱼 记忆库：分词、写入、三路召回。

设计要点
- 中文按重叠 bigram 预分词写进 FTS5（unicode61），两字词也能召回。
- 召回 = 语义(cos) + 关键词(bm25) + 时间衰减 + 重要度，再乘关系加成。
- 返回每条的分数拆解，方便排查"它为什么想起这件事"。
"""

from __future__ import annotations

import json
import math
import re
import sqlite3
from array import array
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional, Sequence

from core.paths import ROOT


def _db_path() -> Path:
    """角色隔离：每个角色有自己的库，路径写在 config.json 里。"""
    try:
        from core import config as _cfg
        d = _cfg.profile().get("dir") or "data"
        return ROOT / d / "feiyu.db"
    except Exception:
        return ROOT / "data" / "feiyu.db"


DB_PATH = _db_path()
SCHEMA = Path(__file__).resolve().parent / "schema.sql"

# 召回权重（可调，总和 1.0）
W_COS, W_BM25, W_RECENCY, W_IMPORTANCE = 0.45, 0.20, 0.20, 0.15
RECENCY_HALF_LIFE_H = 24 * 7  # 一周半衰

_CJK = re.compile(r"[\u4e00-\u9fff]+")
_WORD = re.compile(r"[A-Za-z0-9_]+")


# ------------------------------------------------------------------ 分词
def seg(text: str) -> str:
    """中文 → 重叠 bigram；英文数字 → 整词。用于写 FTS 和查询。"""
    parts: list[str] = []
    for m in _CJK.finditer(text):
        s = m.group()
        if len(s) == 1:
            parts.append(s)
        else:
            parts.extend(s[i : i + 2] for i in range(len(s) - 1))
    parts.extend(w.lower() for w in _WORD.findall(text))
    return " ".join(parts)


def now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


# ------------------------------------------------------------------ 连接
def connect(db_path: Path | str = DB_PATH) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)   # 首次运行时 data/ 还不存在
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode = WAL")
    con.execute("PRAGMA foreign_keys = ON")
    # 空库（首次运行）直接把 schema 建上，省得每个工具都要先记得 init_db
    if not con.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0]:
        try:
            con.executescript(SCHEMA.read_text(encoding="utf-8"))
            con.commit()
        except sqlite3.Error:
            pass
    return con


def connect_ro(db_path: Path | str = DB_PATH) -> sqlite3.Connection:
    """只读连接 —— 面板这类旁观者用。

    普通连接即使只跑 SELECT，SQLite 也可能要拿共享锁；面板每几秒来一次，
    和主循环抢起来就是 database is locked。只读模式直接禁写，永不冲突。
    """
    if not Path(db_path).exists():
        return init_db(db_path)          # 还没建库：建一个空库给面板看
    p = Path(db_path).resolve().as_posix()
    con = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=5)
    con.row_factory = sqlite3.Row
    return con


def init_db(db_path: Path | str | None = None) -> sqlite3.Connection:
    # 不缓存：每次按当前角色算，切了角色也不会接到上一个的库
    if db_path is None:
        db_path = _db_path()
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    con = connect(db_path)
    con.executescript(SCHEMA.read_text(encoding="utf-8"))
    _migrate(con)
    con.commit()
    return con


def _migrate(con: sqlite3.Connection) -> None:
    """给老库补新列。只加列，不删数据 —— 旧记忆不能因为改代码就没了。"""
    try:
        cols = {r[1] for r in con.execute("PRAGMA table_info(facts)")}
        if "superseded_by" not in cols:
            con.execute("ALTER TABLE facts ADD COLUMN superseded_by INTEGER")
    except sqlite3.OperationalError:
        pass
    # 幂等键：同一条微信消息（msgid）只该入库一次
    try:
        con.execute("CREATE INDEX IF NOT EXISTS idx_events_source "
                    "ON events(channel, source_ref)")
    except sqlite3.OperationalError:
        pass


# ------------------------------------------------------------------ 写入
def add_event(
    con: sqlite3.Connection,
    text: str,
    *,
    scene: str,
    channel: str = "dsh",
    actor_id: str | None = None,
    actor_name: str | None = None,
    kind: str = "message",
    importance: int = 3,
    valence: float = 0.0,
    embedding: Sequence[float] | None = None,
    source_ref: str | None = None,
    ts: str | None = None,
) -> int:
    blob = array("f", embedding).tobytes() if embedding else None
    # 幂等：带 msgid 的消息重放（文件重读、崩溃重跑）时直接返回原 id，不写第二条
    if source_ref:
        row = con.execute(
            "SELECT id FROM events WHERE channel=? AND source_ref=? LIMIT 1",
            (channel, source_ref)).fetchone()
        if row:
            return int(row[0])
    cur = con.execute(
        """INSERT INTO events(ts, channel, scene, actor_id, actor_name, kind, text,
                              importance, valence, embedding, source_ref)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (ts or now_iso(), channel, scene, actor_id, actor_name, kind, text,
         importance, valence, blob, source_ref),
    )
    eid = int(cur.lastrowid)
    con.execute("INSERT INTO events_fts(rowid, text_seg) VALUES (?,?)", (eid, seg(text)))
    return eid


def set_state(con: sqlite3.Connection, key: str, value: str) -> None:
    con.execute(
        "INSERT INTO self_state(key,value,updated_at) VALUES (?,?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
        (key, value, now_iso()),
    )


def get_state(con: sqlite3.Connection) -> dict[str, str]:
    return {r["key"]: r["value"] for r in con.execute("SELECT key, value FROM self_state")}


def add_fact(con, subject_id: int | None, predicate: str, object_text: str,
             confidence: float = 0.6, evidence: Sequence[int] = ()) -> int:
    # 同一件事不重复记；已经有就返回旧 id
    row = con.execute(
        "SELECT id FROM facts WHERE subject_id IS ? AND predicate=? AND object_text=?",
        (subject_id, predicate, object_text)).fetchone()
    if row:
        return int(row[0])
    cur = con.execute(
        """INSERT INTO facts(subject_id, predicate, object_text, confidence, evidence, created_at)
           VALUES (?,?,?,?,?,?)""",
        (subject_id, predicate, object_text, confidence, json.dumps(list(evidence)), now_iso()),
    )
    return int(cur.lastrowid)


def add_reflection(con, text: str, period: str = "day",
                   derived_from: Sequence[int] = (), mood: str | None = None) -> int:
    cur = con.execute(
        "INSERT INTO reflections(ts, period, text, derived_from, mood) VALUES (?,?,?,?,?)",
        (now_iso(), period, text, json.dumps(list(derived_from)), mood),
    )
    return int(cur.lastrowid)


def bump_relation(con, src_id: int, dst_id: int, *, affinity: float = 0.0,
                  trust: float = 0.0, familiarity: float = 0.0,
                  label: str | None = None, notes: str | None = None) -> None:
    con.execute(
        """INSERT INTO relations(src_id,dst_id,affinity,trust,familiarity,label,notes,updated_at)
           VALUES (?,?,?,?,?,?,?,?)
           ON CONFLICT(src_id,dst_id) DO UPDATE SET
             affinity    = affinity    + excluded.affinity,
             trust       = max(0.0, min(1.0, trust + excluded.trust)),
             familiarity = min(1.0, familiarity + excluded.familiarity),
             label       = coalesce(excluded.label, label),
             notes       = coalesce(excluded.notes, notes),
             updated_at  = excluded.updated_at""",
        (src_id, dst_id, affinity, trust, familiarity, label, notes, now_iso()),
    )


def ensure_entity(con: sqlite3.Connection, name: str, etype: str = "person") -> int:
    name = (name or "").strip()
    if not name:
        name = "某人"
    row = con.execute("SELECT id FROM entities WHERE etype=? AND name=?", (etype, name)).fetchone()
    if row:
        con.execute("UPDATE entities SET last_seen=? WHERE id=?", (now_iso(), row["id"]))
        return int(row["id"])
    # 两处同时建同一个人时会撞唯一约束，用 OR IGNORE 然后重查
    con.execute(
        "INSERT OR IGNORE INTO entities(etype,name,first_seen,last_seen) VALUES (?,?,?,?)",
        (etype, name, now_iso(), now_iso()),
    )
    row = con.execute("SELECT id FROM entities WHERE etype=? AND name=?", (etype, name)).fetchone()
    return int(row["id"]) if row else 0


def person_card(
    con: sqlite3.Connection,
    names: Sequence[str],
    *,
    facts_per_person: int = 15,
    events_per_person: int = 6,
) -> list[dict[str, Any]]:
    """以人为载体的记忆卡：这个人的身份、经历、喜好、关系、最近说过什么。

    跨会话——不管是在群里还是私聊听到的，都归到这个人身上。
    """
    cards = []
    for name in dict.fromkeys(n for n in names if n):
        ent = con.execute(
            "SELECT id, aliases FROM entities WHERE etype='person' AND name=?", (name,)
        ).fetchone()
        if not ent:
            cards.append({"name": name, "aliases": [], "facts": {}, "recent": [], "rel": None,
                          "total_facts": 0})
            continue
        rel = con.execute(
            "SELECT affinity, familiarity, label FROM relations WHERE dst_id=?", (ent["id"],)
        ).fetchone()
        rows = con.execute(
            """SELECT predicate, object_text FROM facts
               WHERE subject_id=? AND disputed=0
               ORDER BY confidence DESC, id DESC LIMIT ?""",
            (ent["id"], facts_per_person),
        ).fetchall()
        grouped: dict[str, list[str]] = {}
        for r in rows:
            grouped.setdefault(r["predicate"] or "其他", []).append(r["object_text"])
        recent = con.execute(
            """SELECT text, ts FROM events
               WHERE actor_name=? AND kind='message' AND text NOT LIKE '@%'
               ORDER BY ts DESC LIMIT ?""",
            (name, events_per_person),
        ).fetchall()
        total = con.execute(
            "SELECT COUNT(*) FROM facts WHERE subject_id=? AND disputed=0", (ent["id"],)
        ).fetchone()[0]
        cards.append({
            "name": name,
            "aliases": [a for a in json.loads(ent["aliases"] or "[]") if not a.startswith("wxid_")],
            "facts": grouped,
            "recent": [r["text"] for r in recent],
            "rel": dict(rel) if rel else None,
            "total_facts": total,
        })
    return cards


def promote_event(con: sqlite3.Connection, event_id: int, *,
                  importance: int = 10) -> dict[str, Any] | None:
    """她把一条旧记忆收进「现在的记忆」：提到最高权重、取消归档。"""
    row = con.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
    if not row:
        return None
    con.execute("UPDATE events SET importance=?, archived=0 WHERE id=?", (importance, event_id))
    return dict(row)


def forget_event(con: sqlite3.Connection, event_id: int) -> dict[str, Any] | None:
    """她想忘掉的事：标记归档、权重压到底。检索不再出现，但数据不删。"""
    row = con.execute("SELECT id, text, actor_name, scene, ts FROM events WHERE id=?",
                      (event_id,)).fetchone()
    if not row:
        return None
    con.execute("UPDATE events SET archived=1, importance=1 WHERE id=?", (event_id,))
    return dict(row)


def dedupe_facts(con: sqlite3.Connection, *, threshold: float = 0.6) -> int:
    """同一主语下语义近似的事实，标出「被谁取代」，但不删除。

    记忆只增不删：删掉的事实她永远想不起来了，这比重复更糟。
    """
    rows = con.execute(
        "SELECT id, subject_id, object_text, confidence FROM facts "
        "WHERE superseded_by IS NULL ORDER BY subject_id, id").fetchall()
    groups: dict[int | None, list] = {}
    for r in rows:
        groups.setdefault(r["subject_id"], []).append(r)

    def contains(a: str, b: str) -> float:
        A, B = set(seg(a).split()), set(seg(b).split())
        if not A or not B:
            return 0.0
        return len(A & B) / min(len(A), len(B))

    marked = 0
    for items in groups.values():
        keep: list = []
        for it in items:
            dup = next((k for k in keep
                        if contains(k["object_text"], it["object_text"]) >= threshold), None)
            if not dup:
                keep.append(it)
                continue
            if it["confidence"] > dup["confidence"]:
                keep.remove(dup)
                con.execute("UPDATE facts SET superseded_by=? WHERE id=?", (it["id"], dup["id"]))
                keep.append(it)
            else:
                con.execute("UPDATE facts SET superseded_by=? WHERE id=?", (dup["id"], it["id"]))
            marked += 1
    return marked


def recall_facts(
    con: sqlite3.Connection,
    query: str,
    *,
    limit: int = 6,
    subject_names: Sequence[str] = (),
) -> list[dict[str, Any]]:
    """语义记忆召回：关于某人的稳定认知，跨会话共享。

    数据量小，直接内存里打分，不另建索引。
    """
    rows = con.execute(
        """SELECT f.id, f.predicate, f.object_text, f.confidence, e.name AS subject
           FROM facts f LEFT JOIN entities e ON e.id = f.subject_id
           WHERE f.disputed = 0 AND f.superseded_by IS NULL"""
    ).fetchall()
    terms = set(seg(query).split())
    who = {seg(n) for n in subject_names}
    scored: list[tuple[float, dict]] = []
    for r in rows:
        blob = f"{r['subject'] or ''}{r['predicate']}{r['object_text']}"
        overlap = len(terms & set(seg(blob).split()))
        bonus = 1.0 if (r["subject"] and any(n and n in seg(r["subject"] or "") for n in who)) else 0.0
        s = overlap * 1.0 + bonus + float(r["confidence"]) * 0.3
        if s > 0.5:
            scored.append((s, {"subject": r["subject"], "predicate": r["predicate"],
                               "object": r["object_text"], "confidence": r["confidence"]}))
    scored.sort(key=lambda x: -x[0])
    return [d for _, d in scored[:limit]]


# ------------------------------------------------------------------ 召回
def _cosine(a: array, b: array) -> float:
    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def _recency(ts: str, now: datetime) -> float:
    try:
        then = datetime.fromisoformat(ts)
    except ValueError:
        return 0.0
    hours = max(0.0, (now - then).total_seconds() / 3600)
    return math.exp(-hours / RECENCY_HALF_LIFE_H)


def recall(
    con: sqlite3.Connection,
    query: str,
    *,
    limit: int = 8,
    scene: str | None = None,
    boost_scene: str | None = None,
    embedding: Sequence[float] | None = None,
    actor_names: Sequence[str] = (),
    exclude_ids: Sequence[int] = (),
    candidate_cap: int = 60,
    mark: bool = True,
) -> list[dict[str, Any]]:
    """三路召回后融合打分。返回带 score_breakdown 的结果。

    记忆是跨会话共享的：默认不按 scene 过滤，只给当前会话一点加权。
    scene 参数仍然保留，传了就是"只看这个会话"。
    exclude_ids：刚写进去的消息要排除，否则"想起的"永远是眼下这句。
    actor_names：在场的人说过的话，用来捞"陈年旧事"。
    """
    now = datetime.now()
    excl = set(exclude_ids)
    cands: dict[int, dict[str, Any]] = {}

    # 1) 语义
    if embedding:
        qv = array("f", embedding)
        where = ("embedding IS NOT NULL AND archived = 0"
                 + (" AND scene = ?" if scene else ""))
        rows = con.execute(f"SELECT * FROM events WHERE {where} ORDER BY ts DESC LIMIT 500",
                           (scene,) if scene else ()).fetchall()
        for r in rows:
            if r["id"] in excl:
                continue
            c = _cosine(qv, array("f", r["embedding"]))
            if c > 0.2:
                cands[r["id"]] = {"row": r, "cos": c, "bm25": 0.0}

    # 2) 关键词
    q = seg(query)
    terms = [t for t in q.split() if t]
    if terms:
        # bigram 之间用 OR：整句都命中才算会把长问题直接搜空
        # （"某人想去西藏" 里的"某人想"原文根本没有，AND 必然失败）
        match = " OR ".join(f'"{t}"' for t in terms)
        sql = ("SELECT e.*, bm25(events_fts) AS rank FROM events_fts "
               "JOIN events e ON e.id = events_fts.rowid "
               "WHERE events_fts MATCH ? AND e.archived = 0"
               + (" AND e.scene = ?" if scene else "") +
               " ORDER BY rank LIMIT ?")
        try:
            rows = con.execute(sql, (match, scene, candidate_cap) if scene
                               else (match, candidate_cap)).fetchall()
        except sqlite3.OperationalError:
            rows = []
        rows = [r for r in rows if r["id"] not in excl]
        if rows:
            ranks = [abs(r["rank"]) for r in rows]
            lo, hi = min(ranks), max(ranks)
            for r in rows:
                norm = 1.0 if hi == lo else 1.0 - (abs(r["rank"]) - lo) / (hi - lo)
                slot = cands.setdefault(r["id"], {"row": r, "cos": 0.0, "bm25": 0.0})
                slot["bm25"] = norm

    # 3) 实体（提到的人说过的话）
    if actor_names:
        marks = ",".join("?" * len(actor_names))
        for r in con.execute(
            f"SELECT * FROM events WHERE actor_name IN ({marks}) AND archived = 0 "
            f"ORDER BY importance DESC, ts DESC LIMIT {candidate_cap}",
            tuple(actor_names),
        ):
            if r["id"] in excl:
                continue
            cands.setdefault(r["id"], {"row": r, "cos": 0.0, "bm25": 0.0})["entity"] = True

    if not cands:
        return []

    out: list[dict[str, Any]] = []
    for eid, c in cands.items():
        r = c["row"]
        rec = _recency(r["ts"], now)
        imp = (r["importance"] or 3) / 10.0
        score = (W_COS * c.get("cos", 0.0) + W_BM25 * c.get("bm25", 0.0)
                 + W_RECENCY * rec + W_IMPORTANCE * imp)
        if c.get("entity"):
            score *= 1.15
        if boost_scene and r["scene"] == boost_scene:
            score *= 1.15        # 本会话优先，但不是只看本会话
        out.append({
            "id": eid, "ts": r["ts"], "scene": r["scene"], "actor": r["actor_name"],
            "kind": r["kind"], "text": r["text"], "importance": r["importance"],
            "score": round(score, 4),
            "breakdown": {"cos": round(c.get("cos", 0.0), 3), "bm25": round(c.get("bm25", 0.0), 3),
                          "recency": round(rec, 3), "importance": round(imp, 2),
                          "entity": bool(c.get("entity"))},
        })
    out.sort(key=lambda x: x["score"], reverse=True)
    out = out[:limit]

    if mark and out:
        ts = now_iso()
        con.executemany("UPDATE events SET recalled_at=?, recall_count=recall_count+1 WHERE id=?",
                        [(ts, x["id"]) for x in out])
    return out


def decay(con: sqlite3.Connection, *, min_importance: int = 4, days: int = 30) -> int:
    """低重要度且长期未被召回的，归档（不删除）。"""
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S")
    cur = con.execute(
        "UPDATE events SET archived=1 WHERE archived=0 AND importance < ? AND ts < ? "
        "AND (recalled_at IS NULL OR recalled_at < ?)",
        (min_importance, cutoff, cutoff),
    )
    return cur.rowcount


# ------------------------------------------------------------------ 念头 v2
def add_thought(con, content: str, *, kind: str = "reply", target: str | None = None,
                urge: float = 0.5, origin: str | None = None,
                scene: str | None = None) -> int:
    ts = now_iso()
    cur = con.execute(
        "INSERT INTO thoughts(ts, kind, content, target, urge, status, origin, scene,"
        " created_at, updated_at) VALUES (?,?,?,?,?,'pending',?,?,?,?)",
        (ts, kind, content, target, max(0.0, min(1.0, float(urge))), origin, scene, ts, ts))
    return int(cur.lastrowid)


def open_thoughts(con, limit: int = 5) -> list[dict[str, Any]]:
    rows = con.execute(
        "SELECT * FROM thoughts WHERE status IN ('pending','acting') "
        "ORDER BY urge DESC, ts DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


def set_thought_status(con, tid: int, status: str) -> None:
    con.execute("UPDATE thoughts SET status=?, updated_at=? WHERE id=?",
                (status, now_iso(), tid))


def drop_stale_thoughts(con, keep: int = 20) -> int:
    """活跃念头超上限：丢掉最弱最老的 pending，防止无限堆积。"""
    n = con.execute(
        "SELECT COUNT(*) FROM thoughts WHERE status IN ('pending','acting')").fetchone()[0]
    if n <= keep:
        return 0
    cur = con.execute(
        "UPDATE thoughts SET status='dropped', updated_at=? WHERE id IN "
        "(SELECT id FROM thoughts WHERE status='pending' ORDER BY urge ASC, ts ASC LIMIT ?)",
        (now_iso(), n - keep))
    return cur.rowcount


# ------------------------------------------------------------------ 悬着的事
def _zh_only(s: str) -> str:
    """只留中文 —— 判断「是不是同一件事」时，网址和英文名会稀释重合度。"""
    return re.sub(r"[^\u4e00-\u9fff]+", " ", s or "")


def add_loop(con, text: str, *, target: str | None = None, strength: float = 0.5,
             source: str | None = None) -> int:
    """开一条「悬着的事」。措辞不同的同一件事，合并成一条，不重复堆。"""
    ts = now_iso()
    text = (text or "").strip()[:60]
    if not text:
        return 0
    row = con.execute(
        "SELECT id FROM open_loops WHERE closed_at IS NULL AND text=?", (text,)).fetchone()
    if row:                                  # 完全一样：不重复开，只加深
        con.execute("UPDATE open_loops SET strength=MAX(strength,?), updated_at=? WHERE id=?",
                    (strength, ts, row[0]))
        return int(row[0])

    # 措辞不同但说的是同一件事 —— 例如「链接是谁发的」的十几种问法。
    # 只比中文部分（网址、英文名会稀释重合度），bigram 重合够高就当同一条。
    new_terms = set(seg(_zh_only(text)).split())
    if new_terms:
        for r in con.execute(
                "SELECT id, text FROM open_loops WHERE closed_at IS NULL").fetchall():
            old_terms = set(seg(_zh_only(r["text"] or "")).split())
            if not old_terms:
                continue
            overlap = len(new_terms & old_terms) / min(len(new_terms), len(old_terms))
            if overlap >= 0.5:
                con.execute(
                    "UPDATE open_loops SET strength=MAX(strength,?), updated_at=? WHERE id=?",
                    (strength, ts, r["id"]))
                return int(r["id"])

    cur = con.execute(
        "INSERT INTO open_loops(text,target,strength,source,created_at,updated_at)"
        " VALUES (?,?,?,?,?,?)", (text, target, strength, source, ts, ts))
    return int(cur.lastrowid)


def open_loops(con, limit: int = 5) -> list[dict[str, Any]]:
    rows = con.execute(
        "SELECT * FROM open_loops WHERE closed_at IS NULL "
        "ORDER BY strength DESC, updated_at DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


def close_loop(con, lid: int) -> None:
    ts = now_iso()
    con.execute("UPDATE open_loops SET closed_at=?, updated_at=? WHERE id=?", (ts, ts, lid))


def resolve_loop(con, lid: int, answer: str = "") -> dict[str, Any] | None:
    """想明白了：关掉这件事，顺手记下答案。知道答案就不该再挂在头顶。"""
    row = con.execute(
        "SELECT id, text FROM open_loops WHERE id=? AND closed_at IS NULL",
        (lid,)).fetchone()
    if not row:
        return None
    close_loop(con, lid)
    if answer:
        con.execute("UPDATE open_loops SET source=? WHERE id=?",
                    (f"答案：{answer[:80]}", lid))
    return {"id": row["id"], "text": row["text"], "answer": answer}


def abandon_loop(con, lid: int, reason: str = "") -> dict[str, Any] | None:
    """不想了：不是弄明白，是决定不再惦记。答案可以永远没有。"""
    row = con.execute(
        "SELECT id, text FROM open_loops WHERE id=? AND closed_at IS NULL",
        (lid,)).fetchone()
    if not row:
        return None
    close_loop(con, lid)
    con.execute("UPDATE open_loops SET source=? WHERE id=?",
                (f"不想了：{reason[:80]}" if reason else "不想了", lid))
    return {"id": row["id"], "text": row["text"], "reason": reason}


def loops_with_ids(con, limit: int = 5) -> list[dict[str, Any]]:
    """带编号的悬着的事，给 prompt 用 —— 她要用编号去 resolve / drop。"""
    rows = con.execute(
        "SELECT id, text, strength, created_at FROM open_loops WHERE closed_at IS NULL "
        "ORDER BY strength DESC, updated_at DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


# ------------------------------------------------------------------ 搜到的事
# 她自己上网查到的，先放这儿，最多 4 条。
# 记不记进长期记忆由她自己定，不用当场定 —— 可以先去问人，隔几拍再回来决定。
# 超过 4 条还在 open 的，最旧的挤出去：一直没定，等于没看上。
POOL_KEEP = 4

POOL_DDL = """CREATE TABLE IF NOT EXISTS search_pool (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         TEXT NOT NULL,
    query      TEXT NOT NULL,
    title      TEXT NOT NULL,
    digest     TEXT,
    url        TEXT,
    state      TEXT NOT NULL DEFAULT 'open',
    decided_at TEXT
)"""


def _ensure_pool(con) -> None:
    """老库没这张表：用到时补上，不依赖 init_db。"""
    try:
        con.execute(POOL_DDL)
    except sqlite3.OperationalError:
        pass


def _flat(s: str, n: int) -> str:
    """压成一行，去掉换行和多余空格。这里存的是给人看的话，不能过 seg()。"""
    return " ".join(str(s or "").split())[:n]


def pool_add(con, query: str, title: str, digest: str = "", url: str = "",
             *, keep: int = POOL_KEEP) -> dict[str, Any]:
    _ensure_pool(con)
    cur = con.execute(
        "INSERT INTO search_pool(ts, query, title, digest, url, state) VALUES (?,?,?,?,?,'open')",
        (now_iso(), _flat(query, 60), _flat(title, 90), _flat(digest, 200), (url or "")[:200]),
    )
    pid = int(cur.lastrowid)
    opens = [int(r[0]) for r in con.execute(
        "SELECT id FROM search_pool WHERE state='open' ORDER BY id DESC").fetchall()]
    squeezed = []
    for old in opens[keep:]:
        con.execute("UPDATE search_pool SET state='dropped', decided_at=? WHERE id=?",
                    (now_iso(), old))
        squeezed.append(old)
    return {"id": pid, "squeezed": squeezed}


def pool_open(con, limit: int = POOL_KEEP) -> list[dict[str, Any]]:
    _ensure_pool(con)
    rows = con.execute(
        "SELECT * FROM search_pool WHERE state='open' ORDER BY id LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


def pool_keep(con, pid: int, *, scene: str = "inner",
              actor_name: str = "") -> dict[str, Any] | None:
    """她决定记住这条：写进 events（以后 recall 捞得到），然后从暂存区删掉。"""
    _ensure_pool(con)
    row = con.execute("SELECT * FROM search_pool WHERE id=? AND state='open'", (pid,)).fetchone()
    if not row:
        return None
    text = f"我查过：{row['title']}"
    if row["digest"]:
        text += f" —— {row['digest']}"
    if row["url"]:
        text += f"（{row['url']}）"
    eid = add_event(con, text, scene=scene, channel="self", kind="action",
                    actor_name=actor_name or None, importance=4)
    con.execute("UPDATE search_pool SET state='kept', decided_at=? WHERE id=?",
                (now_iso(), pid))
    return {"id": pid, "event_id": eid, "text": text}


def pool_drop(con, pid: int) -> dict[str, Any] | None:
    _ensure_pool(con)
    row = con.execute("SELECT * FROM search_pool WHERE id=? AND state='open'", (pid,)).fetchone()
    if not row:
        return None
    con.execute("UPDATE search_pool SET state='dropped', decided_at=? WHERE id=?",
                (now_iso(), pid))
    return {"id": pid, "title": row["title"]}


# ------------------------------------------------------------------ 自测
if __name__ == "__main__":
    tmp = DB_PATH.parent / "_selftest.db"
    if tmp.exists():
        tmp.unlink()
    con = init_db(tmp)
    add_event(con, "我不想上班了，最近状态很差", scene="g1", actor_id="1", actor_name="甲", importance=7)
    add_event(con, "啊？那你想干嘛", scene="g1", actor_id="2", actor_name="乙", kind="message")
    add_event(con, "今天看了个修自行车的视频，好看", scene="bili", channel="bilibili", kind="observation", importance=5)
    con.commit()

    for q in ["考研", "上班", "驴蹄子", "完全不相关的东西"]:
        hits = recall(con, q, limit=3)
        print(f"[{q}] -> " + ("; ".join(f"{h['text'][:12]}…({h['score']})" for h in hits) or "（无）"))

    assert any("考研" in h["text"] for h in recall(con, "考研")), "两字词召回失败"
    assert any("驴蹄子" in h["text"] for h in recall(con, "驴蹄子")), "长词召回失败"
    print("state:", get_state(con))
    print("OK")
