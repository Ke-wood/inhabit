"""阿鱼的开关。

    python tools/ctl.py start      # 起收件服务器 + 主循环
    python tools/ctl.py stop       # 全停
    python tools/ctl.py status     # 看状态
    python tools/ctl.py restart
    python tools/ctl.py wake       # 叫醒（睡眠中）
    python tools/ctl.py sleep 07:30
    python tools/ctl.py log        # 看最近日志
"""

from __future__ import annotations

import json
import os
import pathlib
import socket
import subprocess
import sys
import time
from datetime import datetime, timedelta

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from core.paths import ROOT
sys.path.insert(0, str(ROOT))

# Windows 控制台默认 GBK，输出会炸；强制 UTF-8
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

DATA = ROOT / "data"
PIDS = DATA / "pids"
LOGS = DATA / "logs"
PY = sys.executable
DETACHED = 0x00000008 | 0x00000200   # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP


def _pid_file(name: str) -> pathlib.Path:
    return PIDS / f"{name}.pid"


def _alive(pid: int) -> bool:
    try:
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                             capture_output=True, text=True, timeout=10).stdout
        return str(pid) in out
    except Exception:
        return False


def _read_pid(name: str) -> int | None:
    p = _pid_file(name)
    if not p.exists():
        return None
    try:
        pid = int(p.read_text().strip())
    except ValueError:
        return None
    return pid if _alive(pid) else None


def _port_open(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.6)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _spawn(name: str, args: list[str]) -> int:
    LOGS.mkdir(parents=True, exist_ok=True)
    PIDS.mkdir(parents=True, exist_ok=True)
    logfile = (LOGS / f"{name}.log").open("a", encoding="utf-8")
    logfile.write(f"\n===== {datetime.now():%Y-%m-%d %H:%M:%S} 启动 =====\n")
    logfile.flush()
    # 子进程必须用 UTF-8 写 stdout，否则 GBK 编不了的字符会让它崩掉
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONUTF8"] = "1"
    proc = subprocess.Popen(args, cwd=str(ROOT), stdout=logfile, stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL, creationflags=DETACHED, env=env)
    _pid_file(name).write_text(str(proc.pid))
    return proc.pid


def _kill(name: str) -> bool:
    pid = _read_pid(name)
    if not pid:
        _pid_file(name).unlink(missing_ok=True)
        return False
    subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)
    _pid_file(name).unlink(missing_ok=True)
    return True


FROZEN = bool(getattr(sys, "frozen", False))   # 打包成 exe 了吗


def _serve_args(port: int = 8771) -> list[str]:
    if FROZEN:
        return [sys.executable, "serve", str(port)]
    return [PY, str(ROOT / "channels" / "wxhook.py"), "serve", str(port)]


def _run_args(v2: bool) -> list[str]:
    if FROZEN:
        args = [sys.executable, "run", "--brain", "gateway"]
    else:
        args = [PY, str(ROOT / "run_wx.py"), "--brain", "gateway"]
    if v2:
        args.append("--v2")
    return args


def _panel_args(port: int = 8788) -> list[str]:
    if FROZEN:
        return [sys.executable, "panel", str(port)]
    return [PY, str(ROOT / "tools" / "panel.py"), str(port)]


def start(v2: bool = False) -> None:
    # 1) 收件服务器
    if _port_open(8771):
        print("· 收件服务器已在跑（8771）")
    else:
        pid = _spawn("wxhook", _serve_args(8771))
        print(f"· 收件服务器启动 pid={pid}")
        for _ in range(20):
            if _port_open(8771):
                break
            time.sleep(0.3)
        if not _port_open(8771):
            print("  ⚠ 8771 没起来，看 data/logs/wxhook.log")

    # 2) 把回调指向本地
    try:
        from channels import wxhook
        # 微信重启后注入的内存会丢，回调要重注册；hook 可能还没就绪，多试几次
        for i in range(5):
            try:
                r = wxhook.set_callback("http://127.0.0.1:8771/cb")
                if r.get("ret") == 0:
                    print(f"· 回调注册：{r.get('retmsg')}")
                    break
                print(f"· 回调注册返回 {r}")
            except Exception as e:
                if i == 4:
                    print(f"  ⚠ 回调注册失败：{e}（微信 hook 没开？）")
                time.sleep(1.5)
    except Exception as e:
        print(f"  ⚠ 回调注册失败：{e}（微信 hook 没开？）")

    # 3) 主循环
    if _port_open(8790) or _read_pid("run_wx"):
        print("· 阿鱼已在跑（8790 被占）")
    else:
        pid = _spawn("run_wx", _run_args(v2))
        print(f"· 阿鱼启动 pid={pid}（{'v2 主体' if v2 else 'v1 门控'}）")


def _pid_on_port(port: int) -> int | None:
    """谁在听这个端口。用来按角色停止。"""
    import subprocess
    try:
        out = subprocess.run(["netstat", "-ano"], capture_output=True,
                             text=True, timeout=15).stdout
    except Exception:
        return None
    for line in out.splitlines():
        if f":{port}" in line and "LISTENING" in line.upper():
            try:
                return int(line.split()[-1])
            except (ValueError, IndexError):
                continue
    return None


def start_profile(name: str) -> int | None:
    """启动某个角色的主循环（各角色占各自的单例端口）。"""
    from core import config as _cfg
    port = _cfg.profile_port(name)
    if _port_open(port):
        print(f"· {name} 已经在跑（{port}）")
        return None
    _cfg.set_value("active", value=name)
    args = _run_args(v2=True)
    if "--profile" not in args:
        args += ["--profile", name]
    pid = _spawn("run_wx", args)
    print(f"· {name} 启动 pid={pid}")
    return pid


def _terminate(pid: int) -> bool:
    """尽力杀一个进程：先 taskkill，不行直接 TerminateProcess。

    在受限令牌下 taskkill 会失败（返回码非 0 但没人看），
    所以这里两条路都试，并且把结果如实返回。
    """
    import subprocess
    try:
        r = subprocess.run(["taskkill", "/PID", str(pid), "/F"],
                           capture_output=True, text=True, timeout=15)
        if r.returncode == 0:
            return True
    except Exception:
        pass
    try:
        import ctypes
        PROCESS_TERMINATE = 0x0001
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        h = k.OpenProcess(PROCESS_TERMINATE, False, int(pid))
        if not h:
            return False
        try:
            return bool(k.TerminateProcess(h, 1))
        finally:
            k.CloseHandle(h)
    except Exception:
        return False


def stop_profile(name: str) -> bool:
    """停掉某个角色。真的停了才返回 True —— 之前不看结果，面板会假装成功。"""
    from core import config as _cfg
    port = _cfg.profile_port(name)
    pid = _pid_on_port(port)
    if not pid:
        return not _port_open(port)
    _terminate(pid)
    for _ in range(30):                 # 最多等 6 秒，端口真关掉才算数
        if not _port_open(port):
            break
        time.sleep(0.2)
    if not _port_open(port):
        try:
            _pid_file("run_wx").unlink(missing_ok=True)
        except OSError:
            pass
        return True
    return False


def start_panel(port: int = 8788) -> None:
    import webbrowser
    url = f"http://127.0.0.1:{port}"
    if _port_open(port):
        print(f"· 面板已在跑：{url}")
    else:
        pid = _spawn("panel", _panel_args(port))
        print(f"· 面板启动 pid={pid}")
        for _ in range(20):
            if _port_open(port):
                break
            time.sleep(0.3)
        print(f"· {url}" if _port_open(port) else "  ⚠ 面板没起来")
    webbrowser.open(url)


def _main_running() -> bool:
    """主循环在跑吗 —— 端口 / netstat / pid 文件，任一个说在就算在。"""
    return _port_open(8790) or bool(_pids_by_port(8790)) or bool(_read_pid("run_wx"))


def _pids_by_port(port: int) -> list[int]:
    """谁占着这个端口 —— pid 文件坏了、或者进程是别的会话起的，就靠这个找。"""
    try:
        out = subprocess.run(["netstat", "-ano"], capture_output=True, text=True, timeout=15).stdout
    except Exception:
        return []
    found = []
    for line in out.splitlines():
        if f":{port}" in line and "LISTEN" in line.upper():
            pid = line.split()[-1].strip()
            if pid.isdigit() and int(pid) not in found:
                found.append(int(pid))
    return found


def _kill_pid(pid: int) -> bool:
    for cmd in (["taskkill", "/PID", str(pid), "/F"],):
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            if r.returncode == 0:
                return True
        except Exception:
            pass
    return False


def stop() -> None:
    stopped = False
    # 主循环：先按 pid 文件，不行就按单例端口找
    if not _kill("run_wx"):
        for pid in _pids_by_port(8790):
            if _kill_pid(pid):
                print(f"· 已停 run_wx（按端口 {8790} 找到 pid={pid}）")
                stopped = True
                break
    else:
        print("· 已停 run_wx")
        stopped = True
    if not stopped:
        print("· run_wx 本来就没跑")

    # 收件服务
    if _kill("wxhook"):
        print("· 已停 wxhook")
    else:
        for pid in _pids_by_port(8771):
            if _kill_pid(pid):
                print(f"· 已停 wxhook（按端口 8771 找到 pid={pid}）")
                break
        else:
            print("· wxhook 本来就没跑")

    for f in PIDS.glob("*.pid"):
        try:
            f.unlink()
        except OSError:
            pass


def status() -> None:
    from memory import store
    from core import sleep

    con = store.connect()
    now = datetime.now()
    print(f"时间     {now:%Y-%m-%d %H:%M:%S}")
    print(f"微信hook {'[OK] 30001' if _port_open(30001) else '[--] 没开'}")
    print(f"收件服务 {'[OK] 8771' if _port_open(8771) or _pids_by_port(8771) else '[--] 没开'}")
    print(f"主循环   {'[OK] 在跑' if _main_running() else '[--] 没跑'}")
    if sleep.asleep(con, now):
        u = sleep.until(con)
        left = (u - now).total_seconds() / 60 if u else 0
        print(f"状态     睡着，{u:%H:%M} 醒（还有 {left:.0f} 分钟）")
    else:
        print("状态     醒着")
    st = store.get_state(con)
    print(f"精力     {st.get('energy', '?')}   心情 {st.get('mood', '?')}")
    print(f"消息     {con.execute('SELECT COUNT(*) FROM events WHERE kind=\'message\'').fetchone()[0]} 条"
          f"   事实 {con.execute('SELECT COUNT(*) FROM facts WHERE disputed=0').fetchone()[0]} 条"
          f"   日记 {con.execute('SELECT COUNT(*) FROM reflections').fetchone()[0]} 篇")


def wake() -> None:
    from core import sleep
    from memory import store
    con = store.init_db()
    info = sleep.wake(con, "人工叫醒")
    print(f"叫醒了。睡着期间 {info['count']} 条消息，最近：")
    for m in info["messages"][-8:]:
        print(f"  {m['actor_name']}：{m['text'][:30]}")


def go_sleep(hhmm: str) -> None:
    from core import sleep
    from memory import store
    con = store.init_db()
    hh, mm = hhmm.split(":")
    now = datetime.now()
    wake_at = now.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
    if wake_at <= now:
        wake_at += timedelta(days=1)
    sleep.go_to_sleep(con, wake_at, "手动安排")
    print(f"让她睡了，{wake_at:%m-%d %H:%M} 醒。")


def show_log(n: int = 20) -> None:
    p = LOGS / "run_wx.log"
    if not p.exists():
        print("还没有日志")
        return
    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    print("\n".join(lines[-n:]))


def sync_files() -> None:
    """把「记忆/我/*.md」里的改动读回库，再把库导回文件。"""
    from core import memfiles
    from memory import store

    con = store.init_db()
    st = memfiles.import_people(con)
    n = memfiles.export_people(con)
    print(f"· 读回：改 {st['updated']} 条、加 {st['added']} 条、忘 {st['forgotten']} 条")
    print(f"· 导出：{n} 个人物档案")


def show_memo() -> None:
    from core import memfiles

    print(memfiles.read_self_memo().strip() or "（她还没记什么）")
    print(f"\n（文件：{memfiles.SELF_MEMO}）")


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "start":
        start(v2=(len(sys.argv) > 2 and sys.argv[2] == "v2"))
        print()
        status()
    elif cmd == "stop":
        stop()
    elif cmd == "restart":
        stop()
        time.sleep(1)
        start()
        print()
        status()
    elif cmd == "status":
        status()
    elif cmd == "wake":
        wake()
    elif cmd == "sleep":
        go_sleep(sys.argv[2] if len(sys.argv) > 2 else "08:00")
    elif cmd == "log":
        show_log(int(sys.argv[2]) if len(sys.argv) > 2 else 20)
    elif cmd == "sync":
        sync_files()
    elif cmd == "memo":
        show_memo()
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
