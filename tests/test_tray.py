"""托盘测试：面板要能被用户关掉，且托盘坏了不能把面板带崩。

    python tests/test_tray.py

为什么测这个
------------
面板是后台进程，关浏览器不等于关面板。没有退出入口时，用户只能去
任务管理器杀 pythonw —— 这既不安全也劝退。托盘是最符合直觉的出口。

但托盘是「锦上添花」，它挂了绝不能影响面板本身。所以这里锁住三件事：
1. 没装 PySide6 时，available() 返回 False 而不是抛异常
2. serve() 在没有托盘时仍能起服务（走降级路径）
3. serve() 的托盘参数能在运行时关掉（--no-tray / tray=False）
"""

from __future__ import annotations

import ast
import inspect
import os
import pathlib
import sys

TMP = pathlib.Path(__file__).with_name("_tray_tmp")
os.environ.setdefault("PYTHONUTF8", "1")
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


def test_tray_available_no_throw():
    """available() 只能返回布尔，不能抛。"""
    from tools import tray
    v = tray.available()
    assert isinstance(v, bool), f"返回了 {type(v)}"


def test_tray_false_when_no_qt():
    """模拟没装 Qt：available() 必须 False，且不抛。"""
    import builtins
    from tools import tray

    real = builtins.__import__

    def fake(name, *a, **kw):
        if name.startswith("PySide6"):
            raise ImportError("模拟没装 PySide6")
        return real(name, *a, **kw)

    builtins.__import__ = fake
    try:
        assert tray.available() is False, "没装 Qt 还说可用"
    finally:
        builtins.__import__ = real


def test_serve_accepts_tray_flag():
    """serve 必须能显式关掉托盘（无图形环境 / 服务器上跑）。"""
    from tools import panel
    sig = inspect.signature(panel.serve)
    assert "tray" in sig.parameters, "serve 没有 tray 参数"


def test_serve_default_keeps_port():
    """加了托盘参数，默认端口不能被改坏。"""
    from tools import panel
    sig = inspect.signature(panel.serve)
    assert sig.parameters["port"].default == 8788


def test_headless_fallback_exists():
    from tools import tray
    assert hasattr(tray, "serve_headless"), "没有无托盘降级路径"
    assert callable(tray.serve_headless)


def test_panel_never_imports_qt_at_module_level():
    """面板主模块不能在顶层 import PySide6。

    否则没装 Qt 的机器连面板都 import 不了 —— 托盘本该是可选的，
    不能变成硬依赖。
    """
    src = (ROOT / "tools" / "panel.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in tree.body:                     # 只看模块顶层
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = ([a.name for a in node.names] if isinstance(node, ast.Import)
                     else [node.module or ""])
            for n in names:
                assert not (n or "").startswith("PySide6"), \
                    f"panel.py 顶层 import 了 {n}，会让没装 Qt 的机器起不来"


def test_tray_quit_action_exists():
    """托盘菜单里必须有「退出面板」—— 这是本次改动的目的。"""
    from tools import tray
    src = inspect.getsource(tray)
    assert "退出面板" in src, "托盘没有退出入口"
    assert "打开面板" in src, "托盘没有打开入口"


def test_tray_module_import_is_cheap():
    """tools.tray 顶层不能 import PySide6（同理，保持可选）。"""
    src = (ROOT / "tools" / "tray.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = ([a.name for a in node.names] if isinstance(node, ast.Import)
                     else [node.module or ""])
            for n in names:
                assert not (n or "").startswith("PySide6"), \
                    f"tray.py 顶层 import 了 {n}"


def main():
    check("available() 不抛", test_tray_available_no_throw)
    check("没装 Qt 时返回 False", test_tray_false_when_no_qt)
    check("serve 能关托盘", test_serve_accepts_tray_flag)
    check("默认端口没被改坏", test_serve_default_keeps_port)
    check("有无托盘降级路径", test_headless_fallback_exists)
    check("panel 顶层不 import Qt", test_panel_never_imports_qt_at_module_level)
    check("托盘有打开/退出", test_tray_quit_action_exists)
    check("tray 顶层不 import Qt", test_tray_module_import_is_cheap)
    print(f"\n{ok} 通过 / {fail} 失败")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
