"""直播后端：把她说的话送到 Live2D 形象上（目前只做 VTube Studio）。

为什么不自己渲染
----------------
Live2D 官方许可对「把 Cubism SDK 编进 AI 产品」单列了 *Expandable Applications*，
需要审核并签专门协议。用 VTube Studio 的 WebSocket，渲染发生在用户自己的电脑上，
我们只发 JSON 参数 —— 不碰 SDK、不分发模型、不落入那条条款。

这是「不自绘」最重要的理由，不是技术偏好。

三条腿
------
1. `NullBackend`   —— 默认。什么都不发，保证没装 VTS 也能跑。
2. `VTSBackend`    —— 连 VTube Studio（本机 ws://127.0.0.1:8001）。
3. `DryRunBackend` —— 只记日志，给调试和不装 VTS 的演示用。

边界（第一版）
-------------
- 不内置渲染、不 vendor Cubism Core、不打包任何 .moc3/.exp3.json
- 不做口型（口型需要解码 TTS 音频，见 docs/pet-and-live.md §4.3）
- 不做弹幕接入
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field

try:                                   # 纯标准库时没有 websocket-client
    import websocket                   # type: ignore
except Exception:                      # pragma: no cover
    websocket = None


DEFAULT_VTS_URL = "ws://127.0.0.1:8001"
API_NAME = "VTubeStudioPublicAPI"
API_VERSION = "1.0"


@dataclass
class Backend:
    """所有后端的共同接口。"""

    def start(self) -> bool:
        return False

    def stop(self) -> None:
        pass

    def available(self) -> bool:
        return False

    def say(self, text: str) -> dict:
        return {"ok": False, "error": "这个后端不说话"}

    def set_params(self, params: dict) -> None:
        pass

    def trigger(self, hotkey: str) -> None:
        pass


class NullBackend(Backend):
    """默认后端：什么都不做。

    没装 VTS、没开直播时用它 —— 主程序的行为一点不变。
    """

    def available(self) -> bool:
        return False


class DryRunBackend(Backend):
    """只记日志。给她挂直播场景但不想连 VTS 时用。"""

    def __init__(self, log=print):
        self.log = log
        self.said: list[str] = []

    def start(self) -> bool:
        self.log("· 直播后端：dry-run（不连 VTS）")
        return True

    def available(self) -> bool:
        return True

    def say(self, text: str) -> dict:
        self.said.append(text)
        self.log(f"· [live] 她要说：{text[:40]}")
        return {"ok": True, "local": True, "via": "dryrun"}


class VTSBackend(Backend):
    """VTube Studio WebSocket 后端。

    协议要点（DenchiSoft/VTubeStudio README）：
    - 先 `AuthenticationTokenRequest` 换 token，VTS 会弹窗让用户点 Allow
    - token 可以落盘复用，之后每次连接发 `AuthenticationRequest`
    - 写参数用 `InjectParameterDataRequest`，且**必须至少每秒重发一次**，
      否则参数会被判丢失、回落到默认值
    """

    def __init__(self, url: str = DEFAULT_VTS_URL, *,
                 plugin_name: str = "Inhabit",
                 developer: str = "inhabit",
                 token: str = "", log=print):
        self.url = url
        self.plugin_name = plugin_name
        self.developer = developer
        self.token = token
        self.log = log
        self._ws = None
        self._rid = 0
        self._authed = False
        self._params: dict[str, float] = {}
        self._last_push = 0.0

    # ---------------------------------------------------------------- 生命周期
    def start(self) -> bool:
        if websocket is None:
            self.log("· 直播后端：没装 websocket-client，跳过 VTS")
            return False
        try:
            self._ws = websocket.create_connection(self.url, timeout=5)
        except Exception as e:
            self.log(f"· 直播后端：连不上 VTS（{type(e).__name__}）")
            return False
        self._authed = self._auth()
        if self._authed:
            self.log("· 直播后端：VTS 已连接")
            self._probe_params()
        return self._authed

    def stop(self) -> None:
        try:
            if self._ws is not None:
                self._ws.close()
        except Exception:
            pass
        self._ws = None
        self._authed = False

    def available(self) -> bool:
        return bool(self._ws is not None and self._authed)

    # ---------------------------------------------------------------- 协议
    def _next_id(self) -> str:
        self._rid += 1
        return f"inhabit-{self._rid}"

    def _call(self, msg_type: str, data: dict | None = None, *,
              timeout: float = 5) -> dict:
        """发一条请求并等响应。失败返回 {}，绝不抛。"""
        if self._ws is None:
            return {}
        body = {
            "apiName": API_NAME,
            "apiVersion": API_VERSION,
            "requestID": self._next_id(),
            "messageType": msg_type,
        }
        if data is not None:
            body["data"] = data
        try:
            self._ws.send(json.dumps(body))
            raw = self._ws.recv()
            d = json.loads(raw or "{}")
            return d.get("data") or {}
        except Exception:
            return {}

    def _auth(self) -> bool:
        # 1) 有 token 先试
        if self.token:
            d = self._call("AuthenticationRequest", {
                "pluginName": self.plugin_name,
                "pluginDeveloper": self.developer,
                "authenticationToken": self.token,
            })
            if d.get("authenticated"):
                return True
        # 2) 没有就申请一个（VTS 弹窗，用户点 Allow）
        d = self._call("AuthenticationTokenRequest", {
            "pluginName": self.plugin_name,
            "pluginDeveloper": self.developer,
        })
        tok = str(d.get("authenticationToken") or "")
        if not tok:
            self.log("· 直播后端：VTS 没给 token（用户没点 Allow？）")
            return False
        self.token = tok
        d2 = self._call("AuthenticationRequest", {
            "pluginName": self.plugin_name,
            "pluginDeveloper": self.developer,
            "authenticationToken": tok,
        })
        return bool(d2.get("authenticated"))

    def _probe_params(self) -> None:
        """探测模型里到底有哪些参数。

        参数名不能写死：VTS 侧叫 MouthOpen，模型侧叫 ParamMouthOpenY。
        wallie-V2 就是在这里踩了坑（README 排障里让用户手动改名字）。
        """
        d = self._call("InputParameterListRequest")
        names = [p.get("name") for p in (d.get("defaultParameters") or [])
                 if isinstance(p, dict)]
        if names:
            self.log(f"· 直播后端：模型有 {len(names)} 个参数")
        self.param_names = [n for n in names if n]

    # ---------------------------------------------------------------- 动作
    def set_params(self, params: dict) -> None:
        """写参数。每秒最多推一次，符合 VTS 的要求。"""
        if not self.available():
            return
        self._params.update(params)
        now = time.time()
        if now - self._last_push < 1.0:
            return
        self._last_push = now
        vals = [{"id": k, "value": float(v)} for k, v in self._params.items()]
        self._call("InjectParameterDataRequest", {
            "faceFound": False, "mode": "set", "parameterValues": vals,
        })

    def trigger(self, hotkey: str) -> None:
        """触发一个热键（表情/动作/换模型）。"""
        if not self.available() or not hotkey:
            return
        self._call("HotkeyTriggerRequest", {"hotkeyID": hotkey})

    def say(self, text: str) -> dict:
        """直播场景下「说话」。

        第一版只把话记下来并触发一个嘴型动作 —— 真正的语音合成 + 口型
        还没接（见 docs/pet-and-live.md）。这里不假装做了。
        """
        if not self.available():
            return {"ok": False, "error": "VTS 没连上"}
        # 一个廉价的「在说话」暗示：嘴巴张一下
        self.set_params({"MouthOpen": 0.6})
        return {"ok": True, "local": True, "via": "vts",
                "note": "只做了嘴型提示，语音与口型同步未接"}


# ------------------------------------------------------------------ 单例
_BACKEND: Backend | None = None


def make_backend(cfg: dict | None = None, *, log=print) -> Backend:
    """按 config 的 live 段造后端。默认 NullBackend。"""
    section = (cfg or {}).get("live") or {}
    if not isinstance(section, dict) or not section.get("enabled"):
        return NullBackend()
    kind = str(section.get("backend") or "vts").lower()
    if kind == "dryrun":
        return DryRunBackend(log=log)
    return VTSBackend(
        url=str(section.get("url") or DEFAULT_VTS_URL),
        token=str(section.get("token") or ""),
        log=log,
    )


def get_backend(cfg: dict | None = None, *, log=print) -> Backend:
    """单例。第一次用时按 config 造。"""
    global _BACKEND
    if _BACKEND is None:
        try:
            if cfg is None:
                from core import config as _c
                cfg = _c.load()
            _BACKEND = make_backend(cfg, log=log)
        except Exception:
            _BACKEND = NullBackend()
    return _BACKEND


def reset_backend() -> None:
    global _BACKEND
    _BACKEND = None


def speak_on_live(scene: str, text: str) -> dict:
    """router 调这个：把话送到直播后端。"""
    be = get_backend()
    if not be.available():
        return {"ok": False, "error": "直播后端没开（config 的 live.enabled）"}
    return be.say(text)
