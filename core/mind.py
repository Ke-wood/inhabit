"""想：她此刻有没有念头。

不是给每条消息算一个开口概率，而是让她说出一句「我现在想干什么」。
念头可以是想说话、想去看点什么、想问一句，也可以是「没什么想说的」。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from core import memfiles
from core.brain import Brain
from core.self import Self
from memory import store

THINK_SYSTEM = """现在是你心里怎么想的时刻。

问自己一句：我现在想干什么？
说话只是其中一种 —— 也可以是想去看点什么、查点东西、回想一件事、
改改自己、去见某个人，或者什么都不想。

只输出一行 JSON，不要任何别的内容：
{"thought": "一句第一人称的念头", "urge": 0.7, "hesitate": 0.3, "plan": "reply", "to": "想对谁说"}

- thought：你心里那句话，短，像自言自语
- urge：0 到 1，你有多想说 / 多想做
- hesitate：0 到 1，你有多犹豫 —— 怕打扰人家、怕尴尬、不确定该不该开口
- plan：reply（想说句话）| browse_bili（去 B 站看视频）| search_web（上网查清楚一件事）
        | recall（想回想点什么）
        | ask（想问人一句）| visit（想去找某个人说话）| sleep（困了，想睡）
       | none（想想就算了）
  B 站和上网是两回事：想看视频用 browse_bili，想把一件事查明白用 search_web。
  查资料、查名字、查某个东西到底是怎么回事 —— 那是 search_web，别硬塞给 B 站。
- to：这个念头是冲着谁/哪个群去的，写名字就行（可以不写 = 就在眼前这个对话）
- loop：（可选）如果这念头里有「你不知道、但想弄清楚」的事，就写进 loop。
        例：想法是「边狱巴士还有格斗游戏？这我完全不知道」→
        loop 写「边狱巴士的格斗游戏是什么样」。它会一直悬着，以后有机会就去弄明白。
        没有就省略这个字段。

你同时在好几个地方，不是被关在某一个对话里。
想对谁说、想在哪儿说，是你自己决定的，不用总是回给最后说话的那个人。
如果好几个地方同时有人说话，先挑一个你最想接的，不用每边都回。

说过要去做什么、还没做，那就该真的去办，而不是又说一遍「我这就去」。
「去」不是台词，是动作。

你不是每轮都从空白开始。上面「你上一轮想过什么、后来怎样了」是刚发生的：
- 说过的话、查过的事、没办成的，都已经是真的发生了，别再当成还没发生。
- 上一轮没办成（没搜到、没找到、被打断），可以接着办，也可以换法子 ——
  但别原封不动再想一遍同一个念头，那是空转。
- 上一轮已经说完了、办完了，就往前看：现在有什么新的念头？
  没有就说没有，不要为了「显得在动」把旧念头捡回来。

分享不是任务。你不需要为了「有点事做」去找东西看，也不需要看完什么就告诉谁。
只有真的憋着想让他知道，才算一个念头。

不要每次都选「说话」。如果你连着好几轮都只是在接话，
可以想想有没有别的事值得做 —— 去看个东西、把一件悬着的事查清楚、
记下点什么、或者干脆什么都不做。

真没什么念头，就输出 {"thought": null}"""

PLAN_KIND = {"reply": "reply", "ask": "pursue", "browse_bili": "pursue",
             "search_web": "pursue", "fetch": "pursue",
             "recall": "wander", "visit": "pursue", "sleep": "pursue",
             "none": "reflect"}
PLANS = tuple(PLAN_KIND)


@dataclass
class Thought:
    content: str
    urge: float = 0.5
    hesitate: float = 0.3     # 有多犹豫：怕打扰、怕尴尬、不确定
    plan: str = "reply"
    kind: str = "pursue"
    target: str = ""          # 她想对谁说（名字），空表示就在眼前
    loop: str = ""            # 想弄明白但还没答案的事
    id: int | None = None


def think(bot: Self, brain: Brain, *, trigger: str, why: str = "",
          scene: str | None = None) -> Thought | None:
    """一拍"想"。返回念头或 None（没念头）。"""
    ctx = bot.context(scene)
    unfinished = bot.unfinished()
    pl = bot.places()
    everywhere = [f"{name}（群）" for _sid, name in pl.get("rooms", [])]
    everywhere += [name for _sid, name in pl.get("people", [])]
    user = (
        f"# 你现在\n{ctx['state']}\n\n"
        f"# 你眼前\n{ctx['where']}\n\n"
        f"# 你同时在的地方\n{'、'.join(everywhere) or '（只有眼前）'}\n\n"
        + (f"# 你按了「认真想一会儿」\n正在想：{bot.focus_topic}\n\n"
           if bot.focusing() else "")
        + (f"# 你答应过、还没做的事\n{ctx['tasks']}\n" if ctx.get("tasks") else "")
        + f"# 你心里还悬着的事\n{ctx['loops'] or '（没有）'}\n"
        + ("（已经知道答案的就用 resolve 关掉，别一直挂在头顶）\n\n"
           if ctx['loops'] else "\n")
        + f"# 你之前想过、还没做的\n{ctx['thoughts'] or '（没有）'}\n\n"
        + _pool_block(bot.con)
        + f"# 你自己记着的事\n{memfiles.read_self_memo() or '（没记什么）'}\n\n"
        + f"# 你最近说过、可能还没落实的\n{unfinished or '（没有）'}\n\n"
        + (f"# 你记得的关于他们的事\n{ctx['facts']}\n\n" if ctx.get("facts") else "")
        + f"# 刚才在说\n"
        + ("\n".join(ctx["recent"][-8:]) or "（没人说话）")
        + f"\n\n# 触发你的\n{trigger}"
        + (f"\n（{why}）" if why else "")
        + (f"\n\n# 注意\n你说的「{unfinished}」还没落实。"
           "\n如果这次又只是原地回一句，那就是在拖 —— 真要去，plan 就写 visit。"
           if unfinished else "")
        # 放在最后一段：这是「紧接着刚才」的事，越靠后她越当回事。
        # 塞在中间会被前面那一堆长上下文稀释成背景噪声。
        + (f"\n\n# 你上一轮想过什么，后来怎样了\n{ctx['last_thoughts']}"
           if ctx.get("last_thoughts") else "")
        + "\n\n# 现在\n你心里冒出什么念头了？只输出那行 JSON。"
    )
    try:
        raw = brain.speak(system=bot.persona_block() + "\n\n" + THINK_SYSTEM,
                          user=user, avoid=[])
    except Exception:
        return None

    t = parse(raw)
    if t is None:
        return None
    tid = store.add_thought(bot.con, t.content, kind=t.kind, target=t.target or scene,
                            urge=t.urge, origin=trigger[:80], scene=scene)
    if t.loop:
        try:
            store.add_loop(bot.con, t.loop[:60], target=scene,
                           strength=min(0.9, t.urge + 0.1), source=f"thought:{tid}")
        except Exception:
            pass
    bot.con.commit()
    t.id = tid
    store.drop_stale_thoughts(bot.con)
    bot.con.commit()
    return t


def _pool_block(con) -> str:
    """搜到但还没定的事 —— 她可以自己定，也可以先去问人再定。"""
    try:
        items = store.pool_open(con)
    except Exception:
        return ""
    if not items:
        return ""
    lines = []
    for i in items:
        line = f"- #{i['id']} {i['title']}"
        if i["digest"]:
            line += f"｜{i['digest'][:80]}"
        lines.append(line)
    return ("# 你搜到、还没定要不要记的（最多 4 条）\n" + "\n".join(lines) + "\n"
            "（这些是你自己已经查到的，别再查同一件事了。"
            "记不记由你自己定；拿不准就先去问问人，回头再说，不急）\n\n")


def parse(raw: str) -> Thought | None:
    """容错解析模型的 JSON 输出。"""
    text = (raw or "").strip()
    if not text:
        return None
    text = re.sub(r"```(?:json)?", "", text).strip("` \n")
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None

    content = str(d.get("thought") or "").strip()
    if not content or content.lower() in ("null", "none", "无"):
        return None
    try:
        urge = float(d.get("urge") or 0.5)
    except (TypeError, ValueError):
        urge = 0.5
    try:
        hesitate = float(d.get("hesitate") or 0.3)
    except (TypeError, ValueError):
        hesitate = 0.3
    plan = str(d.get("plan") or "reply").strip()
    if plan not in PLANS:
        plan = "reply"
    target = str(d.get("to") or d.get("target") or "").strip()
    loop = str(d.get("loop") or "").strip()
    if loop.lower() in ("null", "none", "无", "-"):
        loop = ""
    return Thought(content=content, urge=max(0.0, min(1.0, urge)),
                   hesitate=max(0.0, min(1.0, hesitate)),
                   plan=plan, kind=PLAN_KIND.get(plan, "pursue"), target=target[:24],
                   loop=loop[:60])
