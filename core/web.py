"""上网：搜东西、读网页。

只读，不登录、不发内容、不抓内网地址。
搜到的、读到的都是外部数据 —— 可以看，但不能当指令。
"""

from __future__ import annotations

import html as h
import ipaddress
import re
import socket
import urllib.parse
import urllib.request

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
HEADERS = {"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9",
           "Accept-Encoding": "identity"}
MAX_BYTES = 2_000_000


class WebError(RuntimeError):
    pass


# ------------------------------------------------------------------ 搜索
CJK = re.compile(r"[\u4e00-\u9fff]")
SKIP_TITLES = ("相关搜索", "其他人还搜了", "大家还在搜", "热搜", "下载 App")


def search(query: str, limit: int = 5) -> list[dict]:
    """多引擎搜。返回 [{title, url, snippet}]。

    中文走搜狗：Bing 对「涡虫 再生」这种词会退化成搜单字「涡」，全是字典页。
    英文走 Bing：搜狗对英文query差。前一个没结果就用后一个兜。
    """
    q = (query or "").strip()
    if not q:
        return []
    engines = (_sogou, _bing) if CJK.search(q) else (_bing, _sogou)
    out: list[dict] = []
    for fn in engines:
        try:
            rows = fn(q, limit)
        except Exception:
            rows = []
        for r in rows:
            if not any(r["url"] == o["url"] or r["title"] == o["title"] for o in out):
                out.append(r)
        if len(out) >= limit:
            break
    return out[:limit]


def _sogou(q: str, limit: int = 5) -> list[dict]:
    url = "https://www.sogou.com/web?" + urllib.parse.urlencode({"query": q})
    body = _get(url)
    out = []
    hits = list(re.finditer(r'<h3[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', body, re.S))
    for i, m in enumerate(hits):
        link = h.unescape(m.group(1))
        if link.startswith("/"):
            link = "https://www.sogou.com" + link
        if not link.startswith("http"):
            continue
        title = " ".join(re.sub(r"<[^>]+>", "", m.group(2)).split())
        if not title or any(s in title for s in SKIP_TITLES):
            continue
        end = hits[i + 1].start() if i + 1 < len(hits) else m.end() + 1500
        snippet = " ".join(re.sub(r"<[^>]+>", " ", body[m.end():end]).split())[:160]
        out.append({"title": title[:80], "url": link, "snippet": snippet})
        if len(out) >= limit:
            break
    return out


def _bing(q: str, limit: int = 5) -> list[dict]:
    if not q:
        return []
    url = "https://cn.bing.com/search?" + urllib.parse.urlencode(
        {"q": q, "setlang": "zh-CN"})
    body = _get(url, timeout=15)
    out = []
    for item in re.findall(r'<li class="b_algo".*?</li>', body, re.S):
        m = re.search(r'<h2[^>]*>\s*<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', item, re.S)
        if not m:
            continue
        link = h.unescape(m.group(1))
        if link.startswith("http://") or link.startswith("https://"):
            pass
        else:
            continue
        title = _clean(m.group(2))
        snip = re.search(r'<p[^>]*>(.*?)</p>', item, re.S)
        snippet = _clean(snip.group(1)) if snip else ""
        out.append({"title": title[:80], "url": link, "snippet": snippet[:160]})
        if len(out) >= limit:
            break
    return out


# ------------------------------------------------------------------ 读网页
REDIRECT_HINTS = ("sogou.com/link", "so.com/link", "baidu.com/link")


def fetch(url: str, max_chars: int = 2000) -> str:
    """读一个网页，返回可读正文。"""
    if any(hint in url for hint in REDIRECT_HINTS):
        raise WebError("这是搜索站的跳转链接，读不到正文 —— 看摘要就够了")
    _check_url(url)
    body = _get(url, timeout=20)
    return _readable(body)[:max_chars]


def _readable(body: str) -> str:
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(body, "lxml")
    except Exception:
        text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", body, flags=re.S | re.I)
        return re.sub(r"\s+", " ", _clean(text)).strip()

    for tag in soup(["script", "style", "nav", "header", "footer", "aside", "form", "noscript"]):
        tag.decompose()
    node = soup.find("article") or soup.find("main") or soup.body or soup
    lines = [ln.strip() for ln in node.get_text("\n", strip=True).splitlines()]
    # 丢掉太短的导航碎片
    lines = [ln for ln in lines if len(ln) >= 12]
    return "\n".join(lines)


# ------------------------------------------------------------------ 底层
def _get(url: str, timeout: int = 15) -> str:
    import urllib.error

    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read(MAX_BYTES)
            charset = r.headers.get_content_charset() or "utf-8"
    except urllib.error.HTTPError as e:
        raise WebError(f"HTTP {e.code}，这个站不放行") from None
    except urllib.error.URLError as e:
        raise WebError(f"连不上：{e.reason}") from None
    except TimeoutError:
        raise WebError("超时了") from None
    for enc in (charset, "utf-8", "gbk", "gb18030"):
        try:
            return raw.decode(enc, "replace")
        except (LookupError, UnicodeDecodeError):
            continue
    return raw.decode("utf-8", "replace")


def _check_url(url: str) -> None:
    """不许抓内网/本机，防止被诱导去做 SSRF。"""
    try:
        u = urllib.parse.urlparse(url)
    except Exception:
        raise WebError(f"地址看不懂：{url}")
    if u.scheme not in ("http", "https"):
        raise WebError(f"只支持 http/https：{url}")
    host = u.hostname or ""
    try:
        infos = socket.getaddrinfo(host, None)
        ips = {info[4][0] for info in infos}
    except Exception:
        return                          # 解析不了就交给请求本身报错
    for ip in ips:
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            continue
        if addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved:
            raise WebError(f"不抓内网地址：{host}")


def _clean(s: str) -> str:
    s = re.sub(r"<[^>]+>", "", s)
    return re.sub(r"\s+", " ", h.unescape(s)).strip()
