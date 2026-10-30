"""发言门：该不该开口。

两层：
1. 概率 p = sigmoid(线性打分 + 抖动)。特征取自 MaiBot 的拟合思路，但权重按"陪伴"重调：
   群聊越热闹越想插话（+），自己刚说过就更该闭嘴（-），久没说话更容易开口（+）。
   抖动是故意的——同样的情况有时想说有时不想，这才是人。
2. 频率闸：最小间隔 + 10 分钟上限 + 精力系数。
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

W_INTERCEPT = -1.05
W_MENTIONED = 2.00      # 被点名
W_AT_OTHER = -1.20      # 在跟别人说话，别插嘴
W_QUESTION = 0.50       # 有人在问问题
W_MEDIA_ONLY = -1.20    # 只有表情包/图片
W_SELF_RATIO = -1.20    # 最近都是我在说（原来 -2.0，太容易自我噤声）
W_MSG_COUNT = 0.03      # 越热闹越想参与（封顶 10 条）
W_SECONDS_SINCE = 0.12  # 憋久了更想说话（对数）
W_PENDING = 0.08        # 一次来了好几条
W_SALIENCE = 2.00       # 这条消息值不值得接（情绪浓度 / 重要性）

JITTER_SIGMA = 0.28


@dataclass(frozen=True)
class Features:
    mentioned: bool = False
    at_other: bool = False
    has_question: bool = False
    media_only: bool = False
    recent_self_ratio: float = 0.0
    recent_msg_count: int = 0
    seconds_since_self: float = 3600.0
    pending: int = 1
    salience: float = 0.0        # 0..1，消息本身的"可接程度"


@dataclass
class Gate:
    threshold: float = 0.52            # 群里没人叫它时，要这么想说才开口
    min_gap_seconds: float = 60.0      # 群里两条之间至少隔 1 分钟
    max_per_10min: int = 3             # 群里 10 分钟最多 3 条
    direct_min_gap_seconds: float = 0.5  # 对话模式：几乎无间隔
    direct_max_per_10min: int = 60       # 只防死循环刷屏
    always_reply: bool = False           # 应答模式：群聊里也每条都回（NPC / 客服）
    rng: random.Random = field(default_factory=lambda: random.Random(7))
    sent_at: list[float] = field(default_factory=list)

    def logit(self, f: Features) -> float:
        return (
            W_INTERCEPT
            + W_MENTIONED * f.mentioned
            + W_AT_OTHER * f.at_other
            + W_QUESTION * f.has_question
            + W_MEDIA_ONLY * f.media_only
            + W_SELF_RATIO * min(1.0, max(0.0, f.recent_self_ratio))
            + W_MSG_COUNT * min(10, max(0, f.recent_msg_count))
            + W_SECONDS_SINCE * math.log1p(max(0.0, f.seconds_since_self))
            + W_PENDING * min(10, max(0, f.pending))
            + W_SALIENCE * min(1.0, max(0.0, f.salience))
        )

    def probability(self, f: Features) -> float:
        jitter = self.rng.gauss(0.0, JITTER_SIGMA)
        if f.mentioned:
            jitter = abs(jitter) * 0.5   # 被点名时会犹豫，但偏向开口
        return 1.0 / (1.0 + math.exp(-(self.logit(f) + jitter)))

    def rate_ok(self, now: float, *, directed: bool = False) -> tuple[bool, str]:
        gap = self.direct_min_gap_seconds if directed else self.min_gap_seconds
        cap = self.direct_max_per_10min if directed else self.max_per_10min
        if self.sent_at and now - self.sent_at[-1] < gap:
            return False, f"刚说过话（{now - self.sent_at[-1]:.0f}s < {gap:.0f}s）"
        recent = [t for t in self.sent_at if now - t <= 600]
        if len(recent) >= cap:
            return False, f"10 分钟内已说 {len(recent)} 次"
        return True, ""

    def decide(self, f: Features, now: float, *, energy: float = 0.8) -> tuple[bool, float, float, str]:
        if self.always_reply:
            ok, why = self.rate_ok(now, directed=True)
            return (True, 1.0, 0.0, "应答模式") if ok else (False, 1.0, 0.0, why)
        p = self.probability(f)
        thr = min(0.95, self.threshold + (0.15 if energy < 0.35 else 0.0))
        if p < thr:
            return False, p, thr, "没到阈值"
        ok, why = self.rate_ok(now)
        if not ok:
            return False, p, thr, why
        return True, p, thr, "开口"

    def record(self, now: float) -> None:
        self.sent_at.append(now)
