"""把字幕变成她能读的东西。

B 站的 AI 字幕是逐句的、没有标点、时间连续（句间空隙为 0）。
直接塞给模型读起来很费劲，所以按时间窗合并成段，超预算就均匀抽样。

"看懂" = 她能说出视频里具体发生过什么，而不是复述标题。
"""

from __future__ import annotations

import json

from bili.client import Bilibili, _text


def fetch(bvid: str, cid: int) -> list[dict]:
    """下载字幕，返回 [{from,to,content}]。没有字幕返回 []。"""
    b = Bilibili()
    try:
        d = b._wbi_get("/x/player/wbi/v2", {"bvid": bvid, "cid": cid})
    except Exception:
        return []
    subs = ((d.get("data") or {}).get("subtitle") or {}).get("subtitles") or []
    if not subs:
        return []
    # 优先中文轨
    pick = next((s for s in subs if str(s.get("lan", "")).startswith(("zh", "ai-zh"))), subs[0])
    url = pick.get("subtitle_url") or pick.get("subtitle_url_v2") or ""
    if url.startswith("//"):
        url = "https:" + url
    if not url:
        return []
    try:
        j = json.loads(_text(url))
    except Exception:
        return []
    out = []
    for x in j.get("body") or []:
        c = (x.get("content") or "").strip()
        if c:
            out.append({"from": float(x.get("from") or 0),
                        "to": float(x.get("to") or 0), "content": c})
    return out


def blocks(lines: list[dict], window: float = 30.0) -> list[tuple[float, str]]:
    """按时间窗把逐句字幕合并成段。"""
    out: list[tuple[float, str]] = []
    start: float | None = None
    buf: list[str] = []
    for ln in lines:
        if start is None:
            start = ln["from"]
        if ln["from"] - start >= window:
            if buf:
                out.append((start, "，".join(buf)))
            start, buf = ln["from"], []
        buf.append(ln["content"])
    if buf and start is not None:
        out.append((start, "，".join(buf)))
    return out


def _mmss(t: float) -> str:
    return f"{int(t) // 60:02d}:{int(t) % 60:02d}"


def digest(lines: list[dict], max_chars: int = 1400, window: float = 30.0) -> str:
    """可读的内容梗概，控制在预算内。"""
    if not lines:
        return ""
    segs = blocks(lines, window)
    total = sum(len(text) for _, text in segs)

    # 超预算就均匀抽段，保头保尾
    if total > max_chars and len(segs) > 6:
        keep = max(6, int(len(segs) * max_chars / total))
        step = (len(segs) - 1) / max(1, keep - 1)
        picked = []
        for i in range(keep):
            idx = min(int(round(i * step)), len(segs) - 1)
            if not picked or picked[-1][0] != idx:
                picked.append((idx, segs[idx]))
        segs = [s for _, s in picked]

    text = "\n".join(f"[{_mmss(t)}] {body}" for t, body in segs)
    return text[:max_chars] if len(text) > max_chars else text


def brief(lines: list[dict], max_chars: int = 600) -> str:
    """更短的版本，给记忆/日志用。"""
    return digest(lines, max_chars=max_chars, window=60.0)
