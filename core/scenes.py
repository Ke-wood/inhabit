"""场景画像：不同场景给她不同的节奏，但**绝不改全局默认值**。

为什么需要这一层
----------------
QQ/微信现在的节奏（20 秒才想一次）是对的 —— 它防的是刷屏。
但把同一套节奏搬到直播就废了：观众等 20 秒，人早走了。
反过来，为了直播把全局 `min_think_gap` 改成 0.5，她在群里会变成复读机。

所以节奏不是"全局设置"，而是**场景的属性**：同一个她，在群里慢、在直播间快。
这份文件只回答一个问题：`profile_for("live:bili:123")` 该用哪套参数。

三层取值，谁也别抢谁的
--------------------
1. `chat` 画像 = 现有 config 的默认值（`core/config.py` 的 DEFAULTS["thresholds"]）。
   这是 QQ/微信的老行为，**代码里不写死数字**，直接读 config，所以改了全局阈值
   这里跟着变 —— 零回归靠的是这个，而不是抄一份常量。
2. `desk` / `live` 画像 = 本文件的建议值。
3. `config.json` 的 `scenes.<画像名>` 段可以覆盖单个字段（只写要改的那个）。
   面板改完下一拍就生效，和别的配置一样 —— 每次都现读，不缓存。

为什么用 frozen dataclass
-------------------------
画像会被好几个模块同时读（live / act / self），谁都不该能偷偷改一格：
一旦有人原地改 `profile.poll_sec`，别的场景就跟着变 —— 这正是本次要避免的事故。
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any


@dataclass(frozen=True)
class SceneProfile:
    """一套节奏参数。字段含义见同名属性。"""

    name: str              # 画像名："chat" / "desk" / "live"
    min_think_gap: float   # 两次"想"的最小间隔（秒）
    idle_think_gap: float  # 没人说话时多久想一次
    poll_sec: float        # 主循环轮询间隔
    recall_limit: int      # 每轮召回几条旧事
    facts_here: int        # 每轮带几条事实
    max_steps: int         # act 最多几步
    speak_gap: float       # 说完话后多久能再说


# 读 config 里的画像时要认的字段名。写成一份是为了：
# ① 拼错字段名能当场发现（不然永远是"改了没生效"）；
# ② dataclass 加了新字段也不用改三处。
_FLOAT_FIELDS = ("min_think_gap", "idle_think_gap", "poll_sec", "speak_gap")
_INT_FIELDS = ("recall_limit", "facts_here", "max_steps")
FIELDS = ("name",) + tuple(f.name for f in fields(SceneProfile) if f.name != "name")

# 场景前缀 → 画像名。
# 用「前缀」而不是「包含」，因为 `live:qq_room` 这种场景名里也含 qq_ —
# 子串匹配会让直播场景被误判成 QQ（channel_for 之前就踩过这个坑）。
PREFIX_PROFILE = (
    ("desk:", "desk"),
    ("live:", "live"),
)
DEFAULT_PROFILE = "chat"


def _scene_profile_args() -> dict[str, Any]:
    """chat 画像的底数：**直接读现有 config**，保证和 QQ/微信现在完全一致。

    这里不抄常量是有意的：抄了就会漂 —— 以后面板改了全局 `idle_think_gap`，
    画像还留着旧数字，QQ 的行为就悄悄变了，而这正是本任务要防的回归。
    """
    from core import config

    th = config.thresholds()
    return {
        "min_think_gap": float(th.get("min_think_gap", 20)),
        "idle_think_gap": float(th.get("idle_think_gap", 60)),
        # poll_sec 以前散落在主循环里（`time.sleep(2.0)`），这里给它一个正经的家。
        "poll_sec": 2.0,
        "recall_limit": int(th.get("recall_limit", 12)),
        "facts_here": int(th.get("facts_here", 8)),
        "max_steps": 3,
        "speak_gap": float(th.get("speak_gap", 0)),
    }


# 各画像的代码内建议值。chat 不在这里 —— 它必须跟着 config 走（见上）。
# 数字为什么是这个：
#   desk  屏幕大部分时间静止，突然密集说话会像闹鬼，所以慢一档、少打扰；
#   live  观众等不了，一秒内没反应就走了，所以快一个数量级，上下文也砍到最少
#         （弹幕量大，多带回忆只会拖慢响应，还会让她跑题）。
SUGGESTED = {
    "desk": {"min_think_gap": 60.0, "idle_think_gap": 180.0, "poll_sec": 2.0,
             "recall_limit": 8, "facts_here": 6, "max_steps": 1, "speak_gap": 0.0},
    "live": {"min_think_gap": 0.5, "idle_think_gap": 3.0, "poll_sec": 0.2,
             "recall_limit": 3, "facts_here": 2, "max_steps": 1, "speak_gap": 0.0},
}


def profile_name_for(scene: str | None) -> str:
    """场景名 → 画像名。认不出就回 chat（老的 QQ/微信场景走这条）。"""
    s = (scene or "").strip().lower()
    for prefix, name in PREFIX_PROFILE:
        if s.startswith(prefix):
            return name
    return DEFAULT_PROFILE


def _overrides(name: str) -> dict[str, Any]:
    """config.json 里 `scenes.<画像名>` 的覆盖项。

    只认已知字段、只认类型对的：面板手滑存了个字符串 "20"，
    或者把 `_note` 写进了段里，都不该让她在直播间 20 秒不说话。
    """
    from core import config

    section = config.get("scenes", default=None)
    if not isinstance(section, dict):
        return {}
    raw = section.get(name)
    if not isinstance(raw, dict):
        return {}
    out: dict[str, Any] = {}
    for key in _FLOAT_FIELDS:
        if key not in raw:
            continue
        try:
            out[key] = float(raw[key])
        except (TypeError, ValueError):
            continue
    for key in _INT_FIELDS:
        if key not in raw:
            continue
        try:
            out[key] = int(raw[key])
        except (TypeError, ValueError):
            continue
    return out


def profile_for(scene: str) -> SceneProfile:
    """按场景名前缀返回画像。每次现读 config，改完下一拍生效。"""
    name = profile_name_for(scene)
    if name == DEFAULT_PROFILE:
        base = dict(_scene_profile_args())
    else:
        base = dict(SUGGESTED[name])
    base.update(_overrides(name))
    return SceneProfile(name=name, **base)


def chat_profile() -> SceneProfile:
    """chat 画像。单独开一个口子，方便测试和文档直接对着它看。"""
    return profile_for("")


def all_profiles() -> dict[str, SceneProfile]:
    """三套画像摊开，给面板 / 文档用。"""
    return {n: profile_for({"chat": "qq_group_1", "desk": "desk:x",
                            "live": "live:x"}[n]) for n in ("chat", "desk", "live")}


def describe(scene: str | None = None) -> str:
    """给日志 / prompt 用的一行摘要。"""
    p = profile_for(scene or "") if scene is not None else chat_profile()
    return (f"[{p.name}] 想 {p.min_think_gap:g}s / 闲 {p.idle_think_gap:g}s / "
            f"轮询 {p.poll_sec:g}s / 回忆 {p.recall_limit} / 事实 {p.facts_here} / "
            f"步数 {p.max_steps}")
