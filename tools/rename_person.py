"""把某个人的名字改掉：库里的实体、事件、念头、事实，以及记忆文件。

    python tools/rename_person.py 旧名 新名

旧名字会保留成 alias，这样旧对话里出现的旧称呼仍然能对上这个人。
"""

from __future__ import annotations

import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from memory import store  # noqa: E402

DATA = ROOT / "data"
MEM = ROOT / "记忆"


def main() -> None:
    if len(sys.argv) < 3:
        print(__doc__)
        return
    old, new = sys.argv[1].strip(), sys.argv[2].strip()
    if not old or not new or old == new:
        print("名字不对")
        return

    db = DATA / "feiyu.db"
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    bak = DATA / f"feiyu.before-{old}-to-{new}.{stamp}.db"
    shutil.copy(db, bak)
    print(f"· 已备份到 {bak.name}")

    con = store.init_db(db)
    report = {}

    # 1) 实体：改名，旧名进 aliases
    row = con.execute("SELECT id, aliases FROM entities WHERE name=?", (old,)).fetchone()
    if row:
        aliases = json.loads(row["aliases"] or "[]")
        if old not in aliases:
            aliases.append(old)
        con.execute("UPDATE entities SET name=?, aliases=? WHERE id=?",
                    (new, json.dumps(aliases, ensure_ascii=False), row["id"]))
        report["entity"] = 1
    else:
        report["entity"] = 0

    # 2) 事件里的人名（不动对话正文：别人当时就是这么叫的）
    report["events_actor"] = con.execute(
        "UPDATE events SET actor_name=? WHERE actor_name=?", (new, old)).rowcount

    # 3) 念头 / 事实：她自己的认知，跟着改
    report["thoughts"] = con.execute(
        "UPDATE thoughts SET content=replace(content, ?, ?) WHERE content LIKE ?",
        (old, new, f"%{old}%")).rowcount
    report["facts"] = con.execute(
        "UPDATE facts SET object_text=replace(object_text, ?, ?) WHERE object_text LIKE ?",
        (old, new, f"%{old}%")).rowcount
    report["urges"] = con.execute(
        "UPDATE urges SET note=replace(note, ?, ?) WHERE note LIKE ?",
        (old, new, f"%{old}%")).rowcount
    con.commit()
    con.close()

    # 4) 名字表
    names_file = DATA / "wx_names.json"
    names = json.loads(names_file.read_text(encoding="utf-8"))
    changed = 0
    for wxid, p in (names.get("people") or {}).items():
        if isinstance(p, dict) and p.get("name") == old:
            p["name"] = new
            al = list(p.get("aliases") or [])
            if old not in al:
                al.append(old)
            p["aliases"] = al
            changed += 1
    if changed:
        names_file.write_text(json.dumps(names, ensure_ascii=False, indent=2), encoding="utf-8")
    report["names_json"] = changed

    # 5) 记忆文件：改名 + 内容替换
    files = 0
    up = MEM / "我"
    src = up / f"{old}.md"
    if src.exists():
        text = src.read_text(encoding="utf-8").replace(old, new)
        src.unlink()
        (up / f"{new}.md").write_text(text, encoding="utf-8")
        files += 1
    for md in MEM.rglob("*.md"):
        t = md.read_text(encoding="utf-8", errors="replace")
        if old in t:
            md.write_text(t.replace(old, new), encoding="utf-8")
            files += 1
    report["files"] = files

    print(f"· 实体 {report['entity']}｜事件 {report['events_actor']}｜念头 {report['thoughts']}"
          f"｜事实 {report['facts']}｜名字表 {report['names_json']}｜文件 {report['files']}")


if __name__ == "__main__":
    main()
