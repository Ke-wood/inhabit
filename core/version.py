"""版本号，以及「什么算程序文件」—— 更新模块靠这里决定谁能被覆盖。

为什么要单独一个模块
--------------------
更新最容易出的不是技术问题，是**把用户的东西覆盖掉**：
记忆、人设、config、日志，全都在同一个目录里。

所以规则必须是**白名单**：明确列出哪些是程序文件，其余一律不碰。
而不是黑名单（「除了 data/ 和记忆/ 都覆盖」）—— 黑名单漏一项就是事故。
"""

from __future__ import annotations

import json
import pathlib
import re

from core.paths import ROOT

VERSION_FILE = ROOT / "VERSION"
FALLBACK_VERSION = "0.0.0"


# ------------------------------------------------------------------ 版本号
def read_version() -> str:
    """读 VERSION 文件；没有就退回 config 里的值。"""
    try:
        v = VERSION_FILE.read_text(encoding="utf-8").strip()
        if v:
            return v.split()[0]
    except Exception:
        pass
    try:
        from core import config
        return str((config.load() or {}).get("version") or FALLBACK_VERSION)
    except Exception:
        return FALLBACK_VERSION


_VERSION_RE = re.compile(r"v?(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:[-.+]?([0-9A-Za-z.\-]+))?")


def parse(v: str) -> tuple[int, int, int, str]:
    """把 `v1.2.3-beta` 拆成 (1, 2, 3, 'beta')。解析不了就当 (0,0,0,'')。"""
    m = _VERSION_RE.match(str(v or "").strip())
    if not m:
        return (0, 0, 0, "")
    major = int(m.group(1) or 0)
    minor = int(m.group(2) or 0)
    patch = int(m.group(3) or 0)
    pre = (m.group(4) or "").strip()
    return (major, minor, patch, pre)


def compare(a: str, b: str) -> int:
    """a > b 返回 1，相等 0，小于 -1。预发布版（带后缀）小于同号正式版。"""
    pa, pb = parse(a), parse(b)
    if pa[:3] != pb[:3]:
        return 1 if pa[:3] > pb[:3] else -1
    # 同号：无后缀 > 有后缀（1.2.3 > 1.2.3-beta）
    if pa[3] == pb[3]:
        return 0
    if not pa[3]:
        return 1
    if not pb[3]:
        return -1
    return 1 if pa[3] > pb[3] else -1


def is_newer(remote: str, local: str) -> bool:
    return compare(remote, local) > 0


# ------------------------------------------------------------------ 文件分类
# 这些目录整体算程序代码
PROGRAM_DIRS = {
    "core", "channels", "bili", "memory", "tools", "web",
    "docs", "tests", "memory_template", "presets",
}

# 根目录下按名字认的程序文件
PROGRAM_ROOT_FILES = {
    "run_wx.py", "run_demo.py", "shiyu.py", "inhabit.py",
    "README.md", "INSTALL.md", "CHANGELOG.md", "THIRD_PARTY.md", "LICENSE",
    "requirements.txt", "config.example.json", "VERSION",
    "start.bat", "start.sh", "面板.bat", ".gitignore",
}

# 绝对不碰：用户数据
PROTECTED_TOP = {"data", ".git", "__pycache__", ".venv", "venv", "node_modules"}
PROTECTED_ROOT_FILES = {"config.json", ".env", "hooks", "local.py"}

# 这些扩展名一律不参与更新（二进制/缓存）
SKIP_SUFFIX = {
    ".pyc", ".pyo", ".db", ".db-wal", ".db-shm", ".jsonl", ".log",
    ".dll", ".exe", ".so", ".dylib", ".zip", ".tar", ".gz",
    ".png", ".jpg", ".jpeg", ".webp", ".gif", ".mp3", ".wav", ".mp4",
}


def configured_memory_dirs() -> set[str]:
    """从 config 里读出所有角色的人设目录名（这些必须保护）。"""
    out = {"记忆"}          # 默认名，永远保护
    try:
        from core import config
        cfg = config.load() or {}
        for prof in (cfg.get("profiles") or {}).values():
            mem = str((prof or {}).get("memory") or "").strip()
            if mem:
                out.add(mem.split("/")[0].split("\\")[0])
    except Exception:
        pass
    return {x for x in out if x}


def classify(rel: str, *, memory_dirs: set[str] | None = None) -> str:
    """判断一个相对路径属于哪一类。

    返回 "program"（可更新） / "protected"（用户数据） / "skip"（忽略）。
    """
    rel = str(rel).replace("\\", "/").lstrip("./")
    if not rel:
        return "skip"
    parts = rel.split("/")
    top = parts[0]

    if top in PROTECTED_TOP:
        return "protected"
    if top in (memory_dirs if memory_dirs is not None else configured_memory_dirs()):
        return "protected"
    if top in PROTECTED_ROOT_FILES:
        return "protected"

    path = pathlib.Path(rel)
    if path.suffix.lower() in SKIP_SUFFIX:
        return "skip"
    if any(p.startswith(".") and p not in (".gitignore",) for p in parts[:-1]):
        return "skip"

    if len(parts) == 1:
        return "program" if top in PROGRAM_ROOT_FILES else "protected"

    if top in PROGRAM_DIRS:
        # 预设内部的人设文本属于程序内容（会被 --preset 复制出去）
        return "program"

    return "protected"


def program_files(src: pathlib.Path, *, memory_dirs: set[str] | None = None) -> list[str]:
    """列出一个目录里所有「程序文件」的相对路径。"""
    mem = memory_dirs if memory_dirs is not None else configured_memory_dirs()
    out: list[str] = []
    for p in pathlib.Path(src).rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(src).as_posix()
        if classify(rel, memory_dirs=mem) == "program":
            out.append(rel)
    return sorted(out)


# ------------------------------------------------------------------ 安装信息
STATE_FILE = ROOT / "data" / "update_state.json"


def read_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def write_state(d: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
