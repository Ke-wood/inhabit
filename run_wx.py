"""把阿鱼接进微信：收 hook 回调 -> 过人格和记忆 -> 用 hook 发回去。

    python run_wx.py                 # 从回调文件末尾开始听
    python run_wx.py --replay        # 从头重放（调试用，会重复回复旧消息）
"""

from __future__ import annotations

import argparse
import json
import math
import pathlib
import random
import sys
import time
from datetime import datetime, timedelta

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from channels import names, wxhook
from core import browse, diary, memfiles, recap, reflect, relations, sleep, urges
from core.urges import MEAN_INTERVAL_MIN
from core.brain import make_brain, split_segments
from core.loop import HeartFlow, Msg
from core.persona import build_system_prompt
from memory import store

def _v1_kwargs() -> dict:
    """v1 应答模式的参数，从 config 现读，面板改完下一轮生效。"""
    try:
        from core import config as _c
        th = _c.thresholds()
        return {"always_reply": bool(th.get("always_reply")),
                "recall_limit": int(th.get("recall_limit") or 12),
                "facts_per_person": int(th.get("facts_per_person") or 15),
                "events_per_person": int(th.get("events_per_person") or 6)}
    except Exception:
        return {}


def _ident():
    """身份标识从 config 读 —— 开源包里是空的，由用户填自己的。"""
    try:
        from core import config as _cfg
        return _cfg.ident()
    except Exception:
        return "", "", ("阿鱼",)


SELF_WXID, OWNER_WXID, BOT_NAMES = _ident()
INBOX = pathlib.Path(__file__).resolve().parent / "data" / "wx_inbox.jsonl"
RAW = pathlib.Path(__file__).resolve().parent / "data" / "wx_raw.jsonl"

IDLE_BEFORE_SPEAK = 6 * 60      # 安静这么久之后，可能主动说一句
PROACTIVE_GAP = 12 * 60         # 两次主动之间的最小间隔
BROWSE_GAP = 3 * 3600           # 多久自己去刷一次 B 站
LAST_BROWSE_KEY = "last_browse"
QUIET_HOURS = range(2, 7)       # 这个点不主动打扰


def _who(rec: dict) -> str:
    """通道上报的名字优先（QQ 有昵称），拿不到才查名字表。"""
    name = str(rec.get("actor_name") or "").strip()
    if name:
        return name
    return names.label(rec.get("actor_id") or "")


def log(msg: str) -> None:
    line = time.strftime("[%H:%M:%S] ") + msg
    try:
        print(line, flush=True)
    except UnicodeEncodeError:
        # GBK 控制台装不下的字符（U+2005 之类）替换成 ?，绝不让一条日志打死进程
        enc = getattr(sys.stdout, "encoding", None) or "utf-8"
        try:
            print(line.encode(enc, "replace").decode(enc, "replace"), flush=True)
        except Exception:
            pass
    except Exception:
        pass


SKIPS: dict[str, int] = {}
SKIP_SINCE = [0.0]


def _note_skip(why: str) -> None:
    """跳过的消息不逐条打，攒一分钟汇总一次，别把日志刷没。"""
    now = time.time()
    if SKIP_SINCE[0] == 0:
        SKIP_SINCE[0] = now
    if "type=51" in why:
        label = "微信同步回执"
    elif "10000" in why:
        label = "群系统消息"
    elif "10002" in why:
        label = "系统提示"
    elif "type=49" in why:
        label = "卡片/链接"
    elif "type=47" in why:
        label = "表情包"
    elif "type=3)" in why:
        label = "图片"
    elif "type=34" in why:
        label = "语音"
    elif "自己" in why:
        label = "自己发的"
    else:
        label = why
    SKIPS[label] = SKIPS.get(label, 0) + 1
    total = sum(SKIPS.values())
    idle = now - SKIP_SINCE[0]
    if (idle >= 60 and total >= 3) or idle >= 300:
        detail = "，".join(f"{k}×{v}" for k, v in sorted(SKIPS.items(), key=lambda kv: -kv[1]))
        log(f"跳过汇总：{detail}")
        SKIPS.clear()
        SKIP_SINCE[0] = now


def skip_line(raw: dict | None, rec: dict) -> str | None:
    """返回跳过原因，None 表示要处理。raw 可能为 None（原始文件里没对上）。"""
    t = (raw or {}).get("type") or rec.get("wx_type")
    if t == 10000:
        return "系统消息"
    if t in (10002, 51):
        return "系统通知"
    text = (rec.get("text") or "").strip()
    if not text:
        return "空消息"
    if t is not None and t != 1:
        return f"非文本(type={t})"
    # raw 没对上时的兜底：结构化 XML 不是人说的话
    if text.startswith("<") and ("<msg>" in text or text.startswith("<?xml")):
        return "结构化消息"
    sid = (raw or {}).get("sender") or rec.get("actor_id")
    scene = rec.get("scene") or ""
    low = {str(sid).lower(), str(scene).lower()}
    if low & {"filehelper"}:
        return "文件传输助手"
    if low & {"newsapp", "weixin", "qqnews"}:
        return "服务号消息"
    if sid == SELF_WXID:
        return "SELF"          # 自己号发的，由调用方再分清是谁
    return None


def maybe_extract(con, brain, state: dict) -> None:
    """安静的时候把刚才聊的东西沉淀成"关于某人的事实"，跨会话共享。"""
    now = time.time()
    if now - state["last_activity"] < 45:
        return
    if now - state.get("last_extract", 0.0) < 120:
        return
    cursor = state.get("extract_cursor", 0)
    latest = con.execute("SELECT COALESCE(MAX(id),0) FROM events").fetchone()[0]
    if latest <= cursor:
        return

    try:
        added, new_cursor = reflect.extract(con, brain, since_id=cursor)
    except Exception as e:
        log(f"整理记忆失败：{type(e).__name__}: {e}")
        return
    state["extract_cursor"] = new_cursor
    state["last_extract"] = now
    if added:
        log(f"记住 {len(added)} 条：" + "；".join(f"{a['who']}{a['fact']}" for a in added))
        for a in added:
            urges.from_fact(con, a["who"], a["fact"])
        con.commit()
        try:
            memfiles.export_people(con)      # 新记忆同步到「记忆」
        except Exception as e:
            log(f"同步到文件失败：{type(e).__name__}: {e}")


def maybe_diary(con, brain, state: dict) -> None:
    """深夜写一篇日记，一天只写一次。"""
    now = time.time()
    dt = datetime.fromtimestamp(now)
    if not diary.should_write(con, dt):
        return
    if now - state["last_activity"] < 60:
        return
    text = diary.write(con, brain, dt)
    if text:
        log(f"写了今天的日记：{text[:60]}…")


NUDGE = {
    "video": "你刚看完一个视频，一直想说说它。别报菜名，就说你的感受或吐槽。",
    "followup": "你想起一件事没问完。想问一句，别假装是刚想到的，就顺着问。",
    "person": "有个人好一阵没出现了。你想问问，但别显得很黏。",
    "boredom": "你有点闷，没什么事干。想找人说句话，随便什么。",
    "thought": "你心里有个念头冒出来了，想说一句。",
    "share": "你想跟他说说这个。不是汇报，是「哎你看这个」那种分享。"
             "一句就好，不用铺垫，不用问他感不感兴趣。",
}

# 分享欲现在只对他发
SHARE_TO = OWNER_WXID
SHARE_GAP_H = 4          # 至少隔这么久才会攒出新的分享欲


def seed_urges(con, state: dict) -> None:
    """造念头。不是定时间表，是看眼下有什么在"顶"。"""
    now = time.time()
    idle_h = (now - state["last_activity"]) / 3600
    created = False

    if idle_h > 2 and not urges.has_recent(con, "boredom", 3):
        urges.from_boredom(con)
        created = True

    # 分享欲：手边有点值得说的东西，就想给他看看
    if not urges.has_recent(con, "share", SHARE_GAP_H):
        row = con.execute(
            """SELECT text FROM events
               WHERE kind='observation' AND ts > ?
               ORDER BY importance DESC, ts DESC LIMIT 1""",
            ((datetime.now() - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%S"),)).fetchone()
        if row and row["text"]:
            urges.from_share(con, f"你手边有这么个事：{row['text'][:60]}", to=SHARE_TO)
            created = True

    if not urges.has_recent(con, "person", 12):
        row = con.execute(
            """SELECT e.name AS name, MAX(ev.ts) AS last
               FROM relations r
               JOIN entities e ON e.id = r.dst_id
               LEFT JOIN events ev ON ev.actor_name = e.name AND ev.kind='message'
               GROUP BY e.id HAVING last IS NOT NULL
               ORDER BY r.affinity DESC LIMIT 1""").fetchone()
        if row and row["last"]:
            try:
                gap = (datetime.now() - datetime.fromisoformat(row["last"])).total_seconds() / 3600
            except ValueError:
                gap = 0
            if gap > 8:
                urges.from_person(con, row["name"], gap)
                created = True

    if created:
        con.commit()


def maybe_speak_first(flows: dict, brain, state: dict, con) -> None:
    """不是"到点问一句"，是"憋不住了"。

    心里有念头（urges），强度随时间衰减；强度越高，越容易在这一刻开口。
    没有念头就永远不说话——这比定时问候更像人。
    """
    now = time.time()
    # 没人说过话时，默认对着某人的私聊说——不然她一个人待着就永远开不了口
    scene = state.get("scene") or SHARE_TO
    if not scene or state.get("awaiting_reply"):
        return
    if time.localtime(now).tm_hour in QUIET_HOURS:
        return

    last = state.get("last_urge_eval") or now
    state["last_urge_eval"] = now
    dt = min(120.0, max(0.0, now - last))

    u = urges.top(con, datetime.fromtimestamp(now))
    if not u:
        return

    # 泊松：强度 1.0 ≈ 平均 25 分钟憋不住一次；强度 0.5 ≈ 50 分钟
    lam = u["now_strength"] / (MEAN_INTERVAL_MIN * 60.0)
    p_now = 1 - math.exp(-lam * dt)
    # 每 10 分钟报一次"现在有多想说话"，方便看它在排队还是卡住了
    if now - state.get("last_urge_log", 0) > 120:
        state["last_urge_log"] = now
        eta = (1.0 / lam / 60) if lam > 0 else 999
        log(f"冲动：{u['kind']} 强度 {u['now_strength']:.2f}，平均 {eta:.0f} 分钟一次"
            f"（这一拍命中率 {p_now*100:.1f}%）")
    if random.random() > p_now:
        return

    flow = flows.get(scene)
    target_scene = u.get("scene") or scene
    if target_scene != scene:
        flow = flows.get(target_scene)
    if flow is None:
        flow = HeartFlow(con, scene=target_scene, bot_name=BOT_NAMES[0], brain=brain,
                         alt_names=BOT_NAMES[1:], **_v1_kwargs())
        flows[target_scene] = flow

    recent = [f"{n}：{t}" for _, _, n, t in flow.window[-8:]]
    mems = [f"{r['actor'] or '某人'}：{r['text']}" for r in
            store.recall(flow.con, u.get("note") or " ".join(recent[-2:]), limit=4)]
    system = build_system_prompt(flow.core, flow.rules, flow.state, flow._relations(),
                                 where=flow._where())
    user = (f"# 你心里憋着的事\n{u.get('note') or ''}\n\n"
            + ("# 刚才聊到\n" + "\n".join(recent) + "\n\n" if recent else "")
            + "# 你记得的事\n" + ("\n".join(f"- {m}" for m in mems) if mems else "（没什么特别的）")
            + f"\n\n{NUDGE.get(u['kind'], NUDGE['thought'])}"
              "\n说一句就够，短。真不想说就只回一个 - 。")

    try:
        raw = brain.speak(system=system, user=user, avoid=[])
    except Exception as e:
        log(f"主动发言失败：{type(e).__name__}: {e}")
        return

    raw = (raw or "").strip()
    if not raw or raw in {"-", "—", "……", "…", "没有", "没什么"}:
        urges.mark_used(con, u["id"])
        con.commit()
        log(f"憋着「{u['kind']}」（{u['now_strength']}），想了想没说出来")
        return

    segs = split_segments(raw)
    send_to = u.get("scene") or scene
    for i, seg in enumerate(segs):
        time.sleep(0.9 + 0.8 * i)
        try:
            r = wxhook.send_text(send_to, seg)
            log(f"憋不住了[{u['kind']} {u['now_strength']}]（+{0.9 + 0.8 * i:.1f}s）：{seg}  {r.get('retmsg')}")
        except Exception as e:
            log(f"主动发送失败：{type(e).__name__}: {e}")
            return
    urges.mark_used(con, u["id"])
    con.commit()
    state["last_proactive"] = now
    state["last_activity"] = now
    state["awaiting_reply"] = True
    flow.gate.record(now)


READ_HINTS = ("想看", "往前", "上面说", "更早", "之前说", "再往前", "翻翻", "接着说")


def maybe_wake(con, brain, state: dict) -> None:
    """到点自己醒。只看最近 30 条，剩下的她可以自己要求往前翻。"""
    st = store.get_state(con)
    if st.get("asleep") != "1":
        return
    until = sleep.until(con)
    now = datetime.now()
    if until and now < until:
        return

    info = sleep.wake(con, "到点自己醒")
    by_scene: dict[str, list] = {}
    for m in info["messages"]:
        by_scene.setdefault(m["scene"], []).append(m)

    parts = []
    for scene, items in by_scene.items():
        window, left = recap.take_window(items, 30)      # 只留最近 30 条
        label = names.room(scene) or ("私聊" if not scene.endswith("@chatroom") else "群里")
        body = "；".join(f"{m['actor_name']}：{m['text'][:26]}" for m in window)
        tail = f"（更早还有 {left} 条）" if left else ""
        parts.append(f"【{label}】{body}{tail}")
        if window:
            recap.remember_cursor(scene, window[0]["ts"], left)

    digest = "\n".join(parts) if parts else "没人说话"
    store.add_event(con, "我睡着了，刚醒。睡着的时候：\n" + digest, scene="inner",
                    channel="self", kind="observation", actor_name="阿鱼", importance=7)
    con.commit()
    log(f"醒了 | 睡了 {info.get('slept_since') or '?'} 到现在，期间 {len(info['messages'])} 条，"
        f"只读最近 30 条/会话")

    if info["messages"] and time.localtime().tm_hour not in QUIET_HOURS:
        scene = info["messages"][-1]["scene"]
        try:
            line = brain.speak(
                system="你刚睡醒，看到睡着期间最近的一些消息。说一句话，短，像刚醒的人。可以不说话就回 - 。",
                user="睡着期间最近的消息：\n" + digest, avoid=[])
        except Exception:
            return
        line = (line or "").strip()
        if line and line not in ("-", "—"):
            try:
                wxhook.send_text(scene, line)
                store.add_event(con, line, scene=scene, channel="wechat", kind="speech",
                                actor_id="self", actor_name="阿鱼", importance=4)
                con.commit()
                log(f"  醒来后说：{line}")
            except Exception as e:
                log(f"  醒来发言失败：{e}")


SLEEP_HINTS = ("睡了", "睡觉", "去睡", "晚安", "困了", "眯一会", "晚安啦")
SLEEP_HOURS = (0, 1, 2, 3, 4)
MEMO_HINTS = ("记一下", "记住", "存一下", "别忘了", "我得记着")


def _owner_says_goodnight(text: str) -> bool:
    return any(w in text for w in SLEEP_HINTS)


def maybe_sleep(con, brain, state: dict) -> None:
    """睡眠只能由某人的对话触发；别人聊得再晚也跟她睡不睡无关。"""
    now = time.time()
    dt = datetime.fromtimestamp(now)
    if sleep.asleep(con, dt):
        return
    if dt.hour not in SLEEP_HOURS:
        return
    # 必须是某人最近说过话（10 分钟内），且现在安静下来了
    if now - state.get("owner_last_ts", 0) > 600:
        return
    if now - state["last_activity"] < 60:
        return

    goodnight = _owner_says_goodnight(state.get("owner_last_text", ""))
    d = sleep.decide(con, brain, dt, hint="刚才他跟你说要去睡了" if goodnight else "")
    if not d:
        return
    if d.get("sleep"):
        sleep.go_to_sleep(con, d["wake_at"], d.get("reason", ""))
        log(f"决定睡了，{d['wake_at'].strftime('%m-%d %H:%M')} 醒（{d.get('reason') or '没理由'}）")
    else:
        log("今晚不睡")


def maybe_browse(con, brain, state: dict) -> None:
    """自己去刷个视频。"""
    now = time.time()
    dt = datetime.fromtimestamp(now)
    if sleep.asleep(con, dt) or dt.hour in QUIET_HOURS:
        return
    if now - state["last_activity"] < 300:          # 有人在聊就先不刷
        return
    last = float(store.get_state(con).get(LAST_BROWSE_KEY) or 0)
    if now - last < BROWSE_GAP:
        return
    store.set_state(con, LAST_BROWSE_KEY, str(now))
    con.commit()
    r = browse.watch(con, brain)
    if not r:
        log("想刷 B 站，没挑到视频")
    elif r.get("error"):
        log(f"刷 B 站失败：{r['error']}")
    else:
        log(f"刷了个视频：《{r['title'][:22]}》（{r['owner']}）")
        log(f"  她说：{r['reaction'][:40]}")
        # 观后感只进记忆库，不写「我自己的记忆」—— 那个文件留给真正想记的事
        urges.from_share(con, f"你刚看了《{r['title'][:20]}》，{r['reaction'][:28]}",
                         to=SHARE_TO, strength=0.85)
        con.commit()


def _singleton_port() -> int:
    """每个角色占一个端口：8790 + 序号。面板据此判断谁在跑。"""
    from core import config
    try:
        return config.profile_port(config.get("active"))
    except Exception:
        return 8790


def _acquire_singleton(port: int):
    """单例锁：同一角色只允许一个主循环。

    不同角色可以同时跑（各占各的端口）。
    """
    import socket
    import threading
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", port))
        s.listen(16)
    except OSError:
        s.close()
        return None

    # 必须把连接收下来再关掉，否则 backlog 塞满后 ctl 的探测会超时，
    # 被读成「没在跑」，然后又拉起一个实例。
    def _drain() -> None:
        while True:
            try:
                c, _ = s.accept()
                c.close()
            except OSError:
                return

    threading.Thread(target=_drain, daemon=True).start()

    return s


def main() -> None:
    # 控制台编码兜底：Windows GBK 控制台遇到装不下的字符会抛 UnicodeEncodeError；
    # 面板.bat 用 pythonw 启动时 sys.stdout 直接是 None。
    if sys.stdout is not None:
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--brain", default="auto", help="auto / mock / local / 任意 OpenAI 兼容端点名")
    ap.add_argument("--replay", action="store_true")
    ap.add_argument("--profile", default="", help="跑哪个角色（默认 config 里的 active）")
    ap.add_argument("--v2", action="store_true", help="自主模式（想→做）；不带则走 v1 应答/门控模式")
    args = ap.parse_args()

    from core import config as _cfg
    if args.profile:
        _cfg.set_value("active", value=args.profile)

    port = _singleton_port()
    _lock = _acquire_singleton(port)
    if _lock is None:
        log(f"这个角色已经在跑了（{port} 被占），这个进程退出")
        return

    INBOX.touch(exist_ok=True)
    RAW.touch(exist_ok=True)
    offset = 0 if args.replay else INBOX.stat().st_size

    con = store.init_db()
    brain = make_brain(args.brain)
    flows: dict[str, HeartFlow] = {}
    # 启动时只同步文件的增改，绝不判删除：文件可能还没导出过，
    # 或者上次导出之后 janitor 又抽了新事实（它们在文件里本来就不存在）。
    try:
        st = memfiles.import_people(con, forget=False)
        if any(st.values()):
            log(f"文件同步：改 {st['updated']} 条、加 {st['added']} 条、忘 {st['forgotten']} 条")
    except Exception as e:
        log(f"文件同步失败：{type(e).__name__}: {e}")
    if args.v2:
        main_v2(con, brain, offset)
        return
    log("（v1 旧架构，仅调试用；推荐 --v2）")
    state = {"last_activity": time.time(), "last_proactive": 0.0, "scene": None,
             "owner_last_ts": 0.0, "owner_last_text": ""}
    log(f"阿鱼上线，大脑={args.brain}，监听 {INBOX.name}")

    while True:
        try:
            size = INBOX.stat().st_size
            if size < offset:          # 文件被清空/重建
                offset = 0
            if size == offset:
                maybe_wake(con, brain, state)
                maybe_browse(con, brain, state)
                maybe_extract(con, brain, state)
                maybe_diary(con, brain, state)
                maybe_sleep(con, brain, state)
                seed_urges(con, state)
                maybe_speak_first(flows, brain, state, con)
                time.sleep(3.0)
                continue
            with INBOX.open("rb") as f:
                f.seek(offset)
                chunk = f.read()
                offset = f.tell()
        except Exception as e:
            # 以前只捕 FileNotFoundError：任何别的异常都会打死进程，
            # 而重启时 offset 又跳到文件尾 —— 那段时间的消息就永久丢了。
            log(f"读收件箱出错：{type(e).__name__}: {e}")
            try:
                import traceback
                log("  " + traceback.format_exc(limit=3).replace("\n", " | ")[:400])
            except Exception:
                pass
            time.sleep(1.0)
            continue

        for line in chunk.decode("utf-8", "replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue

            scene = rec["scene"]

            # 多角色同跑：只处理属于自己渠道的消息，别串档。
            # 判定失败就放行 —— 宁可多处理一条，也不能把正常消息挡掉。
            try:
                if not _cfg.scene_belongs_to(scene):
                    continue
            except Exception:
                pass

            # 群系统消息（谁邀请你进群、谁改了群名）也记下来，但不当成聊天
            if rec.get("wx_type") == 10000:
                store.add_event(con, rec["text"], scene=scene, channel="wechat",
                                kind="system", importance=5)
                con.commit()
                log(f"系统消息：{rec['text'][:40]}")
                continue

            # 不再去原始文件里猜 raw：归一化记录里已经带了 wx_type / msgid，
            # 旧写法 `if raw` 会让整条过滤链被跳过（XML 卡片就是这么进的库）
            why = skip_line(None, rec)
            if why:
                _note_skip(why)
                continue

            flow = flows.get(scene)
            if flow is None:
                flow = HeartFlow(con, scene=scene, bot_name=BOT_NAMES[0], brain=brain,
                                 alt_names=BOT_NAMES[1:], **_v1_kwargs())
                flows[scene] = flow

            now = time.time()
            state["last_activity"] = now
            state["scene"] = scene
            if rec["actor_id"] == OWNER_WXID:
                state["owner_last_ts"] = now
                state["owner_last_text"] = rec["text"]

            if sleep.asleep(con, datetime.fromtimestamp(now)):
                flow.record_only([Msg(rec["actor_id"], _who(rec),
                                      rec["text"], now)], now)
                con.commit()
                log(f"[睡着] {_who(rec)}：{rec['text'][:30]}（只记下，不回）")
                continue

            is_group = bool(rec.get("is_group"))
            mentioned = any(n in rec["text"] for n in BOT_NAMES)
            in_conv = flow.in_conversation(now)
            directed = (not is_group) or mentioned or in_conv
            mode = "对话" if directed else "群聊"
            try:
                d = flow.tick([Msg(rec["actor_id"], _who(rec),
                                   rec["text"], now)], now, directed=directed)
            except Exception as e:
                log(f"思考出错：{type(e).__name__}: {e}")
                continue

            log(f"[{mode}] {_who(rec)}：{rec['text'][:40]}  "
                f"→ {'说' if d.speak else '不说'}（p={d.prob:.2f}/{d.threshold:.2f}，{d.reason}）")
            who = _who(rec)
            rel = relations.touch(con, who, replied=d.speak, text=rec["text"])
            if rel and abs(rel.get("delta", 0)) >= 1:
                log(f"  关系：{who} 好感 {rel['affinity']:+.1f}（{rel['label']}）")
            con.commit()
            if not d.speak:
                continue

            # 对话模式要即时；群聊模式留出打字间隔
            base = 0.4 if directed else 0.9
            step = 0.4 if directed else 0.8
            for i, seg in enumerate(d.segments):
                delay = base + step * i
                time.sleep(delay)
                try:
                    r = wxhook.send_text(scene, seg)
                    log(f"  → 发出（+{delay:.1f}s）：{seg}  {r.get('retmsg')}")
                except Exception as e:
                    log(f"  → 发送失败：{type(e).__name__}: {e}")
                    break

            # 她说"记一下"，就真的写进自己的记忆
            for seg in d.segments:
                if any(w in seg for w in MEMO_HINTS) and "?" not in seg:
                    memfiles.append_self_memo(seg[:60])
                    log(f"  她记下了：{seg[:40]}")
                    break

            # 她自己说想往前看：把更早的一批塞进下一轮
            if any(any(w in s for w in READ_HINTS) for s in d.segments):
                older = recap.more(con, scene, 30)
                if older:
                    flow.pending_read = recap.format_lines(older)
                    log(f"  她要往前翻，加载了 {len(older)} 条（更早还剩 {recap.remaining(scene)} 条）")
                else:
                    log("  她想往前翻，但已经没有更早的了")

            # 她自己说了要去睡：那就真去睡（夜里才行，白天说"睡"是在聊睡觉这件事）
            hour = datetime.now().hour
            if (rec["actor_id"] == OWNER_WXID and hour in (22, 23, 0, 1, 2, 3, 4)
                    and any(any(w in s for w in SLEEP_HINTS) for s in d.segments)):
                d2 = sleep.decide(con, brain, datetime.fromtimestamp(time.time()),
                                  hint="你自己刚说要去睡了", force=True)
                if d2 and d2.get("sleep"):
                    sleep.go_to_sleep(con, d2["wake_at"], d2.get("reason") or "自己说要睡")
                    log(f"她自己说要去睡，定 {d2['wake_at'].strftime('%H:%M')} 醒")


def main_v2(con, brain, offset: int) -> None:
    """v2：她一直在，消息只是刺激之一。"""
    from core.live import Live

    live = Live(con, brain, log=log, default_scene=SHARE_TO)
    log(f"阿鱼 v2 上线，大脑={type(brain).__name__}，监听 {INBOX.name}")
    log("心智：想 → 念头 → 做（say / browse_bili / search_web / recall / wait）")

    # 桌宠的输入栏：用户在她身上打字，走这条上行通道进来。
    # 起不来（端口被占）不影响主循环，只是桌宠那边会提示「她不在线」。
    try:
        from core import pet_input
        import queue as _q
        _pet_q: "_q.Queue[str]" = _q.Queue()

        def _on_pet_input(text: str) -> None:
            _pet_q.put(text)

        pet_input.serve(_on_pet_input)
        log(f"桌宠输入：{pet_input.scene()} ← {pet_input.CORE_PORT}")
    except Exception as e:
        _pet_q = None
        log(f"桌宠输入没起来（不影响聊天）：{type(e).__name__}: {e}")

    while True:
        try:
            size = INBOX.stat().st_size
            if size < offset:                 # 文件被清空/重建
                offset = 0
            if size > offset:
                with INBOX.open("rb") as f:
                    f.seek(offset)
                    chunk = f.read()
                    offset = f.tell()
                for line in chunk.decode("utf-8", "replace").splitlines():
                    _v2_handle(con, live, line)
            was = live.paused
            live.paused = sleep.asleep(con, datetime.now())
            if was and not live.paused:          # 到点自己醒
                live.wake_up()
            if not live.paused and live.maybe_sleep():
                live.paused = True               # 刚决定去睡
            # 先把桌宠输入排空：它和文件收件箱是两条独立的来源
            if _pet_q is not None:
                try:
                    while True:
                        text = _pet_q.get_nowait()
                        live.feed({
                            "scene": pet_input.scene(),
                            "actor_id": "desk_user",
                            "actor_name": "你",
                            "text": text,
                            "ts": time.time(),
                            "is_group": False,
                        })
                        log(f"[桌宠] 你：{text[:40]}")
                except _q.Empty:
                    pass
            live.tick()
            # 轮询间隔按场景画像走：聊天 2 秒（和以前一样），直播间 0.2 秒。
            # 以前这里写死 2.0 —— 直播场景下光标还没刷新，弹幕已经刷过去了。
            time.sleep(max(0.05, live.profile().poll_sec))
        except FileNotFoundError:
            time.sleep(1.0)
        except Exception as e:
            log(f"v2 循环出错：{type(e).__name__}: {e}")
            time.sleep(3.0)


def _v2_handle(con, live, line: str) -> None:
    line = line.strip()
    if not line:
        return
    try:
        rec = json.loads(line)
    except json.JSONDecodeError:
        return
    # 多个角色同时跑时，只处理属于自己渠道的消息，别串
    try:
        from core import config as _cfg
        if not _cfg.scene_belongs_to(rec.get("scene")):
            return
    except Exception:
        pass
    why = skip_line(None, rec)
    if why == "SELF":
        _handle_self(con, live, rec)
        return
    if why:
        _note_skip(why)
        return
    live.feed(rec)


def _handle_self(con, live, rec: dict) -> None:
    """自己号发出来的消息：分清是她自己发的，还是某人借她的号打的字。"""
    text = (rec.get("text") or "").strip()
    if not text:
        return
    # 她通过 act 发出去时已经写过 speech 记录，90 秒内同文本 = 她自己发的
    since = (datetime.now() - timedelta(seconds=90)).strftime("%Y-%m-%dT%H:%M:%S")
    row = con.execute(
        "SELECT 1 FROM events WHERE kind='speech' AND text=? AND ts > ? LIMIT 1",
        (text, since)).fetchone()
    if row:
        return                      # 自己刚说的，库里已经有了

    # 不是她说的 —— 某人借她的号打的字，当成某人本人说
    rec = dict(rec)
    rec["actor_id"] = OWNER_WXID
    rec["actor_name"] = ""
    live.feed(rec)
    log(f"[借号] 某人（用阿鱼的号）：{text[:40]}")


if __name__ == "__main__":
    main()
