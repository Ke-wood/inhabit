"""B 站只读通道：匿名可看的部分 + wbi 签名。

不做的事：不评论、不点赞、不投稿、不批量抓取。只读，节奏慢。
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
HEADERS = {"User-Agent": UA, "Referer": "https://www.bilibili.com/",
           "Accept-Language": "zh-CN,zh;q=0.9"}

_MIXIN_TAB = [46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49,
              33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40, 61,
              26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36,
              20, 34, 44, 52]


class BiliError(RuntimeError):
    pass


COOKIE_FILE = Path(__file__).resolve().parent.parent / "data" / "secrets" / "bili_cookie.txt"
COOKIE_CANDIDATES = (
    "bili_cookie.txt", "bili_ccookie.txt", "cookie.txt", "cookies.txt",
    "bilibili_cookie.txt", "bili.txt",
)


def load_cookie() -> str:
    """读 cookie：当前角色的优先，其次全局，最后 data/secrets/ 下的文件。"""
    def _clean(s: str) -> str:
        s = re.sub(r"^\s*cookie\s*:\s*", "", s or "", flags=re.I)
        return " ".join(line.strip() for line in s.splitlines() if line.strip())

    try:
        from core import config as _cfg
        prof = _cfg.profile()                      # 正在跑的那个角色
        c = _clean((prof.get("bilibili") or {}).get("cookie") or "")
        if c:
            return c
        c = _clean((_cfg.load().get("bilibili") or {}).get("cookie") or "")
        if c:
            return c
    except Exception:
        pass
    base = COOKIE_FILE.parent
    # 每个角色一份：面板把 cookie 存这里，config.json 里只留空
    try:
        from core import config as _cfg
        name = (_cfg.profile() or {}).get("name") or ""
        if name:
            p = base / "profiles" / name / "bili_cookie.txt"
            if p.exists():
                raw = _clean(p.read_text(encoding="utf-8", errors="replace").strip())
                if raw:
                    return raw
    except Exception:
        pass
    for name in COOKIE_CANDIDATES:
        p = base / name
        if not p.exists():
            continue
        raw = p.read_text(encoding="utf-8", errors="replace").strip()
        raw = _clean(raw)
        if raw:
            return raw
    return ""


def _get(url: str, params: dict | None = None, timeout: int = 15,
         headers: dict | None = None) -> dict:
    if params:
        url = url + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers=headers or HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _text(url: str, timeout: int = 15) -> str:
    import gzip
    import zlib

    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
        enc = (r.headers.get("Content-Encoding") or "").lower()
    if raw[:2] == b"\x1f\x8b" or enc in ("gzip", "x-gzip"):
        raw = gzip.decompress(raw)
    elif enc == "br":
        try:
            import brotli
            raw = brotli.decompress(raw)
        except ImportError:
            pass
    elif enc == "deflate":
        for wbits in (-15, 15, 47):   # B 站给的是裸 deflate，没有 zlib 头
            try:
                raw = zlib.decompress(raw, wbits)
                break
            except zlib.error:
                continue
    # enc 为空 / 未知：内容本身就是明文（比如 AI 字幕 JSON）。
    # 这里绝不能试探性解压——JSON 首字节 0x7B 会被 deflate 误判成合法块，
    # 解出 1 字节垃圾还不报错，后面整段就废了。
    return raw.decode("utf-8", "replace")


class Bilibili:
    def __init__(self, cookie: str | None = None):
        self.cookie = cookie if cookie is not None else load_cookie()
        self._mixin: str | None = None

    # ---------------------------------------------------------------- 登录态
    def whoami(self) -> dict:
        """用 cookie 查自己是谁。没登录返回 {'login': False}。"""
        req = urllib.request.Request("https://api.bilibili.com/x/web-interface/nav",
                                     headers=self._headers())
        with urllib.request.urlopen(req, timeout=15) as r:
            d = json.loads(r.read().decode("utf-8"))
        data = d.get("data") or {}
        return {"login": bool(data.get("isLogin")), "uname": data.get("uname"),
                "mid": data.get("mid"), "vip": (data.get("vipStatus") == 1),
                "code": d.get("code")}

    # ---------------------------------------------------------------- wbi
    def mixin_key(self) -> str:
        if self._mixin:
            return self._mixin
        d = _get("https://api.bilibili.com/x/web-interface/nav", headers=self._headers())
        wbi = (d.get("data") or {}).get("wbi_img") or {}
        raw = (wbi.get("img_url", "").rsplit("/", 1)[-1].split(".")[0]
               + wbi.get("sub_url", "").rsplit("/", 1)[-1].split(".")[0])
        if len(raw) < 64:
            raise BiliError("拿不到 wbi 密钥（接口可能变了）")
        self._mixin = "".join(raw[i] for i in _MIXIN_TAB)[:32]
        return self._mixin

    def sign(self, params: dict) -> str:
        p = dict(params)
        p["wts"] = int(time.time())
        items = []
        for k in sorted(p):
            v = re.sub(r"[!'()*]", "", str(p[k]))
            items.append(f"{urllib.parse.quote(k, safe='')}={urllib.parse.quote(v, safe='')}")
        q = "&".join(items)
        p["w_rid"] = hashlib.md5((q + self.mixin_key()).encode()).hexdigest()
        return "&".join(f"{k}={urllib.parse.quote(str(v), safe='')}" for k, v in sorted(p.items()))

    def _wbi_get(self, path: str, params: dict) -> dict:
        url = f"https://api.bilibili.com{path}?{self.sign(params)}"
        req = urllib.request.Request(url, headers=self._headers())
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read().decode("utf-8"))

    def _headers(self) -> dict:
        h = dict(HEADERS)
        if self.cookie:
            h["Cookie"] = self.cookie
        return h

    # ---------------------------------------------------------------- 视频
    def video(self, bvid: str) -> dict:
        d = _get("https://api.bilibili.com/x/web-interface/view", {"bvid": bvid})
        if d.get("code") != 0:
            raise BiliError(f"取视频失败: {d.get('message')}")
        v = d["data"]
        return {
            "bvid": v["bvid"], "aid": v["aid"], "title": v["title"],
            "desc": v.get("desc", ""), "duration": v.get("duration", 0),
            "owner": v["owner"]["name"], "cid": v["cid"],
            "stat": {k: v["stat"].get(k) for k in ("view", "like", "danmaku", "reply")},
            "pubdate": v.get("pubdate", 0),
        }

    def danmaku(self, cid: int, limit: int = 200) -> list[str]:
        """弹幕 XML，取前 limit 条（这就是"群友在看的时候的反应"）。"""
        xml = _text(f"https://comment.bilibili.com/{cid}.xml")
        msgs = re.findall(r"<d p=\"[^\"]*\">(.*?)</d>", xml)
        return [m for m in msgs if m.strip()][:limit]

    def subtitle(self, bvid: str, cid: int) -> str:
        """字幕（很多视频没有，返回空串）。需要登录态时可能为空。"""
        try:
            d = self._wbi_get("/x/player/wbi/v2", {"bvid": bvid, "cid": cid})
        except Exception:
            return ""
        subs = ((d.get("data") or {}).get("subtitle") or {}).get("subtitles") or []
        if not subs:
            return ""
        url = subs[0].get("subtitle_url", "")
        if url.startswith("//"):
            url = "https:" + url
        if not url:
            return ""
        try:
            j = json.loads(_text(url))
            return "".join(x.get("content", "") for x in j.get("body", []))[:3000]
        except Exception:
            return ""

    # ---------------------------------------------------------------- 发现
    def dynamics(self, limit: int = 10) -> list[dict]:
        """关注动态（需要 cookie）。UP 主发了什么新视频。"""
        if not self.cookie:
            raise BiliError("关注动态需要登录 cookie")
        d = _get("https://api.bilibili.com/x/polymer/web-dynamic/v1/feed/all",
                 {"type": "video", "page": 1, "offset": ""}, headers=self._headers())
        if d.get("code") != 0:
            raise BiliError(f"取动态失败: {d.get('message')} (code={d.get('code')})")
        out = []
        for item in ((d.get("data") or {}).get("items") or [])[:limit]:
            major = (item.get("modules") or {}).get("module_dynamic", {}).get("major") or {}
            arch = major.get("archive") or {}
            if arch.get("bvid"):
                out.append({"bvid": arch["bvid"], "title": arch.get("title", ""),
                            "owner": (item.get("modules") or {}).get("module_author", {}).get("name", "")})
        return out

    def popular(self, limit: int = 10) -> list[dict]:
        d = _get("https://api.bilibili.com/x/web-interface/popular",
                 {"ps": min(20, limit), "pn": 1})
        return [{"bvid": x["bvid"], "title": x["title"], "owner": x["owner"]["name"],
                 "duration": x["duration"], "desc": x.get("desc", "")}
                for x in (d.get("data") or {}).get("list", [])][:limit]

    def search(self, keyword: str, limit: int = 10) -> list[dict]:
        """按关键词搜视频。标题里的 <em> 高亮标签会被去掉。"""
        if not keyword.strip():
            return []
        d = self._wbi_get("/x/web-interface/wbi/search/type",
                          {"search_type": "video", "keyword": keyword.strip(), "page": 1})
        if d.get("code") != 0:
            raise BiliError(f"搜索失败: {d.get('message')} (code={d.get('code')})")
        out = []
        for x in ((d.get("data") or {}).get("result") or []):
            if not x.get("bvid"):
                continue
            out.append({
                "bvid": x["bvid"],
                "title": re.sub(r"<[^>]+>", "", str(x.get("title") or "")),
                "owner": x.get("author") or "",
                "duration": x.get("duration") or 0,
                "desc": re.sub(r"<[^>]+>", "", str(x.get("description") or "")),
            })
            if len(out) >= limit:
                break
        return out

    def recommend(self, limit: int = 8) -> list[dict]:
        """首页推荐，需要 wbi 签名。"""
        d = self._wbi_get("/x/web-interface/wbi/index/top/feed/rcmd",
                          {"ps": min(12, limit), "fresh_type": 3, "fresh_idx": 1})
        items = ((d.get("data") or {}).get("item") or [])
        out = []
        for x in items:
            if x.get("bvid"):
                out.append({"bvid": x["bvid"], "title": x.get("title", ""),
                            "owner": (x.get("owner") or {}).get("name", ""),
                            "duration": x.get("duration", 0)})
        return out[:limit]

    # ---------------------------------------------------------------- 写（评论 / 弹幕）
    def csrf(self) -> str:
        """从 cookie 里取 bili_jct，写操作要带它。"""
        for part in (self.cookie or "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == "bili_jct":
                return v
        return ""

    def _post_form(self, path: str, form: dict) -> dict:
        token = self.csrf()
        if not token:
            raise BiliError("没有 bili_jct，发不了东西")
        form = {**form, "csrf": token}
        data = urllib.parse.urlencode(form).encode()
        h = self._headers()
        h["Content-Type"] = "application/x-www-form-urlencoded"
        h["Origin"] = "https://www.bilibili.com"
        req = urllib.request.Request(f"https://api.bilibili.com{path}", data=data,
                                     headers=h, method="POST")
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode("utf-8"))

    def reply(self, aid: int | str, message: str) -> dict:
        """在一个视频下发评论。返回 {code, message, rpid}。"""
        d = self._post_form("/x/v2/reply/add",
                            {"oid": str(aid), "type": "1", "message": message})
        return {"code": d.get("code"), "message": d.get("message"),
                "rpid": ((d.get("data") or {}).get("rpid") or (d.get("data") or {}).get("rpid_str"))}

    def del_reply(self, aid: int | str, rpid: int | str) -> dict:
        """删掉自己刚发的评论。"""
        d = self._post_form("/x/v2/reply/del",
                            {"oid": str(aid), "type": "1", "rpid": str(rpid)})
        return {"code": d.get("code"), "message": d.get("message")}

    # ---------------------------------------------------------------- 观看 -> 记忆
    def watch(self, bvid: str, *, danmaku_n: int = 30) -> dict:
        """看一个视频：标题 + 简介 + 弹幕 + 字幕（有的话）。"""
        v = self.video(bvid)
        v["danmaku"] = self.danmaku(v["cid"], danmaku_n)
        v["subtitle"] = self.subtitle(v["bvid"], v["cid"])
        return v


if __name__ == "__main__":
    b = Bilibili()
    print("mixin ok:", len(b.mixin_key()))
    pop = b.popular(3)
    print("\n热门:")
    for x in pop:
        print(" ", x["bvid"], x["title"][:30], "—", x["owner"])
    v = b.watch(pop[0]["bvid"], danmaku_n=8)
    print("\n详情:", v["title"], "|", v["owner"], "|", v["duration"], "秒")
    print("简介:", v["desc"][:80].replace("\n", " "))
    print("弹幕:", v["danmaku"][:8])
    print("字幕长度:", len(v["subtitle"]))
