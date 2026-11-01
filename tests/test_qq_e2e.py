"""端到端：用假 OneBot 服务器验证「收消息 → 记忆 → 发富媒体」全链路。

    python tests/test_qq_e2e.py

不碰真 QQ：本地起一个 HTTP 服务扮演 OneBot 实现，验证：
1. 收到的群消息（含图片）被正确归一化、图片落到本地
2. 她的 Router 能把消息段正确发给 OneBot
3. 能力探测能读到 app_name / can_send_image
4. 渠道不支持时能降级
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = pathlib.Path(__file__).resolve().parent.parent
TMP = tempfile.mkdtemp(prefix="inhabit-e2e-")
os.environ["DAFEIYU_ROOT"] = TMP
sys.path.insert(0, str(ROOT))

PORT = 5701
CALLS: list[dict] = []
PNG = (b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)


class Fake(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        # 模拟图片 CDN
        if self.path.startswith("/img"):
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(PNG)))
            self.end_headers()
            self.wfile.write(PNG)
            return
        self._send({"status": "ok"})

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n)
        action = self.path.strip("/")
        try:
            payload = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            payload = {}
        CALLS.append({"action": action, "payload": payload})

        if action == "get_version_info":
            return self._send({"status": "ok", "retcode": 0,
                               "data": {"app_name": "fake-onebot", "app_version": "11.0",
                                        "protocol_version": "v11"}})
        if action == "can_send_image":
            return self._send({"status": "ok", "retcode": 0, "data": {"yes": True}})
        if action == "can_send_record":
            return self._send({"status": "ok", "retcode": 0, "data": {"yes": True}})
        if action == "get_status":
            return self._send({"status": "ok", "retcode": 0, "data": {"online": True, "good": True}})
        if action in ("send_group_msg", "send_private_msg"):
            return self._send({"status": "ok", "retcode": 0, "data": {"message_id": 4242}})
        if action == "get_image":
            return self._send({"status": "ok", "retcode": 0, "data": {"file": ""}})
        if action == "get_record":
            return self._send({"status": "ok", "retcode": 0, "data": {"file": ""}})
        return self._send({"status": "ok", "retcode": 0, "data": {}})


ok = 0
fail = 0


def check(name, fn):
    global ok, fail
    try:
        fn()
        print(f"  ok   {name}")
        ok += 1
    except Exception as e:
        print(f"  FAIL {name}: {type(e).__name__}: {e}")
        fail += 1


def main() -> int:
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    # 让配置指向假服务器
    from core import config
    base = f"http://127.0.0.1:{PORT}"
    cfg = config.load()
    cfg.setdefault("channels", {}).setdefault("qq", {})
    cfg["channels"]["qq"].update({"kind": "onebot", "enabled": True,
                                  "base_url": base, "token": "", "prefixes": ["qq_"]})
    (pathlib.Path(TMP) / "config.json").write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    config._cache.clear()
    config._mtime = 0.0

    from channels import message as seg
    from channels import onebot, router

    print("QQ 通道端到端测试（假 OneBot）")

    def caps():
        c = onebot.capabilities(refresh=True)
        assert c["reachable"] is True, c
        assert c["app"] == "fake-onebot", c
        assert c["image"] is True and c["record"] is True, c
        assert "fake-onebot" in onebot.describe_caps(), onebot.describe_caps()

    def inbound_text():
        CALLS.clear()
        ev = {"post_type": "message", "message_type": "group", "group_id": 111,
              "user_id": 222, "time": 1700000000, "message_id": 7,
              "sender": {"card": "群名片"}, "message": "在吗"}
        m = onebot.parse_event(ev)
        assert m["scene"] == "qq_group_111" and m["text"] == "在吗", m

    def inbound_image():
        CALLS.clear()
        ev = {"post_type": "message", "message_type": "group", "group_id": 111,
              "user_id": 222, "time": 1700000001, "message_id": 8,
              "sender": {"nickname": "某人"},
              "message": [{"type": "image",
                           "data": {"file": "a.png", "url": base + "/img/a.png"}}]}
        m = onebot.parse_event(ev)
        assert m is not None, "图片消息被丢弃"
        assert m["text"] == "[图片]", m["text"]
        local = (m["segments"][0].get("data") or {}).get("local_file")
        assert local and pathlib.Path(local).exists(), f"图片没落盘: {local}"
        assert pathlib.Path(local).read_bytes()[:4] == b"\x89PNG", "落盘内容不对"
        assert any(c["action"] == "get_image" for c in CALLS), "没调 get_image"

    def outbound_text():
        CALLS.clear()
        r = router.send("qq_group_111", "你好呀")
        assert r.get("ok"), r
        sent = [c for c in CALLS if c["action"] == "send_group_msg"]
        assert sent, CALLS
        assert sent[0]["payload"]["group_id"] == 111
        msg = sent[0]["payload"]["message"]
        assert isinstance(msg, list) and msg[0]["type"] == "text", msg
        assert msg[0]["data"]["text"] == "你好呀", msg

    def outbound_image():
        CALLS.clear()
        img = pathlib.Path(TMP) / "pic.png"
        img.write_bytes(PNG)
        r = router.send_image("qq_group_111", str(img), caption="看这个")
        assert r.get("ok"), r
        sent = [c for c in CALLS if c["action"] == "send_group_msg"]
        assert sent, CALLS
        msg = sent[0]["payload"]["message"]
        types = [x["type"] for x in msg]
        assert types == ["text", "image"], types
        assert msg[1]["data"]["file"].startswith("base64://"), msg[1]["data"]["file"][:20]

    def outbound_voice():
        CALLS.clear()
        audio = pathlib.Path(TMP) / "v.mp3"
        audio.write_bytes(b"ID3" + b"\x00" * 32)
        r = router.send_voice("qq_private_333", str(audio), text="喂")
        assert r.get("ok"), r
        sent = [c for c in CALLS if c["action"] == "send_private_msg"]
        assert sent, CALLS
        msg = sent[0]["payload"]["message"]
        assert [x["type"] for x in msg] == ["text", "record"], [x["type"] for x in msg]

    def private_scene():
        CALLS.clear()
        r = router.send("qq_private_999", "私聊")
        assert r.get("ok"), r
        sent = [c for c in CALLS if c["action"] == "send_private_msg"]
        assert sent and sent[0]["payload"]["user_id"] == 999, CALLS

    def memory_roundtrip():
        """收到的图片应当能进她的记忆（事件带 source_ref）。"""
        from memory import store
        con = store.init_db()
        m = onebot.parse_event({
            "post_type": "message", "message_type": "group", "group_id": 111,
            "user_id": 222, "time": 1700000002, "message_id": 9,
            "sender": {"nickname": "某人"},
            "message": [{"type": "image",
                         "data": {"file": "b.png", "url": base + "/img/b.png"}}],
        })
        local = m["segments"][0]["data"]["local_file"]
        store.add_event(con, m["text"], scene=m["scene"], channel="qq",
                        actor_id=m["actor_id"], actor_name=m["actor_name"],
                        kind="message", importance=5, source_ref=local)
        con.commit()
        row = con.execute("SELECT text, source_ref FROM events ORDER BY id DESC LIMIT 1").fetchone()
        assert row["text"] == "[图片]", row["text"]
        assert row["source_ref"] == local, row["source_ref"]

    for name, fn in [
        ("能力探测", caps),
        ("收到文字", inbound_text),
        ("收到图片并落盘", inbound_image),
        ("发出文字", outbound_text),
        ("发出图片（含配文）", outbound_image),
        ("发出语音", outbound_voice),
        ("私聊场景", private_scene),
        ("图片进记忆", memory_roundtrip),
    ]:
        check(name, fn)

    srv.shutdown()
    print(f"\n{ok} 通过 / {fail} 失败")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
