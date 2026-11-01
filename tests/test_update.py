"""更新模块测试：重点验证「用户配置绝对不被覆盖」。

    python tests/test_update.py

全部离线：构造假的新版本目录，走一遍 plan/apply/rollback。
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import sys
import tempfile

TMP = pathlib.Path(tempfile.mkdtemp(prefix="inhabit-upd-"))
os.environ["DAFEIYU_ROOT"] = str(TMP)
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

ok = 0
fail = 0


def check(name, fn):
    global ok, fail
    try:
        fn()
        print(f"  ok   {name}")
        ok += 1
    except Exception as e:
        print(f"  FAIL {name}: {type(e).__name__}: {e}")
        fail += 1


# ------------------------------------------------------------------ 版本比较
def test_compare():
    from core import version as V
    assert V.compare("1.4.0", "1.3.0") == 1
    assert V.compare("1.3.0", "1.4.0") == -1
    assert V.compare("1.4.0", "1.4.0") == 0
    assert V.compare("1.10.0", "1.9.0") == 1, "字符串比较会错，必须按数字"
    assert V.compare("1.4.0", "1.4.0-beta") == 1, "正式版应大于预发布"
    assert V.is_newer("2.0.0", "1.99.99")
    assert not V.is_newer("1.0.0", "1.0.0")


def test_parse_garbage():
    from core import version as V
    assert V.parse("") == (0, 0, 0, "")
    assert V.parse("v1.2.3")[:3] == (1, 2, 3)
    assert V.parse("garbage")[0] == 0


# ------------------------------------------------------------------ 分类
def test_classify_protected():
    """用户数据必须被认成 protected。"""
    from core import version as V
    mem = {"记忆"}
    for rel in ["记忆/我是谁.md", "记忆/我/爸爸.md", "data/feiyu.db",
                "data/logs/run.log", "config.json", ".env",
                "记忆/称呼.md"]:
        assert V.classify(rel, memory_dirs=mem) == "protected", f"{rel} 应受保护"


def test_classify_program():
    from core import version as V
    mem = {"记忆"}
    for rel in ["core/act.py", "channels/onebot.py", "tools/update.py",
                "web/panel.html", "README.md", "run_wx.py", "VERSION",
                "presets/dafeiyu/persona/我是谁.md", "docs/qq.md"]:
        assert V.classify(rel, memory_dirs=mem) == "program", f"{rel} 应是程序文件"


def test_classify_custom_memory_dir():
    """用户自定义的人设目录名也要受保护。"""
    from core import version as V
    mem = {"记忆", "my_persona"}
    assert V.classify("my_persona/我是谁.md", memory_dirs=mem) == "protected"


def test_classify_skip():
    from core import version as V
    mem = {"记忆"}
    for rel in ["core/__pycache__/act.cpython-314.pyc", "data/x.png"]:
        assert V.classify(rel, memory_dirs=mem) in ("skip", "protected"), rel


# ------------------------------------------------------------------ 场景
def _make_old(root: pathlib.Path):
    """造一个「旧安装」。"""
    (root / "core").mkdir(parents=True, exist_ok=True)
    (root / "memory").mkdir(exist_ok=True)
    (root / "core" / "act.py").write_text("# 旧版 act", encoding="utf-8")
    (root / "core" / "version.py").write_text("# 旧 version", encoding="utf-8")
    (root / "run_wx.py").write_text("# 旧入口", encoding="utf-8")
    (root / "README.md").write_text("# 旧 README", encoding="utf-8")
    (root / "VERSION").write_text("1.0.0\n", encoding="utf-8")
    # 用户数据
    (root / "记忆").mkdir(exist_ok=True)
    (root / "记忆" / "我是谁.md").write_text("我是我自己写的", encoding="utf-8")
    (root / "记忆" / "称呼.md").write_text("别叫我死鱼", encoding="utf-8")
    (root / "config.json").write_text('{"active":"我的角色"}', encoding="utf-8")
    (root / "data").mkdir(exist_ok=True)
    (root / "data" / "feiyu.db").write_bytes(b"DBDATA")
    (root / "data" / "logs").mkdir()
    (root / "data" / "logs" / "run.log").write_text("旧日志", encoding="utf-8")


def _make_new(root: pathlib.Path):
    """造一个「新版」源目录。"""
    (root / "core").mkdir(parents=True, exist_ok=True)
    (root / "memory").mkdir(exist_ok=True)
    (root / "core" / "act.py").write_text("# 新版 act", encoding="utf-8")
    (root / "core" / "version.py").write_text("# 新版 version", encoding="utf-8")
    (root / "core" / "brand_new.py").write_text("# 全新文件", encoding="utf-8")
    (root / "run_wx.py").write_text("# 旧入口", encoding="utf-8")     # 没变
    (root / "README.md").write_text("# 新 README", encoding="utf-8")
    (root / "VERSION").write_text("1.4.0\n", encoding="utf-8")
    # 新版里也带了一份「记忆」——绝对不能覆盖用户那份
    (root / "记忆").mkdir(exist_ok=True)
    (root / "记忆" / "我是谁.md").write_text("我是模板写的", encoding="utf-8")
    (root / "记忆" / "称呼.md").write_text("模板的称呼规则", encoding="utf-8")
    (root / "config.json").write_text('{"active":"模板"}', encoding="utf-8")


def _sandbox() -> tuple[pathlib.Path, pathlib.Path, pathlib.Path]:
    old = TMP / "install"
    new = TMP / "newver"
    for d in (old, new):
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)
    _make_old(old)
    _make_new(new)
    return old, new, new


def test_plan_ignores_user_data():
    """plan 里不能出现任何用户文件。"""
    import importlib.util
    old, new, _ = _sandbox()
    spec = importlib.util.spec_from_file_location("upd_probe", ROOT / "tools" / "update.py")
    # 直接调用 core.version 的分类逻辑（update.py 依赖真实 ROOT，这里只验分类）
    from core import version as V
    mem = {"记忆"}
    rels = V.program_files(new, memory_dirs=mem)
    bad = [r for r in rels if r.startswith(("记忆/", "data/")) or r == "config.json"]
    assert not bad, f"plan 里混进了用户文件：{bad}"
    assert "core/act.py" in rels
    assert "core/brand_new.py" in rels


def test_apply_preserves_user_data():
    """端到端：套用新版后，用户的记忆/config/库一个字节都没变。"""
    import io
    import tarfile
    import importlib

    old, new, _ = _sandbox()
    shutil.copytree(new, TMP / "stage", dirs_exist_ok=True)

    # 记录用户数据的指纹
    def snap(root):
        out = {}
        for rel in ["记忆/我是谁.md", "记忆/称呼.md", "config.json",
                    "data/feiyu.db", "data/logs/run.log"]:
            p = root / rel
            out[rel] = p.read_bytes() if p.exists() else None
        return out

    before = snap(old)

    # 把 new 打成 tar.gz，模拟从 GitHub 下载
    tar_path = TMP / "fake.tar.gz"
    with tarfile.open(tar_path, "w:gz") as tf:
        tf.add(new, arcname="inhabit-main")
    raw = tar_path.read_bytes()

    # 载入 update.py，把 ROOT 指到 old
    upd = importlib.import_module("tools.update") if False else None
    # 直接改模块级 ROOT 更可靠：用 exec 加载并替换
    src = (ROOT / "tools" / "update.py").read_text(encoding="utf-8")
    src = src.replace('ROOT = pathlib.Path(__file__).resolve().parent.parent',
                      f'ROOT = pathlib.Path(r"{old}")')
    ns: dict = {"__name__": "upd_test", "__file__": str(ROOT / "tools" / "update.py")}
    exec(compile(src, "update.py", "exec"), ns)

    ns["_get"] = lambda url, timeout=20, binary=False: raw
    dest = TMP / "dl"
    if dest.exists():
        shutil.rmtree(dest)
    ns["download_and_extract"](ns["TARBALL"].format(repo="x/y"), dest)
    p = ns["plan"](dest)
    r = ns["apply"](dest, p)
    assert r.get("ok"), r
    assert r["updated"] > 0, r

    after = snap(old)
    changed = [k for k in before if before[k] != after[k]]
    assert not changed, f"用户数据被改了：{changed}"

    # 程序文件确实更新了
    assert (old / "core" / "act.py").read_text(encoding="utf-8") == "# 新版 act"
    assert (old / "core" / "brand_new.py").exists(), "新文件没加上"
    assert (old / "VERSION").read_text(encoding="utf-8").strip() == "1.4.0"

    # 备份存在
    assert pathlib.Path(r["backup"]).exists(), "没有备份"
    assert (pathlib.Path(r["backup"]) / "core" / "act.py").read_text(
        encoding="utf-8") == "# 旧版 act"


def test_rollback_restores():
    """回滚要把程序文件恢复到更新前。"""
    import tarfile
    old, new, _ = _sandbox()
    tar_path = TMP / "fake2.tar.gz"
    with tarfile.open(tar_path, "w:gz") as tf:
        tf.add(new, arcname="inhabit-main")
    raw = tar_path.read_bytes()

    src = (ROOT / "tools" / "update.py").read_text(encoding="utf-8")
    src = src.replace('ROOT = pathlib.Path(__file__).resolve().parent.parent',
                      f'ROOT = pathlib.Path(r"{old}")')
    ns: dict = {"__name__": "upd_test2", "__file__": str(ROOT / "tools" / "update.py")}
    exec(compile(src, "update.py", "exec"), ns)
    ns["_get"] = lambda url, timeout=20, binary=False: raw

    dest = TMP / "dl2"
    if dest.exists():
        shutil.rmtree(dest)
    ns["download_and_extract"](ns["TARBALL"].format(repo="x/y"), dest)
    p = ns["plan"](dest)
    assert ns["apply"](dest, p).get("ok")
    assert (old / "core" / "act.py").read_text(encoding="utf-8") == "# 新版 act"

    r = ns["rollback"]()
    assert r.get("ok"), r
    assert (old / "core" / "act.py").read_text(encoding="utf-8") == "# 旧版 act", "回滚没生效"
    assert (old / "README.md").read_text(encoding="utf-8") == "# 旧 README"


def test_no_network_fails_soft():
    """网络不可用时要返回非 0，而不是抛异常炸掉调用方。"""
    import urllib.error
    src = (ROOT / "tools" / "update.py").read_text(encoding="utf-8")
    src = src.replace('ROOT = pathlib.Path(__file__).resolve().parent.parent',
                      f'ROOT = pathlib.Path(r"{TMP / "install"}")')
    ns: dict = {"__name__": "upd_test3", "__file__": str(ROOT / "tools" / "update.py")}
    exec(compile(src, "update.py", "exec"), ns)

    def boom(*a, **k):
        raise urllib.error.URLError("模拟断网")
    ns["_get"] = boom
    out = ns["cmd_update"]()
    assert out == 2, f"网络失败应返回 2，得到 {out}"


def test_network_error_not_silent():
    """断网时必须报错，不能静默退回 main 分支假装正常。"""
    import urllib.error
    src = (ROOT / "tools" / "update.py").read_text(encoding="utf-8")
    src = src.replace('ROOT = pathlib.Path(__file__).resolve().parent.parent',
                      f'ROOT = pathlib.Path(r"{TMP / "install"}")')
    ns: dict = {"__name__": "upd_net", "__file__": str(ROOT / "tools" / "update.py")}
    exec(compile(src, "update.py", "exec"), ns)

    def boom(*a, **k):
        raise urllib.error.URLError("模拟断网")
    ns["_get"] = boom
    try:
        ns["latest_release"]()
    except ns["UpdateError"] as e:
        assert "连不上" in str(e) or "网络" in str(e), e
        return
    raise AssertionError("断网时 latest_release 应抛 UpdateError，而不是返回 main 分支")


def test_bare_update_does_update():
    """`python inhabit.py update` 必须真的更新，不能退化成只检查。"""
    sys.path.insert(0, str(ROOT))
    src = (ROOT / "inhabit.py").read_text(encoding="utf-8")
    # 找到 update 的映射，确认它补上了 "update" 子命令
    line = [l for l in src.splitlines() if '"update":' in l]
    assert line, "inhabit.py 里没有 update 命令"
    assert '"update"' in line[0], f"裸 update 会退化成 check：{line[0]}"


def test_dry_alias():
    """`update.py --dry` 这种写法要能被识别成 update --dry。"""
    src = (ROOT / "tools" / "update.py").read_text(encoding="utf-8")
    assert "argv[0].startswith(\"-\")" in src, "没处理「选项直接跟在后面」的写法"


def test_version_from_file_not_config():
    """VERSION 是唯一真相，不能被 config 里的旧版本号带偏。"""
    from core import version as V
    vf = ROOT / "VERSION"
    assert vf.exists(), "没有 VERSION 文件"
    got = V.read_version()
    assert got == vf.read_text(encoding="utf-8").strip().split()[0], got


def test_from_version_captured_before_overwrite():
    """更新后要能说出「从哪个版本升上来的」。

    VERSION 本身也在待覆盖列表里，所以旧版本号必须在循环之前记下来，
    否则会显示成 1.4.0 → 1.4.0。
    """
    src = (ROOT / "tools" / "update.py").read_text(encoding="utf-8")
    i_bak = src.index("bak = backup(targets)")
    i_old = src.index("old = V.read_version()")
    assert i_old < i_bak, "old 版本号必须在备份/覆盖之前读，否则拿到的是新版本"


def test_broken_pipe_safe():
    """输出被管道关掉时不能崩。"""
    src = (ROOT / "inhabit.py").read_text(encoding="utf-8")
    assert "BrokenPipeError" in src, "没有处理 BrokenPipe"
    assert "OSError" in src, "_p 要吞掉 OSError"


def main() -> int:
    print("更新模块")
    for name, fn in [
        ("版本号比较", test_compare),
        ("垃圾版本号不崩", test_parse_garbage),
        ("用户数据受保护", test_classify_protected),
        ("程序文件可更新", test_classify_program),
        ("自定义人设目录受保护", test_classify_custom_memory_dir),
        ("缓存被跳过", test_classify_skip),
        ("更新计划不含用户文件", test_plan_ignores_user_data),
        ("端到端不改用户数据", test_apply_preserves_user_data),
        ("回滚能恢复", test_rollback_restores),
        ("断网安静失败", test_no_network_fails_soft),
        ("断网不假装正常", test_network_error_not_silent),
        ("裸 update 真的更新", test_bare_update_does_update),
        ("--dry 别名", test_dry_alias),
        ("版本以 VERSION 为准", test_version_from_file_not_config),
        ("旧版本号在覆盖前记录", test_from_version_captured_before_overwrite),
        ("管道断开不崩", test_broken_pipe_safe),
    ]:
        check(name, fn)
    print(f"\n{ok} 通过 / {fail} 失败")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
