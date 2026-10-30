"""阿鱼控制台（Python 版菜单，避免 bat 的编码问题）。"""

from __future__ import annotations

import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from core.paths import ROOT
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import ctl


def clear() -> None:
    os.system("cls" if os.name == "nt" else "clear")


def ask(prompt: str) -> str:
    try:
        return input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        return "0"


def main() -> None:
    while True:
        clear()
        print("=" * 52)
        ctl.status()
        print("=" * 52)
        print("  1 启动      2 停止      3 重启")
        print("  4 叫醒她    5 让她睡    6 日志")
        print("  7 同步记忆  8 她的记忆  9 启动v2  0 退出")
        print("  P 实时面板（好感度/状态/阈值）")
        print("-" * 52)
        choice = ask("  选择: ")

        if choice == "1":
            ctl.start()
        elif choice == "2":
            ctl.stop()
        elif choice == "3":
            ctl.stop()
            ctl.start()
        elif choice == "9":
            ctl.stop()
            ctl.start(v2=True)
        elif choice in ("p", "P"):
            ctl.start_panel()
        elif choice == "4":
            ctl.wake()
        elif choice == "5":
            hhmm = ask("  几点醒（如 07:30）: ") or "08:00"
            ctl.go_sleep(hhmm)
        elif choice == "6":
            clear()
            ctl.show_log(30)
        elif choice == "7":
            ctl.sync_files()
        elif choice == "8":
            clear()
            ctl.show_memo()
        elif choice == "0":
            return
        else:
            continue
        ask("\n  回车继续…")


if __name__ == "__main__":
    main()
