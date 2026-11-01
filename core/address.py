"""称呼：她叫什么、别人叫她什么、叫错了怎么办。

为什么单独一个模块
------------------
「不许叫我某个绰号」这种事，硬编码一句 `if bad_name in text: refuse()` 是最差的做法：
她会变成一台拒绝机器，人味全没了。

真实的反应是有层次的：
- 第一次叫错：纠正一下，带点不高兴，但还客气
- 反复叫：真的会烦，语气变硬
- 别人跟着一起叫：会觉得被起外号
- 叫对了：会高兴一点

所以这里只做**检测**，把「刚才被叫错了」这个事实交给她的 prompt，
让她自己决定怎么反应 —— 反应本身仍然由人格决定。

规则写在 `记忆/称呼.md`，是可读可改的 Markdown，不是代码。
"""

from __future__ import annotations

import pathlib
import re
import time

from core.paths import ROOT


def _memory_dir() -> pathlib.Path:
    try:
        from core import config as _cfg
        p = _cfg.profile()
        return ROOT / (p.get("memory") or "记忆")
    except Exception:
        return ROOT / "记忆"


def rules_file() -> pathlib.Path:
    return _memory_dir() / "称呼.md"


_SECTION = re.compile(r"^##\s*(.+?)\s*$", re.M)


def _sections(text: str) -> dict[str, str]:
    """把 `## 标题` 切成 {标题: 正文}。"""
    out: dict[str, str] = {}
    marks = list(_SECTION.finditer(text))
    for i, m in enumerate(marks):
        start = m.end()
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        out[m.group(1).strip()] = text[start:end].strip()
    return out


def _names(block: str) -> list[str]:
    """从「- 名字：说明」或「- 名字」里取名字。"""
    out: list[str] = []
    for line in (block or "").splitlines():
        line = line.strip()
        if not line.startswith(("-", "*")):
            continue
        body = line.lstrip("-* ").strip()
        if not body or body.startswith("<!--"):
            continue
        name = re.split(r"[：:（(]", body, maxsplit=1)[0].strip()
        if name:
            out.append(name)
    return out


def load() -> dict:
    """读称呼规则。文件不存在时返回空规则（不报错）。"""
    p = rules_file()
    if not p.exists():
        return {"liked": [], "hated": [], "reaction": "", "path": p}
    try:
        text = p.read_text(encoding="utf-8")
    except Exception:
        return {"liked": [], "hated": [], "reaction": "", "path": p}
    sec = _sections(text)
    liked: list[str] = []
    hated: list[str] = []
    # 顺序很重要：「别这么叫我」同时含「别」和「叫」，必须先判否定
    NEG = ("别", "不要", "不许", "不准", "不喜欢", "讨厌", "禁", "错", "雷", "不接受")
    POS = ("可以", "喜欢", "叫我", "名字", "称呼", "normal", "接受")
    for k, v in sec.items():
        if any(w in k for w in NEG):
            hated += _names(v)
        elif any(w in k for w in POS):
            liked += _names(v)
    # 去重，且从「喜欢」里剔除「讨厌」的（规则写冲突时，讨厌优先）
    hated = list(dict.fromkeys(x for x in hated if x))
    liked = list(dict.fromkeys(x for x in liked if x and x not in hated))
    reaction = ""
    for k, v in sec.items():
        if any(w in k for w in ("反应", "怎么办", "怎么回应")):
            reaction = v
            break
    return {"liked": liked, "hated": hated, "reaction": reaction, "path": p}


# ------------------------------------------------------------------ 计数
def _key(name: str) -> str:
    return f"addr_wrong_{name}"


def times_called_wrong(con, name: str) -> int:
    try:
        row = con.execute("SELECT value FROM self_state WHERE key=?", (_key(name),)).fetchone()
        return int(row["value"]) if row else 0
    except Exception:
        return 0


def bump_wrong(con, name: str) -> int:
    """记一次「又被叫错了」，返回累计次数。"""
    n = times_called_wrong(con, name) + 1
    try:
        con.execute(
            "INSERT INTO self_state(key,value,updated_at) VALUES (?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (_key(name), str(n), time.strftime("%Y-%m-%dT%H:%M:%S")))
        con.commit()
    except Exception:
        pass
    return n


# ------------------------------------------------------------------ 检测
def detect(text: str, *, bot_names: tuple[str, ...] = ()) -> dict | None:
    """这条消息里有没有人叫了她不喜欢的名字。

    返回 {"hit", "all"}；没有就 None。

    规则（按优先级）：
    1. 出现在「别这么叫我」里的词 → **算叫错**，哪怕它也在别名表里。
       这正是「不许叫某个绰号」能生效的原因：禁称优先级最高。
    2. 只出现在别名/正名里的词 → 不算叫错。
    `bot_names` 只用来判断「是不是在叫她」，不用来否决禁称。
    """
    t = (text or "").strip()
    if not t:
        return None
    r = load()
    # 禁称优先：不再用 bot_names 排除
    hits = [n for n in r["hated"] if n and n in t]
    if not hits:
        return None
    # 名字越长的越具体，优先报长的那一个（长禁称优先于它的子串）
    hits.sort(key=len, reverse=True)
    return {"hit": hits[0], "all": hits}


def describe(hit: dict | None, count: int = 0) -> str:
    """把「被叫错了」变成一句给 prompt 看的提示。

    分三档：第一次、第二次、第三次以后。语气递进，但决定权仍在她。
    """
    if not hit:
        return ""
    name = hit["hit"]
    r = load()
    reaction = f"\n{r['reaction']}" if r.get("reaction") else ""
    if count <= 1:
        tone = (f'刚才有人叫你「{name}」。这不是你的名字，但这是第一次。\n'
                f'纠正一下就行，别翻脸 —— 不高兴可以露一点，但还留情面。')
    elif count == 2:
        tone = (f'又是「{name}」。你已经说过一次了，他还在叫。\n'
                f'这次可以明显不耐烦，但别重复上次那句原话。')
    else:
        tone = (f'「{name}」。已经第 {count} 次了。\n'
                f'这次可以真的生气 —— 短、硬、不解释。也可以干脆不接这个称呼，只回别的。')
    # 每一档都写明她真正的名字，免得被叫多了就默认自己叫那个
    liked = "、".join(r["liked"][:2]) or "你自己的名字"
    tone += f"\n（你叫{liked}，不叫「{name}」。）"
    return tone + reaction


def stats(con) -> dict:
    """面板用：每个禁称被叫错过几次。"""
    r = load()
    return {n: times_called_wrong(con, n) for n in r["hated"]}
