"""把 biliup 下载到 tools/bin/ 下（只在要用投稿功能时跑一次）。

    python tools/fetch_biliup.py                 # 下载 v1.3.4（约 74 MB）
    python tools/fetch_biliup.py --version 1.3.4
    python tools/fetch_biliup.py --url <zip 地址>  # 自己给地址（镜像 / 内网）

为什么不在仓库里带：biliup 是第三方二进制，Windows 专用、体积大、
版本写死在路径里，而且随附字体另有许可（OFL）。放仓库里既污染 git 历史，
也让杀软误报更难解释。

装好之后 tools/publish.py 会自动找到它；也可以用环境变量 BILIUP_EXE 指定。
"""

from __future__ import annotations

import argparse
import hashlib
import pathlib
import sys
import tempfile
import urllib.request
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
DEST = ROOT / "tools" / "bin" / "biliup"


def download(url: str, dst: pathlib.Path) -> None:
    print(f"下载 {url}")
    with urllib.request.urlopen(url, timeout=120) as r, open(dst, "wb") as f:
        shutil_copy(r, f)


def shutil_copy(src, dst, chunk: int = 1 << 20) -> None:
    while True:
        b = src.read(chunk)
        if not b:
            return
        dst.write(b)


def sha256(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", default="1.3.4")
    ap.add_argument("--url", default="")
    ap.add_argument("--sha256", default="", help="给了就校验，对不上就退出")
    args = ap.parse_args()

    if args.url:
        url = args.url
    else:
        url = (f"https://github.com/biliup/biliup-rs/releases/download/v{args.version}/"
               f"biliupR-v{args.version}-x86_64-windows.zip")

    DEST.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        z = pathlib.Path(td) / "biliup.zip"
        try:
            download(url, z)
        except Exception as e:
            print(f"下载失败：{type(e).__name__}: {e}")
            print("可以手动下载后用 --url 指定本地/镜像地址：")
            print(f"  {url}")
            sys.exit(1)
        if args.sha256:
            got = sha256(z)
            if got.lower() != args.sha256.lower():
                print(f"SHA256 不匹配：\n  期望 {args.sha256}\n  实际 {got}")
                sys.exit(1)
        with zipfile.ZipFile(z) as zf:
            zf.extractall(DEST)

    exes = list(DEST.rglob("biliup.exe"))
    if exes:
        print(f"装好了：{exes[0]}")
        print("接着跑：python tools/bili_login_tv.py   （扫码拿投稿凭证）")
    else:
        print(f"解压完了，但没找到 biliup.exe，去 {DEST} 看看")


if __name__ == "__main__":
    main()
