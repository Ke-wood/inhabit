"""把桌宠的「输入栏」接进主程序：倾听反方向的消息。

为什么需要这个
--------------
原来的链路是单向的：

    主程序 ──say/mood/state──► 桌宠窗口

桌宠只是个显示端，用户不能在桌宠上打字。加上输入栏之后，反方向也要通：

    桌宠窗口 ──input──► 主程序 ──► 当成一条普通消息喂给 Live

这样「点她 → 打字 → 她回话」才成立。不复用 8791（那是主程序推过去的端口），
另开 8792 专收上行，避免两个方向抢一个连接。
"""

from __future__ import annotations

import json
import socketserver
import threading

HOST = "127.0.0.1"
CORE_PORT = 8792          # 桌宠 → 主程序
SCENE = "desk:input"      # 用户从桌宠输入的话，归这个场景


class _Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        on_input = self.server.on_input        # type: ignore[attr-defined]
        for raw in self.rfile:
            line = raw.decode("utf-8", "replace").strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(msg, dict):
                continue
            if msg.get("cmd") == "input":
                text = str(msg.get("text") or "").strip()
                if text:
                    try:
                        on_input(text)
                    except Exception:
                        pass


class _Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def serve(on_input, port: int = CORE_PORT) -> threading.Thread:
    """起上行服务。on_input(text) 会在独立线程里被调。

    on_input 必须自己保证线程安全（通常就是往队列里塞一条）。
    """
    srv = _Server((HOST, port), _Handler)
    srv.on_input = on_input                    # type: ignore[attr-defined]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return t


def scene() -> str:
    """桌宠输入用的场景名。"""
    return SCENE
