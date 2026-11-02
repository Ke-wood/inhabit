"""场景画像测试：desk / live 两套节奏，且 QQ 零回归。

    python tests/test_scenes.py

核心验收：
1. QQ/微信的画像 == 历史默认值（回归保护，最重要）
2. live 更快、上下文更小
3. desk 更慢
4. config 覆盖生效
5. 三个场景都能真的跑一拍
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import sys
import tempfile
import time

TMP = pathlib.Path(tempfile.mkdtemp(prefix="inhabit-scenes-"))
os.environ["DAFEIYU_ROOT"] = str(TMP)
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

ok = 0
fail = 0


def check(name, fn):
    global ok, fail
    try:
        fn()
        print(f"  ok   {name}")
        ok += 1
    except Exception as e:
        print(f"  FAIL {name}: {type(e).__name__}: {e}")
        fail += 1


def _setup() -> None:
    shutil.copytree(ROOT / "memory_template", TMP / "记忆", dirs_exist_ok=True)
    (TMP / "config.json").write_text(json.dumps({
        "active": "阿鱼",
        "profiles": {"阿鱼": {"dir": "data", "memory": "记忆", "persona": "我是谁.md"}},
        "channels": {
            "qq": {"kind": "onebot", "enabled": True, "prefixes": ["qq_"]},
            "wechat": {"kind": "wechat_hook", "enabled": True,
                       "prefixes": ["wxid_", "@chatroom"]},
        },
        "scenes": {"live": {"recall_limit": 3}},
    }, ensure_ascii=False), encoding="utf-8")
    from core import config
    config._cache.clear()
    config._mtime = 0.0


def _live(scene: str):
    from core.brain import MockBrain
    from core.live import Live
    from core.self import Self
    from memory import store
    con = store.init_db()
    bot = Self(con, bot_name="阿鱼")
    lv = Live(con, MockBrain(), bot=bot, sender=lambda s, t: {"ok": True})
    lv.feed({"scene": scene, "actor_id": "u1", "actor_name": "某人",
             "text": "在吗", "ts": time.time(), "is_group": scene.startswith("qq_group")})
    return lv, con


# ---------------------------------------------------------------- 回归
def test_chat_profile_unchanged():
    """QQ/微信的画像必须等于历史默认值。

    这是整件事最重要的一条：加直播不能顺手改掉群聊的 20 秒间隔，
    那是调出来的，改了会让她在群里变复读机。
    """
    from core.scenes import profile_for
    p = profile_for("qq_group_1")
    assert p.min_think_gap == 20.0, f"min_think_gap 被改了: {p.min_think_gap}"
    assert p.idle_think_gap == 60.0, f"idle_think_gap 被改了: {p.idle_think_gap}"
    assert p.poll_sec == 2.0, f"poll_sec 被改了: {p.poll_sec}"
    assert p.recall_limit == 12, f"recall_limit 被改了: {p.recall_limit}"
    assert p.facts_here == 8, f"facts_here 被改了: {p.facts_here}"
    assert p.max_steps == 3, f"max_steps 被改了: {p.max_steps}"


def test_wechat_profile_unchanged():
    from core.scenes import profile_for
    p = profile_for("wxid_abc")
    assert p.min_think_gap == 20.0 and p.max_steps == 3


def test_unknown_falls_back():
    from core.scenes import profile_for
    p = profile_for("unknown:xyz")
    assert p.min_think_gap == 20.0, p.min_think_gap


def test_chat_context_budget_unchanged():
    """chat 的上下文预算和改动前一致（10 条事实 / 12 条最近），只换场景才变。

    画像里 chat 的 facts_here=8 是"配置意图"，而 facts_here() 的历史默认
    是 10。把调用点直接切到画像 = 给 QQ 少带 2 条事实，是隐性回归。
    这条测试专门钉住：聊天不许被画像悄悄瘦身。
    """
    import time as _t
    from core.loop import Msg
    from core.self import Self
    from memory import store as _store

    from memory import store
    con = store.init_db()
    bot = Self(con, bot_name="阿鱼")
    for i in range(15):
        bot.observe(Msg("u1", "某人", f"消息{i}", _t.time()), "qq_group_1")
    assert len(bot.context("qq_group_1")["recent"]) == 12, "chat 的最近上下文条数变了"
    assert bot.context("qq_group_1")["profile"].name == "chat"
    live_ctx = bot.context("live:bili:1")
    assert len(live_ctx["recent"]) <= 3, f"直播上下文没瘦身: {len(live_ctx['recent'])}"
    con.close()


# ---------------------------------------------------------------- live
def test_live_faster():
    from core.scenes import profile_for
    p = profile_for("live:bili:1")
    assert p.min_think_gap < 1.0, p.min_think_gap
    assert p.poll_sec < 0.5, p.poll_sec


def test_live_context_smaller():
    """直播的上下文必须显著小于 QQ —— 这是「上下文爆炸」的解。

    她直播时不需要想起三天前的事，只需要接住眼前这句。
    """
    from core.scenes import profile_for
    live = profile_for("live:bili:1")
    chat = profile_for("qq_group_1")
    assert live.recall_limit < chat.recall_limit / 2, \
        (live.recall_limit, chat.recall_limit)
    assert live.facts_here < chat.facts_here, (live.facts_here, chat.facts_here)
    assert live.max_steps == 1, live.max_steps


# ---------------------------------------------------------------- desk
def test_desk_slower():
    from core.scenes import profile_for
    p = profile_for("desk:screen")
    assert p.min_think_gap >= 30, p.min_think_gap
    assert p.idle_think_gap >= 120, p.idle_think_gap
    assert p.max_steps == 1, p.max_steps


# ---------------------------------------------------------------- 接线
def test_live_object_uses_profile():
    _setup()
    lv, _ = _live("live:bili:1")
    p = lv.profile()
    assert p.min_think_gap < 1.0, f"Live 没走 live 画像: {p}"


def test_desk_object_uses_profile():
    _setup()
    lv, _ = _live("desk:screen")
    p = lv.profile()
    assert p.min_think_gap >= 30, f"Live 没走 desk 画像: {p}"


def test_qq_object_uses_chat_profile():
    _setup()
    lv, _ = _live("qq_group_1")
    p = lv.profile()
    assert p.min_think_gap == 20.0, f"QQ 画像被改了: {p}"


def test_config_override():
    _setup()
    from core.scenes import profile_for
    p = profile_for("live:bili:1")
    assert p.recall_limit == 3, f"config 覆盖没生效: {p.recall_limit}"


def test_config_override_min_gap_and_partial():
    """覆盖要能改任意字段，且**只**改写了的那几个 —— 其余仍用代码里的建议值。

    这条单独拎出来，是因为"改了没生效"和"改成 0 了"都是直播场景的致命伤：
    前者让弹幕等到天荒地老，后者让她在群里变复读机。
    """
    _setup()
    import json
    cfg = json.loads((TMP / "config.json").read_text(encoding="utf-8"))
    cfg["scenes"] = {"live": {"min_think_gap": 0.15, "recall_limit": 5},
                     "desk": {"min_think_gap": 90}}
    (TMP / "config.json").write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    from core import config as cfgmod
    cfgmod._cache.clear()
    cfgmod._mtime = 0.0
    from core.scenes import profile_for
    live = profile_for("live:bili:1")
    assert live.min_think_gap == 0.15, f"scenes.live.min_think_gap 没被读到: {live}"
    assert live.recall_limit == 5, live
    # 没写的字段保持建议值，不该被清成 0
    assert live.idle_think_gap == 3, live
    assert live.poll_sec == 0.2, live
    assert live.facts_here == 2, live
    desk = profile_for("desk:screen")
    assert desk.min_think_gap == 90, desk
    assert desk.recall_limit == 8, desk
    # chat 不受影响
    assert profile_for("qq_group_1").min_think_gap == 20

    # 坏值（写错类型）不该把她卡死，退回建议值
    cfg["scenes"] = {"live": {"min_think_gap": "abc"}}
    (TMP / "config.json").write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    cfgmod._cache.clear()
    cfgmod._mtime = 0.0
    assert profile_for("live:v").min_think_gap == 0.5, "坏配置应退回建议值"
    _setup()


def test_tick_does_not_wedge():
    """desk/live 下 tick 既不能连发，也不能被间隔永久卡住。"""
    _setup()
    from core.scenes import profile_for
    now = time.time()
    for scene in ("desk:screen", "live:bili:1"):
        lv, con = _live(scene)
        lv.last_think = now
        # 喂两条并 tick：刚想过，必须被间隔拦住
        lv.feed({"scene": scene, "actor_id": "u1", "actor_name": "某人",
                 "text": "再说一句", "ts": now + 0.01, "is_group": False}, now=now + 0.01)
        assert lv.tick(now=now + 0.01) is None, f"{scene}: 刚想过就又想了"
        # 越过间隔后必须能再想 —— 证明没有被永久卡死
        gap = profile_for(scene).min_think_gap
        before = lv.stats["thought"]
        for i in range(3):
            lv.last_think = now
            lv.tick(now=now + gap + 1 + i)
        assert lv.stats["thought"] >= before, f"{scene}: 越过间隔后仍不工作"
        assert lv.last_think >= now, f"{scene}: last_think 没推进"
        con.commit()
        con.close()
    _setup()


# ---------------------------------------------------------------- 端到端
def test_all_scenes_tick():
    """三个场景各跑一拍，不能崩。"""
    _setup()
    for scene in ("qq_group_1", "desk:screen", "live:bili:1"):
        lv, con = _live(scene)
        lv.tick()
        con.commit()


def test_channel_routing():
    """desk:/live: 不能被误认成网络渠道。

    历史 bug：channel_for 用子串匹配，live:qq_room 会被判成 QQ。
    """
    _setup()
    from core import config
    assert config.channel_for("desk:screen") == "desk"
    assert config.channel_for("live:qq_room") == "live", \
        "live:qq_room 被误判成网络渠道了"
    assert config.channel_for("qq_group_1") == "qq"


def main() -> int:
    print("场景画像")
    _setup()
    for name, fn in [
        ("QQ 画像零回归", test_chat_profile_unchanged),
        ("微信画像零回归", test_wechat_profile_unchanged),
        ("未知场景回落 chat", test_unknown_falls_back),
        ("chat 上下文预算没被画像悄悄改小", test_chat_context_budget_unchanged),
        ("live 更快", test_live_faster),
        ("live 上下文更小", test_live_context_smaller),
        ("desk 更慢", test_desk_slower),
        ("Live 对象走 live 画像", test_live_object_uses_profile),
        ("Live 对象走 desk 画像", test_desk_object_uses_profile),
        ("Live 对象走 chat 画像", test_qq_object_uses_chat_profile),
        ("config 覆盖生效", test_config_override),
        ("config 覆盖 min_think_gap（且只改写了的那几项）", test_config_override_min_gap_and_partial),
        ("三场景都能跑一拍", test_all_scenes_tick),
        ("desk/live 下 tick 不连发也不卡死", test_tick_does_not_wedge),
        ("场景路由不误判", test_channel_routing),
    ]:
        check(name, fn)
    print(f"\n{ok} 通过 / {fail} 失败")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
