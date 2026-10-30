"""心流：一段对话怎么活着。

不是"收到消息→生成回复"，而是每次醒来看一眼：现在该不该说话？说什么？
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from core import growth, memfiles
from core.brain import Brain, split_segments
from core.gate import Features, Gate
from core.persona import build_system_prompt, build_user_turn
from memory import store

MEDIA_PREFIX = ("[表情", "[图片", "[语音", "[文件]", "[卡片", "[动画")
EMO_WORDS = ("累", "难受", "emo", "烦", "哭", "不想", "没意思", "崩溃")
CONVERSATION_WINDOW = 120.0        # 私聊：她说完话 120 秒内、同一个人接话 = 还在聊
GROUP_CONVERSATION_WINDOW = 45.0   # 群里更短：只在明显接着她说时才继续


@dataclass(frozen=True)
class Msg:
    actor_id: str
    actor_name: str
    text: str
    ts: float
    source_ref: str = ""      # 微信 msgid / 视频 bvid —— 用来去重


@dataclass
class Decision:
    speak: bool
    prob: float
    threshold: float
    reason: str
    segments: list[str] = field(default_factory=list)
    recalled: list[str] = field(default_factory=list)


def importance_of(text: str) -> int:
    score = 3
    if len(text) > 15:
        score += 1
    if any(w in text for w in EMO_WORDS):
        score += 2
    if text.endswith(("?", "？")):
        score += 1
    if "我" in text:
        score += 1
    return max(1, min(10, score))


class HeartFlow:
    def __init__(self, con: sqlite3.Connection, *, scene: str, bot_name: str,
                 brain: Brain, gate: Gate | None = None, log_candidates: bool = True,
                 alt_names: tuple[str, ...] = (), always_reply: bool = False,
                 recall_limit: int = 12, facts_per_person: int = 15,
                 events_per_person: int = 6):
        self.con = con
        self.scene = scene
        self.bot_name = bot_name
        self.bot_names = tuple(dict.fromkeys((bot_name,) + alt_names))
        self.brain = brain
        self.gate = gate or Gate(always_reply=always_reply)
        self.recall_limit = recall_limit
        self.facts_per_person = facts_per_person
        self.events_per_person = events_per_person
        self.log_candidates = log_candidates
        self.state = store.get_state(con)
        self.core, self.rules = _load_persona()
        self.window: list[tuple[float, bool, str, str]] = []  # ts, is_self, actor, text
        self.last_self_ts: float | None = None
        self.last_partner: str | None = None   # 最近在跟谁一来一回
        self.pending_read: list[str] = []      # 她要求往前翻时，下一轮塞进来的旧消息
        self.day_events: list[int] = []
        self.trace = True                      # 每轮把注入内容落盘，方便人看

    # ---------------------------------------------------------------- 主入口
    def tick(self, msgs: list[Msg], now: float, *, directed: bool = False) -> Decision:
        if not msgs:
            return Decision(False, 0.0, self.gate.threshold, "没人说话")

        new_ids: list[int] = []
        for m in msgs:
            new_ids.append(self._remember(m))
            self.window.append((m.ts, False, m.actor_name, m.text))

        feats = self._features(msgs, now)
        energy = self._energy(now)

        if directed or self.gate.always_reply:
            # 私聊 / 被点名 / 应答模式：不用掷骰子，只防刷屏
            ok, why = self.gate.rate_ok(now, directed=True)
            speak, p, thr = (True, 1.0, 0.0) if ok else (False, 1.0, 0.0)
            reason = ("私聊直接回" if directed else "应答模式") if ok else why
        else:
            speak, p, thr, reason = self.gate.decide(feats, now, energy=energy)

        if not speak:
            if self.log_candidates and thr - 0.08 <= p < thr and not feats.media_only:
                growth.note("边缘沉默", f"{msgs[-1].actor_name}：{msgs[-1].text}",
                            why=f"p={p:.2f}，差 {thr - p:.2f} 没开口")
            self._trim(now)
            return Decision(False, p, thr, reason)

        last_text = msgs[-1].text
        speaker_names = [m.actor_name for m in msgs]
        recalled = store.recall(
            self.con, last_text, limit=self.recall_limit,
            boost_scene=self.scene,             # 本会话优先，但别的会话也想得起来
            actor_names=speaker_names + [self.bot_name],   # 也捞自己说过的话
            exclude_ids=new_ids,
        )
        memories = [f"{_ts(r['ts'])}·{_scene_tag(r['scene'])} {r['actor'] or '某人'}：{r['text']}"
                    for r in recalled]
        cards = store.person_card(self.con, speaker_names,
                                  facts_per_person=self.facts_per_person,
                                  events_per_person=self.events_per_person)
        said = self._own_words(limit=4)

        system = build_system_prompt(self.core, self.rules, self.state, self._relations(),
                                     where=self._where())
        user = build_user_turn(
            [f"{name}：{text}" for _, _, name, text in self.window[-16:]],
            memories,
            people=cards,
            said=said,
            read_more=self.pending_read,
            own_memo=memfiles.read_self_memo(),
            anchor=f"{msgs[-1].actor_name}：{msgs[-1].text}",
        )
        if self.trace:
            try:
                memfiles.write_trace(self.scene, f"{msgs[-1].actor_name}：{msgs[-1].text}", system, user)
            except Exception:
                pass
        self.pending_read = []          # 只塞一次
        avoid = [text for _, is_self, _, text in self.window if is_self][-3:]
        raw = self.brain.speak(system=system, user=user, avoid=avoid)
        segments = split_segments(raw)
        if not segments:
            self._trim(now)
            return Decision(False, p, thr, "生成了空话")

        self.gate.record(now)
        self.last_self_ts = now
        self.last_partner = msgs[-1].actor_id
        for seg in segments:
            self.window.append((now, True, self.bot_name, seg))
            # 她自己说的话也是记忆：权重给高一点，不然永远想不起自己说过什么
            store.add_event(self.con, seg, scene=self.scene, channel="wechat",
                            actor_id="self", actor_name=self.bot_name, kind="speech",
                            importance=4)
        self._after_speak(len(segments), now)
        self._trim(now)
        return Decision(True, p, thr, reason, segments, memories)

    def record_only(self, msgs: list[Msg], now: float) -> None:
        """睡着的时候：只把话记下来，不判断、不说话。醒了好翻。"""
        for m in msgs:
            self._remember(m)
            self.window.append((m.ts, False, m.actor_name, m.text))
        self._trim(now)

    # ---------------------------------------------------------------- 对话态
    def in_conversation(self, now: float, actor_id: str | None = None) -> bool:
        """她刚说完话、说话的人又是刚才那个——才算"正在跟人聊天"。

        换个人说话、或者有人在 @ 别人，都不算。
        """
        if self.last_self_ts is None:
            return False
        window = GROUP_CONVERSATION_WINDOW if self.scene.endswith("@chatroom") else CONVERSATION_WINDOW
        if (now - self.last_self_ts) > window:
            return False
        return actor_id is None or actor_id == self.last_partner

    # ---------------------------------------------------------------- 内部
    def _remember(self, m: Msg) -> int:
        if m.actor_name and m.actor_name not in ("对方", "某人"):
            store.ensure_entity(self.con, m.actor_name)   # 人是记忆的载体，先建卡
        return store.add_event(self.con, m.text, scene=self.scene, channel="wechat",
                               actor_id=m.actor_id, actor_name=m.actor_name,
                               kind="message", importance=importance_of(m.text),
                               ts=datetime.fromtimestamp(m.ts).strftime("%Y-%m-%dT%H:%M:%S"),
                               source_ref=(m.source_ref or None))

    def _features(self, msgs: list[Msg], now: float) -> Features:
        texts = [m.text for m in msgs]
        joined = " ".join(texts)
        mentioned = any(n and n in joined for n in self.bot_names)
        at_other = (not mentioned) and ("@" in joined)
        recent = [w for w in self.window if now - w[0] <= 300]
        self_msgs = sum(1 for w in recent if w[1])
        ratio = self_msgs / len(recent) if recent else 0.0
        secs = now - self.last_self_ts if self.last_self_ts else 3600.0
        return Features(
            mentioned=mentioned,
            at_other=at_other,
            has_question=any(t.rstrip().endswith(("?", "？")) for t in texts),
            media_only=all(t.startswith(MEDIA_PREFIX) for t in texts),
            recent_self_ratio=ratio,
            recent_msg_count=len(recent),
            seconds_since_self=secs,
            pending=len(msgs),
            salience=max(0.0, (importance_of(texts[-1]) - 3) / 5.0),
        )

    def _own_words(self, limit: int = 4) -> list[str]:
        """她自己在各个场合说过的话——不分群和私聊，都是"我说过的"。

        优先挑有内容的（太短的语气词没信息量），再按时间排。
        """
        rows = self.con.execute(
            """SELECT text, ts, scene, importance FROM events
               WHERE actor_id='self' AND kind IN ('speech','message') AND text <> ''
               ORDER BY ts DESC LIMIT 60""").fetchall()
        picked, seen = [], set()
        for r in rows:
            t = (r["text"] or "").strip()
            if len(t) < 5 or t in seen:      # 丢掉"啊？""草"这种
                continue
            seen.add(t)
            picked.append(f"{_ts(r['ts'])}·{_scene_tag(r['scene'])}：{t}")
            if len(picked) >= limit:
                break
        if not picked:                        # 实在只有短句，就退回去用短的
            for r in rows[:limit]:
                picked.append(f"{_ts(r['ts'])}·{_scene_tag(r['scene'])}：{r['text']}")
        return list(reversed(picked))

    def _where(self) -> str:
        """她此刻在哪儿——群名或私聊对象，别让她分不清场合。"""
        try:
            from channels import names
            if self.scene.endswith("@chatroom"):
                return f"「{names.room(self.scene) or '一个群'}」这个群里"
            return f"跟{names.label(self.scene)}的私聊里"
        except Exception:
            return "一个微信群里"

    def _energy(self, now: float) -> float:
        """精力会随时间回一点：安静 25 分钟基本回满。"""
        base = float(self.state.get("energy", "0.8"))
        try:
            last = float(self.state.get("energy_ts") or 0)
        except (TypeError, ValueError):
            last = 0.0
        gained = max(0.0, (now - last) / 60.0) * 0.08 if last else 0.0   # 12 分钟回满
        return max(0.0, min(1.0, base + gained))

    def _relations(self) -> str:
        rows = self.con.execute(
            """SELECT e.name, r.affinity, r.familiarity, r.label
               FROM relations r JOIN entities e ON e.id = r.dst_id
               ORDER BY r.affinity DESC LIMIT 5""").fetchall()
        if not rows:
            return ""
        return "你认识的人：" + "；".join(
            f"{r['name']}（好感{r['affinity']:+.0f}，{'熟' if r['familiarity'] > .5 else '一般'}）" for r in rows)

    def _after_speak(self, n_segments: int, now: float) -> None:
        energy = max(0.0, self._energy(now) - 0.015 * n_segments)
        store.set_state(self.con, "energy", f"{energy:.2f}")
        store.set_state(self.con, "energy_ts", str(now))
        self.state["energy"] = f"{energy:.2f}"
        self.state["energy_ts"] = str(now)
        self.state["mood"] = "有点累" if energy < 0.3 else self.state.get("mood", "平静")

    def _trim(self, now: float) -> None:
        self.window = [w for w in self.window if now - w[0] <= 3600][-80:]

    # ---------------------------------------------------------------- 反思
    def reflect(self, now: float, period: str = "day") -> str:
        since = (datetime.fromtimestamp(now) - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%S")
        rows = self.con.execute(
            """SELECT text, actor_name FROM events
               WHERE ts >= ? AND kind = 'message' AND importance >= 5
               ORDER BY importance DESC, ts DESC LIMIT 8""", (since,)).fetchall()
        lines = [f"{r['actor_name'] or '某人'}说{r['text']}" for r in rows]
        text = self.brain.reflect(lines)
        store.add_reflection(self.con, text, period=period, mood=self.state.get("mood"))
        return text


def _load_persona():
    from core.persona import load_persona
    return load_persona()


def _ts(iso: str) -> str:
    try:
        d = datetime.fromisoformat(iso)
        delta = datetime.now() - d
        if delta.days >= 1:
            return f"{delta.days}天前"
        if delta.seconds >= 3600:
            return f"{delta.seconds // 3600}小时前"
        return f"{max(1, delta.seconds // 60)}分钟前"
    except ValueError:
        return "?"


def _scene_tag(scene: str) -> str:
    """给记忆标来源，让她知道这事是在哪个群/私聊听说的。"""
    try:
        from channels import names
        if scene and scene.endswith("@chatroom"):
            return names.room(scene) or "群里"
        return "私聊"
    except Exception:
        return "群里" if scene and scene.endswith("@chatroom") else "私聊"
