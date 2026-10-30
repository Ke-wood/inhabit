"""把记忆落成可读可改的文件。

    记忆/
      人设卡.md          ← L1 人格，改了重启生效
      我自己的记忆.md    ← 她自己记的东西（看了什么、想记住什么）
      我/
        某人.md          ← 按人分的档案，可编辑，会同步回库
      index.md           ← 目录

路径按角色现算（见 _mem()）：运行时 --profile 切角色后，读写立刻跟着走，
不再用 import 时算死的那一份。
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path

from core.paths import ROOT
from memory import store


def _mem_dir() -> "Path":
    """角色隔离：每个角色的记忆在自己的目录里。"""
    try:
        from core import config as _cfg
        return ROOT / (_cfg.profile().get("memory") or "记忆")
    except Exception:
        return ROOT / "记忆"


def _mem() -> Path:
    return _mem_dir()


def _persona_card() -> Path:
    return _mem() / "人设卡.md"


def _self_memo() -> Path:
    return _mem() / "我自己的记忆.md"


def _people() -> Path:
    return _mem() / "我"


def _index() -> Path:
    return _mem() / "index.md"


def _sync_state() -> Path:
    return _mem() / ".sync.json"


def _trace_path() -> Path:
    return _mem() / "她刚收到了什么.md"


# 兼容别名：外部（tools/ctl.py、tools/prompt_review.py）仍按模块常量引用。
# 内部一律走上面的函数，别在新代码里用它们。
MEM = _mem()
PERSONA_CARD = _persona_card()
SELF_MEMO = _self_memo()
PEOPLE = _people()
INDEX = _index()
SYNC_STATE = _sync_state()
TRACE = _trace_path()

SELF_MEMO_BUDGET = 520      # 比人设卡（约 300 字）略大

# 上次导出时的最大 fact id。只在「导出过的行在文件里消失」时才算用户删了记忆：
# 导出之后新抽出来的事实永远不动，否则每次启动都会把新事实误判成删除。
WATERMARK_KEY = "memfiles_export_max_id"


def write_trace(scene: str, incoming: str, system: str, user: str) -> None:
    """只保留最近一轮，看"这一刻她收到了什么"。不累积，所以不会膨胀。"""
    ensure()
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    where = "群里" if scene.endswith("@chatroom") else "私聊"
    _trace_path().write_text(
        f"# 她刚收到了什么\n\n> {stamp} · {where} · 触发消息：{incoming[:60]}\n\n"
        f"## system（{len(system)} 字）\n\n```\n{system}\n```\n\n"
        f"## user（{len(user)} 字）\n\n```\n{user}\n```\n",
        encoding="utf-8")

SELF_HEADER = """# 我自己的记忆

> 我想记住的事。看过的视频、突然想明白的、想以后接着聊的。
> 这个文件我自己会往里写，你也可以加。程序只保留最近的一批。
"""


def ensure() -> None:
    _mem().mkdir(parents=True, exist_ok=True)
    _people().mkdir(parents=True, exist_ok=True)
    if not _self_memo().exists():
        _self_memo().write_text(SELF_HEADER, encoding="utf-8")


# ------------------------------------------------------------------ 自己的记忆
def read_self_memo(limit_chars: int = SELF_MEMO_BUDGET) -> str:
    ensure()
    text = _self_memo().read_text(encoding="utf-8")
    body = "\n".join(l for l in text.splitlines()
                     if l.strip() and not l.startswith(("#", ">")))
    return body[-limit_chars:]


def append_self_memo(line: str, *, keep_chars: int = SELF_MEMO_BUDGET * 2) -> None:
    """她往自己的记忆里加一条。太久远的会被挤掉，不是删除文件。"""
    ensure()
    p = _self_memo()
    text = p.read_text(encoding="utf-8")
    if line in text:
        return
    stamp = datetime.now().strftime("%m-%d %H:%M")
    entries = [l for l in text.splitlines() if l.strip() and not l.startswith(("#", ">"))]
    entries.append(f"- {stamp} {line}")
    # 只保留最近的，按字符预算
    kept, total = [], 0
    for e in reversed(entries):
        total += len(e) + 1
        if total > keep_chars:
            break
        kept.append(e)
    kept.reverse()
    p.write_text(SELF_HEADER + "\n" + "\n".join(kept) + "\n", encoding="utf-8")


# ------------------------------------------------------------------ 人物档案
def _fact_block(name: str, wxid: str, rows: list[sqlite3.Row]) -> str:
    lines = [f"# {name}", f"<!-- wxid: {wxid} -->", "",
             "> 改文字 = 改记忆；加一行 `[新] 类别：内容` = 新增；删掉整行 = 忘掉。", ""]
    for r in rows:
        lines.append(f"- [f{r['id']}] {r['predicate']}：{r['object_text']}")
    lines += ["", f"<!-- 同步 {datetime.now():%Y-%m-%d %H:%M} -->", ""]
    return "\n".join(lines)


def _watermark(con: sqlite3.Connection) -> int | None:
    """上次导出时的最大 fact id。拿不到（首次运行/旧库）返回 None。"""
    try:
        row = con.execute("SELECT value FROM self_state WHERE key=?",
                          (WATERMARK_KEY,)).fetchone()
        if row and row[0] not in (None, ""):
            return int(row[0])
    except (sqlite3.Error, TypeError, ValueError):
        return None
    return None


def export_people(con: sqlite3.Connection) -> int:
    """把库里的档案写到文件。返回写了几个人。"""
    ensure()
    wxids = json.loads((ROOT / "data" / "wx_names.json").read_text(encoding="utf-8")) \
        if (ROOT / "data" / "wx_names.json").exists() else {}
    back = {v.get("name"): k for k, v in (wxids.get("people") or {}).items()}

    rows = con.execute(
        """SELECT e.id, e.name, COUNT(f.id) AS n FROM entities e
           LEFT JOIN facts f ON f.subject_id = e.id AND f.disputed = 0
           WHERE e.etype = 'person' AND e.name NOT IN ('阿鱼')
           GROUP BY e.id ORDER BY n DESC""").fetchall()

    written = 0
    people_dir = _people()
    for e in rows:
        facts = con.execute(
            """SELECT id, predicate, object_text FROM facts
               WHERE subject_id=? AND disputed=0 ORDER BY predicate, id""", (e["id"],)).fetchall()
        if not facts:
            continue
        path = people_dir / f"{e['name']}.md"
        path.write_text(_fact_block(e["name"], back.get(e["name"], ""), facts), encoding="utf-8")
        written += 1
    _write_index(con, rows)

    # 记水位：这之后新抽出来的 fact 不在文件里，下次导入绝不能当成"用户删了"
    try:
        max_id = int(con.execute("SELECT COALESCE(MAX(id), 0) FROM facts").fetchone()[0])
        store.set_state(con, WATERMARK_KEY, str(max_id))
        con.commit()
    except sqlite3.Error:
        pass
    return written


def _write_index(con: sqlite3.Connection, rows) -> None:
    lines = ["# 记忆", "",
             "| 人 | 事实条数 |", "|---|---|"]
    for r in rows:
        lines.append(f"| [{r['name']}](我/{r['name']}.md) | {r['n']} |")
    lines += ["", f"事件条目：{con.execute('SELECT COUNT(*) FROM events').fetchone()[0]} 条",
              f"日记：{con.execute('SELECT COUNT(*) FROM reflections').fetchone()[0]} 篇", ""]
    _index().write_text("\n".join(lines), encoding="utf-8")


LINE = re.compile(r"^- \[(?:f(\d+)|新)\]\s*([^：:]{1,8})[：:]\s*(.+)$")


def import_people(con: sqlite3.Connection, *, forget: bool = True) -> dict:
    """读文件里的改动，写回库。返回 {updated, added, forgotten}。

    forget=False 时只做新增与修改，绝不判删除 —— 启动路径用这个，
    免得文件还没导出过就把库里的记忆标成 disputed。
    删除判定还要求 fid <= 上次导出的水位：导出后新抽的事实不在文件里属正常。
    """
    ensure()
    state = {}
    sync_file = _sync_state()
    if sync_file.exists():
        try:
            state = json.loads(sync_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            state = {}

    # 水位拿不到（首次运行/旧库）就一律不判删除，只做增改
    watermark = _watermark(con) if forget else None
    stats = {"updated": 0, "added": 0, "forgotten": 0}
    for path in _people().glob("*.md"):
        name = path.stem
        mtime = path.stat().st_mtime
        if mtime <= state.get(name, 0):
            continue
        ent = con.execute("SELECT id FROM entities WHERE etype='person' AND name=?", (name,)).fetchone()
        if not ent:
            continue
        sid = ent["id"]
        existing = {r["id"]: r for r in con.execute(
            "SELECT id, predicate, object_text FROM facts WHERE subject_id=? AND disputed=0", (sid,))}

        seen = set()
        for line in path.read_text(encoding="utf-8").splitlines():
            m = LINE.match(line.strip())
            if not m:
                continue
            fid, cat, text = m.group(1), m.group(2).strip(), m.group(3).strip()
            if fid:
                fid = int(fid)
                seen.add(fid)
                old = existing.get(fid)
                if old and (old["predicate"] != cat or old["object_text"] != text):
                    con.execute("UPDATE facts SET predicate=?, object_text=? WHERE id=?", (cat, text, fid))
                    stats["updated"] += 1
            else:
                con.execute(
                    """INSERT INTO facts(subject_id, predicate, object_text, confidence, evidence, created_at)
                       VALUES (?,?,?,?,?,?)""",
                    (sid, cat, text, 0.7, "[]", datetime.now().strftime("%Y-%m-%dT%H:%M:%S")))
                stats["added"] += 1

        # 文件里没了的 = 忘掉。只在明确允许删除、且这条属于"导出过的那一批"时才算。
        if forget and watermark is not None:
            for fid in existing:
                if fid not in seen and fid <= watermark:
                    con.execute("UPDATE facts SET disputed=1 WHERE id=?", (fid,))
                    stats["forgotten"] += 1
        state[name] = mtime

    con.commit()
    sync_file.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    return stats
