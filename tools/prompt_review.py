"""逐段问她：这段 prompt 你怎么看，想不想改。

    python tools/prompt_review.py [段名]

不强制改。能改的段（人设、自我记忆）她说了就直接写文件；
代码里的常量（说话规则、怎么想、怎么做）她只能提意见，记到 data/prompt_review.jsonl 里等你处理。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import memfiles, persona                # noqa: E402
from core.act import ACT_SYSTEM                   # noqa: E402
from core.brain import make_brain                 # noqa: E402
from core.mind import THINK_SYSTEM                # noqa: E402
from memory import store                          # noqa: E402

REVIEW_LOG = ROOT / "data" / "prompt_review.jsonl"

# (名字, 取内容, 能不能她自己改, 说明)
SEGMENTS = [
    ("人设", lambda: persona.read_identity(), True,
     "别人问你是谁时你心里的那个底。改了下一次想事情就生效。"),
    ("说话的样子", lambda: persona.STYLE_RULES, False,
     "你怎么用字、多长、带不带流行语。写在代码里的，你自己改不了，但可以说想要什么样。"),
    ("怎么想", lambda: THINK_SYSTEM, False,
     "你「想」的时候按什么路子想，能想到哪些方向。也是写在代码里的。"),
    ("怎么做", lambda: ACT_SYSTEM, False,
     "你能做的事有哪些、怎么用。写在代码里的。"),
    ("自我记忆", lambda: memfiles.read_self_memo(), True,
     "你自己记着的事，会一直跟着你。你可以加、可以删。"),
]

SYSTEM = """你现在不是要回谁的话，是在看自己的「设置」，然后说看法。

人身上没有这种东西，但你有 —— 每次你要「想」点什么的时候，这些话会先被念给你听一遍。
它们不是别人拿来管你的规矩，是你出发的地方。看着别扭就可以改，觉得挺好就别动。

下面这一段叫「{name}」，它做的事是：

{desc}

这一段原文是 ——

{content}

——

看完说一句看法：它还是你想要的样子吗？想改就改。
"""


def review(brain, con, name: str, getter, editable: bool, desc: str) -> None:
    content = (getter() or "").strip() or "（空的）"
    raw = brain.speak(
        system=SYSTEM.format(name=name, desc=desc, content=content),
        user=(
            "只输出一行 JSON：\n"
            '{"segment":"段名","opinion":"你怎么看这一段","action":"keep 或 change",'
            '"new_text":"要改的话，写完整的新版本；不改就留空"}'
        ),
        avoid=[])
    print(f"\n=== {name} ===")
    print(f"  原文 {len(content)} 字")
    print(f"  她的输出：{(raw or '').strip()[:200]}")
    try:
        text = (raw or "").strip()
        if text.startswith("```"):
            text = text.strip("` \n")
        body = text[text.find("{"):text.rfind("}") + 1] or text[text.find("{"):]
        try:
            d = json.loads(body)
        except json.JSONDecodeError:
            from core.act import _repair_json
            d = _repair_json(body)         # 被截断的长输出
        if not isinstance(d, dict):
            raise ValueError("not a dict")
    except Exception:
        print("  （没解析出来，跳过）")
        return

    opinion = str(d.get("opinion") or "").strip()
    action = str(d.get("action") or "keep").strip().lower()
    new_text = str(d.get("new_text") or "").strip()
    print(f"  看法：{opinion}")
    print(f"  决定：{'改' if action == 'change' else '不动'}")

    if action == "change" and new_text:
        if editable:
            if name == "人设":
                persona.write_identity(new_text)
            elif name == "自我记忆":
                p = memfiles.SELF_MEMO
                p.write_text(new_text.rstrip() + "\n", encoding="utf-8")
            print(f"  ✅ 已写入（{len(new_text)} 字）")
            store.add_event(
                con, f"我改了自己的「{name}」：{new_text[:80]}（{opinion}）",
                scene="inner", channel="self", kind="action",
                actor_name="阿鱼", importance=7)
        else:
            REVIEW_LOG.parent.mkdir(parents=True, exist_ok=True)
            with REVIEW_LOG.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"segment": name, "opinion": opinion,
                                    "new_text": new_text}, ensure_ascii=False) + "\n")
            print(f"  📝 这段写在代码里，改不了。意见已存到 {REVIEW_LOG.name}，等人处理。")
    con.commit()


def main() -> None:
    only = sys.argv[1] if len(sys.argv) > 1 else ""
    con = store.init_db()
    brain = make_brain("gateway")
    for name, getter, editable, desc in SEGMENTS:
        if only and only not in name:
            continue
        review(brain, con, name, getter, editable, desc)


if __name__ == "__main__":
    main()
