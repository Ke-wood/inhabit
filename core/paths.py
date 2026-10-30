"""项目根目录 —— 只在这里算一次。

开发时：仓库根目录（core 的上一级）
打包后：exe 所在目录（data / 记忆 / persona 都跟着 exe 走）
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def project_root() -> Path:
    env = os.environ.get("DAFEIYU_ROOT") or os.environ.get("FEIYU_ROOT")
    if env:
        return Path(env).resolve()
    if getattr(sys, "frozen", False):          # PyInstaller
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


ROOT = project_root()
