"""按目标场景把话发给对应渠道。

她的脑子里不分"QQ 还是微信"，只分"这句话发给哪个场景"。
场景名带前缀，这里按前缀选渠道：

    wxid_xxx / xxx@chatroom  → 微信
    qq_group_123             → QQ
    http_xxx                 → 其他 HTTP 端点

两种发法
--------
`send(scene, text)`           纯文本，所有渠道都支持
`send_segments(scene, segs)`  消息段（图片/语音/表情/@），目前只有 QQ 支持；
                              微信 hook 没有发图接口，会自动降级成文字
"""

from __future__ import annotations

import json
import urllib.request


_PET_LINK = None


def _pet_link():
    """桌宠桥的单例。第一次用时按 config 的 pet 段造一个。"""
    global _PET_LINK
    if _PET_LINK is None:
        try:
            from core import pet_link
            from core import config
            _PET_LINK = pet_link.make_pet_link(config.load())
        except Exception:
            return None
    return _PET_LINK


def reset_pet_link() -> None:
    """配置改了之后重造（面板切换开关时调用）。"""
    global _PET_LINK
    _PET_LINK = None


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
        if name == "desk":
            # 桌宠：把话交给 pet_link，由它转给桌宠窗口弹气泡。
            # 桌宠没开时返回 NullPetLink，静默失败，不影响主程序。
            link = _pet_link()
            if link is None:
                return {"ok": False, "error": "桌宠链路没就绪（core/pet_link.py 缺失？）"}
            link.say(text)
            return {"ok": True, "local": True, "via": "desk"}
        if name == "live":
            # 直播：VTS。第一版只把她要说的话记下来并交给 avatar 后端；
            # 真正的语音合成与口型在 core/avatar.py 里（还没接）。
            try:
                from core import avatar
                return avatar.speak_on_live(scene, text)
            except Exception as e:
                return {"ok": False, "error": f"直播后端没就绪：{type(e).__name__}: {e}"}
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


def send_segments(scene: str, segments) -> dict:
    """发消息段。不支持富媒体的渠道自动降级成纯文本。"""
    from channels import message as seg
    from core import config
    name = config.channel_for(scene)
    try:
        if name == "qq":
            from channels import onebot
            return onebot.send_segments(scene, segments)
        # 其余渠道只能发字
        text = seg.to_text(segments)
        if not text:
            return {"ok": False, "error": "这个渠道只能发文字，内容里没有可发的文字"}
        r = send(scene, text)
        if isinstance(r, dict):
            r = dict(r)
            r["downgraded"] = True
        return r
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def send_image(scene: str, path: str, *, caption: str = "") -> dict:
    """便捷入口：发一张图（可带一句话）。

    本地文件按 base64 发：OneBot 端不一定要和本项目同一台机器，
    用 `file:///` 只在同机时成立，跨机器就发不出去。
    """
    from channels import message as seg
    import pathlib as _p
    pp = _p.Path(str(path))
    pic = seg.image_file(str(pp)) if pp.exists() else seg.image(path)
    parts = ([seg.text(caption)] if caption else []) + [pic]
    return send_segments(scene, parts)


def send_voice(scene: str, audio_path: str, *, text: str = "") -> dict:
    from channels import message as seg
    parts = ([seg.text(text)] if text else []) + [seg.record(audio_path)]
    return send_segments(scene, parts)


def describe() -> str:
    """给 prompt 用：她能往哪些地方说话。"""
    from core import config
    rows = []
    for name, ch in config.channels().items():
        if not ch.get("enabled"):
            continue
        rows.append(f"{ch.get('label') or name}（{name}）")
    # 本地场景不需要「启用」，它取决于桌宠/直播进程在不在
    try:
        link = _pet_link()
        if link is not None and link.available():
            rows.append("桌面上（desk）")
    except Exception:
        pass
    return "、".join(rows) or "（没有启用的渠道）"
