"""看 B 站：把一个视频变成一条记忆，并从中长出兴趣。

用法：
    python -m bili.observe BV1GJ411x7h7      # 看指定视频
    python -m bili.observe --pick            # 按兴趣自己挑一个看
    python -m bili.observe --pick --dry      # 只看不记
"""

from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bili.client import Bilibili, BiliError
from memory import store


def _self_id(con) -> int:
    row = con.execute("SELECT id FROM entities WHERE etype='self'").fetchone()
    if row:
        return int(row["id"])
    cur = con.execute(
        "INSERT INTO entities(etype,name,first_seen,last_seen) VALUES ('self',?,?,?)",
        ("阿鱼", store.now_iso(), store.now_iso()),
    )
    return int(cur.lastrowid)


def interests(con) -> list[str]:
    rows = con.execute(
        "SELECT object_text FROM facts WHERE subject_id=(SELECT id FROM entities WHERE etype='self' LIMIT 1) "
        "AND predicate IN ('喜欢','感兴趣')"
    ).fetchall()
    return [r["object_text"] for r in rows]


def watch(con, bvid: str, *, danmaku_n: int = 40, dry: bool = False) -> dict:
    b = Bilibili()
    v = b.watch(bvid, danmaku_n=danmaku_n)

    top_dm = [d for d in v["danmaku"] if len(d) <= 30][:6]
    text = (f"看了《{v['title']}》，UP 主是{v['owner']}，{v['duration']}秒。"
            f"{v['desc'][:60].replace(chr(10), ' ')}")
    if top_dm:
        text += " 弹幕在刷：" + "／".join(top_dm)

    result = {"bvid": bvid, "title": v["title"], "owner": v["owner"],
              "danmaku": len(v["danmaku"]), "subtitle": len(v["subtitle"]), "text": text}
    if dry:
        return result

    eid = store.add_event(
        con, text, scene="bili", channel="bilibili", kind="observation",
        actor_name=v["owner"], importance=min(9, 4 + min(4, v["stat"]["like"] // 20000)),
        source_ref=bvid,
    )
    result["event_id"] = eid

    # 兴趣：标题/分区里反复出现的词，才配当"喜欢"
    for kw in _keywords(v["title"] + v["desc"]):
        if _seen_before(con, kw):
            _add_interest(con, kw, eid, f"看过 {bvid}")
    return result


def _keywords(text: str) -> list[str]:
    """极简关键词：只挑标签式片段，避免把整句当兴趣。"""
    import re
    parts = re.split(r"[，。！？、|丨【】\[\]（）()\s]+", text)
    return [p for p in parts if 2 <= len(p) <= 8][:5]


def _seen_before(con, kw: str) -> bool:
    """至少出现过两次的词才算兴趣（一次不算）。"""
    n = con.execute(
        "SELECT COUNT(*) FROM events WHERE kind='observation' AND text LIKE ?", (f"%{kw}%",)
    ).fetchone()[0]
    return n >= 2


def _add_interest(con, kw: str, eid: int, why: str) -> None:
    sid = _self_id(con)
    exists = con.execute(
        "SELECT id FROM facts WHERE subject_id=? AND predicate='喜欢' AND object_text=?",
        (sid, kw),
    ).fetchone()
    if exists:
        return
    store.add_fact(con, sid, "喜欢", kw, confidence=0.5, evidence=[eid])


def pick(con) -> str:
    b = Bilibili()
    pop = b.popular(12)
    liked = interests(con)
    if liked:
        scored = [(sum(1 for w in liked if w in x["title"] + x["desc"]), x) for x in pop]
        scored.sort(key=lambda t: t[0], reverse=True)
        if scored and scored[0][0] > 0:
            return scored[0][1]["bvid"]
    return random.choice(pop)["bvid"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("bvid", nargs="?")
    ap.add_argument("--pick", action="store_true")
    ap.add_argument("--dry", action="store_true")
    args = ap.parse_args()

    con = store.init_db()
    bvid = args.bvid or (pick(con) if args.pick else None)
    if not bvid:
        print("给个 BV 号，或者用 --pick")
        return
    try:
        r = watch(con, bvid, dry=args.dry)
    except BiliError as e:
        print("看不了:", e)
        return
    print(f"看了：{r['title']}  （{r['owner']}）")
    print(f"  弹幕 {r['danmaku']} 条，字幕 {r['subtitle']} 字")
    print(f"  记下：{r['text'][:100]}…")
    if not args.dry:
        con.commit()
        print(f"  事件 id={r['event_id']}，当前兴趣：{interests(con)}")


if __name__ == "__main__":
    main()
