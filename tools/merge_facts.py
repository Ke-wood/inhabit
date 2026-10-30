"""把某个人的档案做语义合并：同一件事的多种说法并成一条。

    python tools/merge_facts.py 某人
    python tools/merge_facts.py --all
"""

from __future__ import annotations

import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import memfiles
from core.brain import make_brain
from memory import store

SYSTEM = """你在整理一个人的档案。下面同一个人的事实里有很多是同一件事的不同说法。

任务：合并同类项，保留全部不同信息，去掉重复表述。
- 只输出 JSON 数组：[{"cat":"身份|经历|喜好|关系|观点|共同经历","fact":"..."}]
- 提到"阿鱼"时写"阿鱼"，不要写"我/对方/聊天对象/机器人/它"
- 每条 25 字以内
- 不要新增原文没有的信息"""


def merge_one(con, brain, name: str) -> tuple[int, int]:
    ent = con.execute("SELECT id FROM entities WHERE etype='person' AND name=?", (name,)).fetchone()
    if not ent:
        return 0, 0
    rows = con.execute(
        """SELECT id, predicate, object_text FROM facts
           WHERE subject_id=? AND disputed=0 ORDER BY predicate""", (ent["id"],)).fetchall()
    if len(rows) < 4:
        return 0, 0

    body = "\n".join(f"- [{r['predicate']}] {r['object_text']}" for r in rows)
    raw = brain.speak(system=SYSTEM, user=f"{name} 的档案：\n{body}", avoid=[])
    m = re.search(r"\[.*\]", raw or "", re.S)
    if not m:
        print(f"  {name}: 模型没给出可用结果")
        return 0, 0
    try:
        merged = json.loads(m.group(0))
    except json.JSONDecodeError:
        print(f"  {name}: JSON 解析失败")
        return 0, 0

    old = len(rows)
    con.execute("UPDATE facts SET disputed=1 WHERE subject_id=? AND disputed=0", (ent["id"],))
    for f in merged:
        if not f.get("fact"):
            continue
        cat = str(f.get("cat") or "其他")
        if cat not in ("身份", "经历", "喜好", "关系", "观点", "共同经历"):
            cat = "其他"
        store.add_fact(con, ent["id"], cat, str(f["fact"])[:60], confidence=0.75)
    con.commit()
    return old, len(merged)


def main() -> None:
    con = store.init_db()
    # 合并要吐很多条，默认 200 tokens 不够
    brain = make_brain("gateway")
    if hasattr(brain, "max_tokens"):
        brain.max_tokens = 1600
    target = sys.argv[1] if len(sys.argv) > 1 else "--all"

    names = ([target] if target != "--all" else
             [r["name"] for r in con.execute(
                 """SELECT e.name, COUNT(f.id) c FROM entities e JOIN facts f ON f.subject_id=e.id
                    WHERE e.etype='person' AND f.disputed=0 GROUP BY e.id HAVING c >= 4""")])
    for n in names:
        old, new = merge_one(con, brain, n)
        print(f"  {n}: {old} 条 → {new} 条")
    memfiles.export_people(con)
    print("已回写文件")


if __name__ == "__main__":
    main()
