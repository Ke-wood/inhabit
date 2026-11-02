"""下载桌宠三视图素材到 assets/pet/sprites/（**不进仓库**）。

    python tools/fetch_pet_sprites.py            # 下载全部 13 张
    python tools/fetch_pet_sprites.py --dry-run  # 只打印计划，不联网
    python tools/fetch_pet_sprites.py --force    # 已存在的也重下

为什么素材不随仓库分发
----------------------
仓库是 MIT 的代码，但那只蓝色大肥鱼的形象是 `1190fasheqi/dafeiyu-pet`
作者的原创美术，版权归 ta。代码可以 MIT，图不行 —— 所以我们只给下载脚本，
由使用者自己获取，仅限个人使用。详见 docs/pet.md。

纯标准库：urllib + tarfile，不依赖 requests，没装任何东西也能跑。
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tarfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 目录从项目这边唯一一处根目录定义来，别在脚本里再算一遍
try:
    from core.paths import ROOT
except Exception:                                    # 极端情况下也能独立运行
    ROOT = Path(__file__).resolve().parent.parent

REPO = "1190fasheqi/dafeiyu-pet"
BRANCH = "main"
SPRITE_SUBDIR = "sprites"
DEFAULT_DEST = ROOT / "assets" / "pet" / "sprites"

# 13 张：3 视图 × (原图 + 3 档预缩放) + 托盘图标。
# 档位高度 = 340 × {0.55, 0.7, 0.9} → 187 / 238 / 306，与 tools/pet_window.py 的 SIZE_LEVELS 对齐。
VIEWS = ("正面", "侧面", "背面")
HEIGHTS = (187, 238, 306)
FILES = ["icon.png"]
for _v in VIEWS:
    FILES.append(f"{_v}.png")
    FILES.extend(f"{_v}_{_h}.png" for _h in HEIGHTS)

RAW_URL = "https://raw.githubusercontent.com/{repo}/{branch}/{sub}/{name}"
TARBALL_URL = "https://codeload.github.com/{repo}/tar.gz/refs/heads/{branch}"
MANUAL_URL = "https://github.com/{repo}/tree/{branch}/{sub}"
UA = "inhabit-pet-fetch/1.0 (+https://github.com/{repo})".format(repo=REPO)

LICENSE_NOTE = f"""\
素材来源：GitHub {REPO}（代码 MIT）
角色美术（三视图 PNG、托盘图标）版权归该项目作者，**仅限个人使用**，
不得再分发或用于商业用途。本脚本只负责下载，不改变任何许可。
"""


# ------------------------------------------------------------------ 工具
def human(n: float) -> str:
    """字节数 → 人类可读。"""
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024.0
    return f"{n:.1f} GB"


def _bar(done: int, total: int, width: int = 24) -> str:
    """命令行进度条。total 未知时返回空串，由调用方改印刷字节数。"""
    if total <= 0:
        return ""
    filled = int(width * done / total)
    return "[" + "#" * filled + "-" * (width - filled) + f"] {done * 100 // total:3d}%"


class Fetcher:
    def __init__(self, dest: Path, *, force: bool, retries: int = 3, timeout: float = 30.0) -> None:
        self.dest = Path(dest)
        self.force = force
        self.retries = retries
        self.timeout = timeout

    # -------------------------------------------------- 单文件
    def target(self, name: str) -> Path:
        return self.dest / name

    def url_for(self, name: str) -> str:
        # 中文文件名必须转义，否则 urllib 会抛 UnicodeEncodeError
        quoted = urllib.parse.quote(name)
        return RAW_URL.format(repo=REPO, branch=BRANCH, sub=SPRITE_SUBDIR, name=quoted)

    def need(self, name: str) -> bool:
        path = self.target(name)
        return self.force or not (path.is_file() and path.stat().st_size > 0)

    def fetch_one(self, name: str, index: int, count: int) -> tuple[bool, str]:
        """下载一个文件。返回 (成功, 说明)。"""
        path = self.target(name)
        url = self.url_for(name)
        tag = f"[{index}/{count}] {name}"
        last_err = ""
        for attempt in range(1, self.retries + 1):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": UA})
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    total = int(resp.headers.get("Content-Length") or 0)
                    tmp = path.with_suffix(path.suffix + ".part")
                    got = 0
                    with open(tmp, "wb") as fh:
                        while True:
                            chunk = resp.read(65536)
                            if not chunk:
                                break
                            fh.write(chunk)
                            got += len(chunk)
                            # 进度只在同一行覆盖刷新，别把 13 个文件的输出刷成 13 页
                            bar = _bar(got, total)
                            sys.stdout.write(f"\r  {tag}  {human(got)}/{human(total)} {bar}")
                            sys.stdout.flush()
                    # 先写 .part 再改名：中途断网不会留下一个半张图冒充成品
                    if got < 1024:
                        raise OSError(f"文件太小（{got} 字节），多半不是 PNG")
                    with open(tmp, "rb") as fh:
                        head = fh.read(8)
                    if head != b"\x89PNG\r\n\x1a\n":
                        raise OSError("下载内容不是 PNG（被重定向到登录页或错误页？）")
                    tmp.replace(path)
                    sys.stdout.write("\r" + " " * 78 + "\r")
                    print(f"  {tag}  {human(got)}  ✓")
                    return True, human(got)
            except (urllib.error.URLError, urllib.error.HTTPError, OSError) as ex:
                last_err = str(ex)
                sys.stdout.write("\r" + " " * 78 + "\r")
                if attempt < self.retries:
                    print(f"  {tag}  第 {attempt} 次失败：{last_err}，重试…")
                    # 退避一下：GitHub 抽风时立刻重试通常还是失败
                    time.sleep(min(1.5 * attempt, 4.0))
        return False, last_err

    # -------------------------------------------------- 全部
    def run(self, dry_run: bool = False) -> int:
        print("=" * 68)
        print("  大肥鱼桌宠 · 素材下载")
        print("=" * 68)
        print(LICENSE_NOTE)
        print(f"  仓库：https://github.com/{REPO}")
        print(f"  目标：{self.dest}")

        todo = [n for n in FILES if self.need(n)]
        have = [n for n in FILES if n not in todo]

        if have:
            print(f"  已存在：{len(have)} 个（要覆盖请加 --force）")
        if not todo:
            print("\n  13 个文件都在，不用下。直接跑：python tools/pet_window.py")
            self._write_gitignore()
            return 0

        print(f"  待下载：{len(todo)} 个，共约 1.2 MB\n")

        if dry_run:
            for i, name in enumerate(todo, 1):
                print(f"  [dry-run] [{i}/{len(todo)}] {name}")
                print(f"            {self.url_for(name)}")
            print("\n  dry-run：没有联网、没有写盘。")
            print("  去掉 --dry-run 就真的下载。")
            return 0

        self.dest.mkdir(parents=True, exist_ok=True)
        self._write_gitignore()

        ok, failed = 0, []
        for i, name in enumerate(todo, 1):
            good, detail = self.fetch_one(name, i, len(todo))
            if good:
                ok += 1
            else:
                failed.append((name, detail))

        print()
        if not failed:
            print(f"  完成：{ok} 个文件已就位 → {self.dest}")
            print("  下一步：python tools/pet_window.py")
            print("  如果窗口只是彩色占位方块，说明文件名对不上，检查上面下载的目录。")
            return 0

        print(f"  部分失败：成功 {ok} / 失败 {len(failed)}")
        for name, err in failed:
            print(f"    ✗ {name}：{err}")
        self._print_help()
        return 1

    def _write_gitignore(self) -> None:
        """在素材目录里放一个 .gitignore，防止有人 `git add .` 把美术图提交上去。

        不写根 .gitignore 是刻意的：没开桌宠的人不该看到一个用不上的条目，
        而且这个脚本一跑就能自我保护，比让用户去改根配置更可靠。
        """
        try:
            self.dest.mkdir(parents=True, exist_ok=True)
            marker = self.dest / ".gitignore"
            text = "# 素材由 tools/fetch_pet_sprites.py 下载，版权归原作者，不进版本库\n*\n"
            if not marker.exists() or marker.read_text(encoding="utf-8") != text:
                marker.write_text(text, encoding="utf-8")
        except OSError:
            pass

    def _print_help(self) -> None:
        print("\n  ---- 怎么办 ----")
        print("  1) 网络问题（公司网络/代理/被墙）：先试试能不能打开")
        print(f"     https://github.com/{REPO}")
        print("  2) 手动下载：打开下面的页面，点每个文件 → Download raw file，")
        print(f"     {MANUAL_URL.format(repo=REPO, branch=BRANCH, sub=SPRITE_SUBDIR)}")
        print(f"     放进 {self.dest}（文件名保持原样，中文名别改）")
        print("  3) 或者整包下载：")
        print(f"     {TARBALL_URL.format(repo=REPO, branch=BRANCH)}")
        print(f"     解压后把 {SPRITE_SUBDIR}/ 里 13 个 PNG 全拷到上面的目录")
        print("  4) 用 --tarball 让脚本走整包：python tools/fetch_pet_sprites.py --tarball")


# ------------------------------------------------------------------ 整包
def run_tarball(dest: Path, *, force: bool) -> int:
    """整包下载（raw 被墙时更稳，一次连接拿全部）。

    只摘 `sprites/*.png` 这 13 个文件，其余条目全跳过 —— 别把整个仓库解到 assets 里。
    """
    url = TARBALL_URL.format(repo=REPO, branch=BRANCH)
    dest.mkdir(parents=True, exist_ok=True)
    tgz = dest / "_pet_sprites.tar.gz"
    print(f"  整包下载：{url}")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=60) as resp, open(tgz, "wb") as fh:
            total = int(resp.headers.get("Content-Length") or 0)
            got = 0
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                fh.write(chunk)
                got += len(chunk)
                sys.stdout.write(f"\r  {human(got)}/{human(total)} {_bar(got, total)}")
                sys.stdout.flush()
        print()
    except (urllib.error.URLError, urllib.error.HTTPError, OSError) as ex:
        print(f"  整包下载失败：{ex}")
        tgz.unlink(missing_ok=True)
        Fetcher(dest, force=force)._print_help()
        return 1

    wanted = set(FILES)
    saved = 0
    try:
        with tarfile.open(tgz, "r:gz") as tf:
            for member in tf.getmembers():
                if not member.isfile():
                    continue
                name = Path(member.name).name
                # 只认 sprites/ 下的那 13 个名字，路径穿越和多余文件一律不理
                if Path(member.name).parent.name != SPRITE_SUBDIR or name not in wanted:
                    continue
                out = dest / name
                if out.exists() and not force:
                    continue
                src = tf.extractfile(member)
                if src is None:
                    continue
                with src, open(out, "wb") as fh:
                    shutil.copyfileobj(src, fh)
                saved += 1
                print(f"  {name}  ✓")
    except (tarfile.TarError, OSError) as ex:
        print(f"  解包失败：{ex}")
        return 1
    finally:
        tgz.unlink(missing_ok=True)

    missing = [n for n in FILES if not (dest / n).is_file()]
    print(f"\n  解出 {saved} 个文件")
    if missing:
        print(f"  还缺：{'、'.join(missing)}")
        return 1
    print("  全部就位。下一步：python tools/pet_window.py")
    return 0


# ------------------------------------------------------------------ 入口
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="下载大肥鱼桌宠的三视图素材到 assets/pet/sprites/（不进仓库）")
    ap.add_argument("--dest", type=Path, default=DEFAULT_DEST,
                    help=f"下载到哪，默认 {DEFAULT_DEST}")
    ap.add_argument("--dry-run", action="store_true", help="只打印要下什么，不联网不写盘")
    ap.add_argument("--force", action="store_true", help="已存在的文件也重新下")
    ap.add_argument("--tarball", action="store_true", help="整包下载（raw 连不上时用）")
    ap.add_argument("--retries", type=int, default=3, help="单文件重试次数，默认 3")
    args = ap.parse_args(argv)

    if os.name == "nt":
        # Windows 控制台默认 GBK，中文文件名会直接 UnicodeEncodeError
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass

    dest = Path(args.dest).expanduser().resolve()
    fetcher = Fetcher(dest, force=args.force, retries=max(1, args.retries))
    if args.dry_run:
        return fetcher.run(dry_run=True)
    if args.tarball:
        print(LICENSE_NOTE)
        return run_tarball(dest, force=args.force)
    return fetcher.run()


if __name__ == "__main__":
    raise SystemExit(main())
