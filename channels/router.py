"""按目标场景把话发给对应渠道。

她的脑子里不分"QQ 还是微信"，只分"这句话发给哪个场景"。
场景名带前缀，这里按前缀选渠道：

    wxid_xxx / xxx@chatroom  → 微信
    qq_group_123             → QQ
    http_xxx                 → 其他 HTTP 端点
"""

from __future__ import annotations

import json
import urllib.request


def send(scene: str, text: str) -> dict:
    from core import config
    name = config.channel_for(scene)
    try:
        # 代理 AI 信箱：她发给 ai_xxx 的话不往微信发，落到文件里等人读
        if (scene or "").startswith("ai_"):
            import time as _t
            from core.paths import ROOT
            f = ROOT / "data" / "ai_mail.jsonl"
            f.parent.mkdir(parents=True, exist_ok=True)
            with f.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({"ts": _t.time(), "to": scene, "text": text},
                                    ensure_ascii=False) + "\n")
            return {"ok": True, "local": True}
        if name == "wechat":
            from channels import wxhook
            r = wxhook.send_text(scene, text)
            if "ok" not in r:                 # hook 返回的是 ret/msg，归一化一下
                r = dict(r)
                r["ok"] = r.get("ret") == 0
            return r
        if name == "qq":
            from channels import onebot
            return onebot.send_msg(scene, text)
        if name == "other":
            c = (config.channels().get("other") or {})
            base = (c.get("base_url") or "").rstrip("/")
            if not base:
                return {"ok": False, "error": "没配 other 渠道地址"}
            body = json.dumps({"scene": scene, "text": text},
                              ensure_ascii=False).encode("utf-8")
            req = urllib.request.Request(f"{base}/send", data=body, method="POST")
            req.add_header("Content-Type", "application/json")
            if c.get("token"):
                req.add_header("Authorization", f"Bearer {c['token']}")
            with urllib.request.urlopen(req, timeout=10) as r:
                d = json.loads(r.read().decode("utf-8", "replace") or "{}")
            return {"ok": bool(d.get("ok", True)), **d}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    return {"ok": False, "error": f"没有可用渠道（场景 {scene}）"}


def describe() -> str:
    """给 prompt 用：她能往哪些地方说话。"""
    from core import config
    rows = []
    for name, ch in config.channels().items():
        if not ch.get("enabled"):
            continue
        rows.append(f"{ch.get('label') or name}（{name}）")
    return "、".join(rows) or "（没有启用的渠道）"
