"""角色运行探测测试：又快又准。

    python tests/test_running_profiles.py

这个函数被面板每次刷新都调用一次。它原来用 connect + timeout=0.4 判断
端口有没有人听 —— 角色没启动时要等满超时才返回，两个角色就是 800ms，
页面看起来就是「卡住了 / 连不上」。

验收：
1. 判定结果正确（在听的 True，没在听的 False）
2. 足够快（N 个角色都在空转时，总耗时不能是 N × 400ms）
3. 有 SO_REUSEADDR 陷阱时仍判对（Windows 上会误判成"没在跑"）
"""

from __future__ import annotations

import os
import pathlib
import socket
import sys
import tempfile
import threading
import time

TMP = pathlib.Path(tempfile.mkdtemp(prefix="inhabit-runprof-"))
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


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _listen(port: int):
    """起一个真的在听的 socket，返回 (sock, 关闭函数)。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", port))
    s.listen(1)
    return s


def test_detects_listening():
    from core import config
    p = _free_port()
    s = _listen(p)
    try:
        assert config._port_listening(p) is True, f":{p} 在听，却判成没在跑"
    finally:
        s.close()


def test_detects_closed():
    from core import config
    p = _free_port()
    assert config._port_listening(p) is False, f":{p} 没人听，却判成在跑"


def test_closed_port_is_fast():
    """核心：没人听时必须立刻返回，不能等超时。

    老实现是 connect(..., timeout=0.4)：端口没人听要等满 400ms。
    新实现 bind 探测，应该在几毫秒内。
    """
    from core import config
    ports = [_free_port() for _ in range(5)]
    t = time.perf_counter()
    for p in ports:
        config._port_listening(p)
    ms = (time.perf_counter() - t) * 1000
    # 5 个空端口，老实现要 2000ms；给 200ms 已经很宽松
    assert ms < 200, f"5 个空端口花了 {ms:.0f}ms，说明还在等超时"


def test_reuseaddr_trap():
    """Windows 的 SO_REUSEADDR 行为：设了它，bind 一个在听的本机端口也能成功。

    如果实现里顺手加了 SO_REUSEADDR，就会永远 bind 得上、永远判成「没在跑」，
    面板于是永远显示「没启动」—— 这个 bug 不会报错，只会让人反复点启动。
    这里显式证明：有端口在听时，正确实现必须判 True。
    """
    from core import config
    p = _free_port()
    s = _listen(p)
    try:
        # 先确认这个端口真的在听
        c = socket.create_connection(("127.0.0.1", p), timeout=1)
        c.close()
        assert config._port_listening(p) is True, "在听的端口被误判成没在跑"
    finally:
        s.close()


def test_running_profiles_shape():
    from core import config
    r = config.running_profiles()
    names = list((config.load().get("profiles") or {}).keys())
    assert set(r) == set(names), f"角色对不上：{r} vs {names}"
    assert all(isinstance(v, bool) for v in r.values()), r


def test_running_profiles_fast():
    """整个函数（面板每次都调）要够快。"""
    from core import config
    config.running_profiles()          # 预热
    t = time.perf_counter()
    for _ in range(5):
        config.running_profiles()
    ms = (time.perf_counter() - t) * 1000 / 5
    assert ms < 100, f"running_profiles 平均 {ms:.0f}ms，太慢"


def test_no_timeout_constant():
    """别再退回 connect + timeout 的老写法（只看真正的代码行，不看注释/docstring）。"""
    import ast
    import inspect
    from core import config
    src = inspect.getsource(config.running_profiles) + \
        inspect.getsource(config._port_listening)
    tree = ast.parse(src)

    def strip_docstrings(node):
        for n in ast.walk(node):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef,
                              ast.Module)):
                body = n.body
                if (body and isinstance(body[0], ast.Expr)
                        and isinstance(body[0].value, ast.Constant)
                        and isinstance(body[0].value.value, str)):
                    body.pop(0)

    strip_docstrings(tree)
    code = ast.unparse(tree)
    assert "create_connection" not in code, "又用回 create_connection 了（会等超时）"


def main():
    check("在听的端口判 True", test_detects_listening)
    check("没听的端口判 False", test_detects_closed)
    check("空端口秒回（不等超时）", test_closed_port_is_fast)
    check("SO_REUSEADDR 陷阱下仍判对", test_reuseaddr_trap)
    check("返回所有角色且是布尔", test_running_profiles_shape)
    check("整体够快", test_running_profiles_fast)
    check("没退回 connect 老写法", test_no_timeout_constant)
    print(f"\n{ok} 通过 / {fail} 失败")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
