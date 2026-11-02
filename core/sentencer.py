"""句子切分：把 token 流切成「可以立刻拿去合成语音的句子」。

为什么需要它：TTS 要整句，LLM 给的是碎片。

- 切太早 → 语音断断续续，「你好」被拆成三段，听起来像卡带
- 切太晚 → 等整段生成完才开口，流式就白做了

所以这里的取舍是：只在「人自然会换气」的地方切。
中文里那就是句号 / 问号 / 叹号 / 分号 / 省略号 / 换行。
逗号、顿号**不切** —— 它们只隔开短句，切了语音会很碎，
听起来像一句一喘，比整段等完还难受。

用法：

    s = SentenceStreamer()
    for delta in brain.speak_stream(...):
        for sentence in s.feed(delta):
            tts(sentence)              # 边收边念
    for sentence in s.flush():         # 流结束，吐出最后的半句
        tts(sentence)

注意 flush() 是必须调的：为了不把「。」后面紧跟着的收尾引号切到下一句，
切分器会等到看见下一个字符才落刀，所以最后一句往往留在缓冲里。
"""

from __future__ import annotations

import re

# 句末标点。半角 ! ? ; 也认：模型偶尔会打半角，漏切一句就是好几秒的延迟。
_SENTENCE_END = "。！？；…!?;"

# 换行也是句子边界：模型写「嗯\n好」的时候本来就是在断句。
_NEWLINES = "\r\n"

# 句末标点后面跟着的收尾符号，要一起归到这一句。
# 不吞的话，一句引用的话会被切成「他说好。」+「」」两个碎片。
_CLOSERS = "」』”’）】》〉\"')]"

# 长度兜底阈值（按「字」算，中文一个字占一个字符位置）。
# 40 / 60 是听感上的经验值：40 字差不多是一口气能念完的长度，
# 60 字再等下去，人已经能感觉到「它卡住了」。
_DEFAULT_SOFT_LIMIT = 40
_DEFAULT_HARD_LIMIT = 60

# 软切（找逗号）时，逗号太靠前就不算数 —— 否则会切出「嗯，」这种一片。
_MIN_SOFT_PIECE = 6

# 兜底闸门：再怎么找不到能切的地方，也不能让缓冲无限涨
# （模型抽风一直吐逗号或者干脆不吐标点时，这里保命）。
_HARD_CAP = 160

# 判断「这个位置切开会不会切在英文词/数字中间」用。
_ASCII_TOKEN = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._%+-/:@")

# 有实际内容才算一句（纯标点不值得送 TTS）。
_WORD_RE = re.compile(r"[0-9A-Za-z\u3400-\u4dbf\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]")


class SentenceStreamer:
    """把增量文本切成「可以拿去合成语音的句子」。

    feed() 可以调很多次（每来一个 delta 调一次），返回这一次能确定下来的
    完整句子，可能是 0 句（还没到边界，或者只差一个字符）。
    flush() 在流结束时调一次，吐出剩下的半句。

    一个重要的性质：**结果与喂入的粒度无关**。
    逐字喂和一次性把整段喂进去，切出来的句子序列完全一样 ——
    因为每刀都只依赖「已经看到的内容」，不依赖「这是第几块」。
    """

    def __init__(self, *, soft_limit: int = _DEFAULT_SOFT_LIMIT,
                 hard_limit: int = _DEFAULT_HARD_LIMIT) -> None:
        self.soft_limit = soft_limit
        self.hard_limit = hard_limit
        self._buf = ""
        self._glue = ""       # 句首的「……」这种停顿，粘到下一句开头，别丢
        self._emitted = 0     # 已经吐出去几句（判断「是不是句首」用）

    # ------------------------------------------------------------------ 公开

    @property
    def pending(self) -> str:
        """还没吐出来的内容（调试 / 断言用）。"""
        return self._glue + self._buf

    def feed(self, delta: str) -> list[str]:
        """喂一段增量，返回可以立刻合成的完整句子（可能 0~n 个）。"""
        if delta:
            self._buf += delta
        out: list[str] = []
        while True:
            cut = self._take()
            if cut is None:
                break
            raw, self._buf = self._buf[:cut], self._buf[cut:]
            self._accept(raw, out)
        return out

    def flush(self) -> list[str]:
        """流结束，把剩下的半句吐出来。重复调用不会重复吐。"""
        out: list[str] = []
        rest, self._buf, self._glue = self._glue + self._buf, "", ""
        if rest:
            self._accept(rest, out)
        return out

    # ------------------------------------------------------------------ 内部

    def _accept(self, raw: str, out: list[str]) -> None:
        """把切下来的一块收进结果。纯标点的碎片按情况丢弃或粘到下一句。"""
        text = _clean(raw)
        if not text:
            return
        if _WORD_RE.search(text):
            out.append(self._glue + text)
            self._glue = ""
            self._emitted += 1
            return
        # 全是标点：还一句都没说过的时候，把「……」这类停顿留着，
        # 粘到第一句前面（模型经常用省略号开场，丢了语气就没了）。
        if self._emitted == 0 and text.startswith("…"):
            self._glue += text
        # 其余纯标点（比如空行、多余的「。。。」）直接丢，不值得送 TTS

    def _take(self) -> int | None:
        """算出这一刀切在哪（返回切点下标，None 表示还切不了）。

        返回值是「切完这一刀后，剩下的从哪开始」，即切点本身在切片之后。
        """
        buf = self._buf
        n = len(buf)
        if not n:
            return None

        hit = _find_boundary(buf)
        if hit == -1:
            # 边界已经在眼前了，但后面那个字符还没到 —— 先别落刀，
            # 否则「你好。」「」」会被切成两句。等下一个 delta 或 flush()。
            return None
        if hit is not None:
            return hit

        # 一整段都没有句末标点：按长度兜底，别让人等太久听不到声音。
        if n > self.soft_limit:
            # 窗口固定成 hard_limit，不随 n 浮动 —— 否则同一段文字，网络
            # 快一点（一次到 80 字）和慢一点（分两次到 40+40 字）会在不同
            # 的逗号上落刀，「逐字喂 == 整段喂」这个性质就断了。
            comma = _last_comma(buf, self.hard_limit)
            if comma is not None and comma + 1 >= _MIN_SOFT_PIECE \
                    and comma + 1 <= self.hard_limit:
                return comma + 1          # 逗号留在本句里，念起来有个自然停顿
        if n > self.hard_limit:
            return _safe_cut(buf, self.hard_limit)
        if n > _HARD_CAP:
            return _HARD_CAP              # 保命闸门，正常情况下到不了这
        return None


# ---------------------------------------------------------------------- 工具


def _clean(text: str) -> str:
    return text.strip()


def _find_boundary(buf: str) -> int | None:
    """找句末标点造成的切点。

    返回正数 = 切点；None = 还没有边界；-1 = 有边界但需要再看一个字符才能定
    （为了把「。」后面的收尾引号一起带进这一句）。
    """
    n = len(buf)
    i = 0
    while i < n:
        ch = buf[i]
        if ch in _SENTENCE_END:
            j = i + 1
            while j < n and buf[j] in _SENTENCE_END:   # …… ！！ ？。 连着打的情况
                j += 1
            k = j
            while k < n and buf[k] in _CLOSERS:        # 」” 这类收尾符号
                k += 1
            if k >= n:
                return -1                              # 下一块可能还是收尾符号
            return k
        if ch in _NEWLINES:
            return i + 1 if i + 1 < n else -1
        i += 1
    return None


def _last_comma(buf: str, limit: int) -> int | None:
    """在 buf[0:limit] 里找最后一个逗号/顿号的下标。"""
    window = buf[:limit]
    for idx in range(len(window) - 1, -1, -1):
        if window[idx] in "，、,":
            return idx
    return None


def _safe_cut(buf: str, limit: int) -> int:
    """硬切时尽量别切在英文词、数字、引号中间。

    切在 "3.14" 或 "iPhone" 中间，下一句会从半个词开始，
    TTS 念出来的音就歪了。宁可往前退几个字，退到词边界。
    """
    p = min(limit, len(buf))
    while p > 1 and _splits_token(buf, p):
        p -= 1
    while p > 1 and _inside_quote(buf, p):
        p -= 1
    return p if p > 0 else min(limit, len(buf))


def _splits_token(buf: str, p: int) -> bool:
    """buf 在 p 处切开，会不会把一串英文/数字劈成两半。"""
    return buf[p - 1] in _ASCII_TOKEN and buf[p] in _ASCII_TOKEN


def _inside_quote(buf: str, p: int) -> bool:
    """切点是否落在引号里面的（引号没闭合）。"""
    head = buf[:p]
    if head.count('"') % 2 == 1:
        return True
    if head.count("“") > head.count("”"):
        return True
    if head.count("「") > head.count("」"):
        return True
    return False
