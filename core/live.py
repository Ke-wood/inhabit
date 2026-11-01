"""v2 主循环：一个持续活着的她。

和旧 run_wx 的区别：
- 旧：消息进来 → 门判概率 → 说
- 新：消息进来先落到她身上 → 她"想"出一个念头 → 念头决定做什么

护栏（不是决策，是生理约束）：
- 刚想过就缓一缓，别每条消息都想一遍
- 刚说过话就闭嘴一阵
- 深夜不主动开口
"""

from __future__ import annotations

import random
import socket
import time
from datetime import datetime

from channels import names
from core import address, config, sleep, tasks
from core.act import act
from core.janitor import Janitor
from core.loop import Msg
from core.mind import think
from core.self import QUIET_HOURS, Self
from memory import store

SLEEP_HOURS = (0, 1, 2, 3, 4)


def _port_open(port: int, host: str = "127.0.0.1", timeout: float = 0.5) -> bool:
    try:
        s = socket.create_connection((host, port), timeout=timeout)
        s.close()
        return True
    except OSError:
        return False


def _recent_address(pending: list[dict], scene: str) -> dict | None:
    """这个场景最近一条「叫错名字」的记录（只认最近 5 分钟内的）。"""
    for item in reversed(pending[-10:]):
        if item.get("scene") != scene:
            continue
        pe = item.get("address")
        if pe:
            return pe
    return None


def _media_ref(rec: dict) -> list[str]:
    """从归一化记录里挑出本地媒体路径（图片/语音）。"""
    out: list[str] = []
    for s in (rec.get("segments") or []):
        d = s.get("data") or {}
        f = str(d.get("local_file") or "")
        if f:
            out.append(f)
    return out


class Live:
    def __init__(self, con, brain, *, bot: Self | None = None, log=print,
                 sender=None, min_think_gap: float | None = None,
                 idle_think_gap: float | None = None, speak_gap: float | None = None,
                 focus_gap: float | None = None,
                 min_urge: float = 0.35, default_scene: str = ""):
        self.con = con
        self.brain = brain
        self.bot = bot or Self(con, bot_name=(config.ident()[2] or ("阿鱼",))[0],
                               alt_names=tuple((config.ident()[2] or ("阿鱼",))[1:]))
        self.log = log
        self.sender = sender
        # 传了就用传的，没传就交给 config（每拍现读，UI 改完立刻生效）
        th = config.thresholds()
        self._t_min = min_think_gap if min_think_gap is not None else th["min_think_gap"]
        self._t_idle = idle_think_gap if idle_think_gap is not None else th["idle_think_gap"]
        self._t_focus = focus_gap if focus_gap is not None else th["focus_gap"]
        self._t_speak = speak_gap if speak_gap is not None else th["speak_gap"]
        self.min_urge = min_urge
        self.default_scene = default_scene

        self.pending: list[dict] = []
        self.last_think = 0.0
        self.last_speak = 0.0
        self.last_browse = 0.0
        self.last_search = 0.0
        self.retry_at = 0.0
        self.last_active = time.time()
        self.paused = False
        self.stats = {"fed": 0, "thought": 0, "spoke": 0, "browsed": 0, "searched": 0}
        self.last_health = 0.0
        self.janitor = Janitor(con, brain, log=log)
        self._resume()

    # 阈值每拍现读 —— 面板上改完下一拍就生效
    @property
    def min_think_gap(self) -> float:
        return float(config.thresholds().get("min_think_gap", self._t_min))

    @property
    def idle_think_gap(self) -> float:
        return float(config.thresholds().get("idle_think_gap", self._t_idle))

    @property
    def focus_gap(self) -> float:
        return float(config.thresholds().get("focus_gap", self._t_focus))

    @property
    def speak_gap(self) -> float:
        return float(config.thresholds().get("speak_gap", self._t_speak))

    @property
    def browse_gap_sec(self) -> float:
        return float(config.thresholds().get("browse_gap", 3600))

    @property
    def search_gap_sec(self) -> float:
        """网搜的最小间隔。比 B 站频繁一点，但别让她一分钟查一遍同一件事。"""
        return float(config.thresholds().get("search_gap", 600))

    def _resume(self) -> None:
        """重启后：最近有人跟她说话，就当成还没处理的。"""
        scene = self.bot.last_heard or self.bot.scene
        if not scene:
            return
        recent = self.bot.recent(scene, 6)
        if not recent or recent[-1].actor_id == "self":
            return
        for m in recent[-3:]:
            self.pending.append({"scene": scene, "who": m.actor_name, "text": m.text,
                                 "ts": m.ts, "is_group": scene.endswith("@chatroom")})
        self.log(f"· 接着上次：{scene} 有 {len(self.pending)} 条没回的")

    # ---------------------------------------------------------------- 输入
    def feed(self, rec: dict, now: float | None = None) -> None:
        """一条已归一化的微信消息。"""
        now = now or time.time()
        scene = rec.get("scene") or ""
        if not scene or not (rec.get("text") or "").strip():
            return
        # 用消息自带的时间戳（秒）；离谱值（毫秒时间戳、未来时间）退回当前时间
        ts = now
        try:
            raw_ts = float(rec.get("ts"))
            if 0 < raw_ts <= now + 300:
                ts = raw_ts
        except (TypeError, ValueError):
            pass
        # 名字优先用通道上报的（QQ 上报里就有昵称/群名片），
        # 拿不到才回落到名字表 —— 否则 QQ 消息会全变成「路人XXXX」
        who = str(rec.get("actor_name") or "").strip()
        if not who:
            who = names.label(rec.get("actor_id") or "")
        msg = Msg(rec.get("actor_id") or "", who,
                  rec["text"], ts, str(rec.get("msgid") or ""))
        self.bot.observe(msg, scene, channel=str(rec.get("channel") or "wechat"))
        # 图片/语音：把本地落盘路径记下来，她后面才能「看」或「听」
        media = _media_ref(rec)
        if media:
            self._attach_media(scene, media)
        # 任务 / 共同经历：当场就记，不等后台整理
        try:
            tasks.capture(self.con, rec["text"],
                          who=msg.actor_name if msg.actor_name != self.bot.bot_name else "")
        except Exception:
            pass
        # 被叫错名字：记次数，挂在这条消息上，这一拍就让她知道
        hit = None
        try:
            hit = address.detect(rec["text"], bot_names=self.bot.bot_names)
            if hit:
                hit["count"] = address.bump_wrong(self.con, hit["hit"])
        except Exception:
            hit = None
        self.pending.append({"scene": scene, "who": msg.actor_name,
                             "text": rec["text"], "ts": ts,
                             "is_group": bool(rec.get("is_group")),
                             "media": media, "address": hit})
        self.last_active = now
        self.stats["fed"] += 1
        self.con.commit()

    def _attach_media(self, scene: str, media: list[str]) -> None:
        """把刚收到的那条消息的 source_ref 补成本地媒体路径。"""
        path = media[0] if media else ""
        if not path:
            return
        try:
            eid = self.con.execute(
                "SELECT id FROM events WHERE scene=? ORDER BY id DESC LIMIT 1",
                (scene,)).fetchone()
            if eid:
                store.bind_source(self.con, int(eid["id"]), path)
                self.con.commit()
        except Exception:
            pass

    def _direct(self, batch: list[dict]) -> bool:
        """是不是直接冲她来的：看最新那条，别让旧消息带偏。

        以前只要批里有一条私聊就整批算私聊 —— 系统通知 roomid 为空，
        会把群聊误判成私聊。
        """
        if not batch:
            return False
        last = batch[-1]
        if not last.get("is_group"):
            return True
        text = last.get("text") or ""
        if any(n in text for n in self.bot.bot_names):
            return True
        # 没点名，但明显在对她说话（抱怨不理他、问 AI 怎么回事）
        hints = ("不鸟我", "不理我", "不回我", "卡死我", "这ai", "这AI", "机器人",
                 "你是不是", "你怎么不")
        return any(h in text for h in hints)

    # ---------------------------------------------------------------- 一拍
    def _health(self, now: float) -> None:
        """收件服务还活着吗。

        它悄悄死过一次（导入错误），主循环毫无察觉，微信消息进不来，
        她看上去就是"突然不理人了"。这里每分钟探一次，状态写进 self_state
        好让面板看得见，掉线时留一行日志。
        """
        if now - self.last_health < 60:
            return
        self.last_health = now
        ok = _port_open(8771)
        prev = store.get_state(self.con).get("inbox_ok")
        if ok and prev == "0":
            self.log("· 收件服务(8771)回来了")
        elif not ok and prev != "0":
            self.log("⚠ 收件服务(8771)不响应 —— 微信消息进不来，去重起一下")
        now_flag = "1" if ok else "0"
        if prev != now_flag:
            store.set_state(self.con, "inbox_ok", now_flag)
            self.con.commit()

    def tick(self, now: float | None = None) -> dict | None:
        now = now or time.time()
        self._health(now)                # 先看一眼收件服务还活着没
        if self.paused:
            self.janitor.tick(now)       # 睡着的时候让整理工干活
            return None
        if now < self.retry_at:          # 刚被护栏拦下，等一下再试
            return None

        scene = self.bot.last_heard or self.bot.scene or self.default_scene
        direct = False
        if self.pending:
            if now - self.last_think < self.min_think_gap:
                return None
            batch, self.pending = self.pending[:12], self.pending[12:]
            direct = self._direct(batch)
            # 按场景分组摊开：同时几个地方有人说话时，别糊成一团
            by_scene: dict[str, list[dict]] = {}
            for b in batch:
                by_scene.setdefault(b["scene"], []).append(b)
            if len(by_scene) > 1:
                lines = []
                for sid, items in by_scene.items():
                    lines.append(f"【{self.bot.tag(sid)}】" + "；".join(
                        f"{b['who']}：{b['text'][:50]}" for b in items[-4:]))
                trigger = ("同时在几个地方有人说话：\n" + "\n".join(lines)
                           + "\n（你想先回哪个，就用 to 指定那个地方；只回一个就不用写）")
                why = "几个地方同时有人说话"
            else:
                trigger = ("刚才在说：\n"
                           + "\n".join(f"[{self.bot.tag(b['scene'])}] {b['who']}：{b['text'][:60]}"
                                       for b in batch))
                why = "有人直接冲你说话（不一定是点名）" if direct else "群里有人说话了"
            # 回话默认回消息最密/最新的那个场景
            scene = max(by_scene, key=lambda s: (len(by_scene[s]), by_scene[s][-1]["ts"]))
        else:
            focusing = self.bot.focusing(now)
            gap = self.focus_gap if focusing else self.idle_think_gap
            if now - self.last_think < gap:
                self.janitor.tick(now)   # 没人说话的空档，正好整理
                return None
            hour = datetime.now().hour
            if hour in config.thresholds().get("quiet_hours", QUIET_HOURS) and not focusing:
                return None
            if focusing:
                trigger = f"（你按了「认真想一会儿」，正在想：{self.bot.focus_topic}）"
                why = "思考时刻"
            else:
                trigger = "（没人说话。可以继续待着，也可以去做点什么）"
                why = "闲下来了"
            # 主动开口回她自己上次说话的地方，不是「最后跟她说话的人」
            scene = self.bot.last_spoke or self.bot.scene or self.default_scene
            if not scene:
                self.log("· 没有可用的场景，跳过主动想")
                return None

        t = think(self.bot, self.brain, trigger=trigger, why=why, scene=scene)
        self.last_think = now
        if t is None:
            return {"thought": None, "spoken": []}

        self.stats["thought"] += 1
        self.log(f'· 念头[{t.plan} {t.urge:.2f}/{t.hesitate:.2f}]：{t.content}')

        # 冲动 vs 纠结：不是过闸门，是比谁的劲大
        # 私聊/被点名是一对一说话，门槛比群聊松一档 ——
        # 人家正对着你说话，不该和「群里一堆人在聊」用同一个标准。
        th = config.thresholds()
        floor = th["urge_floor"] * (0.6 if direct else 1.0)
        band = th["score_band"] * (0.67 if direct else 1.0)
        hard = -band * (2.1 if direct else 1.0)
        if t.urge < floor:
            self.log("  （没什么劲头，先放着）")
            return {"thought": t.content, "spoken": []}
        score = t.urge - t.hesitate
        if score < hard:
            self.log(f"  （犹豫压过去了：想 {t.urge:.2f} / 犹豫 {t.hesitate:.2f}）")
            return {"thought": t.content, "spoken": []}
        if abs(score) <= band:
            p = t.urge / (t.urge + t.hesitate) if (t.urge + t.hesitate) > 0 else 0.5
            if random.random() > p:
                self.log(f"  （五五开，这次没动：p={p:.2f}）")
                return {"thought": t.content, "spoken": []}
        if t.plan == "none":
            self.log("  （只是想想，不做什么）")
            self._close(t, "done")
            return {"thought": t.content, "spoken": []}
        # B 站限流：不把念头扔掉 —— 改成去网上查同一件事。
        # 以前这里直接 return，结果她每分钟把同一句话重想一遍（念叨的由来）。
        if t.plan == "browse_bili" and now - self.last_browse < self.browse_gap_sec:
            if now - self.last_search < self.search_gap_sec:
                self.log("  （B 站刚看过，网上也刚查过，先放着）")
                return {"thought": t.content, "spoken": []}
            self.log("  （刚看过 B 站 —— 这件事改去网上查）")
            t.plan = "search_web"
        elif t.plan in ("search_web", "fetch") and now - self.last_search < self.search_gap_sec:
            self.log("  （刚查过，别重复）")
            return {"thought": t.content, "spoken": []}
        # 护栏只拦"她主动连续开口"；别人直接找她时不拦
        if not direct and now - self.last_speak < self.speak_gap:
            self.log("  （刚说过话，先不开口，过会儿再看）")
            self.pending = batch + self.pending
            self.retry_at = now + 30
            return {"thought": t.content, "spoken": []}

        r = act(self.bot, self.brain, t, scene=scene, send=self._send,
                address_pe=_recent_address(self.pending, scene))
        self._close(t, "done")
        if any("看了《" in line for line in r["trace"]):
            self.stats["browsed"] += 1
            self.last_browse = now
        if any(line.startswith(("搜到", "没搜到")) for line in r["trace"]):
            self.stats["searched"] += 1
            self.last_search = now
        if r["spoken"]:
            self.stats["spoke"] += 1
            self.last_speak = now
        for line in r["trace"]:
            self.log(f"  └ {line}")
        self.con.commit()
        # 她说完要睡（act 里睡了），这一拍就停 —— 别睡下了还在想
        if sleep.asleep(self.con, datetime.now()):
            self.paused = True
            self.log("  （睡了，先不想了）")
        return r

    # ---------------------------------------------------------------- 睡与醒
    def maybe_sleep(self, now: float | None = None) -> bool:
        """深夜、安静、她困了 —— 自己决定去睡。"""
        now = now or time.time()
        dt = datetime.fromtimestamp(now)
        if dt.hour not in SLEEP_HOURS:
            return False
        if self.pending:                      # 还有人没回，先不睡
            return False
        if now - self.last_active < 600:       # 刚有人说话，缓一会儿
            return False
        try:
            d = sleep.decide(self.con, self.brain, dt)
        except Exception as e:
            self.log(f"· 决定睡不睡时出错：{type(e).__name__}: {e}")
            return False
        if not d or not d.get("sleep"):
            return False
        self.paused = True                    # 先停下思考，再睡
        sleep.go_to_sleep(self.con, d["wake_at"], d.get("reason") or "")
        self.log(f"· 睡了，{d['wake_at']:%H:%M} 醒（{d.get('reason') or '困了'}）")
        return True

    def wake_up(self, now: float | None = None) -> None:
        """到点醒了：先看看睡着期间发生了什么。"""
        try:
            info = sleep.wake(self.con, "到点自己醒")
        except Exception as e:
            self.log(f"· 醒来时出错：{type(e).__name__}: {e}")
            return
        msgs = info.get("messages") or []
        self.log(f"· 醒了，睡了 {info.get('slept_since') or '?'}，期间 {len(msgs)} 条")
        if not msgs:
            return
        digest = "；".join(f"{m['actor_name']}：{(m['text'] or '')[:30]}" for m in msgs[-10:])
        try:
            store.add_event(self.con, f"我睡了一觉，睡着期间：{digest}", scene="inner",
                            channel="self", kind="observation",
                            actor_name=self.bot.bot_name, importance=6)
            self.con.commit()
        except Exception:
            pass

    # ---------------------------------------------------------------- 输出
    def _close(self, thought, status: str = "done") -> None:
        """念头处理完就关掉，别一直挂在池子里重复冒出来。"""
        if getattr(thought, "id", None):
            store.set_thought_status(self.con, thought.id, status)
            self.con.commit()

    def _send(self, scene: str, text: str):
        if self.sender:
            return self.sender(scene, text)
        from channels import router
        r = router.send(scene, text)
        if r.get("ok"):
            self.log(f"  → {text}  success")
        else:
            self.log(f"  → 发送失败：{r.get('error') or r.get('retmsg') or r}")
        return r
