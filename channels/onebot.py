"""OneBot 11（QQ 机器人）发消息。

前提：先在本机跑一个 OneBot 实现（NapCat / go-cqhttp / Lagrange），
把它的 HTTP API 地址填进 config.json 的 channels.qq.base_url。

场景名约定：
    qq_group_123456     → 群
    qq_private_789012   → 私聊
"""

from __future__ import annotations

import json
import urllib.request


def cfg() -> dict:
    from core import config
    return config.channels().get("qq") or {}


def _post(action: str, payload: dict) -> dict:
    c = cfg()
    base = (c.get("base_url") or "").rstrip("/")
    if not base:
        return {"ok": False, "error": "没配 OneBot 地址（channels.qq.base_url）"}
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(f"{base}/{action}", data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    if c.get("token"):
        req.add_header("Authorization", f"Bearer {c['token']}")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            d = json.loads(r.read().decode("utf-8", "replace") or "{}")
        ok = (d.get("status") in ("ok", "async")) or d.get("retcode") == 0
        return {"ok": bool(ok), **d}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def send_msg(scene: str, text: str) -> dict:
    s = (scene or "").strip()
    if s.startswith("qq_group_"):
        return _post("send_group_msg",
                     {"group_id": int(s[len("qq_group_"):]), "message": text})
    if s.startswith("qq_private_"):
        return _post("send_private_msg",
                     {"user_id": int(s[len("qq_private_"):]), "message": text})
    return {"ok": False, "error": f"看不懂的 QQ 场景：{scene}"}


def parse_event(ev: dict) -> dict | None:
    """把 OneBot 的上报事件转成她认识的形状（和微信那条同一个契约）。"""
    if not isinstance(ev, dict):
        return None
    t = ev.get("post_type")
    if t == "message":                     # 老一点的上报格式
        mt = ev.get("message_type")
        raw = ev.get("raw_message") or ev.get("message") or ""
        text = raw if isinstance(raw, str) else str(raw)
        if mt == "group":
            scene = f"qq_group_{ev.get('group_id')}"
        else:
            scene = f"qq_private_{ev.get('user_id')}"
        sender = ev.get("sender") or {}
        who = sender.get("card") or sender.get("nickname") or str(ev.get("user_id"))
        return {"channel": "qq", "scene": scene, "actor_id": str(ev.get("user_id") or ""),
                "actor_name": who, "text": text, "kind": "message",
                "ts": int(ev.get("time") or 0) or None, "is_group": mt == "group"}
    if t == "message_sent":                # 自己发的（回推）
        return parse_event({**ev, "post_type": "message"})
    return None
