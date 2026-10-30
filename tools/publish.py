"""把视频投稿到 B 站。

    python tools/publish.py check                    # 检查 biliup 与登录态
    python tools/publish.py list                     # 看已投稿件和状态
    python tools/publish.py upload <mp4> --title "..." [--cover x.png]

投稿走 biliup（TV 端凭证在 data/secrets/biliup_cookies.json）。
biliup 本体不进仓库，先跑一次 `python tools/fetch_biliup.py` 下载，
或者自己装好、用环境变量 BILIUP_EXE 指过去。

本工具只负责「把你自己的视频传上去」，不做视频生成、不自动刷量。
平台规则自己看：投稿要实名、版权要干净。
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
COOKIE = ROOT / "data" / "secrets" / "biliup_cookies.json"
DEFAULT_EXE = ROOT / "tools" / "bin" / "biliup" / "biliupR-v1.3.4-x86_64-windows" / "biliup.exe"

DEFAULT_TID = 160          # 生活
DEFAULT_TAGS = "日常,记录"


def biliup_exe() -> pathlib.Path | None:
    """四层找 biliup：环境变量 → config.json → 默认下载位置 → PATH。"""
    env = os.environ.get("BILIUP_EXE")
    if env and pathlib.Path(env).exists():
        return pathlib.Path(env)
    try:
        from core import config as cfg
        p = ((cfg.load().get("publish") or {}).get("biliup") or "").strip()
        if p and pathlib.Path(p).exists():
            return pathlib.Path(p)
    except Exception:
        pass
    if DEFAULT_EXE.exists():
        return DEFAULT_EXE
    found = shutil.which("biliup")
    return pathlib.Path(found) if found else None


def _run(args: list[str], timeout: int = 1800) -> tuple[int, str]:
    exe = biliup_exe()
    if not exe:
        return 1, ("没找到 biliup。先跑 `python tools/fetch_biliup.py`，"
                   "或设置环境变量 BILIUP_EXE 指向 biliup 可执行文件。")
    p = subprocess.run([str(exe), "-u", str(COOKIE), *args],
                       capture_output=True, timeout=timeout)
    raw = (p.stdout or b"") + (p.stderr or b"")
    for enc in ("utf-8", "gbk"):
        try:
            return p.returncode, raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return p.returncode, raw.decode("utf-8", "replace")


def check() -> dict:
    exe = biliup_exe()
    if not exe:
        return {"ok": False, "error": "没找到 biliup（跑 tools/fetch_biliup.py，或设 BILIUP_EXE）"}
    if not COOKIE.exists():
        return {"ok": False, "error": f"没有凭证：{COOKIE}（跑 tools/bili_login_tv.py 扫码登录）"}
    code, out = _run(["list"], timeout=120)
    m = re.search(r"user:\s*(\S+)", out)
    return {"ok": code == 0 and bool(m), "user": m.group(1) if m else None,
            "biliup": str(exe),
            "tail": out.strip().splitlines()[-3:]}


def list_videos() -> list[dict]:
    code, out = _run(["list"], timeout=120)
    rows = []
    for line in out.splitlines():
        m = re.match(r"(BV\w+)\t(.+?)\t(.*)$", line.strip())
        if m:
            rows.append({"bvid": m.group(1), "title": m.group(2),
                         "status": re.sub(r"\x1b\[[0-9;]*m", "", m.group(3))})
    return rows


def upload(video: str, *, title: str, desc: str = "", tags: str = DEFAULT_TAGS,
           tid: int = DEFAULT_TID, cover: str = "", copyright_: int = 1,
           dynamic: str = "") -> dict:
    v = pathlib.Path(video)
    if not v.exists():
        return {"ok": False, "error": f"视频不存在：{v}"}
    args = ["upload", str(v), "--title", title, "--tid", str(tid),
            "--tag", tags, "--desc", desc, "--copyright", str(copyright_)]
    if cover and pathlib.Path(cover).exists():
        args += ["--cover", str(cover)]
    if dynamic:
        args += ["--dynamic", dynamic]

    code, out = _run(args)
    bvid = re.search(r'"bvid":\s*String\("(BV\w+)"\)', out) or re.search(r"(BV[0-9A-Za-z]{10})", out)
    aid = re.search(r'"aid":\s*Number\((\d+)\)', out)
    ok = code == 0 and bvid is not None
    return {
        "ok": ok,
        "bvid": bvid.group(1) if bvid else None,
        "aid": aid.group(1) if aid else None,
        "url": f"https://www.bilibili.com/video/{bvid.group(1)}" if bvid else None,
        "tail": out.strip().splitlines()[-4:],
        "error": None if ok else (out.strip().splitlines()[-1] if out.strip() else "未知错误"),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd")

    sub.add_parser("check")
    sub.add_parser("list")

    up = sub.add_parser("upload")
    up.add_argument("video")
    up.add_argument("--title", required=True)
    up.add_argument("--desc", default="")
    up.add_argument("--tags", default=DEFAULT_TAGS)
    up.add_argument("--tid", type=int, default=DEFAULT_TID)
    up.add_argument("--cover", default="")
    up.add_argument("--dynamic", default="")
    up.add_argument("--reprint", action="store_true", help="转载（默认自制）")

    args = ap.parse_args()
    if args.cmd == "check":
        print(json.dumps(check(), ensure_ascii=False, indent=1))
    elif args.cmd == "list":
        for r in list_videos():
            print(f"{r['bvid']}  {r['status']:6}  {r['title'][:40]}")
    elif args.cmd == "upload":
        r = upload(args.video, title=args.title, desc=args.desc, tags=args.tags,
                   tid=args.tid, cover=args.cover, dynamic=args.dynamic,
                   copyright_=2 if args.reprint else 1)
        print(json.dumps(r, ensure_ascii=False, indent=1))
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
