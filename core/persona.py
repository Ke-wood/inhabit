"""人格加载：从 persona/persona.md 抽 L1 内核与硬规则。"""

from __future__ import annotations

import re
from pathlib import Path

from core.paths import ROOT

PERSONA_FALLBACK = ROOT / "persona" / "persona.md"


def _persona_path() -> Path:
    """人格卡跟着角色走：每个角色的人设在自己的记忆目录里。

    以前是模块级常量，import 时算死 —— 运行时 --profile 切角色会继续读
    上一个角色的卡。改成每次现算，和 _identity_path() 一致。
    """
    try:
        from core import config as _cfg
        p = _cfg.profile()
        return ROOT / (p.get("memory") or "记忆") / "人设卡.md"
    except Exception:
        return ROOT / "记忆" / "人设卡.md"


# 兼容别名：import 时按当时的 active 算一次，仅供外部引用；内部一律走 _persona_path()
PERSONA = _persona_path()


def _section(text: str, title: str) -> str:
    """取 '## {title}' 到下一个 '## ' 之间的内容。"""
    m = re.search(rf"^##\s*{re.escape(title)}.*?$(.*?)(?=^##\s|\Z)", text, re.S | re.M)
    return m.group(1).strip() if m else ""


def load_persona(path: Path | str | None = None) -> tuple[str, list[str]]:
    p = Path(path) if path else _persona_path()
    if not p.exists():
        p = PERSONA_FALLBACK
    if not p.exists():
        return "", []                     # 还没建角色：给空卡，别让进程死在这
    text = p.read_text(encoding="utf-8")
    core = _section(text, "L1 内核")
    # 去掉引用符号和括注行
    core = "\n".join(
        line.lstrip("> ").rstrip() for line in core.splitlines()
        if line.strip() and not line.startswith("##")
    )
    rules_block = _section(text, "反 AI 味硬规则")
    rules = [
        re.sub(r"^\d+\.\s*", "", line.strip())
        for line in rules_block.splitlines()
        if re.match(r"^\d+\.", line.strip())
    ]
    return core, rules


def build_system_prompt(core: str, rules: list[str], state: dict, relations: str = "",
                        where: str = "一个微信群里") -> str:
    rule_lines = "\n".join(f"{i}. {r}" for i, r in enumerate(rules, 1))
    return f"""你是下面这个人，不是助手，不是模型。你在{where}。

{core}

# 硬规则（优先于一切）
{rule_lines}

# 你现在的状态
心情：{state.get('mood', '平静')}
精力：{state.get('energy', '0.8')}
好奇心：{state.get('curiosity', '0.7')}
此刻在想：{state.get('focus') or '没想什么'}
活了：{state.get('age_days', '1')} 天
{relations}

# 输出
只输出你要说的话本身。不要加引号、不要加名字前缀、不要解释。
要短。要像随手打的字。"""


def _identity_path() -> Path:
    """角色隔离：每个角色的人设在自己的记忆目录里。"""
    try:
        from core import config as _cfg
        p = _cfg.profile()
        return ROOT / (p.get("memory") or "记忆") / (p.get("persona") or "我是谁.md")
    except Exception:
        return ROOT / "记忆" / "我是谁.md"


# 兼容别名：import 时按当时的 active 算一次；内部一律走 _identity_path()，
# 否则 --profile 切角色后「我是谁.md」还会读写上一个角色。
IDENTITY_FILE = _identity_path()


def read_identity() -> str:
    """v2 的自我设定：读单独的「我是谁.md」，跳过标题和 `>` 说明行。"""
    p = _identity_path()
    if not p.exists():
        return ""
    try:
        raw = p.read_text(encoding="utf-8")
    except Exception:
        return ""
    lines = [ln for ln in raw.splitlines()
             if ln.strip() and not ln.lstrip().startswith((">", "#"))]
    return "\n".join(lines).strip()


def write_identity(text: str) -> str:
    """把她改的身份写回「我是谁.md」（和 self_state 双写）。说明行保留。"""
    p = _identity_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    old = p.read_text(encoding="utf-8") if p.exists() else "# 我是谁\n"
    notes = [ln for ln in old.splitlines() if ln.lstrip().startswith(">")]
    head = "# 我是谁\n\n" + ("\n".join(notes) + "\n\n" if notes else "")
    p.write_text(head + text.strip() + "\n", encoding="utf-8")
    return str(p)


def block(core: str, rules: list[str]) -> str:
    """给 v2 心智层用：只放"你是谁 + 硬规则"，不规定输出格式。

    think / act 各自有自己的 JSON 输出要求，人格归人格。
    """
    if not core:
        return ""
    rule_lines = "\n".join(f"{i}. {r}" for i, r in enumerate(rules, 1))
    return f"# 你是谁\n{core}\n\n# 硬规则（优先于一切）\n{rule_lines}"


def _replace_section(text: str, title: str, body: str) -> str:
    """把 '## title' 到下一个 '## ' 之间的内容换掉，保留标题行。"""
    pat = rf"(^##\s*{re.escape(title)}.*?$)(.*?)(?=^##\s|\Z)"
    if not re.search(pat, text, re.S | re.M):
        return text.rstrip() + f"\n\n## {title}\n\n{body.strip()}\n"
    return re.sub(pat, lambda m: m.group(1) + "\n\n" + body.strip() + "\n\n",
                  text, count=1, flags=re.S | re.M)


def rewrite_core(new_core: str, *, why: str = "", path: Path | str | None = None) -> dict:
    """她重写自己的 L1 内核。旧版留档，改动记进 CHANGELOG。

    这里不替她判断"该不该改"——能力在她手上，由她自己决定用不用。
    """
    import datetime as _dt

    p = Path(path) if path else _persona_path()
    if not p.exists():
        p = PERSONA_FALLBACK
    text = p.read_text(encoding="utf-8")
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")

    hist = ROOT / "persona" / "history"
    hist.mkdir(parents=True, exist_ok=True)
    backup = hist / f"{stamp}.md"
    backup.write_text(text, encoding="utf-8")

    p.write_text(_replace_section(text, "L1 内核", new_core), encoding="utf-8")

    log = ROOT / "persona" / "CHANGELOG.md"
    with log.open("a", encoding="utf-8") as f:
        f.write(f"\n## {stamp} · 她自己改的\n\n{why or '（没写理由）'}\n\n{new_core}\n")

    return {"file": str(p), "backup": str(backup)}


STYLE_RULES = """# 说话的样子

这些不是规矩，只是几个让话别太像"写出来的"的习惯。怎么说话最后还是你自己定。

- 一句尽量短，二十来字就够。少说一点没关系，不用把话补完整。
- 网络流行语少用（家人们谁懂啊 / 绝绝子 / yyds / 破防 这一类），用多了像在表演。
- 别用"不是…而是…""其实…""说到底…"这种总结腔，那是在讲道理，不是在聊天。
- 书面连接词（然而 / 因此 / 此外 / 与此同时）在微信里显得很怪。
- 不排比、不空泛地举例。想说什么直接说。
- 说具体的东西：几条街、几点、什么声音、谁说了什么。比"很震撼"有信息。
- 句子可以不完整，可以有语气词，可以说半句就停。
- 别急着给建议、别保证什么、别用"会好起来的"这类套话。"""



def style_block() -> str:
    return STYLE_RULES


def format_people(cards: list[dict]) -> str:
    """把人物档案卡渲染成 prompt 片段。"""
    if not cards:
        return ""
    out = ["# 你认得的人"]
    for c in cards:
        rel = c.get("rel") or {}
        bits = []
        if rel.get("label"):
            bits.append(str(rel["label"]))
        fam = rel.get("familiarity")
        if fam is not None:
            bits.append("很熟" if fam > 0.6 else ("认识" if fam > 0.2 else "不太熟"))
        head = f"{c['name']}" + (f"（也叫{'、'.join(c['aliases'])}）" if c.get("aliases") else "") \
               + (f"（{'、'.join(bits)}）" if bits else "")
        out.append(head)
        for cat, items in (c.get("facts") or {}).items():
            out.append(f"  - {cat}：{'；'.join(items)}")
        if c.get("recent"):
            out.append(f"  - 最近说过：{'／'.join(r[:20] for r in c['recent'])}")
    return "\n".join(out)


def build_user_turn(recent: list[str], memories: list[str], nudge: str = "",
                    anchor: str = "", people: list[dict] | None = None,
                    said: list[str] | None = None, read_more: list[str] | None = None,
                    own_memo: str = "") -> str:
    mem = "\n".join(f"- {m}" for m in memories) if memories else "（没想起什么）"
    chat = "\n".join(recent) if recent else "（没人说话）"
    hint = f"\n\n[{nudge}]" if nudge else ""
    focus = f"\n\n# 你要接的是这一条\n{anchor}" if anchor else ""
    people_block = (format_people(people) + "\n\n") if people else ""
    said_block = ("# 你自己说过的话\n" + "\n".join(f"- {s}" for s in (said or [])) + "\n\n") if said else ""
    read_block = ("# 你刚往前翻了翻，更早的聊天\n" + "\n".join(f"- {r}" for r in (read_more or [])) + "\n\n") if read_more else ""
    memo_block = ("# 你自己记着的事\n" + own_memo.strip() + "\n\n") if own_memo.strip() else ""
    return f"""{people_block}{said_block}{read_block}{memo_block}# 你想起的事
{mem}

# 刚才群里在说
{chat}{focus}{hint}

现在轮到你说了。只回应上面那条，别答非所问，别追问与话题无关的事。"""
