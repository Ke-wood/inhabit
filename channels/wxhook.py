"""微信 Hook 的收件服务器 + 发送客户端。

Hook 通过 `/set_callback` 把消息 POST 到这里；这里落盘并转成统一 Msg。
"""

from __future__ import annotations

import json
import pathlib
import sys
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# 直接 `python channels/wxhook.py` 跑的时候，sys.path 里没有项目根
_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

DEFAULT_BASE = "http://127.0.0.1:30001"


def _base() -> str:
    """hook 地址：优先读 config.channels.wechat.hook_url，面板改了立刻生效。"""
    try:
        from core import config as _cfg
        ch = (_cfg.channels() or {}).get("wechat") or {}
        return str(ch.get("hook_url") or DEFAULT_BASE).rstrip("/")
    except Exception:
        return DEFAULT_BASE
from core.paths import ROOT          # noqa: E402
INBOX = ROOT / "data" / "wx_inbox.jsonl"
RAW = ROOT / "data" / "wx_raw.jsonl"


# ------------------------------------------------------------------ 发送
def _post(path: str, body: dict) -> dict:
    req = urllib.request.Request(_base() + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8") or "{}")


def send_text(to: str, msg: str) -> dict:
    return _post("/SendTextMsg", {"wxidorgid": to, "msg": msg})


def send_image(to: str, path: str) -> dict:
    return _post("/SendImgMsg", {"wxidorgid": to, "path": path})


def send_link(to: str, title: str, desc: str, url: str, thumb: str = "") -> dict:
    return _post("/send_xml", {"wxid": to, "title": title, "description": desc,
                               "thumb_url": thumb, "url": url})


def profile() -> dict:
    return _post("/GetSelfProfile", {})


def set_callback(url: str) -> dict:
    return _post("/set_callback", {"url": url})


def query(sql: str) -> dict:
    return _post("/QueryDB/execute", {"optDbName": "", "SQL": sql})


# ------------------------------------------------------------------ 接收
SKIP_TYPES = {10000, 10002, 51}   # 系统消息 / 撤回 / lastMessage 状态同步
SKIP_IDS = {"filehelper", "newsapp", "weixin", "qqnews"}   # 草稿本 / 腾讯新闻等服务号
SKIP_XML = ("<mmreader", "<voip", "<sysmsg", "<?xml")        # 公众号卡片/通话/系统/结构化 XML


def normalize(payload: dict) -> dict | None:
    """把推来的消息转成我们认识的形状。

    同一个入口收两种格式：
    - 微信 hook 的扁平结构（type / roomid / sender / content）
    - OneBot 11 的 post_type 结构（QQ）
    """
    # OneBot 11（QQ）先认，它的字段和微信完全不重叠
    if str(payload.get("post_type") or "") in ("message", "message_sent"):
        try:
            from channels import onebot
            m = onebot.parse_event(payload)
        except Exception:
            m = None
        if m:
            m.setdefault("ts", time.time())
            m["kind"] = "message"
            return m

    g = lambda *keys: next((payload[k] for k in keys if k in payload and payload[k] not in (None, "")), None)
    t = payload.get("type")
    if t in SKIP_TYPES:            # 这些不是人说的话，别进库
        return None
    for k in ("roomid", "sender", "from_wxid", "wxid"):
        if str(payload.get(k) or "").strip().lower() in SKIP_IDS:
            return None            # 文件传输助手 / 腾讯新闻这类整段忽略
    text = g("msg", "content", "text", "Msg")
    if not text:
        return None
    head = str(text).lstrip()[:12]
    if any(head.startswith(x) for x in SKIP_XML):
        return None                # 公众号文章卡片之类，不是人话
    return {
        "channel": "wechat",
        "scene": g("roomid", "room_id", "chatroom", "ChatRoomName") or g("sender", "from_wxid", "wxid") or "unknown",
        # 群消息里 wxid 字段是群 ID，sender 才是人；顺序不能反
        "actor_id": g("sender", "from_wxid", "wxid") or "unknown",
        "actor_name": g("from_nickname", "nickname", "sender_name") or "",
        "text": text,
        "kind": "message",
        "ts": g("timestamp", "time", "create_time") or time.time(),
        "is_group": bool(g("roomid", "room_id", "chatroom")),
        # msgid 是幂等键：重放同一批消息时靠它去重（以前这里丢掉，下游只能靠文本猜）
        "msgid": str(payload.get("msgid") or payload.get("msg_id") or ""),
        "wx_type": t,              # 留给下游判断是不是图片/表情
        "raw_keys": sorted(payload.keys()),
    }


class Handler(BaseHTTPRequestHandler):
    seen: set = set()

    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n).decode("utf-8", "replace")
        with RAW.open("a", encoding="utf-8") as f:
            f.write(body + "\n")
        try:
            payload = json.loads(body)
            items = payload if isinstance(payload, list) else [payload]
            for it in items:
                mid = it.get("msgid") or it.get("message_id")
                if mid:
                    if mid in Handler.seen:      # hook 偶尔把同一条推两次
                        continue
                    Handler.seen.add(mid)
                    if len(Handler.seen) > 4000:
                        Handler.seen.clear()
                m = normalize(it)
                if m:
                    with INBOX.open("a", encoding="utf-8") as f:
                        f.write(json.dumps(m, ensure_ascii=False) + "\n")
        except Exception as e:
            with RAW.open("a", encoding="utf-8") as f:
                f.write(f"# parse error: {e}\n")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"ok":true}')


def serve(port: int = 8771) -> None:
    ROOT.joinpath("data").mkdir(exist_ok=True)
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"收件服务器: http://127.0.0.1:{port}/cb  (落盘 {INBOX})", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "serve":
        serve(int(sys.argv[2]) if len(sys.argv) > 2 else 8771)
    elif len(sys.argv) > 1 and sys.argv[1] == "hook":
        print(set_callback("http://127.0.0.1:8771/cb"))
    elif len(sys.argv) > 1 and sys.argv[1] == "send":
        print(send_text(sys.argv[2], sys.argv[3]))
    else:
        print(profile())
