"""她这个人：跨场景的主体状态。

HeartFlow 回答「这一段对话怎么接」，Self 回答「这个人现在怎么样」。
群聊和私聊只是她的两个窗口，不是两个人 —— 窗口共享记忆、心情和念头。
"""

from __future__ import annotations

import time
from datetime import datetime

from core import persona
from core.loop import Msg, importance_of
from memory import store

WINDOW_KEEP = 40          # 每个场景保留最近多少条（有界）
QUIET_HOURS = range(2, 7)
WEEKDAY = "一二三四五六日"


def ago(ts: float, now: float | None = None) -> str:
    """把时间戳说成人话：刚刚 / 23分钟前 / 3小时前 / 昨天21:40 / 3天前。"""
    now = now or time.time()
    d = max(0.0, now - ts)
    if d < 60:
        return "刚刚"
    if d < 3600:
        return f"{int(d // 60)}分钟前"
    if d < 6 * 3600:
        return f"{int(d // 3600)}小时前"
    try:
        then = datetime.fromtimestamp(ts)
        days = (datetime.fromtimestamp(now).date() - then.date()).days
        if days == 0:
            return f"今天{then:%H:%M}"
        if days == 1:
            return f"昨天{then:%H:%M}"
        if days < 7:
            return f"{days}天前"
        return f"{then:%m-%d}"
    except Exception:
        return f"{int(d // 3600)}小时前"


class Self:
    def __init__(self, con, *, bot_name: str = "阿鱼", alt_names: tuple[str, ...] = ()):
        self.con = con
        self.bot_name = bot_name
        self.bot_names = tuple(dict.fromkeys((bot_name,) + tuple(alt_names)))
        self.state = store.get_state(con)
        # 她现在只知道自己这一句。别的性格靠相处长出来，不靠卡片灌。
        self.identity = (persona.read_identity()
                         or self.state.get("identity")
                         or "十八岁，女的")
        self.windows: dict[str, list[Msg]] = {}
        self.scene: str | None = None        # 最近有动静的地方
        self.last_heard: str | None = None   # 最近一次「有人跟我说话」的地方
        self.last_spoke: str | None = None   # 我最近一次开口的地方
        self.last_activity = time.time()
        self.last_proactive = 0.0
        self.awaiting_reply = False
        # 思考时刻：她自己按下的加速键，期间想得更勤
        try:
            self.focus_until = float(self.state.get("focus_until") or 0)
        except (TypeError, ValueError):
            self.focus_until = 0.0
        self.focus_topic = self.state.get("focus_topic") or ""
        self._warm_up()

    def _warm_up(self, keep: int = 20) -> None:
        """从库里把最近几段对话装回窗口 —— 重启后别断片。"""
        try:
            rows = self.con.execute(
                "SELECT scene, actor_id, actor_name, text, ts FROM events "
                "WHERE kind IN ('message','speech') AND scene NOT IN ('bili','inner') "
                "ORDER BY ts DESC LIMIT 120").fetchall()
        except Exception:
            return
        for r in reversed(list(rows)):
            try:
                ts = datetime.fromisoformat(r["ts"]).timestamp()
            except (TypeError, ValueError):
                continue
            self.windows.setdefault(r["scene"], []).append(
                Msg(r["actor_id"] or "", r["actor_name"] or "", r["text"] or "", ts))
        for w in self.windows.values():
            del w[:-keep]
        if rows:
            self.scene = rows[0]["scene"]
            for r in rows:                   # 最近一条别人说的，就是"谁在等我"
                if (r["actor_id"] or "") != "self":
                    self.last_heard = r["scene"]
                    break

    # ---------------------------------------------------------------- 感知
    def observe(self, msg: Msg, scene: str, channel: str = "wechat") -> None:
        """记一条消息，进对应窗口，更新"最后活动时间"。"""
        if msg.actor_name and msg.actor_name not in ("对方", "某人", "self"):
            store.ensure_entity(self.con, msg.actor_name)
        if scene.endswith("@chatroom"):
            try:
                from channels import names
                names.ensure_room(scene)      # 新群先领个临时名，别跟别的群混
            except Exception:
                pass
        # 用消息自己的时间，不是处理时间 —— 否则重启补处理的消息会被当成"刚刚"
        try:
            ts_iso = datetime.fromtimestamp(msg.ts).strftime("%Y-%m-%dT%H:%M:%S")
        except (OverflowError, OSError, ValueError):
            ts_iso = None
        store.add_event(self.con, msg.text, scene=scene, channel=channel,
                        actor_id=msg.actor_id, actor_name=msg.actor_name,
                        kind="message", importance=importance_of(msg.text),
                        ts=ts_iso, source_ref=(msg.source_ref or None))
        w = self.windows.setdefault(scene, [])
        w.append(msg)
        del w[:-WINDOW_KEEP]
        self.scene = scene
        if msg.actor_id and msg.actor_id != "self":
            self.last_heard = scene          # 谁最后跟我说话，就先回谁
        self.last_activity = time.time()
        self.awaiting_reply = False

    def note_self(self, text: str, scene: str, importance: int = 4, channel: str = "wechat") -> None:
        """她说过的话也是记忆。"""
        self.windows.setdefault(scene, []).append(
            Msg("self", self.bot_name, text, time.time()))
        self.last_spoke = scene
        store.add_event(self.con, text, scene=scene, channel=channel,
                        actor_id="self", actor_name=self.bot_name, kind="speech",
                        importance=importance)

    # ---------------------------------------------------------------- 读取
    def recent(self, scene: str | None = None, n: int = 16) -> list[Msg]:
        return (self.windows.get(scene or self.scene or "", []) or [])[-n:]

    def recent_lines(self, scene: str | None = None, n: int = 12) -> list[str]:
        return [f"{m.actor_name}：{m.text}" for m in self.recent(scene, n)]

    def tag(self, scene: str) -> str:
        """一句话标明这是哪儿，用在同一时间线里。没名字的群也要能区分开。"""
        try:
            from channels import names
            if scene.endswith("@chatroom"):
                return names.room(scene) or f"群{scene.split('@')[0][-4:]}"
            return f"和{names.label(scene)}私聊"
        except Exception:
            return scene

    def recent_all(self, n: int = 12) -> list[str]:
        """她同时在的所有窗口，按时间合并成一条线 —— 不是「当前会话」。

        每条带「多久以前」，不然她分不清刚才和几个小时前。
        """
        items: list[tuple[float, str, Msg]] = []
        for scene, w in self.windows.items():
            for m in w[-n:]:
                items.append((m.ts, scene, m))
        items.sort(key=lambda x: x[0])
        now = time.time()
        return [f"[{self.tag(s)}] {ago(m.ts, now)} {m.actor_name}：{m.text}"
                for _, s, m in items[-n:]]

    def where(self, scene: str | None = None) -> str:
        try:
            from channels import names
            s = scene or self.scene or ""
            if s.endswith("@chatroom"):
                label = names.room(s) or ("群" + s.split("@")[0][-4:])
                return f"「{label}」这个群里"
            if s:
                return f"跟{names.label(s)}的私聊里"
        except Exception:
            pass
        return "微信上"

    def rooms(self) -> list[tuple[str, str]]:
        """她现在能开口的地方：[(scene_id, 名字)] —— 窗口里的 + 库里的，合并去重。

        窗口只留最近 40 条，重启后 warm_up 也只捞 120 条，
        光看窗口会漏掉久没活跃的群，所以库里的永远补一遍。
        """
        try:
            from channels import names
        except Exception:
            return []
        seen: dict[str, str] = {}

        def label_of(sid: str) -> str:
            return names.room(sid) or ("群" + sid.split("@")[0][-4:])

        for sid in self.windows:
            if sid.endswith("@chatroom"):
                seen[sid] = label_of(sid)
        try:
            rows = self.con.execute(
                "SELECT DISTINCT scene FROM events WHERE scene LIKE '%@chatroom' "
                "ORDER BY ts DESC LIMIT 20").fetchall()
        except Exception:
            rows = []
        for r in rows:
            seen.setdefault(r[0], label_of(r[0]))
        return list(seen.items())

    def places(self) -> dict:
        """她能去说话的地方：群 + 有过来往的人。

        「去群里冒个泡」和「去找某人说句话」是一类动作，不该只有前者可行。
        """
        try:
            from channels import names
        except Exception:
            return {"rooms": [], "people": []}

        people: list[tuple[str, str]] = []
        seen: set[str] = set()
        for scene in self.windows:
            if scene.endswith("@chatroom") or scene in ("bili", "inner", "newsapp"):
                continue
            label = names.label(scene)
            if label and label != self.bot_name and label not in seen:
                seen.add(label)
                people.append((scene, label))
        if len(people) < 8:
            try:
                rows = self.con.execute(
                    "SELECT DISTINCT scene FROM events WHERE scene NOT LIKE '%@chatroom' "
                    "AND scene NOT IN ('bili','inner','newsapp') "
                    "ORDER BY ts DESC LIMIT 20").fetchall()
            except Exception:
                rows = []
            for r in rows:
                label = names.label(r[0])
                if label and label != self.bot_name and label not in seen:
                    seen.add(label)
                    people.append((r[0], label))
        return {"rooms": self.rooms(), "people": people[:8]}

    def unfinished(self, n: int = 12) -> str:
        """她最近说过"要去/等我"之类的话 —— 用来提醒她别忘了。"""
        try:
            rows = self.con.execute(
                "SELECT text FROM events WHERE actor_id='self' AND kind='speech' "
                "ORDER BY id DESC LIMIT ?", (n,)).fetchall()
        except Exception:
            return ""
        marks = ("我去", "这就去", "等我", "马上", "等下", "回头", "去会会", "去试试")
        for r in rows:
            t = (r["text"] or "").strip()
            if t and any(w in t for w in marks):
                return t
        return ""

    # ---------------------------------------------------------------- 思考时刻
    def focusing(self, now: float | None = None) -> bool:
        """是不是在「思考时刻」里。"""
        return (now or time.time()) < self.focus_until

    def set_focus(self, minutes: float = 10, topic: str = "") -> dict:
        """自己按下加速键：接下来这段时间想得更勤。"""
        minutes = max(1.0, min(60.0, float(minutes)))
        self.focus_until = time.time() + minutes * 60
        self.focus_topic = (topic or "还没想明白的事")[:60]
        store.set_state(self.con, "focus_until", str(self.focus_until))
        store.set_state(self.con, "focus_topic", self.focus_topic)
        return {"minutes": minutes, "topic": self.focus_topic,
                "until": self.focus_until}

    def clear_focus(self) -> None:
        self.focus_until = 0.0
        self.focus_topic = ""
        store.set_state(self.con, "focus_until", "0")
        store.set_state(self.con, "focus_topic", "")

    def persona_block(self) -> str:
        """她是谁 —— 每轮都从文件重读，你手动改也能立刻生效。"""
        try:
            fresh = persona.read_identity()
            if fresh:
                self.identity = fresh
        except Exception:
            pass
        try:
            style = persona.style_block()
        except Exception:
            style = ""
        return f"# 你是谁\n{self.identity}" + (f"\n\n{style}" if style else "")

    def set_identity(self, text: str, why: str = "") -> dict:
        """她自己改「我是谁」。双写：人设卡 + self_state，旧值留在事件里。"""
        new = (text or "").strip()
        if not new:
            return {"ok": False, "error": "空的"}
        old = self.identity
        self.identity = new
        store.set_state(self.con, "identity", new)
        try:
            persona.write_identity(new)      # 让「记忆/人设卡.md」也跟上
        except Exception:
            pass
        store.add_event(self.con, f"我把自己从「{old}」改成了「{new}」", scene="inner",
                        channel="self", kind="action", actor_name=self.bot_name,
                        importance=8)
        self.con.commit()
        return {"ok": True, "old": old, "new": new}

    def energy(self) -> float:
        base = float(self.state.get("energy", "0.8"))
        try:
            last = float(self.state.get("energy_ts") or 0)
        except (TypeError, ValueError):
            last = 0.0
        gained = max(0.0, (time.time() - last) / 60.0) * 0.08 if last else 0.0
        return max(0.0, min(1.0, base + gained))

    def spend_energy(self, n_segments: int = 1) -> None:
        e = max(0.0, self.energy() - 0.015 * max(1, n_segments))
        store.set_state(self.con, "energy", f"{e:.2f}")
        store.set_state(self.con, "energy_ts", str(time.time()))
        self.state["energy"] = f"{e:.2f}"
        self.state["energy_ts"] = str(time.time())
        if e < 0.3:
            self.state["mood"] = "有点累"

    def relations_text(self, limit: int = 5) -> str:
        rows = self.con.execute(
            "SELECT e.name, r.affinity, r.familiarity FROM relations r"
            " JOIN entities e ON e.id = r.dst_id ORDER BY r.affinity DESC LIMIT ?",
            (limit,)).fetchall()
        if not rows:
            return ""
        return "你认识的人：" + "；".join(
            f"{r['name']}（好感{r['affinity']:+.0f}，{'熟' if r['familiarity'] > .5 else '一般'}）"
            for r in rows)

    def thoughts_text(self, limit: int = 5) -> str:
        rows = store.open_thoughts(self.con, limit)
        return "\n".join(f"- {r['content']}（{r['urge']:.1f}）" for r in rows)

    def loops_text(self, limit: int = 5) -> str:
        rows = store.loops_with_ids(self.con, limit)
        return "\n".join(f"- #{r['id']} {r['text']}（{r['strength']:.1f}）" for r in rows)

    # ---------------------------------------------------------------- 提示
    def clock(self) -> str:
        now = datetime.now()
        h = now.hour
        if h < 5:
            part = "凌晨"
        elif h < 9:
            part = "早上"
        elif h < 12:
            part = "上午"
        elif h < 14:
            part = "中午"
        elif h < 18:
            part = "下午"
        elif h < 20:
            part = "晚上"
        elif h < 23:
            part = "夜里"
        else:
            part = "深夜"
        return (f"现在是{part} {now.hour:02d}:{now.minute:02d}，"
                f"星期{WEEKDAY[now.weekday()]}")

    @property
    def quiet(self) -> bool:
        return datetime.now().hour in QUIET_HOURS

    @property
    def age_days(self) -> str:
        """活了几天，从出生时间算 —— 不能靠一个永远不更新的字段。"""
        born = self.state.get("born_at")
        if born:
            try:
                d = (datetime.now() - datetime.fromisoformat(born)).total_seconds() / 86400
                return str(max(1, int(d) + 1))
            except (TypeError, ValueError):
                pass
        return self.state.get("age_days", "1")

    def state_block(self) -> str:
        return (f"{self.clock()}\n"
                f"心情：{self.state.get('mood', '平静')}  精力：{self.energy():.2f}\n"
                f"你活了：{self.age_days} 天\n"
                f"{self.relations_text()}")

    def facts_here(self, scene: str | None = None, limit: int = 10) -> str:
        """关于眼前这几个人，我记着他们什么。

        事实库以前只在 recall 时才被翻到，导致隔了几小时的事
        （比如他叫我女儿）明明记着，当下却想不起来。常驻一小份。
        """
        s = scene or self.last_heard or self.scene or ""
        who: list[str] = []
        for m in reversed(self.windows.get(s, [])[-8:]):
            n = (m.actor_name or "").strip()
            if n and n != self.bot_name and n not in who:
                who.append(n)
        out: list[str] = []
        # 每类给个配额，别让某一类（比如一堆「上课」）把位置占满
        QUOTA = (("身份", 3), ("关系", 3), ("喜好", 2),
                 ("观点", 1), ("共同经历", 1), ("经历", 2))
        for name in who[:3]:
            rows = self.con.execute(
                "SELECT f.predicate, f.object_text FROM facts f "
                "JOIN entities e ON e.id = f.subject_id "
                "WHERE e.name = ? AND f.disputed = 0 AND f.superseded_by IS NULL "
                "ORDER BY f.id DESC", (name,)).fetchall()
            by: dict[str, list[str]] = {}
            for r in rows:
                by.setdefault(r["predicate"] or "其他", []).append(r["object_text"])
            for pred, cap in QUOTA:
                for text in by.get(pred, [])[:cap]:
                    out.append(f"{name}：{text}")
        return "\n".join(out[:limit])

    def context(self, scene: str | None = None, n: int = 12) -> dict:
        """一次性给 Think / Act 用的上下文。"""
        s = scene or self.last_heard or self.scene
        return {
            "where": self.where(s),
            "clock": self.clock(),
            "state": self.state_block(),
            "recent": self.recent_all(n),
            "facts": self.facts_here(s),
            "thoughts": self.thoughts_text(),
            "loops": self.loops_text(),
            "quiet": self.quiet,
        }
