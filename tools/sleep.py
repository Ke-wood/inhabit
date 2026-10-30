"""睡眠控制。

    python tools/sleep.py status     # 她现在是睡是醒
    python tools/sleep.py sleep 07:30 [理由]
    python tools/sleep.py wake       # 人工叫醒
"""

from __future__ import annotations

import pathlib
import sys
from datetime import datetime, timedelta

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import sleep
from memory import store


def main() -> None:
    con = store.init_db()
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"

    if cmd == "status":
        st = store.get_state(con)
        now = datetime.now()
        if sleep.asleep(con, now):
            u = sleep.until(con)
            left = (u - now).total_seconds() / 60 if u else 0
            print(f"睡着。{u.strftime('%m-%d %H:%M') if u else '?'} 醒，还有 {left:.0f} 分钟。")
            print(f"睡前理由：{st.get('wake_reason') or '没说'}")
        else:
            print("醒着。")
            print(f"上次醒：{st.get('sleep_until') or '—'}")
    elif cmd == "sleep":
        hhmm = sys.argv[2] if len(sys.argv) > 2 else "08:00"
        reason = sys.argv[3] if len(sys.argv) > 3 else "手动安排"
        hh, mm = hhmm.split(":")
        now = datetime.now()
        wake_at = now.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
        if wake_at <= now:
            wake_at += timedelta(days=1)
        sleep.go_to_sleep(con, wake_at, reason)
        print(f"让她睡了，{wake_at.strftime('%m-%d %H:%M')} 醒。")
    elif cmd == "wake":
        info = sleep.wake(con, "人工叫醒")
        print(f"叫醒了。睡着期间有 {info['count']} 条消息：")
        for m in info["messages"][-10:]:
            print(f"  {m['actor_name']}：{m['text'][:30]}")
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
