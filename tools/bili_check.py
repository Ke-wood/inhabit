"""B 站通道体检：逐步验证 cookie / wbi / 各接口 / 弹幕 / 字幕。

只读：不写数据库、不写任何文件、不打印凭据内容。

    python tools/bili_check.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bili.client import Bilibili, COOKIE_FILE, load_cookie  # noqa: E402


def step(name: str, fn):
    t0 = time.time()
    try:
        r = fn()
        print(f"[ OK ] {name}  ({time.time() - t0:.2f}s)  {r}")
        return r
    except Exception as e:  # noqa: BLE001
        print(f"[FAIL] {name}  ({time.time() - t0:.2f}s)  {type(e).__name__}: {e}")
        return None


def main() -> None:
    print("=" * 60)
    print("B 站通道体检")
    print("=" * 60)

    print(f"cookie 文件: {COOKIE_FILE}")
    print(f"  存在: {COOKIE_FILE.exists()}")
    c = load_cookie()
    print(f"  长度: {len(c)}  SESSDATA: {'SESSDATA' in c}  bili_jct: {'bili_jct' in c}")

    b = Bilibili()
    nav = step("登录态 /x/web-interface/nav", lambda: b.whoami())
    step("wbi 密钥", lambda: len(b.mixin_key()))

    pop = step("热门 /x/web-interface/popular",
               lambda: [(x["bvid"], x["title"][:16]) for x in b.popular(3)])
    step("首页推荐 /wbi/.../feed/rcmd",
         lambda: [(x["bvid"], x["title"][:16]) for x in b.recommend(3)])
    step("关注动态 /polymer/.../feed/all",
         lambda: [(x["bvid"], x["title"][:16]) for x in b.dynamics(3)])

    if pop:
        bvid = pop[0][0]
        v = step(f"视频详情 {bvid}",
                 lambda: {k: b.video(bvid)[k] for k in ("title", "owner", "duration", "cid")})
        if v:
            step("弹幕 comment.bilibili.com",
                 lambda: b.danmaku(v["cid"], 5))
            step("字幕 /x/player/wbi/v2",
                 lambda: f"{len(b.subtitle(bvid, v['cid']))} 字")

    print("-" * 60)
    ok = bool(pop) and bool(nav and nav.get("login"))
    print("结论:", "可用" if ok else ("匿名可用、登录态失效" if pop else "不可用"))


if __name__ == "__main__":
    main()
