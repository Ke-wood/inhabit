"""OneBot 11（QQ 机器人）通道：消息、富媒体、管理接口。

前提：先在本机跑一个 OneBot 11 实现，把它的 HTTP API 地址填进
config.json 的 channels.qq.base_url。实测可用的实现：

    NapCatQQ      https://github.com/NapNeko/NapCatQQ        （活跃，推荐）
    Lagrange.Core https://github.com/LagrangeDev/Lagrange.Core
    go-cqhttp     https://github.com/Mrs4s/go-cqhttp          （老牌，已停更）

本项目只依赖 OneBot 11 标准，不依赖任何实现的私有扩展。
标准：https://github.com/botuniverse/onebot-11

场景名约定：
    qq_group_123456     → 群
    qq_private_789012   → 私聊

能力探测
--------
不同实现对「能不能发图 / 发语音」的支持不一样，硬编码会踩坑。
`capabilities()` 在启动时问一次 `can_send_image` / `can_send_record` /
`get_version_info`，结果缓存起来；调用方据此决定要不要降级成文字。
"""

from __future__ import annotations

import base64
import json
import pathlib
import urllib.request

from channels import message as seg

# 能力探测结果缓存（进程内）
_CAPS: dict | None = None


def cfg() -> dict:
    from core import config
    return config.channels().get("qq") or {}


def base_url() -> str:
    return (cfg().get("base_url") or "").rstrip("/")


# ------------------------------------------------------------------ 底层调用
def call(action: str, payload: dict | None = None, *, timeout: float = 10) -> dict:
    """调 OneBot API。返回归一化后的 {ok, ...data}。"""
    base = base_url()
    if not base:
        return {"ok": False, "error": "没配 OneBot 地址（channels.qq.base_url）"}
    c = cfg()
    data = json.dumps(payload or {}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(f"{base}/{action}", data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    if c.get("token"):
        req.add_header("Authorization", f"Bearer {c['token']}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.loads(r.read().decode("utf-8", "replace") or "{}")
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    ok = (d.get("status") in ("ok", "async")) or d.get("retcode") == 0
    out = dict(d)
    out["ok"] = bool(ok)
    if not ok and not out.get("error"):
        out["error"] = f"retcode={d.get('retcode')} {d.get('wording') or d.get('message') or ''}".strip()
    return out


def _data(resp: dict):
    return (resp or {}).get("data")


# ------------------------------------------------------------------ 发送
def _target(scene: str) -> tuple[str, int] | None:
    s = (scene or "").strip()
    if s.startswith("qq_group_"):
        try:
            return "group", int(s[len("qq_group_"):])
        except ValueError:
            return None
    if s.startswith("qq_private_"):
        try:
            return "private", int(s[len("qq_private_"):])
        except ValueError:
            return None
    return None


def send_segments(scene: str, segments) -> dict:
    """发任意消息段数组。scene 决定发到群还是私聊。"""
    t = _target(scene)
    if not t:
        return {"ok": False, "error": f"看不懂的 QQ 场景：{scene}"}
    kind, tid = t
    action = "send_group_msg" if kind == "group" else "send_private_msg"
    payload = {"message": segments}
    payload["group_id" if kind == "group" else "user_id"] = tid
    r = call(action, payload)
    if r.get("ok"):
        r["message_id"] = (_data(r) or {}).get("message_id")
    return r


def send_msg(scene: str, text: str) -> dict:
    """向后兼容：纯文本，走消息段。"""
    return send_segments(scene, [seg.text(text)])


def send_image(scene: str, path_or_url: str, *, flash: bool = False) -> dict:
    caps = capabilities()
    if not caps.get("image", True):
        return {"ok": False, "error": "这个 OneBot 实现说不能发图"}
    p = str(path_or_url)
    # 本地文件走 base64：不要求 OneBot 端和本项目在同一台机器 / 同一文件系统
    if pathlib.Path(p).exists():
        return send_segments(scene, [seg.image_file(p)])
    return send_segments(scene, [seg.image(p, flash=flash)])


def send_record(scene: str, path_or_url: str) -> dict:
    caps = capabilities()
    if not caps.get("record", True):
        return {"ok": False, "error": "这个 OneBot 实现说不能发语音"}
    p = str(path_or_url)
    if pathlib.Path(p).exists():
        return send_segments(scene, [seg.record_file(p)])
    return send_segments(scene, [seg.record(p)])


def send_like(user_id: str | int, times: int = 1) -> dict:
    return call("send_like", {"user_id": int(user_id), "times": max(1, min(10, times))})


def delete_msg(message_id: str | int) -> dict:
    return call("delete_msg", {"message_id": int(message_id)})


# ------------------------------------------------------------------ 能力探测
def capabilities(refresh: bool = False) -> dict:
    """问一次实现支持什么。结果缓存，失败时保守地当作「都支持」。"""
    global _CAPS
    if _CAPS is not None and not refresh:
        return _CAPS
    caps: dict = {"image": True, "record": True, "app": "", "version": "",
                  "protocol": "", "online": None, "reachable": False}
    if not base_url():
        _CAPS = caps
        return caps

    r = call("get_version_info", {}, timeout=5)
    if r.get("ok"):
        d = _data(r) or {}
        caps["reachable"] = True
        caps["app"] = str(d.get("app_name") or "")
        caps["version"] = str(d.get("app_version") or "")
        caps["protocol"] = str(d.get("protocol_version") or "")

    for action, key in (("can_send_image", "image"), ("can_send_record", "record")):
        rr = call(action, {}, timeout=5)
        if rr.get("ok"):
            yes = (_data(rr) or {}).get("yes")
            if isinstance(yes, bool):
                caps[key] = yes

    st = call("get_status", {}, timeout=5)
    if st.get("ok"):
        caps["online"] = (_data(st) or {}).get("online")

    _CAPS = caps
    return caps


def describe_caps() -> str:
    """给面板/日志的一句话。"""
    c = capabilities()
    if not c.get("reachable"):
        return "QQ：连不上（检查 channels.qq.base_url 与 OneBot 是否在跑）"
    bits = [f"{c.get('app') or 'OneBot'} {c.get('version') or ''}".strip()]
    bits.append("可发图" if c.get("image") else "不能发图")
    bits.append("可发语音" if c.get("record") else "不能发语音")
    if c.get("online") is False:
        bits.append("QQ 离线")
    return "QQ：" + " · ".join(bits)


# ------------------------------------------------------------------ 接收
def _download(url: str, dest_dir: pathlib.Path, name: str = "", *, timeout: float = 20) -> str:
    """把图片/语音拉到本地。失败返回空串，不抛。"""
    if not url:
        return ""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
        ext = seg.guess_ext(name or url)
        p = seg.save_image_data(raw, dest_dir, (name or "media") + ext)
        return str(p)
    except Exception:
        return ""


def _media_dirs() -> tuple[pathlib.Path, pathlib.Path]:
    from core.paths import ROOT
    base = ROOT / "data" / "media"
    return base / "image", base / "record"


def _fetch_segment_media(segs: list[dict]) -> list[dict]:
    """把 image/record 段补上本地路径（`local_file`），供上层读取。"""
    img_dir, rec_dir = _media_dirs()
    for s in segs:
        typ = s.get("type")
        if typ not in ("image", "record"):
            continue
        d = s.setdefault("data", {})
        if d.get("local_file"):
            continue
        name = str(d.get("file") or "")
        url = str(d.get("url") or "")
        dest = img_dir if typ == "image" else rec_dir
        # 优先用实现自带的下载接口（能处理私有文件名）
        action = "get_image" if typ == "image" else "get_record"
        payload = {"file": name}
        if typ == "record":
            payload["out_format"] = "mp3"
        r = call(action, payload, timeout=30)
        if r.get("ok"):
            f = str((_data(r) or {}).get("file") or "")
            if f and pathlib.Path(f).exists():
                try:                      # 拷到自己的目录，避免实现清缓存后失效
                    raw = pathlib.Path(f).read_bytes()
                    ext = seg.guess_ext(f)
                    d["local_file"] = str(seg.save_image_data(raw, dest, (name or "media") + ext))
                except Exception:
                    d["local_file"] = f
                continue
        if url:
            got = _download(url, dest, name)
            if got:
                d["local_file"] = got
    return segs


def parse_event(ev: dict) -> dict | None:
    """把 OneBot 的上报事件转成她认识的形状（和微信那条同一个契约）。

    只为 message 类事件返回；notice/request/meta 一律忽略。
    """
    if not isinstance(ev, dict):
        return None
    t = ev.get("post_type")
    if t == "message_sent":                # 自己发的（回推）—— 转成普通 message 同构
        ev = {**ev, "post_type": "message"}
        t = "message"
    if t != "message":
        return None

    mt = ev.get("message_type")
    if mt == "group":
        scene = f"qq_group_{ev.get('group_id')}"
    elif mt == "private":
        scene = f"qq_private_{ev.get('user_id')}"
    else:
        return None

    segs = seg.parse(ev.get("message"))
    if not segs:
        raw = ev.get("raw_message") or ""
        segs = seg.parse_cq(str(raw)) if raw else []
    if not segs:
        return None

    sender = ev.get("sender") or {}
    who = sender.get("card") or sender.get("nickname") or str(ev.get("user_id"))

    # 媒体落到本地：她要「看」图片，就得先有一份能读的文件
    segs = _fetch_segment_media(segs)

    body = seg.to_text(segs)
    if not body.strip():
        return None                        # 纯图片无文字，也要收 —— 由上层决定
    return {
        "channel": "qq",
        "scene": scene,
        "actor_id": str(ev.get("user_id") or ""),
        "actor_name": str(who),
        "text": body,
        "kind": "message",
        "ts": int(ev.get("time") or 0) or None,
        "is_group": mt == "group",
        "msgid": str(ev.get("message_id") or ""),
        "segments": segs,                  # 原始消息段，供上层决定要不要看图/听语音
        "self_id": str(ev.get("self_id") or ""),
    }
