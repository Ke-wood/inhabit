"""真机验证：用本机 OpenAI 兼容网关跑一次真实 SSE，对比首字节 / 整段延迟。

    python tools/_live_stream_check.py

不是单元测试（依赖本机网关），放 tools/ 下当手动检查脚本。
"""

from __future__ import annotations

import pathlib
import os
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.brain import OpenAICompatBrain
from core.sentencer import SentenceStreamer


def main() -> int:
    key_path = pathlib.Path(os.path.expanduser("~/.dsh/openai-gateway/api-key"))
    if not key_path.exists():
        print("没有 api-key，跳过")
        return 2
    key = key_path.read_text().strip()

    brain = OpenAICompatBrain(
        api_key=key,
        base_url="http://127.0.0.1:8326/v1",
        model=os.environ.get("STREAM_MODEL", "deepseek-account/deepseek-flash"),
        # 这个模型会先吐一堆 reasoning_content 再吐正文，预算给小了正文就没了
        max_tokens=400,
    )
    system = "你在跟熟人聊天。中文，一句话，短，像随手打的，不要句号。"
    user = "刚才群里在说：甲：今天好累啊。你说一句。"

    # --- 非流式：整段延迟
    t0 = time.perf_counter()
    whole = brain.speak(system=system, user=user, avoid=())
    t_whole = time.perf_counter() - t0
    print(f"[非流式] 整段耗时 {t_whole*1000:.0f} ms → {whole!r}")

    # --- 流式：首字节 / 首句 / 整段
    t0 = time.perf_counter()
    first_delta_at = None
    first_sentence_at = None
    chunks: list[str] = []
    sentences: list[str] = []
    s = SentenceStreamer()
    for delta in brain.speak_stream(system=system, user=user, avoid=()):
        if first_delta_at is None:
            first_delta_at = time.perf_counter() - t0
        chunks.append(delta)
        for sent in s.feed(delta):
            if first_sentence_at is None:
                first_sentence_at = time.perf_counter() - t0
            sentences.append(sent)
    for sent in s.flush():
        if first_sentence_at is None:
            first_sentence_at = time.perf_counter() - t0
        sentences.append(sent)
    t_stream = time.perf_counter() - t0

    joined = "".join(chunks)
    fd = (first_delta_at or 0) * 1000
    fs = (first_sentence_at or 0) * 1000
    print(f"[流式]   首字 {fd:.0f} ms / 首句 {fs:.0f} ms / 整段 {t_stream*1000:.0f} ms")
    print(f"         chunk 数 {len(chunks)}，{joined!r}")
    print(f"         切句 {sentences}")
    if chunks:
        saved = t_whole * 1000 - fd
        print(f"[对比]   首字相对整段省 {saved:.0f} ms（{(1-fd/(t_whole*1000))*100:.0f}%）")
    else:
        print("[对比]   这个模型只吐了 reasoning_content，正文没进内容流，"
              "首字延迟无从比较（解析器行为正确：推理内容不该念出来）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
