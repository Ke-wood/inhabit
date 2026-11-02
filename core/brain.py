"""大脑：可替换的说话后端。

- MockBrain：本地占位，不联网。用来验证"决策层的节奏"，台词是模板，不代表真实智力。
- OpenAICompatBrain：任何 OpenAI 兼容端点（DeepSeek / 本地 vLLM / Ollama）。
"""

from __future__ import annotations

import json
import os
import random
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Protocol, Sequence


class Brain(Protocol):
    def speak(self, *, system: str, user: str, avoid: Sequence[str] = ()) -> str: ...
    def reflect(self, events: Sequence[str]) -> str: ...

    def speak_stream(self, *, system: str, user: str,
                     avoid: Sequence[str] = ()) -> Iterator[str]:
        """流式说话：逐个 yield 增量文本（delta）。

        Protocol 里放「默认实现」不成立（方法体不会被执行），真正的兜底在
        speak_stream_of() —— 用 getattr 探测，老后端就退回整段 yield。
        """
        ...


@dataclass
class MockBrain:
    """离线占位大脑。只演示节奏、记忆联动和分段，不追求像人。"""

    rng: random.Random = field(default_factory=lambda: random.Random(3))
    stream_delay: float = 0.01      # 每个字的间隔（秒）。测试里设 0 就不用等

    def speak(self, *, system: str, user: str, avoid: Sequence[str] = ()) -> str:
        last = _last_chat_line(user)
        mem = "\n".join(l for l in user.splitlines() if l.startswith("- "))

        cands: list[str] = []
        if any(w in last for w in ("累", "烦", "难受", "emo", "崩溃")):
            cands = ["……那先别想这个了", "唉", "要不睡一觉"]
        elif any(w in last for w in ("考试", "面试", "没考上")):
            cands = ["你不是说上次没过吗", "那也没多大事"]
        elif "猫" in last and "猫" in mem:
            cands = ["它现在多重了", "上次那只也挺胖的"]
        elif "?" in last or "？" in last:
            cands = ["啊？", "不知道诶", "你猜"]
        else:
            cands = ["草", "行吧", "在的", "嗯"]

        fresh = [c for c in cands if c not in avoid]
        return self.rng.choice(fresh or cands)

    def speak_stream(self, *, system: str, user: str,
                     avoid: Sequence[str] = ()) -> Iterator[str]:
        """逐字吐台词，模拟流式。

        直接复用 speak() 挑台词，保证「拼起来 == speak() 的输出」——
        测试就是靠这个性质钉住「流式只换切片方式，内容不许变」。
        注意它会消耗 self.rng（和 speak() 一样），所以同一个实例连着调两次
        本来就会拿到不同台词，不是 bug。
        """
        text = self.speak(system=system, user=user, avoid=avoid)
        for ch in text:
            if self.stream_delay:
                time.sleep(self.stream_delay)
            yield ch

    def reflect(self, events: Sequence[str]) -> str:
        if not events:
            return "今天没什么。"
        who, _, what = events[0].partition("说")
        return f"{who or '有人'}说了「{what[:12]}」，我记着。"


class OpenAICompatBrain:
    """OpenAI 兼容 chat/completions。"""

    def __init__(self, *, api_key: str, base_url: str, model: str,
                 temperature: float = 1.15, max_tokens: int = 120, proxy: str | None = None,
                 extra: dict | None = None):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.proxy = proxy
        self.extra = extra or {}

    def _request_body(self, *, system: str, user: str, avoid: Sequence[str],
                      stream: bool) -> bytes:
        """拼请求体。speak() 和 speak_stream() 共用，保证参数逐字一致 ——
        否则流式和非流式生成的东西会不一样，排查起来很痛苦。"""
        if avoid:
            system += "\n\n你刚说过：" + " / ".join(avoid) + "。别再说一样的话。"
        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            **self.extra,
        }
        if stream:
            payload["stream"] = True
        return json.dumps(payload).encode()

    def _open(self, req: urllib.request.Request):
        if self.proxy:
            opener = urllib.request.build_opener(
                urllib.request.ProxyHandler({"http": self.proxy, "https": self.proxy}))
        else:
            opener = urllib.request.build_opener()
        return opener.open(req, timeout=60)

    def speak(self, *, system: str, user: str, avoid: Sequence[str] = ()) -> str:
        body = self._request_body(system=system, user=user, avoid=avoid, stream=False)
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions", data=body,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"},
        )
        with self._open(req) as resp:
            data = json.loads(resp.read())
        text = data["choices"][0]["message"]["content"].strip()
        text = re.sub(r"<｜begin▁of▁thinking｜>.*?<｜end▁of▁thinking｜>", "", text, flags=re.S)  # 有些本地模型会带思考块
        return re.sub(r"^[\"“]|[\"”]$", "", text).strip()

    def speak_stream(self, *, system: str, user: str,
                     avoid: Sequence[str] = ()) -> Iterator[str]:
        """真正的 SSE 流式：边收边 yield 增量，不必等整段生成完。

        为什么不在失败时自动退回 speak()：调用方是「边收边念」的，
        先吐了一半再整段重来，会让人听见两句一样的话。所以这里
        只做「优雅地断」—— 把异常抛给调用方，由它决定要不要重试
        （见 speak_stream_of 的 fallback 参数）。
        """
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=self._request_body(system=system, user=user, avoid=avoid, stream=True),
            headers={"Content-Type": "application/json",
                     "Accept": "text/event-stream",
                     "Authorization": f"Bearer {self.api_key}"},
        )
        # 增量文本要过两道闸：thinking 块跨 chunk 的过滤、首尾引号的清理
        guard = _DeltaFilter()
        try:
            with self._open(req) as resp:
                for raw_line in resp:
                    for piece in _parse_sse_line(raw_line):
                        for delta in guard.feed(piece):
                            if delta:
                                yield delta
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
            # 断流 / 超时不往外炸：这一路已经吐出去的东西就留在那儿，
            # 让主循环继续跑，别为了半句话把整个进程带崩。
            guard.error = f"{type(e).__name__}: {e}"
        except GeneratorExit:
            # 调用方提前 break / 被 GC 回收。这里必须立刻收手：
            # 在 finally 里 yield 会触发 RuntimeError("generator ignored
            # GeneratorExit")，比断流更糟 —— 直接让主循环崩掉。
            return
        for delta in guard.close():        # 正常结束后再把尾巴交出去
            if delta:
                yield delta

    def reflect(self, events: Sequence[str]) -> str:
        joined = "\n".join(f"- {e}" for e in events) or "（今天没什么事）"
        system = ("你在写自己的日记。第一人称，三五句，像人随手记的。"
                  "只写你今天真的看到、听到的事，不要把自己当成别人，不要编造。"
                  "可以冷淡，可以只写一句。不要总结收获，不要励志，不要分点。")
        return self.speak(system=system, user=f"今天发生的事：\n{joined}\n\n写今天的日记。")


_THINK_PREFIX = "<｜begin▁of▁thinking｜>"
_THINK_SUFFIX = "<｜end▁of▁thinking｜>"

# speak() 里那条 re.sub(r"^[\"“]|[\"”]$") 拆开写：开头剥一层，结尾剥一层。
_LEADING_QUOTES = "\"“"
_TRAILING_QUOTES = "\"”"


class _DeltaFilter:
    """把原始 delta 流整成「能直接拿去念」的文本。

    干三件事，都是为了对齐 speak() 的最终行为 —— 流式和非流式吐出来的
    话必须一模一样，否则调用方会因为「我换了个接口」而听到不同的内容：

    1. **thinking 块过滤**：有些本地模型会在正文里夹
       <|begin▁of▁thinking|>…<|end▁of▁thinking|>。speak() 是拿到整段后
       一把正则删掉，流式下块会被 token 边界劈开（开标签可能横跨两个 chunk），
       所以这里必须**跨 chunk 累积**：用状态机判断当前是在「正文」还是
       「思考块里」，而不是对每个 delta 单独跑正则。
    2. **首部引号**：speak() 会去掉开头一层引号。流式下先扣住，等第一个
       真字符到了再决定要不要吐。
    3. **尾部引号**：结尾那个引号得等流结束才知道是不是最后一个字符，
       所以**永远留一个字符不发**，close() 时再决定它是不是要被剥掉。
    """

    def __init__(self) -> None:
        self.thinking = False     # 是否正处在 thinking 块里
        self.started = False      # 首部引号处理完没
        self.pending = ""         # 可能是半个 thinking 标签，先攒着
        self.hold = ""            # 尾部扣住的一个字符
        self.done = False
        self.error: str | None = None   # 断流时记一笔，方便排查

    def feed(self, delta: str) -> list[str]:
        if self.done or not delta:
            return []
        out: list[str] = []
        buf = self.pending + delta
        self.pending = ""
        i = 0
        n = len(buf)
        while i < n:
            if not self.thinking:
                k = buf.find(_THINK_PREFIX, i)
                if k == -1:
                    # 没有开标签。但缓冲区末尾可能是半个开标签（被 chunk 劈开了），
                    # 先扣住，等下一块拼上再说，否则标签会漏进正文被念出来。
                    keep = _partial_prefix_len(buf[i:], _THINK_PREFIX)
                    body = buf[i:n - keep] if keep else buf[i:]
                    out.extend(self._emit(body))
                    self.pending = buf[n - keep:] if keep else ""
                    i = n
                else:
                    out.extend(self._emit(buf[i:k]))    # 标签之前的是正文
                    self.thinking = True
                    i = k + len(_THINK_PREFIX)
                continue
            # thinking 块里：只找结束标签，块里的内容一个字都不吐
            k = buf.find(_THINK_SUFFIX, i)
            if k == -1:
                keep = _partial_prefix_len(buf[i:], _THINK_SUFFIX)
                self.pending = buf[n - keep:] if keep else ""
                i = n
            else:
                self.thinking = False
                i = k + len(_THINK_SUFFIX)
        return out

    def close(self) -> list[str]:
        """流结束：把攒着的尾巴交出来。"""
        if self.done:
            return []
        self.done = True
        out: list[str] = []
        if self.pending:
            rest, self.pending = self.pending, ""
            if not self.thinking:
                out.extend(self._emit(rest))
        if self.hold:
            held, self.hold = self.hold, ""
            if held not in _TRAILING_QUOTES:      # 收尾只剥一层
                out.append(held)
        return [p for p in out if p]

    # ------------------------------------------------------------------ 内部

    def _emit(self, text: str) -> list[str]:
        """正文进管道：处理首部引号，并扣住最后一个字符等流结束。"""
        if not text:
            return []
        out: list[str] = []
        if not self.started:
            stripped = text.lstrip()
            if not stripped:
                return []          # 只有空白，还没到第一个真字符
            if stripped[0] in _LEADING_QUOTES:
                stripped = stripped[1:]
            if not stripped:
                return []          # 整块就是个引号，等后面的
            self.started = True
            text = stripped
        full = self.hold + text
        self.hold = full[-1]
        body = full[:-1]
        if body:
            out.append(body)
        return out


def _partial_prefix_len(buf: str, token: str) -> int:
    """buf 末尾是不是 token 的前缀？返回要扣住的字符数（0 表示不用扣）。"""
    for k in range(min(len(buf), len(token) - 1), 0, -1):
        if token.startswith(buf[len(buf) - k:]):
            return k
    return 0


def _parse_sse_line(raw_line: bytes) -> list[str]:
    """解析一行 SSE，返回这一行里带的所有增量文本。

    OpenAI 兼容端点的流长这样（每行一条记录，空行分隔）：

        data: {"choices":[{"delta":{"content":"你"}}]}
        data: {"choices":[{"delta":{"content":"好"}}]}
        data: [DONE]

    要处理的边界：
    - `data: [DONE]` —— 结束标记，不是 JSON
    - `delta` 里没有 content（只有 role、或者只有 reasoning_content）—— 跳过，别 KeyError
    - `choices` 是空列表（有些端点用它发心跳 / usage 统计）—— 跳过
    - `: keep-alive` 注释行、空行、`event:` 之类 —— 跳过
    - 行前缀带 `data:` 但没空格 —— 也认
    - 编码坏了 / 不是 JSON —— 跳过这一行，不要为一行烂数据弄断整条流
    """
    try:
        line = raw_line.decode("utf-8", errors="replace").strip()
    except Exception:                      # noqa: BLE001  解码都不行就丢这一行
        return []
    if not line or not line.startswith("data:"):
        return []
    payload = line[5:].strip()
    if not payload or payload == "[DONE]":
        return []
    try:
        obj = json.loads(payload)
    except json.JSONDecodeError:
        return []                          # 半行 / 心跳 / 非 JSON 的杂项
    if not isinstance(obj, dict):
        return []
    choices = obj.get("choices") or []
    if not isinstance(choices, list):
        return []
    out: list[str] = []
    for choice in choices:
        if not isinstance(choice, dict):
            continue
        delta = choice.get("delta")
        if not isinstance(delta, dict):
            # 少数端点流到一半会突然回一个完整 message 对象，
            # 这时把 content 当增量收下，总比丢字好
            delta = choice.get("message") if isinstance(choice.get("message"), dict) else None
            if not delta:
                continue
        content = delta.get("content")
        if isinstance(content, str) and content:
            out.append(content)
    return out


def speak_stream_of(brain: Brain, *, system: str, user: str,
                    avoid: Sequence[str] = (), fallback: bool = False) -> Iterator[str]:
    """拿一个既有大脑，流式说话。给「不一定支持流式」的后端兜底。

    为什么需要这层：Brain 是 Protocol，第三方 / 测试 / 老代码可能只实现了
    speak()。直接调 brain.speak_stream() 会 AttributeError，所以这里用
    getattr 探测，没有就退回「先整段生成，再一次性 yield」。
    行为上仍然满足「碎片 vs 整段」的契约，只是没有低延迟收益 ——
    这比抛异常让主循环崩掉好得多。

    fallback=True 时，若流式实现中途抛异常，改成整段重生成一次。
    注意：只有在还没吐出去任何字节时才安全（否则会重复出声），
    所以这里自己记账。
    """
    fn = getattr(brain, "speak_stream", None)
    if not callable(fn):
        yield brain.speak(system=system, user=user, avoid=avoid)
        return
    emitted = False
    try:
        for delta in fn(system=system, user=user, avoid=avoid):
            if delta:
                emitted = True
                yield delta
    except Exception:                      # noqa: BLE001
        if not (fallback and not emitted):
            return
        yield brain.speak(system=system, user=user, avoid=avoid)


def _last_chat_line(user: str) -> str:
    """从 user prompt 里取"刚才群里在说"的最后一条。"""
    m = re.search(r"#\s*刚才群里在说\s*\n(.*?)(?:\n\n|\Z)", user, re.S)
    block = m.group(1) if m else user
    lines = [l.strip() for l in block.splitlines() if l.strip()]
    return re.sub(r"^[^：:]{1,12}[：:]\s*", "", lines[-1]) if lines else ""


def make_brain(kind: str = "auto") -> Brain:
    """按名字造一个大脑。

    auto  —— config.api 填了就优先用；否则读 DEEPSEEK_API_KEY / OPENAI_API_KEY
    mock  —— 不联网，用固定台词，跑 demo / 看流程用
    local —— 本地 OpenAI 兼容端点（Ollama / LM Studio），不需要 key
    """
    if kind == "mock":
        return MockBrain()

    # 面板里填了 API，就用填的那个（比 --brain 优先级高）
    try:
        from core import config as _cfg
        api = (_cfg.load().get("api") or {})
        if api.get("base_url") and api.get("api_key"):
            return OpenAICompatBrain(
                api_key=api["api_key"],
                base_url=api["base_url"],
                model=api.get("model") or "deepseek-chat",
                max_tokens=700,
                proxy=None,
                extra={"reasoning_effort": "none"},
            )
    except Exception:
        pass

    if kind == "local":
        # Ollama / LM Studio 等本地 OpenAI 兼容端点，不需要 key
        return OpenAICompatBrain(
            api_key=os.environ.get("LOCAL_API_KEY", "local"),
            base_url=os.environ.get("LOCAL_BASE_URL", "http://127.0.0.1:11434/v1"),
            model=os.environ.get("LOCAL_MODEL", "qwen3:8b"),
            proxy=None,
        )
    key = os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not key:
        raise SystemExit("没有 API key：export DEEPSEEK_API_KEY=... 或继续用 --brain mock")
    return OpenAICompatBrain(
        api_key=key,
        base_url=os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"),
        model=os.environ.get("DEEPSEEK_MODEL", "deepseek-chat"),
        proxy=os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy"),
    )


def split_segments(text: str, *, max_chars: int = 1000, max_segments: int = 3) -> list[str]:
    """像人一样分条发：只在标点处断，不按字数硬切。

    max_chars 只是给极端长句兜底，正常一句话不会被动刀。
    """
    text = (text or "").strip()
    if not text:
        return []
    parts = [p for p in re.split(r"(?<=[。！？?!~])|\n+", text) if p.strip()]
    out: list[str] = []
    for p in parts:
        p = p.strip()
        if re.fullmatch(r"[。！？?!~…\s]+", p):   # 只有标点，不单独发
            continue
        # 微信里没人拿句号收尾 —— 句子说完就停。保留 ！？… 这些有情绪的。
        p = re.sub(r"[\u3002\s]+$", "", p).strip()
        if not p:
            continue
        while len(p) > max_chars:                 # 只兜极端长句，正常不触发
            out.append(p[:max_chars])
            p = p[max_chars:]
        if not p:
            continue
        if out and len(out[-1]) <= 1:             # 太短的碎片并到上一句
            out[-1] = out[-1] + p
        else:
            out.append(p)
    if len(out) > max_segments:                   # 段数超了合并尾巴，不丢内容
        out = out[:max_segments - 1] + ["".join(out[max_segments - 1:])]
    return out
