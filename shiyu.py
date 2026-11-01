"""兼容入口。真正的统一入口是 inhabit.py。

保留这个文件为了两件事：
1. 已经打包好的 exe 名字叫这个，改了会让人困惑
2. PyInstaller 的 spec 指向它

    python shiyu.py            → 等同 python inhabit.py
    python shiyu.py --help     → 看所有命令
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

if __name__ == "__main__":
    import inhabit
    sys.exit(inhabit.main())
