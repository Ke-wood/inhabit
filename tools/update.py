"""更新：从 GitHub 拉新版，只替换程序文件，用户的东西一个字节不动。

    python tools/update.py check           # 看有没有新版
    python tools/update.py update          # 更新（会先备份）
    python tools/update.py update --dry    # 只看会改哪些文件
    python tools/update.py rollback        # 撤回上一次更新

设计原则
--------
1. **白名单**：只覆盖 `core/version.py: PROGRAM_DIRS` 里列的东西。
   记忆、config、日志、数据库一律不碰（`classify()` 里定的）。
2. **先备份**：动手前把将被覆盖的文件打包到 `data/backups/`。
3. **失败回滚**：任何一步出错，自动恢复备份，不留半新半旧的目录。
4. **不依赖 git**：用户可能没装 git，也可能没 clone 过——直接用
   GitHub 的 codeload 压缩包。
5. **没有网络就安静地失败**：不阻塞启动，不吓人。
"""

from __future__ import annotations

import io
import json
import os
import pathlib
import shutil
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import version as V

DEFAULT_REPO = "Ke-wood/inhabit"
API = "https://api.github.com/repos/{repo}/releases/latest"
API_LIST = "https://api.github.com/repos/{repo}/releases?per_page=10"
TARBALL = "https://codeload.github.com/{repo}/tar.gz/refs/heads/main"
TARBALL_TAG = "https://codeload.github.com/{repo}/tar.gz/refs/tags/{tag}"

BACKUP_DIR = ROOT / "data" / "backups"
UA = "inhabit-updater/1.0"


# ------------------------------------------------------------------ 网络
def repo_slug() -> str:
    """仓库地址：config.updater.repo 优先，其次默认值。"""
    try:
        from core import config
        up = (config.load() or {}).get("updater") or {}
        r = str(up.get("repo") or "").strip()
        if r:
            return r
    except Exception:
        pass
    return DEFAULT_REPO


def _get(url: str, *, timeout: float = 20, binary: bool = False):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "application/vnd.github+json",
    })
    token = os.environ.get("GITHUB_TOKEN") or _token_from_config()
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
    return raw if binary else raw.decode("utf-8", "replace")


def _token_from_config() -> str:
    try:
        from core import config
        up = (config.load() or {}).get("updater") or {}
        return str(up.get("token") or "").strip()
    except Exception:
        return ""


def _pick_release(repo: str) -> dict:
    """取最新 release。

    不用 /releases/latest：它有 CDN 缓存，刚发的版本可能还没生效
    （实测过：v1.4.2 发出来了，latest 还指着 v1.4.1）。
    改用列表接口，按语义版本挑最新的，比「按发布顺序」更可靠。
    """
    try:
        rows = json.loads(_get(API_LIST.format(repo=repo)))
    except urllib.error.HTTPError:
        rows = None
    if isinstance(rows, list) and rows:
        cands = [r for r in rows
                 if isinstance(r, dict) and not r.get("draft") and r.get("tag_name")]
        if cands:
            best = max(cands, key=lambda r: V.parse(str(r.get("tag_name") or "").lstrip("vV")))
            tag = str(best.get("tag_name") or "").strip()
            return {
                "version": tag.lstrip("vV"),
                "tag": tag,
                "notes": str(best.get("body") or ""),
                "url": TARBALL_TAG.format(repo=repo, tag=tag),
                "source": "release",
                "published": str(best.get("published_at") or ""),
            }
        # 列表成功但全是草稿/预发布 → 当作「没有正式 release」
        raise LookupError("没有可用的 release")

    # 列表接口不可用（老版本 API 或权限）→ 退回 /releases/latest
    d = json.loads(_get(API.format(repo=repo)))
    tag = str(d.get("tag_name") or "").strip()
    return {
        "version": tag.lstrip("vV") or V.read_version(),
        "tag": tag,
        "notes": str(d.get("body") or ""),
        "url": TARBALL_TAG.format(repo=repo, tag=tag),
        "source": "release",
        "published": str(d.get("published_at") or ""),
    }


def latest_release() -> dict | None:
    """查最新 release。

    返回 None 表示「真的查不到」（网络不通 / 仓库地址错），
    这种情况必须让调用方知道，不能假装成「没有 release」然后退回 main ——
    否则断网时用户会以为一切正常。
    """
    repo = repo_slug()
    try:
        return _pick_release(repo)
    except LookupError:
        pass
    except urllib.error.HTTPError as e:
        if e.code != 404:
            # 401/403（限流或无权限）/500：网络层问题，不是「没有 release」
            raise UpdateError(f"GitHub API 返回 {e.code}（可能被限流或仓库地址不对）") from e
        # 404 = 仓库确实还没有 release，退回 main 分支
    except urllib.error.URLError as e:
        raise UpdateError(f"连不上 GitHub：{e.reason}") from e
    except (TimeoutError, OSError) as e:
        raise UpdateError(f"网络异常：{e}") from e

    return {
        "version": "",
        "tag": "main",
        "notes": "（这个仓库还没有发布 release，直接取自 main 分支）",
        "url": TARBALL.format(repo=repo),
        "source": "branch",
        "published": "",
    }


class UpdateError(RuntimeError):
    """更新过程中可以预期的失败（网络/权限/结构）。"""


# ------------------------------------------------------------------ 下载解压
def _open_archive(raw: bytes):
    """返回 (解压根目录名, 打开方式)。支持 tarball 与 zip。"""
    if raw[:2] == b"\x1f\x8b":
        tf = tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz")
        names = tf.getnames()
        root = names[0].split("/")[0] if names else ""
        return root, tf
    zf = zipfile.ZipFile(io.BytesIO(raw))
    names = zf.namelist()
    root = names[0].split("/")[0] if names else ""
    return root, zf


def _iter_archive(tf, root: str):
    """遍历压缩包里的文件，产出 (相对路径, 读取函数)。"""
    if isinstance(tf, tarfile.TarFile):
        for m in tf.getmembers():
            if not m.isfile():
                continue
            rel = m.name
            if root and rel.startswith(root + "/"):
                rel = rel[len(root) + 1:]
            if not rel:
                continue
            yield rel, (lambda m=m: tf.extractfile(m).read())
    else:
        for name in tf.namelist():
            if name.endswith("/"):
                continue
            rel = name
            if root and rel.startswith(root + "/"):
                rel = rel[len(root) + 1:]
            if not rel:
                continue
            yield rel, (lambda n=name: tf.read(n))


def download_and_extract(url: str, dest: pathlib.Path) -> str:
    """下载并解到 dest。返回压缩包里的版本号（读到 VERSION 才有）。"""
    try:
        raw = _get(url, timeout=120, binary=True)
    except urllib.error.URLError as e:
        raise UpdateError(f"下载失败：{e.reason}") from e
    except (TimeoutError, OSError) as e:
        raise UpdateError(f"下载失败：{e}") from e
    root, tf = _open_archive(raw)
    dest.mkdir(parents=True, exist_ok=True)
    mem = V.configured_memory_dirs()
    written = 0
    for rel, reader in _iter_archive(tf, root):
        if V.classify(rel, memory_dirs=mem) != "program":
            continue
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(reader())
        written += 1
    try:
        (tf.close if hasattr(tf, "close") else (lambda: None))()
    except Exception:
        pass
    if not written:
        raise RuntimeError("压缩包里没有可更新的程序文件（结构变了吗？）")
    vfile = dest / "VERSION"
    return vfile.read_text(encoding="utf-8").strip().split()[0] if vfile.exists() else ""


# ------------------------------------------------------------------ 备份
def _stamp() -> str:
    return time.strftime("%Y%m%d-%H%M%S")


def backup(rels: list[str]) -> pathlib.Path:
    """把即将被覆盖的现有文件备份成一个目录。"""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    out = BACKUP_DIR / f"pre-{V.read_version()}-{_stamp()}"
    n = 0
    for rel in rels:
        src = ROOT / rel
        if not src.exists():
            continue
        dst = out / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        n += 1
    (out / "_manifest.json").write_text(json.dumps({
        "version": V.read_version(), "at": _stamp(), "files": n,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def _latest_backup() -> pathlib.Path | None:
    if not BACKUP_DIR.exists():
        return None
    cands = [d for d in BACKUP_DIR.iterdir() if d.is_dir() and d.name.startswith("pre-")]
    return max(cands, key=lambda d: d.stat().st_mtime) if cands else None


# ------------------------------------------------------------------ 应用
def plan(src: pathlib.Path) -> dict:
    """算一下这次更新会做什么（不落盘）。"""
    mem = V.configured_memory_dirs()
    new_files = V.program_files(src, memory_dirs=mem)
    changed, added, same = [], [], []
    for rel in new_files:
        cur = ROOT / rel
        if not cur.exists():
            added.append(rel)
            continue
        try:
            if cur.read_bytes() == (src / rel).read_bytes():
                same.append(rel)
            else:
                changed.append(rel)
        except Exception:
            changed.append(rel)
    return {"changed": changed, "added": added, "same": same,
            "total": len(new_files), "version": _guess_version(src)}


def _guess_version(src: pathlib.Path) -> str:
    f = src / "VERSION"
    if f.exists():
        try:
            return f.read_text(encoding="utf-8").strip().split()[0]
        except Exception:
            pass
    return ""


def apply(src: pathlib.Path, p: dict | None = None, *, dry: bool = False) -> dict:
    """把 src 里的程序文件覆盖到 ROOT。失败自动回滚。"""
    p = p or plan(src)
    targets = p["changed"] + p["added"]
    if not targets:
        return {"ok": True, "updated": 0, "note": "已经是最新，没有文件需要改"}

    if dry:
        return {"ok": True, "dry": True, "changed": p["changed"],
                "added": p["added"], "updated": len(targets)}

    # 先记下旧版本号：VERSION 本身也在 targets 里，循环一跑就变成新版了
    old = V.read_version()
    bak = backup(targets)
    written: list[str] = []
    try:
        for rel in targets:
            s = src / rel
            d = ROOT / rel
            d.parent.mkdir(parents=True, exist_ok=True)
            tmp = d.with_suffix(d.suffix + ".new")
            shutil.copy2(s, tmp)
            os.replace(tmp, d)          # 原子替换，避免半截文件
            written.append(rel)
    except Exception as e:
        # 回滚：把备份里的文件写回去
        for rel in written:
            b = bak / rel
            if b.exists():
                try:
                    shutil.copy2(b, ROOT / rel)
                except Exception:
                    pass
        return {"ok": False, "error": f"{type(e).__name__}: {e}",
                "rolled_back": len(written), "backup": str(bak)}

    new = _guess_version(src) or old
    (ROOT / "VERSION").write_text(new + "\n", encoding="utf-8")
    V.write_state({"version": new, "from": old, "at": _stamp(),
                   "updated": len(written), "backup": bak.name})
    return {"ok": True, "from": old, "to": new, "updated": len(written),
            "backup": str(bak), "changed": p["changed"], "added": p["added"]}


def rollback() -> dict:
    """把最近一次备份的文件写回去。"""
    bak = _latest_backup()
    if not bak:
        return {"ok": False, "error": "没有可回滚的备份"}
    man = bak / "_manifest.json"
    ver = ""
    if man.exists():
        try:
            ver = str(json.loads(man.read_text(encoding="utf-8")).get("version") or "")
        except Exception:
            pass
    n = 0
    for p in bak.rglob("*"):
        if not p.is_file() or p.name == "_manifest.json":
            continue
        rel = p.relative_to(bak).as_posix()
        dst = ROOT / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, dst)
        n += 1
    if ver:
        (ROOT / "VERSION").write_text(ver + "\n", encoding="utf-8")
    return {"ok": True, "restored": n, "version": ver, "from": bak.name}


# ------------------------------------------------------------------ CLI
def _color(s: str, code: str) -> str:
    if os.name == "nt" or not sys.stdout.isatty():
        return s
    return f"\033[{code}m{s}\033[0m"


def cmd_check() -> int:
    local = V.read_version()
    print(f"当前版本：{local}")
    try:
        rel = latest_release()
    except UpdateError as e:
        print(f"查不到远端：{e}")
        return 2
    if not rel:
        print("查不到远端（网络？仓库地址？）")
        return 2
    print(f"仓库：{repo_slug()}（{rel['source']}）")
    remote = rel["version"]
    if not remote:
        print("远端没有版本号信息，无法比较。可以直接 update 试试。")
        return 0
    print(f"远端版本：{remote}")
    if V.is_newer(remote, local):
        print(_color("有新版本可用。", "32") + "  python tools/update.py update")
        if rel["notes"]:
            print("\n更新说明：")
            for line in rel["notes"].splitlines()[:20]:
                print("  " + line)
        return 0
    print(_color("已是最新。", "32"))
    return 0


def cmd_update(dry: bool = False) -> int:
    local = V.read_version()
    try:
        rel = latest_release()
    except UpdateError as e:
        print(f"查不到远端，放弃更新：{e}")
        return 2
    if not rel:
        print("查不到远端，放弃更新。")
        return 2
    print(f"本地 {local} → 远端 {rel['tag']}，下载中…")
    with tempfile.TemporaryDirectory() as td:
        src = pathlib.Path(td) / "new"
        try:
            got = download_and_extract(rel["url"], src)
        except UpdateError as e:
            print(f"下载失败：{e}")
            return 2
        except Exception as e:
            print(f"下载/解压失败：{type(e).__name__}: {e}")
            return 2
        newv = got or rel["version"] or ""
        if newv and not V.is_newer(newv, local) and not dry:
            print(f"远端版本 {newv} 不比本地新，跳过。")
            return 0
        p = plan(src)
        print(f"可更新文件 {p['total']} 个：改动 {len(p['changed'])}，"
              f"新增 {len(p['added'])}，未变 {len(p['same'])}")
        for f in p["added"][:10]:
            print(f"  + {f}")
        for f in p["changed"][:15]:
            print(f"  ~ {f}")
        r = apply(src, p, dry=dry)
        if not r.get("ok"):
            print(f"更新失败：{r.get('error')}（已回滚 {r.get('rolled_back')} 个文件）")
            return 1
        if r.get("dry"):
            print("（dry-run，没有真的改文件）")
            return 0
        # 用下载前读到的 local 作「从哪个版本来的」，别信 apply() 的返回值：
        # 升级时跑的是「旧版 apply」，它的 old 可能已经被覆盖成新值（自举问题）。
        from_v = local or r.get("from") or "?"
        print(_color(f"更新完成：{from_v} → {r['to']}，改了 {r['updated']} 个文件", "32"))
        print(f"备份在：{r['backup']}")
        print("重启她才会生效：python tools/ctl.py restart")
    return 0


def cmd_rollback() -> int:
    r = rollback()
    if not r.get("ok"):
        print(r.get("error"))
        return 1
    print(f"已回滚 {r['restored']} 个文件，版本回到 {r['version'] or '(未知)'}")
    print("重启才生效：python tools/ctl.py restart")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    # 允许 `update.py --dry` 这种「选项直接跟在后面」的写法
    if argv and argv[0].startswith("-"):
        argv = ["update"] + list(argv)
    cmd = argv[0] if argv else "check"
    if cmd == "check":
        return cmd_check()
    if cmd == "update":
        return cmd_update(dry="--dry" in argv or "-n" in argv)
    if cmd == "rollback":
        return cmd_rollback()
    print(__doc__)
    return 0


if __name__ == "__main__":
    sys.exit(main())
