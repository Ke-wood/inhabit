"""消息段（Message Segment）：把「一句话」升级成「一段可以混着图片和语音的内容」。

为什么需要它
------------
早期版本里，`send(scene, text)` 只能传字符串。QQ 生态里能做的事远不止纯文本：
图片、语音、表情、@ 人、回复引用、链接分享、合并转发……这些在 OneBot 11 里
统一用「消息段」表达：

    {"type": "text",  "data": {"text": "看这个"}}
    {"type": "image", "data": {"file": "https://... 或 base64://... 或 file:///..."}}
    {"type": "record","data": {"file": "base64://..."}}
    {"type": "at",    "data": {"qq": "10001"}}
    {"type": "reply", "data": {"id": "123456"}}
    {"type": "face",  "data": {"id": "178"}}

本模块提供三件事：
1. `Seg` —— 一个轻量的消息段构造与校验；
2. `parse(message)` —— 把 OneBot 的任意格式（CQ 码字符串 / 数组 / 字符串）统一成 list[dict]；
3. `to_text(segs)` —— 退回纯文本，给「只认文字」的旧通道或给 prompt 用。

规范来源：OneBot 11 消息段
https://github.com/botuniverse/onebot-11/blob/master/message/segment.md
"""

from __future__ import annotations

import base64
import mimetypes
import pathlib
import re

# 纯文本里出现这些时，提示调用方「这段内容不是人能读的」
NON_TEXT_TYPES = {
    "image", "record", "video", "face", "mface", "reply", "forward", "node",
    "json", "xml", "share", "music", "contact", "location", "poke", "dice",
    "rps", "shake", "anonymous",
}

# 文本化时给这些类型一个可读占位，方便进 prompt 与记忆
PLACEHOLDER = {
    "image": "[图片]",
    "record": "[语音]",
    "video": "[视频]",
    "face": "[表情]",
    "mface": "[表情]",
    "reply": "[回复]",
    "forward": "[合并转发]",
    "node": "[合并转发]",
    "json": "[卡片]",
    "xml": "[卡片]",
    "share": "[链接]",
    "music": "[音乐]",
    "contact": "[名片]",
    "location": "[位置]",
    "poke": "[戳一戳]",
    "dice": "[骰子]",
    "rps": "[猜拳]",
    "shake": "[窗口抖动]",
    "anonymous": "[匿名]",
}

_CQ_RE = re.compile(r"\[CQ:([a-zA-Z_]+)((?:,[^\]]*)?)\]")
_CQ_ESC = (("&#91;", "["), ("&#93;", "]"), ("&#44;", ","), ("&amp;", "&"))


def _cq_unescape(s: str) -> str:
    for a, b in _CQ_ESC:
        s = s.replace(a, b)
    return s


def _cq_escape(s: str) -> str:
    for a, b in _CQ_ESC:
        s = s.replace(b, a)
    return s


# ------------------------------------------------------------------ 构造
def text(s: str) -> dict:
    return {"type": "text", "data": {"text": str(s)}}


def image(path_or_url: str, *, flash: bool = False) -> dict:
    """图片。file 支持：本地绝对路径 / http(s) URL / base64:// / 收到的文件名。

    本地路径会被转成 `file:///` URI —— 直接传盘符路径在部分实现里会被当成文件名。
    """
    f = str(path_or_url).strip()
    p = pathlib.Path(f)
    if p.exists() and p.is_file():
        f = p.resolve().as_uri()
    d = {"file": f}
    if flash:
        d["type"] = "flash"
    return {"type": "image", "data": d}


def image_bytes(raw: bytes, *, name: str = "a.png", flash: bool = False) -> dict:
    """内存里的图直接发，不用先落盘。"""
    b64 = base64.b64encode(raw).decode("ascii")
    d = {"file": f"base64://{b64}"}
    if flash:
        d["type"] = "flash"
    return {"type": "image", "data": d}


def image_file(path: str) -> dict:
    """把本地图片读进来，按 base64 发（不依赖 OneBot 端能访问你的文件系统）。"""
    p = pathlib.Path(path)
    raw = p.read_bytes()
    return image_bytes(raw, name=p.name)


def record(path_or_url: str) -> dict:
    """语音。多数实现接受 mp3/amr/silk；mp3 最省事。

    注意：QQ 语音对格式挑剔，silk 是原生格式，edge-tts 产出的 mp3 在
    NapCat 上通常能直接发；发不出去就用 `to_silk`（需要 ffmpeg）或改用文件。
    """
    f = str(path_or_url).strip()
    p = pathlib.Path(f)
    if p.exists() and p.is_file():
        f = p.resolve().as_uri()
    return {"type": "record", "data": {"file": f}}


def record_file(path: str) -> dict:
    p = pathlib.Path(path)
    raw = p.read_bytes()
    return {"type": "record", "data": {"file": "base64://" + base64.b64encode(raw).decode("ascii")}}


def at(qq: str | int) -> dict:
    return {"type": "at", "data": {"qq": str(qq)}}


def reply(message_id: str | int) -> dict:
    return {"type": "reply", "data": {"id": str(message_id)}}


def face(face_id: str | int) -> dict:
    return {"type": "face", "data": {"id": str(face_id)}}


def share(url: str, title: str = "", content: str = "", image_url: str = "") -> dict:
    d = {"url": str(url), "title": title or url}
    if content:
        d["content"] = content
    if image_url:
        d["image"] = image_url
    return {"type": "share", "data": d}


def music(song_id: str | int, *, provider: str = "163") -> dict:
    """provider: qq / 163 / xm"""
    return {"type": "music", "data": {"type": provider, "id": str(song_id)}}


# ------------------------------------------------------------------ 解析
def _seg_from_cq(m: re.Match) -> dict:
    typ = m.group(1)
    raw = (m.group(2) or "").lstrip(",")
    data: dict = {}
    for pair in raw.split(","):
        if not pair or "=" not in pair:
            continue
        k, v = pair.split("=", 1)
        data[k.strip()] = _cq_unescape(v)
    return {"type": typ, "data": data}


def parse_cq(s: str) -> list[dict]:
    """CQ 码字符串 → 消息段列表。没有 CQ 码就是单个 text 段。"""
    s = s or ""
    out: list[dict] = []
    pos = 0
    for m in _CQ_RE.finditer(s):
        if m.start() > pos:
            piece = s[pos:m.start()]
            if piece:
                out.append(text(_cq_unescape(piece)))
        out.append(_seg_from_cq(m))
        pos = m.end()
    if pos < len(s):
        rest = s[pos:]
        if rest:
            out.append(text(_cq_unescape(rest)))
    return out


def parse(message) -> list[dict]:
    """统一成 list[dict]。接受 None / 字符串 / 单个段 / 段数组。"""
    if message is None:
        return []
    if isinstance(message, str):
        return parse_cq(message)
    if isinstance(message, dict):
        t = message.get("type")
        d = message.get("data")
        if t and isinstance(d, dict):
            return [{"type": str(t), "data": dict(d)}]
        # 有的实现直接给 {"text": ...}
        if "text" in message:
            return [text(message["text"])]
        return []
    if isinstance(message, (list, tuple)):
        out: list[dict] = []
        for item in message:
            out.extend(parse(item))
        return out
    return [text(str(message))]


# ------------------------------------------------------------------ 转换
def to_text(segs) -> str:
    """消息段 → 纯文本。图片变 [图片]，@ 变 @名字，其余按占位。"""
    parts: list[str] = []
    for s in parse(segs):
        typ = s.get("type")
        d = s.get("data") or {}
        if typ == "text":
            parts.append(str(d.get("text") or ""))
        elif typ == "at":
            qq = str(d.get("qq") or "")
            parts.append("@全体成员" if qq == "all" else f"@{d.get('name') or qq}")
        elif typ == "face":
            parts.append(PLACEHOLDER["face"])
        else:
            parts.append(PLACEHOLDER.get(typ, f"[{typ}]"))
    return "".join(parts).strip()


def text_only(segs) -> str:
    """只取文字部分，丢掉所有非文字段（用于「不把图片当成一句话」的判断）。"""
    return "".join(str((s.get("data") or {}).get("text") or "")
                   for s in parse(segs) if s.get("type") == "text").strip()


def seg_types(segs) -> list[str]:
    return [s.get("type", "") for s in parse(segs)]


def has(segs, *types: str) -> bool:
    got = set(seg_types(segs))
    return any(t in got for t in types)


def first(segs, typ: str) -> dict | None:
    for s in parse(segs):
        if s.get("type") == typ:
            return s
    return None


def summary(segs) -> str:
    """给日志用：文字 + 类型计数，例如 `看这个 [图片×1]`。"""
    body = to_text(segs)
    counts: dict[str, int] = {}
    for t in seg_types(segs):
        if t in NON_TEXT_TYPES:
            counts[t] = counts.get(t, 0) + 1
    extra = " ".join(f"[{k}×{v}]" for k, v in counts.items())
    return (body + (" " if body and extra else "") + extra).strip()


# ------------------------------------------------------------------ 图片落盘
def guess_ext(file_field: str, default: str = ".png") -> str:
    """从 OneBot 的 file 字段猜扩展名。"""
    f = (file_field or "").split("?")[0]
    suffix = pathlib.Path(f).suffix
    if suffix and len(suffix) <= 6:
        return suffix
    return mimetypes.guess_extension(mimetypes.guess_type(f)[0] or "") or default


def save_image_data(raw: bytes, dest_dir: pathlib.Path, name: str = "") -> pathlib.Path:
    """把收到的图片存到本地，返回路径。给「看图片」用。"""
    dest_dir.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^\w.\-]", "_", name or "img")
    if "." not in safe:
        safe += ".png"
    p = dest_dir / safe
    p.write_bytes(raw)
    return p
