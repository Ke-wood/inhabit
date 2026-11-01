"""配置体检：能不能跑起来，哪一项还没配。

给面板用，也给命令行用：

    python -m core.doctor
"""

from __future__ import annotations

import os
import socket
import sqlite3
from pathlib import Path

from core.paths import ROOT


def _port_open(port: int, host: str = "127.0.0.1", timeout: float = 0.5) -> bool:
    try:
        s = socket.create_connection((host, port), timeout=timeout)
        s.close()
        return True
    except OSError:
        return False


def _host_port(url: str, default_port: int = 80) -> tuple[str, int]:
    from urllib.parse import urlparse
    try:
        u = urlparse(url if "://" in url else "http://" + url)
        return (u.hostname or "127.0.0.1"), (u.port or default_port)
    except Exception:
        return "127.0.0.1", default_port


def check() -> dict:
    from core import config

    cfg = config.load()
    items: list[dict] = []

    def add(key, name, ok, level, detail, hint=""):
        items.append({"key": key, "name": name, "ok": bool(ok),
                      "level": level, "detail": detail, "hint": hint})

    # 1. 大脑
    api = cfg.get("api") or {}
    if api.get("base_url") and api.get("api_key"):
        add("api", "大脑 API", True, "ok",
            f"用配置里的 {api.get('base_url')} · {api.get('model') or '默认模型'}")
    else:
        env_key = os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("OPENAI_API_KEY")
        if env_key:
            add("api", "大脑 API", True, "ok",
                "用环境变量里的 key（DEEPSEEK_API_KEY / OPENAI_API_KEY）",
                "想固定下来就在面板「大脑 / API」填")
        else:
            add("api", "大脑 API", False, "error",
                "既没填 API，也没有 DEEPSEEK_API_KEY / OPENAI_API_KEY",
                "面板里填 base_url + api_key，或设环境变量")

    # 2. 渠道
    chans = cfg.get("channels") or {}
    enabled = [k for k, v in chans.items() if v.get("enabled")]
    if not enabled:
        add("channel", "消息渠道", False, "error", "一个都没启用", "至少启用一个渠道")
    for name in enabled:
        c = chans[name] or {}
        kind = c.get("kind")
        label = c.get("label") or name
        if kind == "wechat_hook":
            url = c.get("hook_url") or "http://127.0.0.1:30001"
            host, port = _host_port(url, 30001)
            ok = _port_open(port, host)
            add(f"channel:{name}", f"渠道 · {label}", ok, "ok" if ok else "warn",
                f"hook {url} " + ("可达" if ok else "连不上"),
                "" if ok else "先启动微信 hook 程序，再重启她")
        elif kind == "onebot":
            url = c.get("base_url") or ""
            if not url:
                add(f"channel:{name}", f"渠道 · {label}", False, "error",
                    "没填 OneBot 地址", "跑一个 NapCat / go-cqhttp，把 HTTP 地址填进来")
            else:
                host, port = _host_port(url, 5700)
                ok = _port_open(port, host)
                detail = f"OneBot {url} " + ("可达" if ok else "连不上")
                tip = "" if ok else "QQ 框架没跑，或地址/端口不对"
                if ok:
                    try:                    # 端口通了再问一次能力，比「端口开着」有用
                        from channels import onebot
                        caps = onebot.capabilities(refresh=True)
                        detail = onebot.describe_caps()
                        if not caps.get("image"):
                            tip = "这个实现不支持发图；她发图时会退回文字"
                        elif not caps.get("record"):
                            tip = "这个实现不支持发语音；她会退回文字"
                    except Exception:
                        pass
                add(f"channel:{name}", f"渠道 · {label}", ok, "ok" if ok else "warn",
                    detail, tip)
        else:  # http
            url = c.get("base_url") or ""
            ok = bool(url)
            add(f"channel:{name}", f"渠道 · {label}", ok, "ok" if ok else "error",
                url or "没填地址", "" if ok else "填一个能接收 POST /send 的地址")

    # 3. 收件服务
    ok = _port_open(8771)
    add("inbox", "收件服务", ok, "ok" if ok else "warn",
        "127.0.0.1:8771 " + ("在跑" if ok else "没起来"),
        "" if ok else "面板点「启动」会一起拉起来")

    # 4. 数据与记忆
    prof = config.profile(viewing=True)
    d = ROOT / (prof.get("dir") or "data")
    db = d / "feiyu.db"
    try:
        d.mkdir(parents=True, exist_ok=True)
        probe = d / ".write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        writable = True
    except OSError:
        writable = False
    add("storage", "数据目录", writable, "ok" if writable else "error",
        str(d) + (" 可写" if writable else " 不可写"))
    if db.exists():
        try:
            con = sqlite3.connect(str(db), timeout=3)
            n = con.execute("SELECT COUNT(*) FROM events").fetchone()[0]
            con.close()
            add("db", "记忆库", True, "ok", f"{db.name} · {n} 条事件")
        except Exception as e:
            add("db", "记忆库", False, "error", f"打不开：{type(e).__name__}")
    else:
        add("db", "记忆库", True, "warn", "还没有，第一次启动会自动建")

    mem = ROOT / (prof.get("memory") or "记忆")
    persona = mem / (prof.get("persona") or "我是谁.md")
    ok = persona.exists()
    add("persona", "人设文件", ok, "ok" if ok else "warn",
        str(persona) if ok else "还没写，她会不知道自己是谁",
        "" if ok else "面板「人设」里写几句，或直接建这个文件")

    # 5. B 站
    cookie = ((prof.get("bilibili") or {}).get("cookie")
              or (cfg.get("bilibili") or {}).get("cookie") or "")
    if not cookie:
        f = ROOT / "data" / "secrets" / "bili_cookie.txt"
        cookie = f.read_text(encoding="utf-8", errors="replace").strip() if f.exists() else ""
    fields = [p.split("=", 1)[0].strip() for p in cookie.split(";") if "=" in p]
    if not cookie:
        add("bilibili", "B 站", False, "warn", "没配 cookie", "要用 B 站功能就填一条")
    elif "bili_jct" not in fields:
        add("bilibili", "B 站", False, "warn",
            f"有 cookie 但缺 bili_jct（{len(fields)} 个字段）",
            "发评论/点赞会 -111，补齐 bili_jct")
    else:
        add("bilibili", "B 站", True, "ok", f"cookie {len(fields)} 个字段")

    # 6. 明文凭证：config.json 里不该再出现 cookie / 真实 wxid
    try:
        raw_cfg = (ROOT / "config.json").read_text(encoding="utf-8", errors="replace")
    except OSError:
        raw_cfg = ""
    leaked = [w for w in ("SESSDATA=", "bili_jct", "DedeUserID") if w in raw_cfg]
    if leaked:
        add("secrets", "凭据存放", False, "warn",
            f"config.json 里还有明文凭证（{', '.join(leaked)}）",
            "搬到 data/secrets/ 并把 config.json 里的清空；"
            "已经提交/分享过的 cookie 视为泄露，要重新扫码")
    else:
        add("secrets", "凭据存放", True, "ok", "config.json 里没有明文凭证")

    errors = [i for i in items if i["level"] == "error"]
    warns = [i for i in items if i["level"] == "warn"]
    return {"ok": not errors, "items": items,
            "errors": len(errors), "warns": len(warns),
            "summary": ("可以跑了" if not errors and not warns else
                        f"{len(errors)} 个必须修、{len(warns)} 个建议修")}


def main() -> None:
    import sys
    # Windows 控制台是 GBK，打不出 ✓ —— 先切 UTF-8，切不了就退回 ASCII
    try:
        if sys.stdout is not None:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError, OSError):
        pass
    r = check()
    for it in r["items"]:
        mark = {"ok": "✓", "warn": "!", "error": "✗"}[it["level"]]
        try:
            print(f"  {mark} {it['name']:14} {it['detail']}")
        except UnicodeEncodeError:
            alt = {"ok": "OK", "warn": "!", "error": "X"}[it["level"]]
            print(f"  {alt} {it['name']:14} {it['detail']}".encode("ascii", "replace").decode("ascii"))
        if it["hint"]:
            print(f"      → {it['hint']}")
    print()
    print("  " + r["summary"])


if __name__ == "__main__":
    main()
