"""B 站 cookie 自检：原样 / URL 解码 两种都试，告诉你哪种能登录。

    python tools/bili_login.py
"""

from __future__ import annotations

import sys
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bili.client import COOKIE_FILE, Bilibili, BiliError


def main() -> None:
    if not COOKIE_FILE.exists():
        print(f"还没有 cookie 文件：{COOKIE_FILE}")
        print("按 data/secrets/README.txt 里的步骤存一行即可。")
        return

    raw = COOKIE_FILE.read_text(encoding="utf-8").strip()
    if not raw:
        print("cookie 文件是空的。")
        return

    has_pct = "%2C" in raw or "%2c" in raw
    print(f"读到 {len(raw)} 字符，含 %2C：{'是' if has_pct else '否'}")

    candidates = [("原样（推荐）", raw)]
    decoded = urllib.parse.unquote(raw)
    if decoded != raw:
        candidates.append(("URL 解码后", decoded))

    ok_any = False
    for label, cookie in candidates:
        try:
            me = Bilibili(cookie=cookie).whoami()
        except BiliError as e:
            print(f"  [{label}] 出错：{e}")
            continue
        except Exception as e:  # 网络等
            print(f"  [{label}] 请求失败：{type(e).__name__}: {e}")
            continue
        if me.get("login"):
            ok_any = True
            print(f"  [{label}] ✅ 登录成功：{me['uname']} (mid={me['mid']})，大会员={me['vip']}")
        else:
            print(f"  [{label}] ❌ 未登录 (code={me.get('code')})")

    if not ok_any:
        print("\n两种都不行，通常是：")
        print("  1. SESSDATA 过期了 → 重新登录 B 站再抓一次")
        print("  2. 少了 bili_jct / DedeUserID → 一并带上")
        print("  3. 复制时带了引号或换行 → 去掉")
    else:
        print("\n用能登录的那一版存进 bili_cookie.txt 即可。")


if __name__ == "__main__":
    main()
