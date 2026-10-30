"""B 站 TV 端扫码登录，产出 biliup 能用的 cookies.json。

    python tools/bili_login_tv.py           # 生成二维码
    python tools/bili_login_tv.py --wait    # 生成后等扫码（最多 3 分钟）

产物：data/secrets/biliup_cookies.json（含 access_token / refresh_token）
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys
import time
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "secrets" / "biliup_cookies.json"
QR = ROOT / "data" / "bili_qr.png"

APPKEY = "4409e2ce8ffd12b8"
APPSEC = "59b43e04ad6965f34319062b478f83dd"
HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.bilibili.com/"}


def _signed(params: dict) -> str:
    p = {k: str(v) for k, v in params.items() if v is not None}
    p["appkey"] = APPKEY
    p["ts"] = str(int(time.time()))
    query = urllib.parse.urlencode(sorted(p.items()))
    sign = hashlib.md5((query + APPSEC).encode()).hexdigest()
    return f"{query}&sign={sign}"


def _get(url: str) -> dict:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


def _post(path: str, params: dict) -> dict:
    body = _signed(params).encode()
    req = urllib.request.Request(
        "https://passport.bilibili.com" + path, data=body,
        headers={**HEADERS, "Content-Type": "application/x-www-form-urlencoded"}, method="POST")
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


def auth_code() -> tuple[str, str]:
    d = _post("/x/passport-tv-login/qrcode/auth_code", {"local_id": "0"})
    if d.get("code") != 0:
        raise RuntimeError(f"取二维码失败：{d}")
    data = d["data"]
    return data["url"], data["auth_code"]


def qr_image(url: str, path: pathlib.Path) -> pathlib.Path:
    import qrcode
    img = qrcode.make(url)
    img = img.resize((420, 420))
    img.save(path)
    return path


def poll(auth_code_value: str) -> dict:
    return _post("/x/passport-tv-login/qrcode/poll",
                 {"auth_code": auth_code_value, "local_id": "0"})


def to_biliup(data: dict) -> dict:
    ci = data.get("cookie_info") or {}
    return {
        "cookie_info": ci,
        "token_info": data.get("token_info") or {},
        "sso": data.get("sso") or ["https://passport.bilibili.com/api/v2/sso"],
        "platform": data.get("platform") or "BiliTV",
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wait", action="store_true")
    ap.add_argument("--timeout", type=int, default=180)
    args = ap.parse_args()

    url, code = auth_code()
    qr_image(url, QR)
    print("登录链接:", url)
    print("二维码:", QR, QR.stat().st_size // 1024, "KB")

    if not args.wait:
        (ROOT / "data" / "bili_qr.txt").write_text(f"{url}\n{code}\n", encoding="utf-8")
        return

    print("等扫码…", flush=True)
    deadline = time.time() + args.timeout
    last = None
    while time.time() < deadline:
        d = poll(code)
        c = d.get("code")
        msg = {86101: "还没扫", 86090: "扫了，等确认", 86038: "二维码过期", 0: "成功"}.get(c, str(c))
        if msg != last:
            print(f"  [{int(time.time()-time.time()%1)}] {msg}", flush=True)
            last = msg
        if c == 0:
            data = d["data"]
            OUT.parent.mkdir(parents=True, exist_ok=True)
            OUT.write_text(json.dumps(to_biliup(data), ensure_ascii=False, indent=1), encoding="utf-8")
            print("已写入:", OUT)
            print("mid:", data.get("mid"), "| 有 access_token:", bool((data.get("token_info") or {}).get("access_token")))
            return
        if c == 86038:
            print("二维码过期，重跑一次")
            return
        time.sleep(2)
    print("超时，没等到确认")


if __name__ == "__main__":
    main()
