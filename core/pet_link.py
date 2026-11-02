"""桌宠通信桥 —— 让她的话能传到桌宠窗口，且不把 PySide6 拖进主依赖。

为什么单独一层
--------------
桌宠窗口（tools/pet_window.py）是 PySide6 写的，而主程序是纯标准库。
如果主程序直接 import 那个模块，没装 PySide6 的人连主程序都起不来。
所以两边只认一个约定：**本机 socket 上，一行一个 JSON 对象**。
各自怎么实现对方不用知道，装不装 PySide6 也互不牵连。

约定（版本 1，字段只增不改）
---------------------------
::

    -> {"cmd": "say",   "text": "你好", "inner": false}   弹气泡；inner=True 是心声（灰色斜体）
    -> {"cmd": "mood",  "energy": 0.6, "mood": "平静"}    同步精力/心情，影响速度与动作频率
    -> {"cmd": "state", "state": "thinking"}              idle/walking/thinking/talking/sleeping
    -> {"cmd": "ping"}                                   探活，桌宠回 {"ok": true, "pong": true}

发送失败一律**安静吞掉**：桌宠没开是常态，不能让它影响主程序。
"""

from __future__ import annotations

import json
import socket
import threading
import time
from typing import Any, Protocol

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8791
PROTOCOL_VERSION = 1

# 合法状态。写在这里是为了两边用同一份白名单，免得桌宠收到没见过的字符串乱猜。
STATES = ("idle", "walking", "thinking", "talking", "sleeping")


class PetLinkProtocol(Protocol):
    """两种实现（IPC / 直连）和空实现共用的形状。

    用 Protocol 而不是 ABC：这不值得一条继承链，能 duck typing 就够了。
    """

    def say(self, text: str, inner: bool = False) -> None:
        ...

    def set_mood(self, energy: float, mood: str = "") -> None:
        ...

    def set_state(self, state: str) -> None:
        ...

    def close(self) -> None:
        ...


# ------------------------------------------------------------------ 空实现
class NullPetLink:
    """什么都不做。**默认就是它** —— 没装桌宠的人不该有任何额外开销。"""

    def say(self, text: str, inner: bool = False) -> None:
        return None

    def set_mood(self, energy: float, mood: str = "") -> None:
        return None

    def set_state(self, state: str) -> None:
        return None

    def close(self) -> None:
        return None

    def available(self) -> bool:
        return False

    def __repr__(self) -> str:  # 方便调试时一眼看出这是空桥
        return "<NullPetLink>"


# ------------------------------------------------------------------ IPC
class PetLink:
    """心智体 <-> 桌宠窗口的通信桥。

    两种实现：
    - IPC 模式（默认）：桌宠是独立进程，通过本机 socket 通信
    - 直连模式：同进程内调用（测试用），见 DirectPetLink

    设计取舍：一条**长连接** + 断线重连，而不是每句话开一次连接。
    一句台词很短，但 set_mood 可能被每个节拍调用一次；每次 connect 在
    Windows 上是几百微秒起步，攒起来就会拖慢主循环。
    """

    def __init__(
        self,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        *,
        timeout: float = 0.4,
        retry_after: float = 5.0,
    ) -> None:
        self.host = host
        self.port = int(port)
        self.timeout = float(timeout)
        # 连不上之后先安静这么久，别每句台词都去撞一次墙
        self.retry_after = float(retry_after)
        self._sock: socket.socket | None = None
        self._lock = threading.Lock()
        self._down_until = 0.0
        self._last_probe = 0.0
        self._last_ok = False

    # -------------------------------------------------- 对内
    def _connect(self) -> socket.socket | None:
        """拿一条可用连接；拿不到就返回 None（调用方负责安静降级）。"""
        if self._sock is not None:
            return self._sock
        try:
            sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
            # 桌宠那边回应可能很慢，写超时不用留太久；读只在探活时用到
            sock.settimeout(self.timeout)
            self._sock = sock
            self._down_until = 0.0
            return sock
        except OSError:
            self._down_until = time.monotonic() + self.retry_after
            return None

    def _drop(self) -> None:
        sock, self._sock = self._sock, None
        if sock is None:
            return
        try:
            sock.close()
        except OSError:
            pass

    def _send(self, payload: dict[str, Any]) -> bool:
        """发一条 JSON 行。成功 True，任何异常都 False（绝不往上抛）。"""
        # 处于退避窗口内就直接放弃，连 socket 都不碰
        if time.monotonic() < self._down_until:
            return False
        line = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
        with self._lock:
            sock = self._connect()
            if sock is None:
                return False
            try:
                sock.sendall(line)
                return True
            except OSError:
                # 桌宠被关掉/重启都会走到这里：丢掉旧连接，下一句自动重连
                self._drop()
                self._down_until = time.monotonic() + self.retry_after
                return False

    # -------------------------------------------------- 对外
    def say(self, text: str, inner: bool = False) -> None:
        """让她说一句话（弹气泡）。inner=True 是心声（灰色斜体）。

        空台词直接丢掉：桌宠那边画一个空气泡很蠢，而调用方不必为此写判断。
        """
        text = str(text or "").strip()
        if not text:
            return
        self._send({"cmd": "say", "text": text, "inner": bool(inner)})

    def set_mood(self, energy: float, mood: str = "") -> None:
        """把她的精力/心情同步给桌宠，影响行走速度与动作频率。

        energy 归一化到 0~1：桌宠那边按 `0.45 + energy` 倍速走，
        所以能量低的时候她自己就慢吞吞的，不用桌宠知道「精力」是什么。
        """
        try:
            e = float(energy)
        except (TypeError, ValueError):
            return
        # 越界的值大多来自上游算法，钳一下比报错有用
        e = max(0.0, min(1.0, e))
        self._send({"cmd": "mood", "energy": round(e, 3), "mood": str(mood or "")})

    def set_state(self, state: str) -> None:
        """状态：idle / walking / thinking / talking / sleeping"""
        state = str(state or "").strip().lower()
        if state not in STATES:
            # 记一笔就够了，不抛异常：状态机偶尔写错不该让主程序停摆
            return
        self._send({"cmd": "state", "state": state})

    def available(self) -> bool:
        """探测桌宠是否在跑：真发一个 ping，收到 pong 才算数。

        只判断「端口能连上」是不够的 —— 端口被别的程序占着时会误报。
        结果缓存 0.5 秒，避免界面每帧都去探一次。
        """
        now = time.monotonic()
        if now - self._last_probe < 0.5:
            return self._last_ok
        self._last_probe = now
        ok = False
        with self._lock:
            sock = self._connect()
            if sock is not None:
                try:
                    sock.sendall((json.dumps({"cmd": "ping"}) + "\n").encode("utf-8"))
                    # 桌宠只回一行短 JSON；用 makefile 保证按行读，不会粘包
                    sock.settimeout(self.timeout)
                    data = b""
                    while not data.endswith(b"\n"):
                        chunk = sock.recv(4096)
                        if not chunk:
                            break
                        data += chunk
                    reply = json.loads(data.decode("utf-8").strip() or "{}")
                    ok = bool(reply.get("ok"))
                except (OSError, ValueError):
                    self._drop()
        self._last_ok = ok
        return ok

    def close(self) -> None:
        with self._lock:
            self._drop()

    # 当上下文管理器用：hooks 里 with 一下就不用管收尾
    def __enter__(self) -> PetLink:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def __repr__(self) -> str:
        return f"<PetLink {self.host}:{self.port}{'' if self._sock else ' (未连接)'}>"


# ------------------------------------------------------------------ 直连
class DirectPetLink:
    """直连模式：同一个进程里直接调用目标对象的方法（测试用）。

    存在的理由：桌面端把窗口跑在同一进程里时，不值得多为它开一个 socket；
    单元测试也不该为了验证「气泡内容对不对」去起线程和端口。
    """

    def __init__(self, target: Any) -> None:
        self._target = target
        self._closed = False

    def say(self, text: str, inner: bool = False) -> None:
        if self._closed:
            return
        self._target.say(text, inner=inner)

    def set_mood(self, energy: float, mood: str = "") -> None:
        if self._closed:
            return
        self._target.set_mood(energy, mood)

    def set_state(self, state: str) -> None:
        if self._closed:
            return
        self._target.set_state(state)

    def available(self) -> bool:
        return not self._closed

    def close(self) -> None:
        self._closed = True

    def __repr__(self) -> str:
        return f"<DirectPetLink->{type(self._target).__name__}>"


# ------------------------------------------------------------------ 工厂
def make_pet_link(cfg: dict[str, Any] | None = None) -> PetLinkProtocol:
    """按 config 里的 ``pet`` 段造一个桥；没配置或没开就返回 NullPetLink。

    默认返回空实现，是刻意的：**装桌宠是加装件，不该改变主程序的默认行为**。
    config 里写::

        "pet": {"enabled": true, "host": "127.0.0.1", "port": 8791}
    """
    section = (cfg or {}).get("pet") or {}
    if not isinstance(section, dict) or not section.get("enabled"):
        return NullPetLink()
    return PetLink(
        host=str(section.get("host") or DEFAULT_HOST),
        port=int(section.get("port") or DEFAULT_PORT),
    )
