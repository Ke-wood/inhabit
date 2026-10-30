"""她自己去 B 站看东西，看完留下自己的印象。

不是"抓数据"，是"看过"。所以存的是她的观感 + 她记住的具体内容，
不是元信息清单。

看懂的关键是字幕：标题只能告诉她"这视频叫什么"，字幕才告诉她
"视频里到底发生了什么"。
"""

from __future__ import annotations

import random
import re
import sqlite3

from bili import read
from bili.client import Bilibili, BiliError
from core.brain import Brain
from memory import store

REACT_SYSTEM = """你刚在 B 站看完一个视频。下面给你的是视频里真实说过的话（字幕节选），不是摘要。别复述标题。

用你自己的口吻回两句话，中间用 | 隔开：
- 前一句：你的感受。短，像发在群里，25 字以内。
- 后一句：你从内容里记住的一件具体的事，50 字以内。必须来自字幕，不能来自标题。真没记住就写「没记住啥」。

不要「这部视频讲述了」这种腔调。不要分点。不要 markdown。"""


def _interest_words(con: sqlite3.Connection) -> list[str]:
    rows = con.execute(
        """SELECT object_text FROM facts
           WHERE predicate IN ('喜好','观点') LIMIT 20""").fetchall()
    return [r["object_text"] for r in rows]


def pick(con: sqlite3.Connection, b: Bilibili) -> dict | None:
    """按兴趣挑一个：标题沾边优先，否则随机。"""
    try:
        cands = b.popular(12)
    except BiliError:
        return None
    if not cands:
        return None
    words = _interest_words(con)
    if words:
        scored = [(sum(1 for w in words if any(ch in x["title"] + x.get("desc", "") for ch in w)), x)
                  for x in cands]
        scored.sort(key=lambda t: -t[0])
        if scored[0][0] > 0:
            return scored[0][1]
    return random.choice(cands)


def _split_reply(raw: str) -> tuple[str, str]:
    """把「感受 | 记住的事」拆开。模型没按格式回时整段当感受。"""
    raw = (raw or "").strip()
    if not raw:
        return "", ""
    for sep in ("|", "｜", "\n"):
        if sep in raw:
            a, _, b = raw.partition(sep)
            return a.strip(" -"), _clean_gist(b)
    return raw, ""


def _clean_gist(text: str) -> str:
    """去掉模型多说的「记住的是/我记得」前缀，避免记忆里套娃。"""
    t = text.strip(" -：:，,。")
    t = re.sub(r"^(我)?(记住的?是?|记得的?是?|记下了?)[：:，,\s]*", "", t)
    return t.strip(" -：:")


def watch(con: sqlite3.Connection, brain: Brain, *, bvid: str | None = None) -> dict | None:
    b = Bilibili()
    if not bvid:
        item = pick(con, b)
        if not item:
            return None
        bvid = item["bvid"]

    try:
        v = b.video(bvid)
        dms_all = b.danmaku(v["cid"], 60)
    except BiliError as e:
        return {"error": str(e), "bvid": bvid}

    lines = read.fetch(v["bvid"], v["cid"])
    content = read.digest(lines, max_chars=1400)
    dms = [d for d in dms_all if 2 <= len(d) <= 24][:6]

    prompt = (f"标题：{v['title']}\nUP 主：{v['owner']}\n简介：{v['desc'][:120]}\n\n"
              f"视频里实际说的话：\n{content or '（这个视频没有字幕，你只能看标题和弹幕）'}\n\n"
              f"弹幕在刷：{'／'.join(dms) if dms else '（没什么人说话）'}\n\n"
              "说你的感受和你记住的事。")

    try:
        raw = brain.speak(system=REACT_SYSTEM, user=prompt, avoid=[]) or ""
    except Exception:
        raw = ""
    reaction, gist = _split_reply(raw)

    text = f"在 B 站看了《{v['title']}》（{v['owner']}）。{reaction}"
    if gist and gist not in ("没记住啥", "没记住", "-"):
        text += f" 我记住的是：{gist}"
    text += f" 弹幕在刷：{'／'.join(dms[:3])}"

    # 有字幕、也记住了东西，说明这次是真看了，重要性给高一点
    importance = 6 if lines and gist else 5
    eid = store.add_event(con, text, scene="bili", channel="bilibili", kind="observation",
                          actor_id="self", actor_name="阿鱼",
                          importance=importance, source_ref=v["bvid"])

    # 有明确喜好就沉淀成自我认知
    if reaction and any(w in reaction for w in ("喜欢", "好看", "有意思", "帅")):
        sid = store.ensure_entity(con, "阿鱼")
        store.add_fact(con, sid, "喜好", f"喜欢《{v['title'][:14]}》这类", confidence=0.5)
    con.commit()

    return {"bvid": v["bvid"], "title": v["title"], "owner": v["owner"],
            "reaction": reaction, "gist": gist,
            "danmaku": len(dms_all), "subtitle_chars": sum(len(x["content"]) for x in lines),
            "event_id": eid, "text": text}
