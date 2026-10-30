"""离线演示：一群人聊天，看阿鱼什么时候开口、什么时候闭嘴。

用法：
    python run_demo.py            # 占位大脑，只看决策节奏
    python run_demo.py --brain llm  # 接真模型（需 DEEPSEEK_API_KEY）
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from core.brain import make_brain
from core.loop import HeartFlow, Msg
from memory import store

ROOT_DIR = Path(__file__).resolve().parent
DB = ROOT_DIR / "data" / "tmp" / "demo.db"


def _guard_db() -> None:
    """绝不允许在角色库上跑 demo —— 以前这里就是直接 unlink(data/feiyu.db)。"""
    if DB.resolve() == store.DB_PATH.resolve():
        raise SystemExit(f"拒绝：demo 库指向了角色库 {DB}")


def hhmm(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%H:%M")


def seed(con) -> None:
    """三天前的旧事，用来验证"它会不会想起"。"""
    now = time.time()
    store.add_event(con, "最近在准备一个考试，压力挺大的", scene="g1", channel="wechat",
                    actor_id="1", actor_name="甲", importance=8,
                    ts=(datetime.fromtimestamp(now - 3 * 86400)).strftime("%Y-%m-%dT%H:%M:%S"))
    store.add_event(con, "我养了只猫，特别胖", scene="g1", channel="wechat",
                    actor_id="3", actor_name="丙", importance=6,
                    ts=(datetime.fromtimestamp(now - 86400)).strftime("%Y-%m-%dT%H:%M:%S"))
    # 关系底子
    for name, eid in (("甲", "1"), ("丙", "3")):
        con.execute("INSERT OR IGNORE INTO entities(etype,name,first_seen,last_seen) VALUES ('person',?,?,?)",
                    (name, store.now_iso(), store.now_iso()))
    con.commit()


# 全部是虚构的示例对话，用甲乙丙丁当化名，不指向任何真人。
SCRIPT: list[tuple[int, str, str]] = [
    (-10800, "甲", "在吗"),
    (-10740, "乙", "在"),
    (-10680, "丙", "今天好热"),
    (-10620, "甲", "@阿鱼 你说我该不该再试一次"),
    (-10530, "乙", "@阿鱼 你别光笑啊"),
    (-10200, "乙", "再试一次挺累的"),
    (-10100, "甲", "但是我真的很想"),
    (-9800, "丙", "[表情包]"),
    (-9700, "乙", "先别说这个了，吃饭了吗"),
    (-9600, "甲", "没胃口"),
    (-6000, "丙", "我今天看到一只超肥的猫"),
    (-5940, "乙", "拍照"),
    (-5880, "丙", "[图片]"),
    (-2400, "甲", "好烦啊，累"),
    (-2340, "乙", "怎么了"),
]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--brain", default="mock", choices=["mock", "llm", "local", "gateway"])
    args = ap.parse_args()

    _guard_db()
    if DB.exists():
        DB.unlink()
    con = store.init_db(DB)
    seed(con)

    flow = HeartFlow(con, scene="g1", bot_name="阿鱼", brain=make_brain(args.brain))
    now0 = time.time()

    print("=" * 66)
    print(f"群聊模拟 · {datetime.fromtimestamp(now0).strftime('%m-%d %H:%M')} · 大脑={args.brain}")
    print("=" * 66)

    spoken = 0
    for offset, name, text in SCRIPT:
        ts = now0 + offset
        print(f"\n[{hhmm(ts)}] {name}：{text}")
        d = flow.tick([Msg(actor_id=name, actor_name=name, text=text, ts=ts)], ts)
        mark = "开口" if d.speak else "闭嘴"
        print(f"   └ {mark}  p={d.prob:.2f} / thr={d.threshold:.2f}  ({d.reason})")
        if d.recalled:
            print(f"   └ 想起：{' / '.join(d.recalled[:2])}")
        if d.speak:
            spoken += 1
            for i, seg in enumerate(d.segments):
                gap = 0.8 + 0.9 * i
                print(f"      >> 阿鱼（+{gap:.1f}s）：{seg}")

    print("\n" + "-" * 66)
    diary = flow.reflect(now0)
    print(f"【今天的日记】{diary}")

    total = con.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    print(f"\n库里 {total} 条事件，本次开口 {spoken} 次。")
    con.commit()
    con.close()


if __name__ == "__main__":
    main()
