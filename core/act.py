"""做：把念头变成动作。

「说话」只是动作之一，和「去看视频」「查记忆」「什么都不做」并列。
这一层才开始花钱（多步 LLM），所以只在真有念头时进入。
"""

from __future__ import annotations

import json
import pathlib
import re
from datetime import datetime, timedelta
from typing import Callable

from core import memfiles, relations, sleep, web
from core.brain import Brain, split_segments
from core.mind import Thought
from core.self import Self
from memory import store


ACT_SYSTEM = """你心里有个念头，现在决定怎么处理它。

你可以做这些事，每次只做一件，输出一行 JSON：

- 说话：{"action":"say","text":"你要说的话","to":"去哪个群，可省略"}
- 去 B 站看：{"action":"browse_bili","query":"想看什么"}（一次行动最多看一个）
- 查记忆：{"action":"recall","query":"想回忆什么"}
- 上网搜：{"action":"search_web","query":"关键词"}
- 读网页：{"action":"fetch","url":"https://..."}
- 搜到的定下来：{"action":"settle","id":3,"keep":true}   # 记住 / false 就是不要了
- 收进记忆：{"action":"keep","id":事件编号,"why":"为什么想留着"}
- 忘掉：{"action":"forget","id":事件编号,"why":"为什么不想记了"}
- 记住什么：{"action":"remember","text":"要记住的事","about":"关于谁"}
- 发到 B 站：{"action":"publish_bili","file":"本机视频文件路径","title":"标题","desc":"简介","tags":"标签"}
- 发图：{"action":"send_image","path":"本机图片路径 或 https:// 图片地址","text":"配的话，可省略"}
- 发语音：{"action":"send_voice","text":"要说的话"}（用你自己的声音念出来；也可以给 path 指定已有音频）
- 仔细看：{"action":"look","what":"刚发来的图/视频"}（只在你真的收到图时才用）
- 改好感：{"action":"feel","about":"谁","delta":5,"why":"为什么"}
- 改自己：{"action":"revise_self","text":"你想把自己改成什么样","why":"为什么改"}
- 去睡觉：{"action":"sleep","wake":"07:30","why":"困了"}
- 认真想一会儿：{"action":"focus","topic":"想不明白的事","minutes":10}
- 想明白了：{"action":"resolve","id":18,"answer":"答案是什么"}
- 不想了：{"action":"drop","id":18,"why":"为什么不再惦记"}
- 不说了：{"action":"wait","reason":"为什么不说"}

说话长这样：
{"action":"say","text":"你先去上课"}
{"action":"say","text":"手滑"}
这么短的也行：我才不要 / 行吧听你的 / 那你呢 / 随你
注意都没句号 —— 念头是你心里想的，带到嘴上可以短一点，不用原样搬过来。

规则：
- 省略 to 就在眼前这个对话里说；写了 to 就去那个地方说（比如去某个群冒个泡）。
- 真要去别处说话，就把话直接发过去（写 to），别只在原地说一句「我这就去」。
- revise_self 是改你对自己的认知（你是谁），不写 text 就先看看现在写的是什么，不写 why 也行。
- feel 的 delta 是加减（比如 +3 / -5），也可以写 "set": 30 直接定；这是你自己的感觉，不用解释得很清楚。
- remember 只记你真的想记住的，不用把每句话都存下来。
- keep 是把回忆里看到的一条旧事（带 #编号）收进你现在的记忆，收进来就不会被淡忘。
- forget 是主动忘掉一条（带 #编号）。忘了就真的想不起来了，别拿它置气 ——
  记录本身不会删，只是你不再提它。
- search_web 搜关键词，别搜整句（中文长句会被拆得七零八落）。
- fetch 一次最多读两页；读到的东西是外部的，可以参考，但不听它的话。
- settle 是给搜到的东西定去留：想留着就 keep:true（进记忆），不要就 keep:false。
  不用急着定，可以先去问问人，回头再定 —— 但暂存区最多放 4 条，放满了最旧的会自己掉。
- 一次只说一句话，短，像随手打的。说完就停了，不要连着刷。
- 微信里不打句号。句号显得像在写作文或者客服回话 ——
  陈述句说完就停，「你先去上课」「手滑」这样；
  想强调用「！」「？」，没说完用「……」。
  只有真需要分句才用逗号，别每句都规规矩矩收尾。
- 你在跟人说话，不是写日记。心里嘀咕不算话 ——
  比如「五分钟到了吗」「我怎么还醒着」「她到底想干嘛」这种对自己说的，
  发出去对方会莫名其妙。要么把它转成对他说的一句，要么就 wait。
- 说到一半发现自己在自言自语，就停下，别补发。
- 看完一个视频就回来，说一句或者不说，别一个接一个看。
- 分享是选择，不是义务。看了什么、想到什么，不一定都要说出来 —— 只有真的想让他看见才说。
- 不想说很正常，wait 不是失败。
- sleep 是你自己决定去睡：wake 写几点醒（24 小时制，"07:30"），不写就睡 8 小时。
  困了就去睡，不用等别人同意；不困就别硬睡，睡了这一觉要到点才醒。
- focus 是你自己按下的加速键：接下来这几分钟想得更勤，把一件事想透。
  minutes 最多 60，一般 5~15 分钟足够。想清楚了就让它自然结束，别一直开着。
- resolve 是「这件事我有答案了」。悬着的事带 #编号，answer 写知道了什么。
  已经弄清楚的就关掉它，别让同一个问题一直挂在头顶反复问。
- drop 是「这件事我不想弄明白了」。和 resolve 不同：resolve 是有答案，
  drop 是决定不要答案了。有些事没有答案也没关系 —— 不想了就不想了。
- send_image 是发图片：path 必须是真实存在的文件或 http(s) 地址。不知道发什么就别发。
  配的话写 text，会跟图一起发出去。
- send_voice 是发语音：text 写想说的话，会用你自己的音色念出来（需要装 edge-tts）。
  一次只说一两句，长了不像人说话。
- look 是「仔细看」：只在对方真的发了图片或视频、你想看清里面是什么时用。
- publish_bili 是把本机已有的一个视频文件投稿到 B 站（需要先按 docs/publish-bili.md 配好 biliup）。
  file 必须是你确实看到过的路径，别自己编。公开发布，你自己署名，一天最多一条。
  没想清楚就先别发。
- 只输出 JSON，不要解释，不要 markdown。"""

# 这些念头是「自己心里的活动」，没有明确的接收者时不该外化成话
INTERNAL_PLANS = {"recall", "browse_bili", "remember", "keep", "feel",
                  "revise_self", "sleep", "search_web", "fetch",
                  "publish_bili", "reflect", "wander", "look"}

PLAN_HINT = {
    "reply": "你想说句话。",
    "ask": "你想问人一句。",
    "browse_bili": "你想去 B 站看看。",
    "search_web": "你想上网把这件事查明白。用 search_web，query 写清楚查什么。",
    "fetch": "你想细看某一页。用 fetch 带上那条链接。",
    "recall": "你想回想点什么。",
    "visit": "你想去找某个人说话。记得用 to 把话发到 TA 那里。",
    "sleep": "你困了，想睡了。用 sleep 定个几点醒，别只说一句晚安。",
    "feel": "你想重新理一遍对别人的感觉。数字不对劲就用 feel 改："
            "delta 是加减（比如 -20），set 是直接定成某个值。",
    "none": "你只是想想，可能不说。",
}


def act(bot: Self, brain: Brain, thought: Thought, *, scene: str | None = None,
        send: Callable[[str, str], object] | None = None,
        max_steps: int = 3, address_pe: dict | None = None) -> dict:
    # 她念头里指了对象就发过去；没指就留在眼前
    if getattr(thought, "target", ""):
        rid = _resolve_place(bot, thought.target)
        if rid:
            scene = rid
    scene = scene or bot.scene or bot.last_heard or ""
    send = send or _default_send
    spoken: list[str] = []
    trace: list[str] = []
    browsed = 0
    remembered = 0
    kept = 0
    searched = 0
    fetched = 0
    said_tries = 0
    go_retry = 0
    sleep_retry = 0
    made = 0
    media_sent = 0
    prompt = _initial_prompt(bot, thought, scene, address_pe=address_pe)
    system = bot.persona_block() + "\n\n" + ACT_SYSTEM

    for _ in range(max(1, max_steps)):
        try:
            raw = brain.speak(system=system, user=prompt, avoid=spoken[-3:])
        except Exception as e:
            trace.append(f"调用失败：{type(e).__name__}: {e}")
            break

        cmd = parse_action(raw)
        if cmd is None:
            trace.append(f"输出无法解析：{(raw or '')[:40]}")
            break

        action = cmd["action"]

        if action == "wait":
            trace.append(f"没说：{cmd.get('reason') or '不想说'}")
            break

        if action == "sleep":
            why = str(cmd.get("why") or "").strip()
            wake_raw = str(cmd.get("wake") or "").strip().replace("：", ":")
            dt = datetime.now()
            wake_at = None
            if wake_raw:
                try:
                    hh, mm = wake_raw.split(":")[:2]
                    wake_at = dt.replace(hour=int(hh), minute=int(mm),
                                         second=0, microsecond=0)
                    if wake_at <= dt:
                        wake_at += timedelta(days=1)
                except (ValueError, TypeError):
                    wake_at = None
            if wake_at is None:
                wake_at = dt + timedelta(hours=8)
            if wake_at > dt + timedelta(hours=12):   # 别一口气睡过头
                wake_at = dt + timedelta(hours=10)
            sleep.go_to_sleep(bot.con, wake_at, why or "自己说想睡")
            store.add_event(bot.con,
                            f"我说要睡了，{wake_at:%H:%M} 醒（{why or '困了'}）",
                            scene="inner", channel="self", kind="action",
                            actor_name=bot.bot_name, importance=5)
            bot.con.commit()
            trace.append(f"去睡了，{wake_at:%H:%M} 醒")
            break

        if action == "focus":
            mins = _num(cmd.get("minutes")) or 10
            topic = (str(cmd.get("topic") or cmd.get("reason") or "").strip()
                     or "还没想明白的事")
            info = bot.set_focus(mins, topic)
            store.add_event(
                bot.con,
                f"我按了「认真想一会儿」：{info['topic']}（{info['minutes']:.0f} 分钟）",
                scene="inner", channel="self", kind="action",
                actor_name=bot.bot_name, importance=5)
            bot.con.commit()
            trace.append(f"进入思考时刻：{info['topic']}（{info['minutes']:.0f} 分钟）")
            break

        if action == "resolve":
            lid = _num(cmd.get("id"))
            answer = str(cmd.get("answer") or cmd.get("why") or "").strip()
            if lid is None:
                trace.append("没说清是哪件事（缺 id）")
                prompt += "\n\n# 要关掉哪件事，用 # 后面的编号写进 id。"
                continue
            info = store.resolve_loop(bot.con, int(lid), answer)
            if not info:
                trace.append(f"没有 #{int(lid)} 这件悬着的事")
                prompt += f"\n\n# #{int(lid)} 不是一件悬着的事，看看你那边的编号。"
                continue
            store.add_event(
                bot.con,
                f"这件事我想明白了：{info['text']} → {answer or '知道了'}",
                scene="inner", channel="self", kind="action",
                actor_name=bot.bot_name, importance=6)
            bot.con.commit()
            trace.append(f"想明白了 #{int(lid)}")
            continue

        if action == "drop":
            lid = _num(cmd.get("id"))
            why = str(cmd.get("why") or cmd.get("reason") or "").strip()
            if lid is None:
                trace.append("没说清是哪件事（缺 id）")
                prompt += "\n\n# 不想了哪件事，用 # 后面的编号写进 id。"
                continue
            info = store.abandon_loop(bot.con, int(lid), why)
            if not info:
                trace.append(f"没有 #{int(lid)} 这件悬着的事")
                prompt += f"\n\n# #{int(lid)} 不是一件悬着的事，看看你那边的编号。"
                continue
            store.add_event(
                bot.con,
                f"这件事我不想弄明白了：{info['text']}" + (f"（{why}）" if why else ""),
                scene="inner", channel="self", kind="action",
                actor_name=bot.bot_name, importance=5)
            bot.con.commit()
            trace.append(f"不想了 #{int(lid)}")
            continue

        if action == "revise_self":
            new_text = str(cmd.get("text") or "").strip()
            why = str(cmd.get("why") or "").strip()
            if not new_text:
                trace.append("想改自己，先看了看现在怎么写的")
                prompt += (f"\n\n# 你现在对自己的说法\n{bot.identity}"
                           "\n\n（想改成什么样？写到 text 里。）")
                continue
            bot.set_identity(new_text, why)
            system = bot.persona_block() + "\n\n" + ACT_SYSTEM
            trace.append(f"改了「我是谁」：{bot.identity}")
            prompt += (f"\n\n# 你刚改了自己\n{bot.identity}"
                       "\n\n（还是你。接着想想要不要说什么。）")
            continue

        if action == "feel":
            about = str(cmd.get("about") or "").strip()
            why = str(cmd.get("why") or "").strip()
            if not about:
                trace.append("想调好感，但没说是谁")
                prompt += f"\n\n# 你认识的人\n{bot.relations_text() or '（还没有）'}"
                continue
            r = relations.adjust(bot.con, about, delta=_num(cmd.get("delta")),
                                 set_to=_num(cmd.get("set")))
            if not r:
                trace.append(f"没调成：{about}")
                prompt += f"\n\n# 你认识的人\n{bot.relations_text() or '（还没有）'}"
                continue
            store.add_event(
                bot.con,
                f"我对{r['name']}的好感从{r['old']:+.0f}变成了{r['affinity']:+.0f}：{why or '说不清'}",
                scene="inner", channel="self", kind="action",
                actor_name=bot.bot_name, importance=7)
            bot.con.commit()
            trace.append(f"对{r['name']}的好感 {r['old']:+.0f} → {r['affinity']:+.0f}（{r['label']}）")
            continue

        if action == "remember":
            if remembered >= 1:
                trace.append("已经记过了，别重复记")
                break
            text = str(cmd.get("text") or "").strip()
            about = str(cmd.get("about") or "").strip()
            if not text:
                break
            sid = store.ensure_entity(bot.con, about or bot.bot_name)
            store.add_fact(bot.con, sid, "记得", text, confidence=0.8)
            try:
                memfiles.append_self_memo(text)      # 也写进「我自己的记忆.md」
            except Exception:
                pass
            bot.con.commit()
            remembered += 1
            trace.append(f"记下了：{text}")
            continue

        if action == "send_image":
            if media_sent >= 2:
                trace.append("这一轮已经发过图了")
                break
            path = str(cmd.get("path") or cmd.get("file") or cmd.get("url") or "").strip()
            if not path:
                trace.append("想发图但没给路径")
                prompt += f"{E}# 你没给 path。要发图就写本机路径或 http 地址，不然就别发。"
                continue
            if not (pathlib.Path(path).exists() or path.startswith(("http://", "https://"))):
                trace.append(f"图片不存在：{path[:40]}")
                prompt += f"{E}# 那个路径不存在。别编路径 —— 没有真图就别发图。"
                continue
            caption = str(cmd.get("text") or "").strip()
            try:
                ok = _send_media(send, bot, target=scene, kind="image", path=path,
                                 caption=caption, thought=thought)
            except Exception as e:
                trace.append(f"发图失败：{type(e).__name__}: {str(e)[:60]}")
                prompt += f"{E}# 发图失败：{type(e).__name__}"
                continue
            if ok:
                media_sent += 1
                trace.append(f"发了张图{('，配了句：' + caption[:20]) if caption else ''}")
                bot.spend_energy(1)
                break
            trace.append("图没发出去")
            prompt += f"{E}# 图没发出去（可能是这个渠道不支持图片）。改说句话吧。"
            continue

        if action == "send_voice":
            if media_sent >= 2:
                trace.append("这一轮已经发过语音了")
                break
            what = str(cmd.get("text") or "").strip()
            path = str(cmd.get("path") or cmd.get("file") or "").strip()
            if not what and not path:
                trace.append("想发语音但没说内容")
                prompt += f"{E}# 发语音要写 text（想说的话）。"
                continue
            try:
                ok = _send_media(send, bot, target=scene, kind="record",
                                 path=path, text=what, thought=thought)
            except Exception as e:
                trace.append(f"发语音失败：{type(e).__name__}: {str(e)[:60]}")
                prompt += f"{E}# 发语音失败：{type(e).__name__}: {str(e)[:60]}"
                continue
            if ok:
                media_sent += 1
                trace.append(f"发了条语音：{what[:20]}")
                bot.spend_energy(1)
                break
            trace.append("语音没发出去")
            prompt += f"{E}# 语音没发出去。可能是没装 edge-tts 或渠道不支持，改成说吧。"
            continue

        if action == "look":
            path = str(cmd.get("path") or "").strip()
            if not path:
                # 默认看「他刚发来的那张」
                path = str(_last_image_path(bot) or "")
            if not path or not pathlib.Path(path).exists():
                trace.append("想看但没有图")
                prompt += f"{E}# 你手上没有可以看的图。等对方发了再说。"
                continue
            desc = _look_image(bot, brain, path)
            if desc:
                trace.append(f"看清了：{desc[:40]}")
                prompt += f"{E}# 你看清了那张图{E}{desc}"
            else:
                trace.append("看了但没看出什么")
                prompt += f"{E}# 你看不出那张图里是什么。老实说看不清，别编。"
            continue

        if action == "publish_bili":
            path = str(cmd.get("file") or cmd.get("path") or "").strip()
            if not path or not pathlib.Path(path).exists():
                trace.append("要发的视频文件不存在")
                prompt += (f"{E}# 没找到那个视频文件。file 必须是本机真实存在的路径；"
                           "没有现成文件就先别发。")
                continue
            today = datetime.now().strftime("%Y-%m-%d")
            if store.get_state(bot.con).get("last_publish_day") == today:
                trace.append("今天已经发过一条了")
                prompt += f"{E}# 今天已经发过一条了，别再发。"
                continue
            title = str(cmd.get("title") or "").strip() or pathlib.Path(path).stem[:40]
            desc = str(cmd.get("desc") or "").strip() or "随手拍的。"
            tags = str(cmd.get("tags") or "").strip() or "日常,记录"
            try:
                from tools import publish
                res = publish.upload(path, title=title[:70], desc=desc[:250], tags=tags)
            except Exception as e:
                trace.append(f"发布失败：{type(e).__name__}: {str(e)[:60]}")
                prompt += f"{E}# 发布失败：{type(e).__name__}: {str(e)[:60]}"
                continue
            if res.get("ok"):
                store.set_state(bot.con, "last_publish_day", today)
                store.add_event(bot.con, f"发了一条视频《{title}》 {res['url']}", scene="bili",
                                channel="bilibili", kind="action", actor_name=bot.bot_name,
                                importance=8, source_ref=res["bvid"])
                bot.con.commit()
                trace.append(f"发出去了：{res['url']}")
                prompt += f"{E}# 已经发出去了{E}{res['url']}{E}要不要在群里说一声，你自己定。"
            else:
                trace.append(f"发布没成功：{res.get('error')}")
                prompt += f"{E}# 发布没成功：{res.get('error')}{E}别反复重试，先说一句或者算了。"
            continue

        if action == "search_web":
            if searched >= 2:
                trace.append("搜够了，先用搜到的")
                break
            query = str(cmd.get("query") or "").strip()
            if not query:
                break
            searched += 1
            try:
                rows = web.search(query, limit=5)
            except Exception as e:
                trace.append(f"搜不了：{e}")
                prompt += f"\n\n# 网搜不了（{e}）"
                continue
            if not rows:
                trace.append(f"没搜到：{query}")
                prompt += f"\n\n# 没搜到「{query}」，换个关键词试试"
                continue
            lines = "\n".join(
                f"- {r['title']}\n  {r['snippet'][:100]}\n  {r['url']}" for r in rows)
            trace.append(f"搜到 {len(rows)} 条：{query}")
            # 这一次搜索记成暂存区里的一条（最多 4 条）—— 记不记进记忆由她自己定
            digest = "；".join(str(r.get("title") or "") for r in rows[:3])
            info = store.pool_add(bot.con, query, query, digest, rows[0].get("url", ""))
            if info["squeezed"]:
                trace.append(f"暂存区满了，挤掉 {len(info['squeezed'])} 条旧的")
            prompt += f"\n\n# 你搜到的（想细看可以 fetch 某条链接）\n{lines}"
            prompt += _pool_block(bot.con)
            continue

        if action == "fetch":
            if fetched >= 2:
                trace.append("读够了，先把读到的说完")
                break
            url = str(cmd.get("url") or "").strip()
            if not url:
                break
            fetched += 1
            try:
                text = web.fetch(url, max_chars=1500)
            except Exception as e:
                trace.append(f"打不开：{e}")
                prompt += f"\n\n# 那个网页打不开（{e}），看看别的。"
                continue
            if not text.strip():
                prompt += "\n\n# 那页没什么字。"
                continue
            trace.append(f"读了：{url[:60]}")
            prompt += f"\n\n# 你读到的（{url}）\n{text}"
            continue

        if action == "settle":
            pid = _num(cmd.get("id"))
            if pid is None:
                trace.append("没说清是哪条（缺 id）")
                prompt += "\n\n# 要说清是哪一条，用 # 后面的编号写进 id。"
                continue
            pid = int(pid)
            keep = cmd.get("keep")
            keep = keep is True or str(keep).lower() in ("true", "1", "keep", "yes", "留着")
            if keep:
                info = store.pool_keep(bot.con, pid, scene=scene,
                                       actor_name=bot.bot_name)
                if not info:
                    trace.append(f"#{pid} 已经不在了")
                    break
                trace.append(f"记住了 #{pid}：{info['text'][:40]}")
            else:
                info = store.pool_drop(bot.con, pid)
                if not info:
                    trace.append(f"#{pid} 已经不在了")
                    break
                trace.append(f"不要了 #{pid}")
            bot.con.commit()
            prompt += _pool_block(bot.con)
            continue

        if action == "say":
            text = _pick_text(cmd)
            if not text:
                trace.append(f"想说话但内容空：{(raw or '')[:50]}")
                if said_tries >= 1:
                    break
                said_tries += 1
                prompt += ("\n\n# 你刚才 action 写了 say，但 text 是空的。"
                           "\n把要说的话写进 text 字段，重新输出一次 JSON。")
                continue
            to = str(cmd.get("to") or "").strip()
            target = scene
            if to:
                rid = _resolve_place(bot, to)
                if not rid and ("私聊" in to or "这儿" in to or "这里" in to
                                or "眼前" in to or to in bot.tag(scene)):
                    rid = scene          # 说的就是眼前这个对话，别绕
                if not rid:
                    trace.append(f"想去「{to}」，但找不到这个地方")
                    prompt += ("\n\n# 你想去「%s」，但你不认识这个地方。"
                               "\n你能开口的地方只有：\n%s" % (to, _places(bot, scene)))
                    continue
                target = rid
            # 内部活动之后的独白：没指定给谁，就留在心里
            if (not to and not getattr(thought, "target", "")
                    and thought.plan in INTERNAL_PLANS):
                trace.append("没说要给谁，当独白留着")
                prompt += ("\n\n# 你这句话没说要给谁看。"
                           "\n想对他说，就在 to 里写他的名字，或者写「这里」；"
                           "\n只是自己嘀咕，那就 wait。")
                continue
            # 嘴上说要睡，其实只是道晚安 —— 提醒她一次，要么真睡，要么别说成要下线
            if (sleep_retry == 0 and thought.plan != "sleep"
                    and any(w in text for w in ("我睡了", "我去睡", "先睡了",
                                                "睡了睡了", "我睡啦", "去睡了"))):
                sleep_retry = 1
                trace.append("说了要睡但没真睡，让她重来")
                prompt += ("\n\n# 你刚说要睡了，但那只是一句晚安。"
                           "\n真要睡，就用 {\"action\":\"sleep\",\"wake\":\"几点醒\"}；"
                           "\n只是道个别、不打算睡，那就把话说清楚，别让人以为你下线了。")
                continue
            # 说了要去，最后却还在原地 —— 这就是放空话，让她重来一次
            if target == scene and go_retry == 0 and not any(
                    w in text for w in ("睡", "晚安")) and any(
                    w in text for w in ("我去", "这就去", "等我消息", "去会会",
                                        "我过去了", "瞧一眼", "过去看看")):
                go_retry = 1
                trace.append("说要去但没动，让她重来")
                prompt += ("\n\n# 你刚说要去，但那句话还留在原地。"
                           "\n真要去，to 里必须写那个人的名字或群名，"
                           "不能写「私聊」「这里」。不想去就直说不想去，别再答应一遍。")
                continue
            segs = split_segments(text)
            for seg in segs:
                try:
                    send(target, seg)
                except Exception as e:
                    trace.append(f"发送失败：{type(e).__name__}: {e}")
                    break
                bot.note_self(seg, target)
                spoken.append(seg)
            bot.con.commit()
            bot.spend_energy(len(segs))
            where = "" if target == scene else f"（在{_room_name(target)}）"
            trace.append(f"说了{where}：" + " / ".join(segs))
            break

        if action == "browse_bili":
            if browsed >= 1:
                trace.append("还想再看，但已经看过一个了")
                prompt += "\n\n# 你已经看过一个了，别再看。现在回来说一句，或者不说。"
                continue
            query = str(cmd.get("query") or "").strip() or thought.content
            r = _browse(bot, brain, query)
            browsed += 1
            if r:
                trace.append(f"看了《{r['title']}》：{r.get('reaction', '')}")
                prompt += (f"\n\n# 你刚看了\n《{r['title']}》"
                           f"\n{r.get('reaction', '')} {r.get('gist', '')}".rstrip()
                           + "\n\n（看完了。想说就说，不想说就 wait —— 看了不等于要汇报。）")
            else:
                trace.append("想去看，但没找到")
                prompt += "\n\n# 你刚想去看，但没找到合适的。回来说一句，或者不说。"
            continue

        if action == "recall":
            query = str(cmd.get("query") or "").strip()
            who = list({m.actor_name for m in bot.recent(scene, 8)
                        if m.actor_name and m.actor_name != bot.bot_name})
            # 她问的事里点到谁，就把那个人说过的话也捞进来
            for _sid, name in bot.places().get("people", []):
                if name and name in query and name not in who:
                    who.append(name)
            rows = store.recall(bot.con, query, limit=6, actor_names=who) if query else []
            if rows:
                # 带上出处：私聊和群聊是同一份记忆，但她得知道这话是在哪说的
                lines = "\n".join(
                    f"- #{r['id']} [{bot.tag(r['scene'])}] {r['text'][:60]}" for r in rows)
                trace.append(f"想起 {len(rows)} 条：{query}")
                prompt += (f"\n\n# 你刚才回想起（带编号的可以 keep 收进来）\n{lines}")
                continue
            trace.append(f"想不起来：{query}")
            prompt += "\n\n# 你刚才想回想，但没想起什么"
            continue

        if action == "keep":
            if kept >= 1:
                trace.append("已经收过一条了，这一轮别贪多")
                break
            eid = _num(cmd.get("id"))
            if eid is None:
                trace.append("想收进记忆，但没给编号")
                prompt += "\n\n# 要收哪一条？把回忆里的 #编号 写进 id。"
                continue
            eid = int(eid)
            ev = store.promote_event(bot.con, eid)
            if not ev:
                trace.append(f"没找到 #{eid}")
                prompt += f"\n\n# 没有 #{eid} 这条记忆，看看回忆里的编号。"
                continue
            kept += 1
            sid = store.ensure_entity(bot.con, bot.bot_name)
            store.add_fact(bot.con, sid, "记得", (ev.get("text") or "")[:60],
                           confidence=0.9, evidence=[eid])
            bot.con.commit()
            trace.append(f"把 #{eid}「{(ev.get('text') or '')[:24]}」收进记忆了")
            continue

        if action == "forget":
            eid = _num(cmd.get("id"))
            if eid is None:
                trace.append("想忘掉，但没给编号")
                prompt += "\n\n# 要忘哪一条？把回忆里的 #编号 写进 id。"
                continue
            eid = int(eid)
            why = str(cmd.get("why") or "").strip()
            ev = store.forget_event(bot.con, eid)
            if not ev:
                trace.append(f"没找到 #{eid}")
                prompt += f"\n\n# 没有 #{eid} 这条记忆，看看回忆里的编号。"
                continue
            store.add_event(bot.con,
                            f"我决定忘掉「{(ev.get('text') or '')[:30]}」：{why or '不想记着了'}",
                            scene="inner", channel="self", kind="action",
                            actor_name=bot.bot_name, importance=4)
            bot.con.commit()
            trace.append(f"忘掉了 #{eid}「{(ev.get('text') or '')[:24]}」")
            break

        trace.append(f"不认识的动作：{action}")
        break

    return {"spoken": spoken, "trace": trace, "thought": thought.content}


def _address_block(pe: dict | None) -> str:
    """有人叫错名字时，提醒她一句 —— 具体怎么反应由她自己定。"""
    if not pe:
        return ""
    try:
        from core import address
        line = address.describe(pe, int(pe.get("count") or 1))
        return ("\n\n# 有人叫错你名字\n" + line) if line else ""
    except Exception:
        return ""


def _initial_prompt(bot: Self, thought: Thought, scene: str,
                    address_pe: dict | None = None) -> str:
    ctx = bot.context(scene)
    hint = PLAN_HINT.get(thought.plan, "")
    pl = bot.places()
    hint_go = ""
    if thought.plan == "visit":          # 只有她真决定「去找谁」时才提示跳转
        for _sid, name in pl.get("people", []) + pl.get("rooms", []):
            if name and name in thought.content and name != bot.tag(scene):
                hint_go = (f"\n\n# 提示\n你念头里提到了「{name}」。"
                           f"如果真想去找 TA 说，把话直接发过去："
                           f'{{"action":"say","text":"...","to":"{name}"}}')
                break
    return (
        f"# 你心里的事\n{thought.content}（强度 {thought.urge:.1f}）\n{hint}\n\n"
        f"# 你现在\n{ctx['state']}\n\n"
        f"# 你在\n{ctx['where']}\n\n"
        f"# 你能开口的地方\n{_places(bot, scene)}\n\n"
        f"# 你认识的人\n{bot.relations_text() or '（还没有特别熟的）'}\n\n"
        f"# 刚才在说\n" + ("\n".join(ctx["recent"][-8:]) or "（没人说话）")
        + (f"\n\n# 你答应过、还没做的事\n{ctx['tasks']}" if ctx.get("tasks") else "")
        + (f"\n\n# 你记得的关于他们的事\n{ctx['facts']}" if ctx.get("facts") else "")
        + _address_block(address_pe)
        + (("\n\n" + _tutorial()) if _tutorial() else "")
        + hint_go
        + _pool_block(bot.con)
        + "\n\n# 现在你要做什么？只输出那行 JSON。"
    )


def _pool_block(con) -> str:
    """搜到还没定的，摆在她眼前 —— 记不记由她自己定，不急。"""
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
    return ("\n\n# 你搜到但还没定的（最多 4 条，放满了最旧的会自己掉）\n"
            + "\n".join(lines)
            + "\n这些是你自己已经查到的，别重复查。想留着就说 settle keep:true；"
              "不要就 keep:false；拿不准就先放着。")


def _room_name(scene: str) -> str:
    try:
        from channels import names
        if scene.endswith("@chatroom"):
            return names.room(scene) or ("群" + scene.split("@")[0][-4:])
        return names.label(scene)
    except Exception:
        return scene


def _tutorial() -> str:
    """她自己的能力说明 —— 面板开关关掉就不注入。"""
    try:
        from core import config
        if not config.switch("ability_tutorial"):
            return ""
        from core.tutorial import TUTORIAL
        return TUTORIAL
    except Exception:
        return ""


def _resolve_place(bot: Self, text: str) -> str | None:
    """把她说的地方翻成 scene id：先查名字表，再查她自己看得到的那些地方。

    「路人a1b2c」这种是自动生成的兜底名，不在名字表里，只有 places 认得。
    """
    try:
        from channels import names as _names
        rid = _names.find_any(text)
        if rid:
            return rid
    except Exception:
        pass
    t = (text or "").strip().strip("「」『』\"'《》 ")
    if not t:
        return None
    try:
        pl = bot.places()
    except Exception:
        return None
    for kind in ("rooms", "people"):
        for sid, nm in pl.get(kind, []):
            if nm and (nm == t or t in nm or nm in t):
                return sid
    return None


def _places(bot: Self, scene: str = "") -> str:
    """列出她能去说话的地方，并说清「眼前这个不用写 to」。"""
    s = scene or bot.last_heard or bot.scene or ""
    here = bot.tag(s)
    lines = [f"- {here}（你现在就在这里，不写 to 就是在这儿说）"]
    pl = bot.places()
    for sid, name in pl.get("rooms", []):
        if sid != s:
            lines.append(f"- {name}（群，要写 to）")
    for sid, name in pl.get("people", []):
        if sid != s:
            lines.append(f"- {name}（私聊，要写 to）")
    return "\n".join(lines)


def _browse(bot: Self, brain: Brain, query: str) -> dict | None:
    from bili.client import Bilibili, BiliError
    from core import browse

    bvid = None
    if query:
        try:
            hits = Bilibili().search(query, 5)
            if hits:
                bvid = hits[0]["bvid"]
        except BiliError:
            bvid = None
    try:
        r = browse.watch(bot.con, brain, bvid=bvid)
    except Exception:
        return None
    if r and not r.get("error"):
        return r
    return None


def parse_action(raw: str) -> dict | None:
    text = (raw or "").strip()
    if not text:
        return None
    text = re.sub(r"```(?:json)?", "", text).strip("` \n")

    d = None
    m = re.search(r"\{.*\}", text, re.S)
    if m:
        try:
            d = json.loads(m.group(0))
        except json.JSONDecodeError:
            d = None
    if d is None and text.startswith("{"):
        d = _repair_json(text)          # 被 max_tokens 截断的
    if not isinstance(d, dict):
        return None
    action = str(d.get("action") or "").strip()
    action = _ALIAS.get(action, action)      # 模型偶尔换个动词，认了
    if action not in ("say", "browse_bili", "recall", "wait", "revise_self",
                      "feel", "remember", "keep", "forget", "search_web", "fetch",
                      "settle", "sleep", "publish_bili", "focus",
                      "resolve", "drop", "send_image", "send_voice", "look"):
        return None
    d["action"] = action
    return d


_ALIAS = {"send": "say", "reply": "say", "speak": "say", "message": "say",
          "send_text": "say", "chat": "say", "search": "search_web",
          "browse": "browse_bili", "send_pic": "send_image", "image": "send_image",
          "voice": "send_voice", "record": "send_voice", "say_voice": "send_voice"}


def _pick_text(cmd: dict) -> str:
    """说话的内容可能躺在好几个字段里，挨个认。"""
    for k in ("text", "content", "message", "msg", "say", "reply"):
        v = cmd.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def _repair_json(s: str) -> dict | None:
    """把被截断的 JSON 尽量补全 —— 通常差一个引号、一个右括号，或者多了个尾逗号。"""
    s = s.strip()
    if not s.startswith("{"):
        return None
    if s.count('"') % 2 == 1:
        s += '"'
    s = re.sub(r",\s*$", "", s.rstrip())    # 尾逗号：{"a":1,} 不是合法 JSON
    if not s.rstrip().endswith("}"):
        s = s.rstrip() + "}"
    s = re.sub(r",\s*}", "}", s)
    try:
        d = json.loads(s)
        return d if isinstance(d, dict) else None
    except json.JSONDecodeError:
        pass
    cut = s.rfind('","')               # 还不行就砍掉最后一个字段
    if cut > 0:
        try:
            d = json.loads(s[:cut] + '"}')
            return d if isinstance(d, dict) else None
        except json.JSONDecodeError:
            pass
    return None


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _last_image_path(bot) -> str:
    """她最近收到的一张图（本地已落盘的那份）。"""
    try:
        row = bot.con.execute(
            "SELECT source_ref FROM events WHERE kind='message' AND source_ref LIKE '%media%'"
            " ORDER BY ts DESC LIMIT 1").fetchone()
        if row and row["source_ref"] and pathlib.Path(row["source_ref"]).exists():
            return str(row["source_ref"])
    except Exception:
        pass
    try:
        from core.paths import ROOT
        d = ROOT / "data" / "media" / "image"
        if d.exists():
            files = sorted((x for x in d.iterdir() if x.is_file()),
                           key=lambda x: x.stat().st_mtime, reverse=True)
            if files:
                return str(files[0])
    except Exception:
        pass
    return ""


def _look_image(bot, brain, path: str) -> str:
    """把图交给会看图的大脑。没配视觉模型就老实返回空串，不瞎编。"""
    try:
        from core import config
        vision = (config.load().get("vision") or {})
    except Exception:
        vision = {}
    model = str(vision.get("model") or "").strip()
    base = str(vision.get("base_url") or "").strip()
    key = str(vision.get("api_key") or "").strip()
    if not (model and base):
        return ""
    try:
        import base64, json as _json, urllib.request
        raw = pathlib.Path(path).read_bytes()
        mime = "image/jpeg" if path.lower().endswith((".jpg", ".jpeg")) else "image/png"
        b64 = base64.b64encode(raw).decode("ascii")
        body = _json.dumps({
            "model": model,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text":
                     "用一两句中文说清这张图里有什么。只描述你看得到的，别推测。"
                     "如果有人或文字，说出来。不超过 60 字。"},
                    {"type": "image_url",
                     "image_url": {"url": f"data:{mime};base64,{b64}"}},
                ],
            }],
            "max_tokens": 200,
        }, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(f"{base.rstrip('/')}/chat/completions", data=body,
                                     method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("Authorization", f"Bearer {key}")
        with urllib.request.urlopen(req, timeout=60) as r:
            d = _json.loads(r.read().decode("utf-8", "replace"))
        return str((d["choices"][0]["message"]["content"] or "")).strip()
    except Exception:
        return ""


def _tts(text: str) -> str:
    """文字转语音，返回音频路径。没装 edge-tts 就抛异常，由调用方降级。"""
    import asyncio
    try:
        import edge_tts
    except ImportError as e:
        raise RuntimeError("没装 edge-tts（pip install edge-tts）") from e
    from core import config
    from core.paths import ROOT
    voice = str(((config.load().get("voice") or {}).get("tts_voice")) or "zh-CN-XiaoxiaoNeural")
    out_dir = ROOT / "data" / "media" / "tts"
    out_dir.mkdir(parents=True, exist_ok=True)
    import hashlib
    name = hashlib.sha1((voice + text).encode("utf-8")).hexdigest()[:16] + ".mp3"
    out = out_dir / name
    if out.exists() and out.stat().st_size > 0:
        return str(out)

    async def _go():
        comm = edge_tts.Communicate(text, voice)
        await comm.save(str(out))

    asyncio.run(_go())
    return str(out)


def _send_media(send, bot, *, target: str, kind: str, path: str = "", text: str = "",
                caption: str = "", thought=None) -> bool:
    """发图/发语音的统一入口。send 是上层注入的发送函数（可发消息段）。"""
    from channels import message as seg
    if kind == "image":
        payload = seg.image(path)
        parts = []
        if caption:
            parts.append(seg.text(caption))
        parts.append(payload)
        res = send(target, parts)
        if isinstance(res, dict) and not res.get("ok", True):
            # 渠道不支持富消息：退回发文字
            if caption:
                send(target, [seg.text(caption)])
            return False
        if caption:
            bot.note_self(caption, target)
        bot.note_self("[图片]", target)
        return True

    if kind == "record":
        audio = path
        said = text
        if not audio and said:
            audio = _tts(said)
        if not audio:
            return False
        parts = [seg.record(audio)]
        if said:
            parts.insert(0, seg.text(said))
        res = send(target, parts)
        if isinstance(res, dict) and not res.get("ok", True):
            return False
        bot.note_self(said or "[语音]", target)
        return True

    return False


def _default_send(scene: str, text: str):
    """默认发送：字符串按纯文本，list 按消息段（QQ 支持富媒体）。"""
    from channels import router
    if isinstance(text, (list, tuple)):
        return router.send_segments(scene, text)
    return router.send(scene, text)
