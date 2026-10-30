"""单独一轮：让她看看自己的「人设」，确认还是不是她。

    python tools/identity_check.py [轮数]

同 loop_lesson：只给功能说明，不带说话约束、不带悬着的事。
改不改由她决定，不催她。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import persona                      # noqa: E402
from core.act import parse_action            # noqa: E402
from core.brain import make_brain            # noqa: E402
from memory import store                     # noqa: E402

SYSTEM = """# 关于「人设」

你有一份自我认知，每次你「想」点什么的时候，第一段就是它。

它大概决定了几件事：
- 你怎么看自己 —— 多大、什么脾气、在意什么
- 别人问「你是谁」的时候，你心里有个底
- 遇到事的时候，你从什么立场出发

它不是写死的，也不是别人替你定死的规矩。
你可以改它，改完下一次「想」就生效。也可以不改，觉得现在的样子就挺好。

现在写的是这一段 ——

{identity}

——

看完你心里应该有数：这是不是你。
"""


def show(con) -> None:
    current = persona.read_identity() or "（空）"
    print("  现在的人设：")
    for line in current.strip().splitlines():
        print(f"    {line}")


def one_round(brain, con, round_no: int) -> None:
    ident = persona.read_identity() or "（还没写）"
    user = (
        "看完上面那段，你现在可以做一个决定，只输出一行 JSON：\n\n"
        '- 想改：{"action":"revise_self","text":"你希望它变成什么样","why":"为什么改"}\n'
        '- 不改：{"action":"wait","reason":"为什么觉得不用改"}\n\n'
        "改不改都行。改的话，text 要写完整的一段，不是只写改动的部分。"
    )
    raw = brain.speak(system=SYSTEM.format(identity=ident), user=user, avoid=[])
    cmd = parse_action(raw)
    print(f"--- 第 {round_no} 轮 ---")
    print(f"  模型输出: {(raw or '').strip()[:120]}")
    action = (cmd or {}).get("action")
    if action == "wait":
        print(f"  保持原样：{cmd.get('reason', '')}")
        return
    if action != "revise_self":
        print(f"  没做决定（{action}）")
        return
    new_text = str(cmd.get("text") or "").strip()
    why = str(cmd.get("why") or "").strip()
    if not new_text:
        print("  没写新内容")
        return
    old = persona.read_identity() or ""
    persona.write_identity(new_text)
    store.add_event(
        con, f"我改了自己的自我认知：{new_text[:60]}" + (f"（{why}）" if why else ""),
        scene="inner", channel="self", kind="action",
        actor_name="阿鱼", importance=8)
    con.commit()
    print("  改前：", old.replace("\n", " / ")[:60])
    print("  改后：", new_text.replace("\n", " / ")[:60])
    print(f"  理由：{why}")


def main() -> None:
    rounds = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    con = store.init_db()
    brain = make_brain("gateway")
    print("=== 改之前 ===")
    show(con)
    print()
    for i in range(1, rounds + 1):
        one_round(brain, con, i)
    print("\n=== 结束时 ===")
    show(con)


if __name__ == "__main__":
    main()
