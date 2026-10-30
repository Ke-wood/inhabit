"""大脑：可替换的说话后端。

- MockBrain：本地占位，不联网。用来验证"决策层的节奏"，台词是模板，不代表真实智力。
- OpenAICompatBrain：任何 OpenAI 兼容端点（DeepSeek / 本地 vLLM / Ollama）。
"""

from __future__ import annotations

import json
import os
import random
import re
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, Sequence


class Brain(Protocol):
    def speak(self, *, system: str, user: str, avoid: Sequence[str] = ()) -> str: ...
    def reflect(self, events: Sequence[str]) -> str: ...


@dataclass
class MockBrain:
    """离线占位大脑。只演示节奏、记忆联动和分段，不追求像人。"""

    rng: random.Random = field(default_factory=lambda: random.Random(3))

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

    def speak(self, *, system: str, user: str, avoid: Sequence[str] = ()) -> str:
        if avoid:
            system += "\n\n你刚说过：" + " / ".join(avoid) + "。别再说一样的话。"
        body = json.dumps({
            "model": self.model,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            **self.extra,
        }).encode()
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions", data=body,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"},
        )
        if self.proxy:
            opener = urllib.request.build_opener(
                urllib.request.ProxyHandler({"http": self.proxy, "https": self.proxy}))
        else:
            opener = urllib.request.build_opener()
        with opener.open(req, timeout=60) as resp:
            data = json.loads(resp.read())
        text = data["choices"][0]["message"]["content"].strip()
        text = re.sub(r"<｜begin▁of▁thinking｜>.*?<｜end▁of▁thinking｜>", "", text, flags=re.S)  # 有些本地模型会带思考块
        return re.sub(r"^[\"“]|[\"”]$", "", text).strip()

    def reflect(self, events: Sequence[str]) -> str:
        joined = "\n".join(f"- {e}" for e in events) or "（今天没什么事）"
        system = ("你在写自己的日记。第一人称，三五句，像人随手记的。"
                  "只写你今天真的看到、听到的事，不要把自己当成别人，不要编造。"
                  "可以冷淡，可以只写一句。不要总结收获，不要励志，不要分点。")
        return self.speak(system=system, user=f"今天发生的事：\n{joined}\n\n写今天的日记。")


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
