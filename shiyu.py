"""阿鱼的入口 —— 一个 exe，几种活法。

    阿鱼.exe                打开菜单
    阿鱼.exe run [选项]      主循环（会被菜单/ctl 调起）
    阿鱼.exe serve [端口]    收件服务（默认 8771）
    阿鱼.exe panel [端口]    控制面板（默认 8788）
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

# 打包后：模块在 _internal，可写目录在 exe 旁边
if getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(sys.executable).resolve().parent))
    os.environ.setdefault("FEIYU_ROOT", str(Path(sys.executable).resolve().parent))


def main() -> None:
    argv = sys.argv[1:]
    cmd = argv[0].lower() if argv else ""

    if cmd == "run":
        sys.argv = ["run_wx.py"] + argv[1:]
        import run_wx
        run_wx.main()
    elif cmd == "serve":
        from channels import wxhook
        wxhook.serve(int(argv[1]) if len(argv) > 1 else 8771)
    elif cmd == "panel":
        from tools import panel
        panel.serve(int(argv[1]) if len(argv) > 1 else 8788)
    else:
        from tools import menu
        menu.main()


if __name__ == "__main__":
    main()
