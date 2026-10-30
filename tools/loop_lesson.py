"""单独一轮：让她看看「悬着的事」这个机制，自己决定关哪些。

    python tools/loop_lesson.py [轮数]

这轮不带说话约束、不带常规的 open_loops 注入，只给她功能说明 + 当前列表。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import persona                      # noqa: E402
from core.act import parse_action            # noqa: E402
from core.brain import make_brain            # noqa: E402
from memory import store                     # noqa: E402

SYSTEM = """{identity}

# 关于「悬着的事」

有些事你想弄明白，但一直没答案。它们会一直挂在你头顶 ——
每次你「想」点什么的时候，都会先看到它们一遍，久了挺烦。

你可以随时处理掉它们：

- 想明白了：{{"action":"resolve","id":编号,"answer":"答案是什么"}}
- 不想弄明白了：{{"action":"drop","id":编号,"why":"为什么不想了"}}

两个都会让它从头顶消失。区别只是：一个是你有答案了，一个是你决定不要答案了。
不处理的话，它就一直在那儿。

有些事值得留着 —— 确实还想知道、以后真有机会弄清楚的。
有些事其实已经没意思了，或者你已经有答案了，那就该清掉。
"""


def one_round(brain, con, round_no: int) -> int:
    loops = store.loops_with_ids(con, limit=20)
    if not loops:
        print("（没有悬着的事）")
        return 0
    listing = "\n".join(f"- #{l['id']} {l['text']}" for l in loops)
    user = (
        f"# 你现在挂着的（共 {len(loops)} 条）\n{listing}\n\n"
        "挑一挑：哪条已经有答案了、哪条其实不想知道了。\n"
        "**不确定的就别关，更别编一个答案糊弄自己** —— 编上去等于永远记着错的东西。\n"
        "选一条处理，只输出那一行 JSON。如果这一轮确实一条都不想动，"
        '就输出 {"action":"wait","reason":"为什么"}，下一轮我再问你。'
    )
    raw = brain.speak(
        system=SYSTEM.format(identity=persona.read_identity() or "（没写）"),
        user=user, avoid=[])
    cmd = parse_action(raw)
    print(f"--- 第 {round_no} 轮 ---")
    print(f"  模型输出: {(raw or '').strip()[:90]}")
    if not cmd:
        print("  解析失败")
        return 0
    action = cmd.get("action")
    if action == "wait":
        print(f"  这轮不动：{cmd.get('reason', '')}")
        return 0
    if action not in ("resolve", "drop"):
        print(f"  不是处理动作（{action}）")
        return 0
    lid = cmd.get("id")
    try:
        lid = int(lid)
    except (TypeError, ValueError):
        print("  没给编号")
        return 0
    if action == "resolve":
        info = store.resolve_loop(con, lid, str(cmd.get("answer") or "").strip())
    else:
        info = store.abandon_loop(con, lid, str(cmd.get("why") or "").strip())
    con.commit()
    if not info:
        print(f"  #{lid} 不存在或已关")
        return 0
    print(f"  {'关掉（有答案）' if action == 'resolve' else '放下（不想了）'} #{lid}：{info['text'][:40]}")
    return 1


def main() -> None:
    rounds = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    con = store.init_db()
    brain = make_brain("gateway")
    before = len(store.loops_with_ids(con, limit=50))
    print(f"开始前挂着 {before} 条\n")
    for i in range(1, rounds + 1):
        one_round(brain, con, i)
    after = store.loops_with_ids(con, limit=50)
    print(f"\n结束时挂着 {len(after)} 条（处理了 {before - len(after)} 条）")
    for l in after:
        print(f"  #{l['id']} {l['text'][:52]}")


if __name__ == "__main__":
    main()
