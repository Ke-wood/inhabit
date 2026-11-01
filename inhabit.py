"""inhabit 的统一入口 —— 一个命令，几种活法。

    python inhabit.py                 打开菜单（第一次跑会自动初始化）
    python inhabit.py run [--v2]      主循环
    python inhabit.py panel [端口]    控制面板（默认 8788）
    python inhabit.py serve [端口]    只起收件服务（默认 8771）
    python inhabit.py update          检查并更新到最新版
    python inhabit.py update --dry    只看会改哪些文件
    python inhabit.py rollback        撤回上一次更新
    python inhabit.py version         看当前版本
    python inhabit.py doctor          环境体检
    python inhabit.py init [--preset X]  初始化人设与记忆

为什么要有这个文件
------------------
以前入口是散的：`run_wx.py` 起主循环、`tools/panel.py` 起面板、
`tools/ctl.py` 管启停、`tools/update.py` 更新。
对写代码的人无所谓，对使用者来说「一堆脚本」就是没做成产品。

这里把它们收成一个命令，并且保证：
- 第一次跑会引导初始化，不会让你对着满屏报错
- 更新前会自己检查，更新完提示重启
- 出错给人话，不给 traceback
"""

from __future__ import annotations

import os
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

# 打包后：模块在 _internal，可写目录在 exe 旁边
if getattr(sys, "frozen", False):
    sys.path.insert(0, str(pathlib.Path(sys.executable).resolve().parent))
    os.environ.setdefault("DAFEIYU_ROOT", str(pathlib.Path(sys.executable).resolve().parent))

# Windows 控制台默认 GBK，中文会炸
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


BANNER = r"""
  _       _           _     _
 (_)_ __ | |__   __ _(_)_ _| |_
 | | '_ \| '_ \ / _` | | ' \  _|
 | | | | | | | | (_| | | || \ |_
 |_|_| |_|_| |_|\__,_|_|\_,_/\__|
"""


def _p(msg: str) -> None:
    print(msg, flush=True)


def _need_setup() -> bool:
    """还没初始化：没有 config.json 或没有记忆目录。"""
    if not (HERE / "config.json").exists():
        return True
    try:
        from core import config
        prof = config.profile()
        mem = HERE / str(prof.get("memory") or "记忆")
        return not mem.is_dir()
    except Exception:
        return True


def _ensure_setup(preset: str = "") -> None:
    """第一次跑时引导初始化，而不是让命令直接失败。"""
    if not _need_setup():
        return
    _p("还没初始化过。先花十秒建一个人：")
    _p("")
    argv = [str(HERE / "tools" / "init_persona.py")]
    if preset:
        argv += ["--preset", preset]
    import subprocess
    rc = subprocess.call([sys.executable, *argv], cwd=str(HERE))
    if rc != 0:
        _p("\n初始化没完成。可以手动跑：python tools/init_persona.py")
        sys.exit(rc)


def cmd_version() -> int:
    from core import version as V
    from core import config
    v = V.read_version()
    try:
        prof = config.profile()
        who = config.load().get("active") or "?"
    except Exception:
        who = "?"
    _p(f"inhabit {v}" + (f"  ·  角色：{who}" if who != "?" else ""))
    _p(f"根目录：{HERE}")
    st = V.read_state()
    if st.get("version"):
        _p(f"上次更新：{st.get('from')} → {st.get('version')}（{st.get('at')}）")
    return 0


def cmd_update(argv: list[str]) -> int:
    from tools import update
    return update.main(argv)


def cmd_doctor() -> int:
    from core import doctor
    r = doctor.check()
    items = r.get("items") if isinstance(r, dict) else r
    bad = 0
    for it in (items or []):
        level = it.get("level") or ("ok" if it.get("ok") else "warn")
        mark = {"ok": "  ok ", "warn": " warn", "error": "ERROR"}.get(level, "  -- ")
        _p(f"{mark} {it.get('name')}：{it.get('detail')}")
        if it.get("hint"):
            _p(f"        → {it.get('hint')}")
        if level == "error":
            bad += 1
    if bad:
        _p(f"\n{bad} 项需要处理。")
        return 1
    _p("\n没发现问题。")
    return 0


def cmd_init(argv: list[str]) -> int:
    import subprocess
    argv = [str(HERE / "tools" / "init_persona.py")] + argv
    return subprocess.call([sys.executable, *argv], cwd=str(HERE))


def cmd_run(argv: list[str]) -> int:
    _ensure_setup()
    sys.argv = ["run_wx.py"] + argv
    import run_wx
    run_wx.main()
    return 0


def cmd_serve(argv: list[str]) -> int:
    _ensure_setup()
    from channels import wxhook
    port = int(argv[0]) if argv else 8771
    wxhook.serve(port)
    return 0


def cmd_panel(argv: list[str]) -> int:
    _ensure_setup()
    from tools import panel
    panel.serve(int(argv[0]) if argv else 8788)
    return 0


def cmd_menu() -> int:
    from tools import menu
    menu.main()
    return 0


COMMANDS = {
    "run": cmd_run,
    "serve": cmd_serve,
    "panel": cmd_panel,
    # 裸 `inhabit.py update` 要真的更新，不能只检查
    "update": lambda a: cmd_update(["update"] + list(a)),
    "check": lambda a: cmd_update(["check"] + list(a)),
    "rollback": lambda a: cmd_update(["rollback"]),
    "version": lambda a: cmd_version(),
    "doctor": lambda a: cmd_doctor(),
    "init": lambda a: cmd_init(a),
    "menu": lambda a: cmd_menu(),
    "help": lambda a: _help(),
}


def _help() -> int:
    _p(BANNER)
    _p(__doc__.split("为什么要有这个文件")[0].strip())
    _p("")
    _p("更多文档：README.md / INSTALL.md / docs/")
    return 0


def main() -> int:
    argv = sys.argv[1:]
    cmd = (argv[0] or "").lower() if argv else ""
    rest = argv[1:]

    if not cmd:
        # 无参数：没初始化就先问你，初始化了就进菜单
        if _need_setup():
            _p(BANNER)
            _ensure_setup()
            return 0
        return cmd_menu()

    fn = COMMANDS.get(cmd)
    if fn is None:
        _p(f"没有这个命令：{cmd}")
        return _help()
    try:
        return int(fn(rest) or 0)
    except KeyboardInterrupt:
        _p("\n已停止。")
        return 130
    except SystemExit:
        raise
    except Exception as e:
        _p(f"出错了：{type(e).__name__}: {e}")
        _p("加 --debug 看详细栈；或先 python inhabit.py doctor 体检。")
        if "--debug" in argv:
            raise
        return 1


if __name__ == "__main__":
    sys.exit(main())
