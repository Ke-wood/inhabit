"""记忆整理轮：把某个人的记忆摊给她看，让她自己改、删、合并。

    python tools/memory_review.py --list          # 看有哪些人、各多少条
    python tools/memory_review.py 某人             # 整理某人的（默认每轮 8 条）
    python tools/memory_review.py 某人 6           # 每轮 6 条

设计：一次只给一小把，让她一条条看。
她可以：
  改  —— 把这条写得准一点
  删  —— 这条记错了 / 不想留着
  加  —— 想起还有件事没记
什么都不做也行。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import persona                      # noqa: E402
from core.brain import make_brain             # noqa: E402
from memory import store                      # noqa: E402

SYSTEM = """{identity}

你在整理自己记得的事。下面是我记下的关于「{who}」的几条。

这些是你自己记的，现在回头看，可能有的记歪了、有的太啰嗦、有的已经不对了。
你可以：
- 改：把它写得更准（{"id": 编号, "text": "改成什么"}）
- 删：这条记错了，或者你不想留（{"id": 编号, "drop": true}）
- 加：想起还有件事没记（{"text": "要加什么"}）

不想动的就别写。改不动的地方也没关系。

只输出一个 JSON 对象，不要解释：
{"edit": [{"id": 1, "text": "..."}], "drop": [2], "add": ["..."], "done": false}

done 写 true 表示这个人你看完了，没别的要改。"""


def render_system(ident: str, who: str) -> str:
    # 不能用 str.format —— 里面全是 JSON 花括号
    return SYSTEM.replace("{identity}", ident).replace("{who}", who)


def pick_people(con) -> list[tuple[str, int]]:
    rows = con.execute(
        """SELECT e.name, COUNT(f.id) n FROM facts f
           JOIN entities e ON e.id = f.subject_id
           WHERE f.disputed = 0 AND f.superseded_by IS NULL
           GROUP BY e.id ORDER BY n DESC""").fetchall()
    return [(r["name"], r["n"]) for r in rows]


def parse_reply(raw: str) -> dict:
    text = (raw or "").strip()
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        # 截断了：补个右括号试试
        s = m.group(0)
        if s.count('"') % 2:
            s += '"'
        s = re.sub(r",\s*$", "", s)
        if not s.endswith("}"):
            s += "}"
        try:
            return json.loads(s)
        except json.JSONDecodeError:
            return {}


def run(who: str, batch: int = 8, max_rounds: int = 6) -> None:
    con = store.init_db()
    brain = make_brain("gateway")
    ident = persona.read_identity() or "十八岁，女的。"

    rows = con.execute(
        """SELECT f.id, f.predicate, f.object_text FROM facts f
           JOIN entities e ON e.id = f.subject_id
           WHERE e.name = ? AND f.disputed = 0 AND f.superseded_by IS NULL
           ORDER BY f.id""", (who,)).fetchall()
    if not rows:
        print(f"· 「{who}」没有记忆")
        return

    total = len(rows)
    print(f"=== {who}：共 {total} 条 ===")
    pos = 0
    for rnd in range(1, max_rounds + 1):
        chunk = rows[pos:pos + batch]
        if not chunk:
            print("  （看完了）")
            break
        lines = [f"  #{r['id']} [{r['predicate']}] {r['object_text']}" for r in chunk]
        user = (
            "关于「%s」的记忆，这是第 %d~%d 条（一共 %d 条，后面还有）。\n\n%s\n\n"
            "这一批要不要改？只输出 JSON。done 只在最后一批写 true。"
            % (who, pos + 1, pos + len(chunk), total, "\n".join(lines))
        )
        try:
            raw = brain.speak(system=render_system(ident, who), user=user)
        except Exception as e:
            print(f"  调用失败：{type(e).__name__}: {e}")
            break
        print(f"\n--- 第 {rnd} 批（第 {pos+1}~{pos+len(chunk)} 条 / 共 {total}）---")
        print("  她说:", (raw or "").strip()[:220])
        cmd = parse_reply(raw)
        changed = 0
        for e in (cmd.get("edit") or []):
            if not isinstance(e, dict):
                continue
            fid, txt = e.get("id"), str(e.get("text") or "").strip()
            if fid and txt:
                con.execute("UPDATE facts SET object_text = ? WHERE id = ?", (txt[:120], int(fid)))
                print(f"    改 #{fid} → {txt[:40]}")
                changed += 1
        for fid in (cmd.get("drop") or []):
            if isinstance(fid, (int, str)) and str(fid).isdigit():
                con.execute("UPDATE facts SET disputed = 1 WHERE id = ?", (int(fid),))
                print(f"    删 #{fid}")
                changed += 1
        for a in (cmd.get("add") or []):
            txt = str(a or "").strip()
            if txt:
                eid = store.ensure_entity(con, who)
                store.add_fact(con, eid, "记得", txt[:120])
                print(f"    加：{txt[:40]}")
                changed += 1
        con.commit()
        pos += len(chunk)
        if cmd.get("done") or changed == 0 and rnd > 1:
            if cmd.get("done"):
                print("  （她说看完了）")
                break

    print(f"\n=== 结束时：{who} 现有 {con.execute('SELECT COUNT(*) FROM facts f JOIN entities e ON e.id=f.subject_id WHERE e.name=? AND f.disputed=0 AND f.superseded_by IS NULL', (who,)).fetchone()[0]} 条 ===")
    con.close()


def main() -> None:
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        return
    if args[0] == "--list":
        con = store.init_db()
        for name, n in pick_people(con):
            print(f"  {name:16} {n} 条")
        con.close()
        return
    who = args[0]
    batch = int(args[1]) if len(args) > 1 else 8
    run(who, batch=batch)


if __name__ == "__main__":
    main()
