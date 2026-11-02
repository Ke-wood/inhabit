"""流式输出测试：不联网，全部用 Mock / 假 SSE。

    python tests/test_stream.py

覆盖三块：

1. MockBrain.speak_stream() —— 逐块产出，拼起来必须等于 speak()
2. SentenceStreamer —— 中文切分的每一条规则，逐字喂 vs 整段喂必须一致
3. SSE 解析 + thinking 块跨 chunk 过滤 + 断流不崩
"""

from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

ok = 0
fail = 0


def check(name: str, fn) -> None:
    global ok, fail
    try:
        fn()
        print(f"  ok   {name}")
        ok += 1
    except Exception as e:                    # noqa: BLE001
        print(f"  FAIL {name}: {type(e).__name__}: {e}")
        fail += 1


def eq(got, want, what: str = "") -> None:
    assert got == want, f"{what} 期望 {want!r}，实得 {got!r}"


# --------------------------------------------------------------------- 1 流式


def test_mock_stream_chunks() -> None:
    """逐块产出，拼起来等于 speak()。"""
    from core.brain import MockBrain

    prompt = {"system": "你在群里", "user": "刚才群里在说\n甲：在吗"}
    whole = MockBrain(stream_delay=0).speak(**prompt)
    parts = list(MockBrain(stream_delay=0).speak_stream(**prompt))

    assert parts, "流式一个字都没吐"
    assert len(parts) > 1, "只吐了一块，那不是流式"
    eq("".join(parts), whole, "拼起来")


def test_mock_stream_all_blank() -> None:
    """不同输入都成立，不只是碰巧命中的那一条。"""
    from core.brain import MockBrain

    for user in ("在吗", "我今天好累", "考试没过", "？", "我家猫呢"):
        whole = MockBrain(stream_delay=0).speak(system="s", user=user)
        parts = list(MockBrain(stream_delay=0).speak_stream(system="s", user=user))
        eq("".join(parts), whole, f"「{user}」")


def test_stream_no_attr_fallback() -> None:
    """老后端只实现了 speak()，speak_stream_of 要兜住，不能 AttributeError。"""
    from core.brain import speak_stream_of

    class OldBrain:
        def speak(self, *, system: str, user: str, avoid=()) -> str:
            return "老后端也能说话"

    got = list(speak_stream_of(OldBrain(), system="s", user="u"))
    eq(got, ["老后端也能说话"], "兜底")


def test_stream_midway_break() -> None:
    """生成器提前退出（调用方 break）不许抛异常。"""
    from core.brain import MockBrain

    gen = MockBrain(stream_delay=0).speak_stream(system="s", user="在吗")
    first = next(gen)
    assert first
    gen.close()                     # 这就是「流式中断」


def test_stream_survives_broken_backend() -> None:
    """流式实现中途炸了：speak_stream_of 不外抛，主循环不能崩。"""
    from core.brain import speak_stream_of

    class BrokenBrain:
        def speak(self, *, system: str, user: str, avoid=()) -> str:
            return "不该走到这"

        def speak_stream(self, *, system: str, user: str, avoid=()):
            yield "说一半"
            raise OSError("连接被掐了")

    got = list(speak_stream_of(BrokenBrain(), system="s", user="u"))
    eq(got, ["说一半"], "已经吐出去的要留住，后面不追加")


# ------------------------------------------------------------------ 2 句子切分


def collect(text: str, *, per_char: bool = True) -> list[str]:
    from core.sentencer import SentenceStreamer

    s = SentenceStreamer()
    out: list[str] = []
    if per_char:
        for ch in text:
            out.extend(s.feed(ch))
    else:
        out.extend(s.feed(text))
    out.extend(s.flush())
    return out


def test_cut_two_sentences() -> None:
    eq(collect("你好。今天天气不错。"), ["你好。", "今天天气不错。"], "两句")


def test_comma_does_not_cut() -> None:
    eq(collect("你好，今天天气不错。"), ["你好，今天天气不错。"], "逗号不切")


def test_short() -> None:
    eq(collect("嗯。"), ["嗯。"], "一个字加句号")


def test_long_no_punct_fallback() -> None:
    """超长无标点：软阈值（40 字）后在最近的逗号切，硬阈值（60 字）强切。"""
    from core.sentencer import SentenceStreamer

    s = SentenceStreamer()
    long_text = "这个句子非常长" * 20          # 140 字，一个标点都没有
    pieces = s.feed(long_text) + s.flush()
    assert len(pieces) >= 2, f"超长句没被切开：{pieces}"
    eq("".join(pieces), long_text, "切开但不能丢字")
    for p in pieces:
        assert len(p) <= 60, f"超过硬阈值 60 还没切：{len(p)} 字"

    # 有逗号时，应该在逗号处软切，而不是硬切
    s2 = SentenceStreamer()
    with_comma = "一二三四五六七八九十" * 5 + "，" + "甲乙丙丁戊己庚辛" * 5
    got2 = s2.feed(with_comma) + s2.flush()
    assert got2[0].endswith("，"), f"软切应切在逗号后：{got2[0]!r}"


def test_granularity_invariance() -> None:
    """逐字喂和一次性喂，结果必须完全一样（这是流式正确性的核心性质）。"""
    cases = [
        "你好。今天天气不错。",
        "你好，今天天气不错。",
        "嗯。",
        "他说：「明天见。」然后走了。",
        "真的吗？！我不信……算了。",
        "第一行\n第二行\n第三行",
    ]
    for text in cases:
        eq(collect(text, per_char=True), collect(text, per_char=False), f"粒度「{text[:12]}」")


def test_granularity_random_chunks() -> None:
    """随机分块 vs 逐字 vs 整段，三者必须一致（流式最容易埋雷的地方）。"""
    import random

    from core.sentencer import SentenceStreamer

    text = ("今天挺热的，你那边开空调没。我下午去了趟超市，人特别多，"
            "排队排了二十分钟才出来！顺便买了点水果……你要不要。"
            "对了那个事我还没想好，等我再看看吧。") * 3

    def run(chunks) -> list[str]:
        s = SentenceStreamer()
        out: list[str] = []
        for c in chunks:
            out.extend(s.feed(c))
        out.extend(s.flush())
        return out

    base = run([text])
    assert base, "整段喂居然没切出句子"
    eq("".join(base), text.strip(), "不丢字")

    rng = random.Random(7)
    for _ in range(30):
        i, chunks = 0, []
        while i < len(text):
            k = rng.randint(1, 9)
            chunks.append(text[i:i + k])
            i += k
        eq(run(chunks), base, "随机分块")


def test_flush_rest() -> None:
    """没有句末标点的半句，只能靠 flush() 交出来。"""
    from core.sentencer import SentenceStreamer

    s = SentenceStreamer()
    eq(s.feed("话说了一半"), [], "没到边界不该切")
    eq(s.flush(), ["话说了一半"], "flush 吐出剩余")
    eq(s.flush(), [], "重复 flush 不重复吐")


def test_quote_closes() -> None:
    """引号收尾要跟同一句，不能被切到下一句去。"""
    got = collect("他说「明天见。」然后走了。")
    eq(got, ["他说「明天见。」", "然后走了。"], "引号")


def test_ellipsis() -> None:
    eq(collect("真的吗？！我不信……算了。"), ["真的吗？！", "我不信……", "算了。"], "省略号")


def test_newline_cuts() -> None:
    eq(collect("第一行\n第二行\n"), ["第一行", "第二行"], "换行")


def test_pure_punct_dropped() -> None:
    """纯标点不值得送 TTS，但开场的省略号要留住语气。"""
    from core.sentencer import SentenceStreamer

    s = SentenceStreamer()
    got = s.feed("。。。") + s.feed("嗯。") + s.flush()
    eq(got, ["嗯。"], "纯标点")

    s2 = SentenceStreamer()
    got2 = s2.feed("……") + s2.feed("算了。") + s2.flush()
    eq(got2, ["……算了。"], "开场省略号")


def test_no_split_inside_number() -> None:
    """硬切不许切在数字/英文词中间。"""
    from core.sentencer import SentenceStreamer

    s = SentenceStreamer()
    text = "版本号是v1.4.3" * 20
    pieces = s.feed(text) + s.flush()
    eq("".join(pieces), text, "不丢字")
    for i, p in enumerate(pieces[:-1]):
        assert not (p[-1].isalnum() and pieces[i + 1][0].isalnum()
                    and p[-1].isascii() and pieces[i + 1][0].isascii()), \
            f"切在英文/数字中间了：{p[-6:]!r} | {pieces[i + 1][:6]!r}"


# --------------------------------------------------------------------- 3 SSE


def test_sse_parse_basic() -> None:
    from core.brain import _parse_sse_line

    eq(_parse_sse_line(b'data: {"choices":[{"delta":{"content":"\xe4\xbd\xa0"}}]}\n'),
       ["你"], "普通增量")
    eq(_parse_sse_line(b"data: [DONE]\n"), [], "[DONE]")
    eq(_parse_sse_line(b"\n"), [], "空行")
    eq(_parse_sse_line(b": keep-alive\n"), [], "注释行")
    eq(_parse_sse_line(b'data: {"choices":[]}\n'), [], "空 choices")
    eq(_parse_sse_line(b'data: {"choices":[{"delta":{"role":"assistant"}}]}\n'),
       [], "delta 没有 content")
    eq(_parse_sse_line(b'data: {"choices":[{"delta":{}}]}\n'), [], "空 delta")
    eq(_parse_sse_line(b"data: {broken\n"), [], "半行 JSON")
    eq(_parse_sse_line(b'not-data {"a":1}\n'), [], "非 data 行")
    eq(_parse_sse_line(b"data:[DONE]"), [], "没空格的 data:")


def test_thinking_cross_chunk() -> None:
    """thinking 块被 chunk 劈开时不许漏字，也不许把思考内容念出来。"""
    from core.brain import _THINK_PREFIX, _THINK_SUFFIX, _DeltaFilter

    pieces = ["你", "好。", _THINK_PREFIX[:5], _THINK_PREFIX[5:] + "我在想来想去",
              "想完了" + _THINK_SUFFIX, "再见"]
    f = _DeltaFilter()
    got: list[str] = []
    for p in pieces:
        got.extend(f.feed(p))
    got.extend(f.close())
    eq("".join(got), "你好。再见", "跨 chunk 过滤")


def test_thinking_single_chunk() -> None:
    from core.brain import _THINK_PREFIX, _THINK_SUFFIX, _DeltaFilter

    f = _DeltaFilter()
    got = f.feed(f"前{_THINK_PREFIX}思考{_THINK_SUFFIX}后") + f.close()
    eq("".join(got), "前后", "同块内过滤")


def test_quote_strip_matches_speak() -> None:
    """流式剥引号的结果要和 speak() 的正则一致。"""
    from core.brain import _DeltaFilter

    f = _DeltaFilter()
    got = []
    for ch in '"你好。"':
        got.extend(f.feed(ch))
    got.extend(f.close())
    eq("".join(got), "你好。", "首尾引号")


def test_sse_generator_early_exit() -> None:
    """调用方提前 break 时，SSE 生成器不能抛 GeneratorExit 相关的 RuntimeError。

    这是真机踩到的坑：曾经把「吐尾巴」放在 finally 里 yield，于是
    g.close() 会 RuntimeError: generator ignored GeneratorExit，
    比断流更糟 —— 直接把主循环带崩。
    """
    import gc

    from core.brain import OpenAICompatBrain

    class Resp:
        def __init__(self):
            self.lines = [b'data: {"choices":[{"delta":{"content":"\xe4\xbd\xa0"}}]}\n',
                          b'data: {"choices":[{"delta":{"content":"\xe5\xa5\xbd"}}]}\n']

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def __iter__(self):
            return iter(self.lines)

    def make():
        b = OpenAICompatBrain(api_key="k", base_url="http://x/v1", model="m")
        b._open = lambda req: Resp()
        return b

    g = make().speak_stream(system="s", user="u")
    assert next(g)
    g.close()                       # 不许抛

    g2 = make().speak_stream(system="s", user="u")
    next(g2)
    del g2
    gc.collect()                    # 被回收时也不许抛

    eq("".join(make().speak_stream(system="s", user="u")), "你好", "正常走完仍然完整")


def test_fake_sse_end_to_end() -> None:
    """用假响应把 OpenAICompatBrain.speak_stream 整条路走一遍，不联网。"""
    import json as _json

    from core.brain import OpenAICompatBrain

    class FakeResp:
        def __init__(self, lines):
            self._lines = list(lines)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def __iter__(self):
            return iter(self._lines)

    def make(lines):
        b = OpenAICompatBrain(api_key="k", base_url="http://x/v1", model="m")
        b._open = lambda req: FakeResp(lines)      # 换掉网络层
        return b

    lines = [
        b'data: {"choices":[{"delta":{"content":"\xe4\xbd\xa0"}}]}\n',
        b"\n",
        b'data: {"choices":[{"delta":{"content":"\xe5\xa5\xbd"}}]}\n',
        b'data: {"choices":[{"delta":{}}]}\n',          # 空 delta
        b"data: [DONE]\n",
    ]
    got = "".join(make(lines).speak_stream(system="s", user="u"))
    eq(got, "你好", "端到端")

    # 断流：迭代到一半抛 OS Error，不能把异常冒出去
    class Exploding(FakeResp):
        def __iter__(self):
            yield b'data: {"choices":[{"delta":{"content":"\xe4\xbd\xa0"}}]}\n'
            raise OSError("连接被掐了")

    b = OpenAICompatBrain(api_key="k", base_url="http://x/v1", model="m")
    b._open = lambda req: Exploding([])
    got2 = "".join(b.speak_stream(system="s", user="u"))
    eq(got2, "你", "断流保留已收到的")


def test_stream_body_has_stream_true() -> None:
    """请求体必须带 stream: true，且 speak() 的请求体不带。"""
    import json as _json

    from core.brain import OpenAICompatBrain

    b = OpenAICompatBrain(api_key="k", base_url="http://x/v1", model="m")
    a = _json.loads(b._request_body(system="s", user="u", avoid=(), stream=True))
    c = _json.loads(b._request_body(system="s", user="u", avoid=(), stream=False))
    eq(a.get("stream"), True, "流式")
    assert "stream" not in c, "非流式不该带 stream"
    eq(a["messages"], c["messages"], "消息体一致")


def test_speak_unchanged() -> None:
    """speak() 的行为不许被流式改动波及（同一请求体、同样的返回值）。"""
    import json as _json

    from core.brain import OpenAICompatBrain

    class FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return _json.dumps({"choices": [{"message": {"content": "  你好。  "}}]}).encode()

    b = OpenAICompatBrain(api_key="k", base_url="http://x/v1", model="m")
    b._open = lambda req: FakeResp()
    eq(b.speak(system="s", user="u"), "你好。", "speak 仍然去空白")


def main() -> int:
    print("流式输出 / 句子切分")
    check("MockBrain 流式逐块，拼起来等于 speak", test_mock_stream_chunks)
    check("多组输入都成立", test_mock_stream_all_blank)
    check("老后端没有 speak_stream 时的兜底", test_stream_no_attr_fallback)
    check("流式中断不崩", test_stream_midway_break)
    check("后端中途炸了不崩、已吐出的留住", test_stream_survives_broken_backend)

    check("中文两句切分", test_cut_two_sentences)
    check("逗号不切", test_comma_does_not_cut)
    check("短句「嗯。」", test_short)
    check("超长无标点按兜底切", test_long_no_punct_fallback)
    check("逐字喂 == 整段喂", test_granularity_invariance)
    check("随机分块 == 整段喂", test_granularity_random_chunks)
    check("flush 吐出剩余半句", test_flush_rest)
    check("引号收尾跟同一句", test_quote_closes)
    check("省略号切分", test_ellipsis)
    check("换行切分", test_newline_cuts)
    check("纯标点丢弃 / 开场省略号保留", test_pure_punct_dropped)
    check("不切在数字英文中间", test_no_split_inside_number)

    check("SSE 行解析边界", test_sse_parse_basic)
    check("thinking 块跨 chunk 过滤", test_thinking_cross_chunk)
    check("thinking 块同 chunk 过滤", test_thinking_single_chunk)
    check("首尾引号与 speak 对齐", test_quote_strip_matches_speak)
    check("假 SSE 端到端 + 断流", test_fake_sse_end_to_end)
    check("SSE 生成器提前退出不崩", test_sse_generator_early_exit)
    check("请求体带 stream:true", test_stream_body_has_stream_true)
    check("speak() 行为不变", test_speak_unchanged)

    print(f"\n{ok} 通过 / {fail} 失败")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
