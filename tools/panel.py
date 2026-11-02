"""阿鱼控制面板：看状态、调阈值、改人设记忆、开思考模式。

    python tools/panel.py            # http://127.0.0.1:8788
    python tools/panel.py 8790       # 换端口

设计上只有"读写 config.json"和"读写记忆文件"两件事，
所有阈值都是变量，改完下一拍生效，不用重启。
"""

from __future__ import annotations

import json
import sys
import threading
import time
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ROOT_DIR = Path(__file__).resolve().parent.parent

from core import config, persona, sleep      # noqa: E402
from core.self import Self                    # noqa: E402
from memory import store                      # noqa: E402

PANEL = Path(__file__).resolve().parent.parent / "web" / "panel.html"

# 阈值的中文名、单位、范围 —— 面板照着这个渲染
THRESHOLD_META = [
    ("idle_think_gap", "空闲时多久想一次", "秒", 10, 3600, 5),
    ("focus_gap", "思考时刻里的间隔", "秒", 5, 600, 5),
    ("min_think_gap", "两次想的最短间隔", "秒", 5, 600, 5),
    ("speak_gap", "说完话后静默多久", "秒", 0, 600, 5),
    ("browse_gap", "两次刷 B 站的最短间隔", "秒", 60, 86400, 60),
    ("urge_floor", "低于这个劲头就不做", "", 0, 1, 0.05),
    ("score_band", "冲动与犹豫相近时掷骰子的带宽", "", 0, 0.5, 0.01),
    ("facts_here", "每轮带出几条记忆", "条", 0, 20, 1),
    ("keep_loops", "最多保留几件悬着的事", "条", 3, 80, 1),
    ("focus_max_minutes", "思考模式最长", "分钟", 1, 120, 1),
]

SWITCH_META = [
    ("browse_bili", "自己去看 B 站"),
    ("publish_bili", "发到 B 站"),
    ("focus_mode", "主动开思考模式"),
    ("auto_sleep", "会困会自己睡"),
    ("share", "会忍不住分享"),
    ("judge_relations", "会自己给人打好感分"),
    ("ability_tutorial", "给她能力教程（让她知道自己能改人设）"),
]


THRESHOLD_KEYS = {row[0] for row in THRESHOLD_META}
SWITCH_KEYS = {row[0] for row in SWITCH_META}


# ------------------------------------------------------------------ 数据
def _viewing() -> dict:
    """面板正在看的角色（可以不是正在跑的那个）。"""
    return config.profile(viewing=True)


def _view_con():
    """连到 viewing 角色的库；没有就返回 None。"""
    import sqlite3
    prof = _viewing()
    db = ROOT_DIR / prof["dir"] / "feiyu.db"
    if not db.exists():
        return None
    # 只读连接：面板每 3 秒来一次，可写连接会和主循环抢锁（历史上 42 次 database is locked）
    from memory import store
    try:
        return store.connect_ro(db)
    except sqlite3.Error:
        return None


def _view_file(name: str) -> Path:
    prof = _viewing()
    return ROOT_DIR / prof["memory"] / name


def snapshot() -> dict:
    prof = _viewing()
    con = _view_con()
    now = datetime.now()
    running = config.running_profiles()
    active = config.get("active")
    # 角色里的 B 站 cookie 也不回给浏览器（前端只需要知道"配没配"）
    prof_view = dict(prof)
    bili = dict(prof_view.get("bilibili") or {})
    ck = str(bili.get("cookie") or "").strip()
    if not ck:
        # config 里已经清空，真实凭证在 data/secrets —— 按实际能不能用显示
        try:
            from bili.client import load_cookie
            ck = (load_cookie() or "").strip()
        except Exception:
            ck = ""
    bili["cookie"] = ""
    bili["cookie_set"] = bool(ck)
    bili["cookie_tail"] = ck[-4:] if ck else ""
    prof_view["bilibili"] = bili
    base = {
        "now": now.strftime("%Y-%m-%d %H:%M:%S"),
        "version": _version(),
        "update_available": _update_available(),
        "edition": config.get("edition") or "local",
        "profile": prof_view,
        "viewing": prof.get("name"),
        "active": active,
        "running": running,
        "running_here": bool(running.get(prof.get("name"))),
        "running_other": [n for n, on in running.items() if on and n != prof.get("name")],
        "profiles": list((config.load().get("profiles") or {}).keys()),
        "identity": _read_identity(),
        "memory": _read_memory(),
        "config": config.public(prof.get("name")),
        # 阈值/开关的元数据唯一来源：前端不再自己抄一份（抄了必然漂）
        "meta": {
            "thresholds": [{"key": k, "label": lb, "unit": u,
                            "min": mn, "max": mx, "step": st}
                           for (k, lb, u, mn, mx, st) in THRESHOLD_META],
            "switches": [{"key": k, "label": lb} for (k, lb) in SWITCH_META],
        },
    }
    if con is None:
        base.update({"asleep": False, "wake_at": "", "focusing": False, "focus_topic": "",
                     "focus_left": 0, "mood": "—", "energy": 0, "age": "—",
                     "counts": {}, "relations": [], "thoughts": [], "loops": [],
                     "empty": True})
        return base

    try:
        st = store.get_state(con)
        bot = Self(con)
        asleep = sleep.asleep(con, now)
        wake = sleep.until(con)
        rel = con.execute(
            """SELECT e.name, r.affinity, r.familiarity, r.label
               FROM relations r JOIN entities e ON e.id = r.dst_id
               ORDER BY r.affinity DESC""").fetchall()
        thoughts = con.execute(
            "SELECT ts, content, urge, kind, status FROM thoughts "
            "ORDER BY id DESC LIMIT 12").fetchall()
        loops = con.execute(
            "SELECT id, text, strength, created_at FROM open_loops "
            "WHERE closed_at IS NULL ORDER BY strength DESC, id DESC LIMIT 12").fetchall()
        base.update({
            "asleep": asleep,
            "wake_at": wake.strftime("%H:%M") if wake else "",
            "focusing": bot.focusing(),
            "focus_topic": bot.focus_topic or "",
            "focus_left": max(0, int(bot.focus_until - time.time())) if bot.focusing() else 0,
            "mood": st.get("mood", "平静"),
            "energy": round(bot.energy(), 2),
            "age": bot.age_days,
            "counts": {
                "事件": con.execute("SELECT COUNT(*) FROM events").fetchone()[0],
                "事实": con.execute(
                    "SELECT COUNT(*) FROM facts WHERE disputed=0 AND superseded_by IS NULL"
                ).fetchone()[0],
                "念头": con.execute("SELECT COUNT(*) FROM thoughts").fetchone()[0],
                "日记": con.execute("SELECT COUNT(*) FROM reflections").fetchone()[0],
            },
            "relations": [dict(r) for r in rel],
            "thoughts": [dict(t) for t in thoughts],
            "loops": [dict(l) for l in loops],
        })
    finally:
        con.close()
    return base


def _read_identity() -> str:
    try:
        return _view_file(_viewing().get("persona") or "我是谁.md").read_text(encoding="utf-8")
    except OSError:
        return ""


def _read_memory() -> str:
    try:
        return _view_file("我自己的记忆.md").read_text(encoding="utf-8")
    except OSError:
        return ""


# ------------------------------------------------------------------ 动作
def _guard_writable() -> dict | None:
    """运行中的角色不许改东西 —— 先关掉再改，避免改一半被判读错。"""
    prof = _viewing()
    name = prof.get("name")
    if config.running_profiles().get(name):
        return {"ok": False, "error": f"{name} 正在运行，先在面板上关闭它再改"}
    return None


def _version() -> str:
    try:
        from core import version as V
        return V.read_version()
    except Exception:
        return str(config.get("version") or "")


def _update_available() -> dict:
    """缓存上一次检查结果，别每次刷新都打 GitHub。"""
    try:
        from core import version as V
        st = V.read_state()
        return {
            "checked_at": st.get("checked_at") or "",
            "latest": st.get("latest") or "",
            "newer": bool(st.get("newer")),
            "notes": (st.get("notes") or "")[:600],
        }
    except Exception:
        return {"checked_at": "", "latest": "", "newer": False, "notes": ""}


def _changelog(n: int = 30) -> str:
    """本地读更新说明（最新那一段），面板上直接显示，不用去 GitHub。"""
    f = ROOT_DIR / "CHANGELOG.md"
    if not f.exists():
        return ""
    text = f.read_text(encoding="utf-8")
    i = text.find(chr(10) + "## ")
    if i < 0:
        return ""
    out = []
    for line in text[i + 1:].splitlines():
        if line.startswith("## ") and out:
            break
        out.append(line)
        if len(out) >= n:
            break
    return chr(10).join(out).strip()


def do_update(payload: dict) -> dict:
    """面板上的「检查更新 / 更新 / 回滚」。"""
    from tools import update as updater
    from core import version as V
    action = str(payload.get("action") or "check")
    if action == "check":
        try:
            rel = updater.latest_release()
        except Exception as e:
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}
        if not rel:
            return {"ok": False, "error": "查不到远端"}
        local = V.read_version()
        newer = bool(rel["version"]) and V.is_newer(rel["version"], local)
        try:
            V.write_state({**V.read_state(),
                           "checked_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                           "latest": rel["version"] or rel["tag"],
                           "newer": newer,
                           "notes": rel["notes"]})
        except Exception:
            pass
        return {"ok": True, "local": local, "latest": rel["version"] or rel["tag"],
                "newer": newer, "source": rel["source"], "notes": rel["notes"],
                "local_notes": _changelog()}
    if action == "update":
        rc = updater.cmd_update()
        return {"ok": rc == 0, "rc": rc, "version": V.read_version(),
                "note": "改完要重启她才生效"}
    if action == "rollback":
        rc = updater.cmd_rollback()
        return {"ok": rc == 0, "rc": rc, "version": V.read_version()}
    return {"ok": False, "error": f"不认识的动作：{action}"}


def do_config(payload: dict) -> dict:
    # 阈值/开关改了会影响正在跑的那一拍，必须锁
    # 渠道/API/B站 是启动参数和外部凭证，随时能改
    if "thresholds" in payload or "switches" in payload:
        g = _guard_writable()
        if g:
            return g
    cfg = config.load()
    th = payload.get("thresholds")
    if isinstance(th, dict):
        # 阈值按角色存：写进「正在看」那个角色的 profiles.<名>.thresholds，
        # 顶层 thresholds 保留为所有角色的默认值。
        viewing = cfg.get("viewing") or cfg.get("active")
        profs = cfg.setdefault("profiles", {})
        prof = profs.get(viewing)
        if not isinstance(prof, dict):
            return {"ok": False, "error": f"没有角色「{viewing}」"}
        cur = dict(prof.get("thresholds") or {})
        for k, v in th.items():
            if k in THRESHOLD_KEYS:
                try:
                    cur[k] = float(v) if isinstance(v, float) or "." in str(v) else int(v)
                except (TypeError, ValueError):
                    pass
        prof["thresholds"] = cur
        profs[viewing] = prof
    sw = payload.get("switches")
    if isinstance(sw, dict):
        cur = dict(cfg.get("switches") or {})
        for k, v in sw.items():
            if k in SWITCH_KEYS:
                cur[k] = bool(v)
        cfg["switches"] = cur
    api = payload.get("api")
    if isinstance(api, dict):
        cur = dict(cfg.get("api") or {})
        for k in ("base_url", "api_key", "model"):
            if k in api:
                cur[k] = str(api[k] or "").strip()
        cfg["api"] = cur
    bili = payload.get("bilibili")
    if isinstance(bili, dict) and "cookie" in bili:
        viewing = cfg.get("viewing") or cfg.get("active")
        cookie = str(bili["cookie"] or "").strip()
        if cookie:
            # 凭证写 data/secrets（data/ 已被 .gitignore 覆盖），config.json 只留空
            target = (ROOT_DIR / "data" / "secrets" / "profiles"
                      / str(viewing or "default") / "bili_cookie.txt")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(cookie, encoding="utf-8")
        prof = (cfg.get("profiles") or {}).get(viewing)
        if isinstance(prof, dict):
            prof["bilibili"] = {"cookie": ""}
            cfg["profiles"][viewing] = prof
    ch = payload.get("channels")
    if isinstance(ch, dict):
        cur = dict(cfg.get("channels") or {})
        for name, patch in ch.items():
            if name not in cur or not isinstance(patch, dict):
                continue
            c = dict(cur[name])
            for k in ("enabled", "base_url", "hook_url", "token"):
                if k in patch:
                    c[k] = patch[k]
            cur[name] = c
        cfg["channels"] = cur
    config.save(cfg)
    # 回给前端的是脱敏视图，别把刚保存的密钥原样带回去
    return {"ok": True, "config": config.public()}


def do_run(payload: dict) -> dict:
    """启动 / 停止当前查看的角色。别的角色在跑时，启动会被挡住并报名字。"""
    from tools import ctl
    action = str(payload.get("action") or "")
    name = str(payload.get("name") or _viewing().get("name") or "")
    running = config.running_profiles()
    if action == "start":
        others = [n for n, on in running.items() if on and n != name]
        if others:
            return {"ok": False, "error": f"{'、'.join(others)} 还在运行哦"}
        if running.get(name):
            return {"ok": False, "error": f"{name} 已经在跑了"}
        ctl.start_profile(name)
        return {"ok": True, "msg": f"启动 {name}"}
    if action == "stop":
        if not running.get(name):
            return {"ok": False, "error": f"{name} 没在跑"}
        ok = ctl.stop_profile(name)
        if ok:
            return {"ok": True, "msg": f"停止 {name}"}
        return {"ok": False, "error": f"{name} 停不掉：进程杀了但端口还开着，可能权限不够"}
    return {"ok": False, "error": f"不认识的动作 {action}"}


def do_person(payload: dict) -> dict:
    """某个人的完整记忆（点名字展开用）。"""
    name = str(payload.get("name") or "").strip()
    if not name:
        return {"ok": False, "error": "缺名字"}
    con = _view_con()
    if con is None:
        return {"ok": False, "error": "这个角色还没有记忆库"}
    try:
        rows = con.execute(
            """SELECT f.id, f.predicate, f.object_text, f.confidence, f.created_at
               FROM facts f JOIN entities e ON e.id = f.subject_id
               WHERE e.name = ? AND f.superseded_by IS NULL
               ORDER BY f.id DESC""", (name,)).fetchall()
        rel = con.execute(
            """SELECT r.* FROM relations r JOIN entities e ON e.id = r.dst_id
               WHERE e.name = ?""", (name,)).fetchone()
        ev = con.execute(
            "SELECT ts, text FROM events WHERE actor_name = ? "
            "ORDER BY id DESC LIMIT 20", (name,)).fetchall()
        return {"ok": True, "name": name,
                "relation": dict(rel) if rel else None,
                "facts": [dict(r) for r in rows],
                "events": [dict(r) for r in ev]}
    finally:
        con.close()


def do_fact(payload: dict) -> dict:
    """改一条事实 / 删掉它。"""
    g = _guard_writable()
    if g:
        return g
    fid = payload.get("id")
    if fid is None:
        return {"ok": False, "error": "缺 id"}
    con = _write_con()
    if con is None:
        return {"ok": False, "error": "没有记忆库"}
    try:
        if payload.get("delete"):
            con.execute("UPDATE facts SET disputed = 1 WHERE id = ?", (int(fid),))
        else:
            text = str(payload.get("text") or "").strip()
            if not text:
                return {"ok": False, "error": "内容不能为空"}
            con.execute("UPDATE facts SET object_text = ? WHERE id = ?",
                        (text[:120], int(fid)))
        con.commit()
        return {"ok": True}
    finally:
        con.close()


def _write_con():
    """要写库的操作用这个。

    以前这些地方也用 _view_con（只读），一写就报
    「attempt to write a readonly database」—— 开思考模式失败就是这个原因。
    这里用可写连接，并给足 busy_timeout，等主循环把锁放掉。
    """
    import sqlite3
    prof = _viewing()
    db = ROOT_DIR / prof["dir"] / "feiyu.db"
    if not db.exists():
        return None
    try:
        con = store.connect(db)
        con.execute("PRAGMA busy_timeout = 8000")
        return con
    except sqlite3.Error:
        return None


def do_focus(payload: dict) -> dict:
    con = _write_con()
    if con is None:
        return {"ok": False, "error": "这个角色还没有记忆库"}
    try:
        bot = Self(con)
        minutes = payload.get("minutes")
        if minutes in (None, 0, "off"):
            bot.clear_focus()
            return {"ok": True, "focusing": False, "msg": "已关闭思考模式"}
        minutes = max(1, min(int(config.thresholds().get("focus_max_minutes", 60)),
                             int(minutes)))
        topic = str(payload.get("topic") or "").strip() or "自己想不明白的事"
        bot.set_focus(minutes, topic)
        store.add_event(con, f"（面板）开启思考模式：{topic}（{minutes} 分钟）",
                        scene="inner", channel="self", kind="action",
                        actor_name=bot.bot_name, importance=4)
        con.commit()
        return {"ok": True, "focusing": True, "minutes": minutes, "topic": topic}
    finally:
        con.close()


def do_identity(payload: dict) -> dict:
    g = _guard_writable()
    if g:
        return g
    text = str(payload.get("text") or "").strip()
    if not text:
        return {"ok": False, "error": "不能为空"}
    f = _view_file(_viewing().get("persona") or "我是谁.md")
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(text, encoding="utf-8")
    return {"ok": True}


def do_memory(payload: dict) -> dict:
    g = _guard_writable()
    if g:
        return g
    f = _view_file("我自己的记忆.md")
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(str(payload.get("text") or ""), encoding="utf-8")
    return {"ok": True}


def do_control(payload: dict) -> dict:
    action = str(payload.get("action") or "")
    from tools import ctl
    if action == "sleep":
        hhmm = str(payload.get("wake") or "08:00")
        ctl.go_sleep(hhmm)
        return {"ok": True, "msg": f"已让她睡到 {hhmm}"}
    if action == "wake":
        ctl.wake()
        return {"ok": True, "msg": "叫她醒了"}
    return {"ok": False, "error": f"不认识的动作 {action}"}


def do_profile(payload: dict) -> dict:
    """切换「正在看」的角色 —— 不影响谁在跑。"""
    name = str(payload.get("name") or "").strip()
    cfg = config.load()
    profiles = cfg.get("profiles") or {}
    if name not in profiles:
        return {"ok": False, "error": f"没有角色「{name}」"}
    cfg["viewing"] = name
    config.save(cfg)
    return {"ok": True, "name": name}


def do_profile_new(payload: dict) -> dict:
    """新建角色：建目录、写默认人设。"""
    name = str(payload.get("name") or "").strip()
    if not name or "/" in name or "\\" in name or name in ("..", "."):
        return {"ok": False, "error": "名字不合法"}
    cfg = config.load()
    profiles = cfg.setdefault("profiles", {})
    if name in profiles:
        return {"ok": False, "error": "角色已存在"}
    sub = f"profiles/{name}"
    profiles[name] = {"dir": sub, "memory": f"{sub}/记忆", "persona": "我是谁.md"}
    config.save(cfg)
    d = ROOT_DIR / sub / "记忆"
    d.mkdir(parents=True, exist_ok=True)
    f = d / "我是谁.md"
    if not f.exists():
        f.write_text(f"# 你是谁\n{name}。\n", encoding="utf-8")
    return {"ok": True, "name": name, "need_restart": True}


def do_doctor(payload: dict) -> dict:
    from core import doctor
    try:
        r = doctor.check()
        return {"ok": True, **r}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


LESSONS = {
    "loops": ("loop_lesson.py", "让她清理悬着的事"),
    "identity": ("identity_check.py", "让她确认自己的人设"),
    "prompt": ("prompt_review.py", "让她评审收到的 prompt"),
    "memory": ("memory_review.py", "让她整理某个人的记忆"),
}


def do_memory_people(payload: dict) -> dict:
    """整理记忆用：有哪些人、各多少条记忆。"""
    con = _view_con()
    if con is None:
        return {"ok": False, "error": "这个角色还没有记忆库"}
    try:
        rows = con.execute(
            """SELECT e.name, COUNT(f.id) n, MAX(f.created_at) last_fact,
                      (SELECT MAX(ts) FROM events WHERE actor_name = e.name) last_talk
               FROM facts f JOIN entities e ON e.id = f.subject_id
               WHERE f.disputed = 0 AND f.superseded_by IS NULL
               GROUP BY e.id ORDER BY n DESC""").fetchall()
        now = time.time()
        cutoff = now - 30 * 86400
        recent, older = [], []
        for r in rows:
            last = r["last_talk"] or 0
            try:
                last_ts = last if isinstance(last, (int, float)) else \
                    datetime.fromisoformat(str(last)).timestamp()
            except (ValueError, TypeError):
                last_ts = 0
            item = {"name": r["name"], "n": r["n"], "last": int(last_ts)}
            (recent if last_ts >= cutoff else older).append(item)
        # 类别分布：一个人内部按 predicate 分
        for it in recent + older:
            prow = con.execute(
                """SELECT f.predicate, COUNT(*) n FROM facts f
                   JOIN entities e ON e.id = f.subject_id
                   WHERE e.name = ? AND f.disputed = 0 AND f.superseded_by IS NULL
                   GROUP BY f.predicate ORDER BY n DESC""", (it["name"],)).fetchall()
            it["cats"] = [{"k": x["predicate"], "n": x["n"]} for x in prow]
        return {"ok": True, "people": recent + older,
                "recent": recent, "older": older, "older_n": len(older)}
    finally:
        con.close()


def do_tutorial(payload: dict) -> dict:
    """跑一轮「她自己看设定」的教程 —— 和思考模式并列的那个功能。"""
    import os
    import subprocess
    kind = str(payload.get("kind") or "")
    if kind not in LESSONS:
        return {"ok": False, "error": f"没有这种教程：{kind}"}
    script, title = LESSONS[kind]
    path = ROOT_DIR / "tools" / script
    if not path.exists():
        return {"ok": False, "error": f"{script} 不在 tools 里"}

    if kind == "memory":
        who = str(payload.get("who") or "").strip()
        if not who:
            return {"ok": False, "error": "先选一个人"}
        batch = max(1, min(20, int(payload.get("rounds") or 8)))
        args = [who, str(batch)]
        title = f"整理「{who}」的记忆（每批 {batch} 条）"
    else:
        rounds = max(1, min(9, int(payload.get("rounds") or 1)))
        args = [str(rounds)]

    log = ROOT_DIR / "data" / "logs" / f"tutorial_{kind}.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("", encoding="utf-8")
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    try:
        p = subprocess.Popen(
            [sys.executable, "-u", str(path), *args],
            cwd=str(ROOT_DIR),
            stdout=open(log, "a", encoding="utf-8"),
            stderr=subprocess.STDOUT,
            env=env,
        )
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    return {"ok": True, "title": title, "pid": p.pid, "log": str(log)}


def do_tutorial_log(payload: dict) -> dict:
    kind = str(payload.get("kind") or "")
    if kind not in LESSONS:
        return {"ok": False, "error": "没有这种教程"}
    log = ROOT_DIR / "data" / "logs" / f"tutorial_{kind}.log"
    text = log.read_text(encoding="utf-8", errors="replace") if log.exists() else ""
    return {"ok": True, "text": text[-4000:], "exists": log.exists()}


def do_channel_new(payload: dict) -> dict:
    """加一个渠道：名称、类型、地址、场景前缀。"""
    name = str(payload.get("name") or "").strip()
    kind = str(payload.get("kind") or "http").strip()
    if not name:
        return {"ok": False, "error": "名字不能为空"}
    if name in (config.load().get("channels") or {}):
        return {"ok": False, "error": f"渠道「{name}」已经存在"}
    if kind not in ("onebot", "wechat_hook", "http"):
        return {"ok": False, "error": "类型只能是 onebot / wechat_hook / http"}
    cfg = config.load()
    ch = cfg.setdefault("channels", {})
    prefix = str(payload.get("prefix") or f"{name}_").strip()
    ch[name] = {
        "kind": kind,
        "enabled": bool(payload.get("enabled")),
        "label": str(payload.get("label") or name),
        "base_url": str(payload.get("base_url") or ""),
        "hook_url": str(payload.get("base_url") or ""),
        "token": str(payload.get("token") or ""),
        "prefixes": [prefix],
        "note": str(payload.get("note") or ""),
    }
    config.save(cfg)
    return {"ok": True, "name": name}


def do_channel_del(payload: dict) -> dict:
    name = str(payload.get("name") or "")
    if name in ("wechat", "qq", "other"):
        return {"ok": False, "error": "内置渠道不能删，关掉就行"}
    cfg = config.load()
    ch = cfg.get("channels") or {}
    if name not in ch:
        return {"ok": False, "error": "没有这个渠道"}
    del ch[name]
    cfg["channels"] = ch
    config.save(cfg)
    return {"ok": True}


ROUTES = {
    "/api/config": do_config,
    "/api/update": do_update,
    "/api/focus": do_focus,
    "/api/identity": do_identity,
    "/api/memory": do_memory,
    "/api/control": do_control,
    "/api/profile": do_profile,
    "/api/profile/new": do_profile_new,
    "/api/run": do_run,
    "/api/person": do_person,
    "/api/fact": do_fact,
    "/api/doctor": do_doctor,
    "/api/channel/new": do_channel_new,
    "/api/channel/del": do_channel_del,
    "/api/tutorial": do_tutorial,
    "/api/memory/people": do_memory_people,
    "/api/tutorial/log": do_tutorial_log,
}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code: int, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            html = PANEL.read_text(encoding="utf-8") if PANEL.exists() else "<h1>panel.html 不见了</h1>"
            return self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")
        if path == "/api/state":
            try:
                data = snapshot()
            except Exception as e:                     # 面板不能因为一个查询挂掉
                data = {"error": f"{type(e).__name__}: {e}"}
            return self._send(200, json.dumps(data, ensure_ascii=False).encode("utf-8"),
                              "application/json; charset=utf-8")
        return self._send(404, b"not found", "text/plain; charset=utf-8")

    def do_POST(self):
        path = urlparse(self.path).path
        fn = ROUTES.get(path)
        if not fn:
            return self._send(404, b"no route", "text/plain; charset=utf-8")
        try:
            n = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(n) or b"{}")
        except (ValueError, json.JSONDecodeError):
            payload = {}
        try:
            res = fn(payload)
        except Exception as e:
            res = {"ok": False, "error": f"{type(e).__name__}: {e}"}
        return self._send(200, json.dumps(res, ensure_ascii=False).encode("utf-8"),
                          "application/json; charset=utf-8")


def _port_busy(port: int) -> bool:
    import socket
    try:
        s = socket.create_connection(("127.0.0.1", port), timeout=0.5)
        s.close()
        return True
    except OSError:
        return False


def serve(port: int = 8788) -> None:
    url = f"http://127.0.0.1:{port}"
    # 已经在跑：只打开浏览器，不再起第二个
    if _port_busy(port):
        print(f"面板已经在跑了：{url}", flush=True)
        webbrowser.open(url)
        return
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"控制面板: {url}  （Ctrl+C 退出）", flush=True)
    threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


def main() -> None:
    config.ensure_file()
    serve(int(sys.argv[1]) if len(sys.argv) > 1 else 8788)


if __name__ == "__main__":
    main()
