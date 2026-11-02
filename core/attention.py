"""值不值得动：一个廉价的本地判断层，决定这一拍要不要真的调模型。

为什么需要它
------------
现在的节奏是「按时间」的：隔 N 秒想一次，不管有没有值得说的事。
桌面宠物场景下这会变成两种坏结果：

- 她一直沉默，用户忘了她在
- 她按固定节拍说废话，用户觉得吵

真正该决定的是「**这一刻有没有值得反应的事**」，不是「距离上次过了几秒」。

这一层做什么
------------
把几个廉价信号合成一个 0~1 的分数，**不调模型**：

- `silence`   多久没说话了（憋久了想开口）
- `energy`    精力（越低越不想动）
- `novelty`   环境有没有新东西（屏幕变了吗、有没有人说话）
- `interest`  这次的事本身重不重要（有人叫她 > 群里有消息 > 什么都没发生）

输出一个 `Verdict`：
- `should_think`  这一拍要不要调模型
- `reason`        为什么（日志/面板可看，便于调参）
- `score`         分数本身

为什么不用第二层 LLM 当裁判
--------------------------
那等于每拍多一次调用，正好抵消掉省下的钱。这里全是算术，零成本。

和现有机制的关系
----------------
**不替换** `min_think_gap` / `idle_think_gap` 那套时间闸，而是在它**之上**再加一道：
时间闸决定「最早什么时候能想」，这一层决定「想不想」。

两者是 AND：时间没到一定不想；时间到了但没意思，也可以不想。
"""

from __future__ import annotations

import random
from dataclasses import dataclass

# 各类信号的权重。数值是起点，靠实际观察调，不要当真理。
W_SILENCE = 0.30
W_INTEREST = 0.45
W_NOVELTY = 0.15
W_ENERGY = 0.10

# 低于这个分数就不想
FLOOR = 0.35

# 憋久了：沉默超过这个秒数，silence 信号拉满
SILENCE_FULL_SEC = 900.0        # 15 分钟


@dataclass(frozen=True)
class Signals:
    """喂给判断层的原料。全部是可廉价的量，不需要模型。"""

    silence_sec: float = 0.0    # 距她上次开口多久
    energy: float = 0.8         # 0~1
    has_message: bool = False   # 有没有人刚说话
    mentioned: bool = False     # 是不是在叫她
    novelty: float = 0.0        # 0~1，环境有多新（屏幕变了/有图/有动作）
    quiet_hour: bool = False    # 是不是深夜（不该主动打扰）


@dataclass(frozen=True)
class Verdict:
    should_think: bool
    score: float
    reason: str

    def __bool__(self) -> bool:      # 方便 `if verdict:`
        return self.should_think


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def judge(sig: Signals, *, rng: random.Random | None = None) -> Verdict:
    """这一拍值不值得动。纯算术，零模型调用。"""
    rng = rng or random

    # 1) 有人直接冲她说话：基本不用算
    if sig.mentioned:
        return Verdict(True, 1.0, "在叫她")

    # 2) 深夜不主动：但如果有人说话，还是该看
    if sig.quiet_hour and not sig.has_message:
        return Verdict(False, 0.0, "深夜，不主动")

    # 3) 精力见底：不想动，但被人叫醒还是要应
    if sig.energy < 0.15 and not sig.has_message:
        return Verdict(False, _clamp(sig.energy), "太累了，不主动")

    # 4) 常规打分
    silence = _clamp(sig.silence_sec / SILENCE_FULL_SEC)
    interest = 1.0 if sig.has_message else 0.0
    novelty = _clamp(sig.novelty)
    energy = _clamp(sig.energy)

    score = (W_SILENCE * silence
             + W_INTEREST * interest
             + W_NOVELTY * novelty
             + W_ENERGY * energy)

    # 被冷落太久会「憋」，给一点随机性 —— 否则她的节奏像定时器
    if silence > 0.7:
        score += rng.uniform(0.0, 0.08)

    if score < FLOOR:
        why = []
        if not sig.has_message:
            why.append("没人说话")
        if silence < 0.2:
            why.append("刚说过")
        if novelty < 0.1:
            why.append("没什么新的")
        return Verdict(False, score, "、".join(why) or "分数不够")

    return Verdict(True, score, "有人说话" if sig.has_message else "憋久了")


def explain(sig: Signals) -> str:
    """给面板/日志用的一行人话。"""
    parts = []
    parts.append(f"沉默 {sig.silence_sec:.0f}s")
    parts.append(f"精力 {sig.energy:.2f}")
    if sig.has_message:
        parts.append("有人说话")
    if sig.mentioned:
        parts.append("在叫她")
    if sig.novelty > 0.1:
        parts.append(f"新变化 {sig.novelty:.2f}")
    return " · ".join(parts)
