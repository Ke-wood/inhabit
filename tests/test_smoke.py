"""冒烟测试：不联网、不碰真实数据，验证「装好了能跑」。

    python tests/test_smoke.py

会在临时目录里建一个空库，用 MockBrain 走一遍：
  v1 一拍（收到消息 → 决定说 → 生成回复 → 落库）
  v2 一拍（护栏 → 想 → 裁决）
  记忆读写（事件 / 事实 / 召回 / 人物卡）
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
import tempfile
import time

TMP = tempfile.mkdtemp(prefix="inhabit-smoke-")
os.environ["DAFEIYU_ROOT"] = TMP
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

ok = 0
fail = 0


def check(name: str, fn) -> None:
    global ok, fail
    try:
        fn()
        print(f"  ok   {name}")
        ok += 1
    except Exception as e:                    # noqa: BLE001
        print(f"  FAIL {name}: {type(e).__name__}: {e}")
        fail += 1


def main() -> int:
    print(f"临时根目录：{TMP}")
    import shutil
    shutil.copytree(ROOT / "memory_template", pathlib.Path(TMP) / "记忆")
    from core.brain import MockBrain
    from core.loop import HeartFlow, Msg
    from memory import store

    con = store.init_db()

    def memory_roundtrip() -> None:
        store.add_event(con, "今天去展览馆看了个展", scene="test", actor_name="示例甲",
                        kind="message", importance=6)
        store.add_fact(con, store.ensure_entity(con, "示例甲"), "经历", "上周去看展览了")
        rows = store.recall(con, "展览", limit=5)
        assert rows, "召回为空"
        card = store.person_card(con, ["示例甲"])
        assert card and card[0]["name"] == "示例甲"
        con.commit()

    def v1_tick() -> None:
        flow = HeartFlow(con, scene="test", bot_name="阿鱼", brain=MockBrain(),
                         always_reply=True)
        d = flow.tick([Msg("u1", "示例甲", "在吗", time.time())], time.time(), directed=True)
        assert d.speak, f"应答应回但没有：{d.reason}"
        assert d.segments, "没有生成内容"

    def v2_tick() -> None:
        from core.live import Live
        live = Live(con, MockBrain(), sender=lambda scene, text: {"ok": True})
        live.feed({"scene": "test", "actor_id": "u1", "actor_name": "示例甲",
                   "text": "在吗", "ts": time.time(), "is_group": False})
        live.tick()
        con.commit()

    def config_and_template() -> None:
        from core import config
        assert config.thresholds()["always_reply"] in (True, False)
        assert config.ident()[2], "身份叫法为空"

    def act_parse() -> None:
        from core.act import parse_action
        a = parse_action('{"action":"say","text":"嗯"}')
        assert a and a["action"] == "say"
        a2 = parse_action('{"action":"publish_bili","file":"x.mp4","title":"t"}')
        assert a2 and a2["action"] == "publish_bili"

    check("记忆读写 / 召回 / 人物卡", memory_roundtrip)
    check("v1 一拍（应答模式）", v1_tick)
    check("v2 一拍（自主模式）", v2_tick)
    check("配置与身份", config_and_template)
    check("动作解析", act_parse)

    print(f"\n{ok} 通过 / {fail} 失败")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
