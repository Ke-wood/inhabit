"""运行时配置：阈值、开关、角色。

所有"魔法数字"都从这里读，UI 改这个文件立刻生效。
改完不用重启 —— `get()` 每次都看文件修改时间。
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from core.paths import ROOT

CONFIG = ROOT / "config.json"

# 默认值：首次运行或字段缺失时用这些
DEFAULTS = {
    "version": "1.4.1",
    "edition": "open",        # open = 开源版（默认 QQ 通道）；local = 本机自用（微信 hook）
    "active": "阿鱼",          # 正在跑的那个（主循环启动时写）
    "viewing": "阿鱼",         # 面板正在看的那个（可以在跑别的时看另一个）
    "profiles": {
        "阿鱼": {
            "dir": "data",
            "memory": "记忆",
            "persona": "我是谁.md",
        },
    },
    "thresholds": {
        "idle_think_gap": 60,      # 没人说话时多久想一次（秒）
        "focus_gap": 20,           # 思考时刻里的间隔（秒）
        "min_think_gap": 20,       # 两次想的最短间隔
        "speak_gap": 0,            # 说完话后多久才能再说
        "browse_gap": 3600,        # 两次自己刷 B 站的最短间隔
        "search_gap": 600,         # 两次自己上网查的最短间隔
        "urge_floor": 0.2,         # 低于这个劲头，只想不做
        "score_band": 0.15,        # 冲动-犹豫 落在这个区间就掷骰子
        "quiet_hours": [2, 3, 4, 5, 6],
        "sleep_hours": [0, 1, 2, 3, 4],
        "max_sleep_hours": 10,
        "decay_days": 30,
        "keep_loops": 20,
        "facts_here": 8,
        "focus_max_minutes": 60,
        # —— v1 应答模式（NPC / 客服）：群聊里也每条都回，记忆多带一些 ——
        "always_reply": False,     # 打开 = 群聊不做概率判断，只防刷屏
        "recall_limit": 12,        # v1 每轮捞几条旧事
        "facts_per_person": 15,    # v1 每个人带几条事实
        "events_per_person": 6,    # v1 每个人带几条最近事件
    },
    "switches": {
        "browse_bili": True,       # 能不能自己去看视频
        "publish_bili": False,     # 能不能发到 B 站
        "focus_mode": True,        # 能不能自己按「思考时刻」
        "auto_sleep": True,        # 会困、会自己睡
        "share": True,             # 会忍不住分享
        "judge_relations": True,   # 会自己给人打好感分
        "ability_tutorial": True,  # 每轮告诉她「你能改自己」这份说明
    },
    "api": {                       # OpenAI 兼容端点；留空则读 DEEPSEEK_API_KEY / OPENAI_API_KEY
        "base_url": "",
        "api_key": "",
        "model": "",
    },
    # 她的身份标识：哪条是"她自己发的"、谁算主人、别人怎么叫她
    "identity": {
        "self_wxid": "",
        "owner_wxid": "",
        "names": ["阿鱼"],
    },
    "bilibili": {                  # 留空读 data/secrets/bili_cookie.txt
        "cookie": "",
    },
    "publish": {                   # B 站投稿（tools/publish.py）
        "biliup": "",              # biliup 可执行文件路径；留空则自动找 / 读 BILIUP_EXE
    },
    # 自动更新：从哪个 GitHub 仓库拉新版
    "updater": {
        "repo": "Ke-wood/inhabit",   # owner/name
        "token": "",                 # 私有库或遇到限流才需要；也可用 GITHUB_TOKEN
    },
    # 让她「看得懂图」：任意 OpenAI 兼容的多模态端点。留空 = 不看图，只记「有人发了张图」。
    "vision": {
        "base_url": "",
        "api_key": "",
        "model": "",
    },
    # 让她「会说话」：edge-tts（LGPLv3，可选安装）。留空用默认音色。
    "voice": {
        "tts_voice": "zh-CN-XiaoxiaoNeural",
    },
    # 渠道：一个角色可以同时挂好几个，按目标场景分流
    "channels": {
        "wechat": {
            "kind": "wechat_hook",
            "enabled": False,
            "label": "微信",
            "hook_url": "http://127.0.0.1:30001",
            "prefixes": ["wxid_", "@chatroom", "filehelper"],
            "note": "需要本机的 WeChat-Hook（Windows + 指定微信版本）",
        },
        "qq": {
            "kind": "onebot",
            "enabled": True,
            "label": "QQ",
            "base_url": "http://127.0.0.1:5700",
            "token": "",
            "prefixes": ["qq_"],
            "note": "OneBot 11 协议，先跑 NapCat / go-cqhttp 再填这里的地址",
        },
        "other": {
            "kind": "http",
            "enabled": False,
            "label": "其他",
            "base_url": "",
            "token": "",
            "prefixes": ["http_"],
            "note": "任意 HTTP 端点，POST /send 收消息",
        },
    },
}

_cache: dict = {}
_mtime: float = 0.0


def _merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load() -> dict:
    global _cache, _mtime
    try:
        m = CONFIG.stat().st_mtime
        if _cache and m == _mtime:
            return _cache
        raw = json.loads(CONFIG.read_text(encoding="utf-8")) if CONFIG.exists() else {}
        _cache = _merge(DEFAULTS, raw)
        _mtime = m
        return _cache
    except (OSError, json.JSONDecodeError):
        return _merge(DEFAULTS, {})


def save(cfg: dict) -> None:
    global _cache, _mtime
    CONFIG.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    _cache = cfg
    try:
        _mtime = CONFIG.stat().st_mtime
    except OSError:
        _mtime = 0.0


def get(*path, default=None):
    """config.get("thresholds", "idle_think_gap")"""
    cur = load()
    for p in path:
        if not isinstance(cur, dict) or p not in cur:
            return default
        cur = cur[p]
    return cur


def set_value(*path, value) -> dict:
    cfg = load()
    cfg = json.loads(json.dumps(cfg))          # 深拷贝，避免改缓存
    cur = cfg
    for p in path[:-1]:
        cur = cur.setdefault(p, {})
    cur[path[-1]] = value
    save(cfg)
    return cfg


def profile(name: str | None = None, *, viewing: bool = False) -> dict:
    """取一个角色：默认取「正在跑的」，viewing=True 取「面板正在看的」。"""
    cfg = load()
    key = name or (cfg.get("viewing") if viewing else cfg.get("active")) or "阿鱼"
    profiles = cfg.get("profiles") or {}
    p = profiles.get(key) or {"dir": "data", "memory": "记忆", "persona": "我是谁.md"}
    p = dict(p)
    p["name"] = key
    return p


def portrait_config() -> dict:
    """面板/记忆编辑器用：看 viewing 那个角色的库和记忆。"""
    cfg = load()
    key = cfg.get("viewing") or cfg.get("active") or "阿鱼"
    p = profile(key)
    return p


def profile_port(name: str | None = None) -> int:
    """每个角色一个单例端口：8790 + 序号，面板据此判断谁在跑。"""
    cfg = load()
    names = list((cfg.get("profiles") or {}).keys()) or ["阿鱼"]
    key = name or cfg.get("viewing") or cfg.get("active") or names[0]
    idx = names.index(key) if key in names else 0
    return 8790 + idx


def running_profiles() -> dict:
    """探测每个角色是否在跑（占着端口就是在跑）。"""
    import socket
    out = {}
    names = list((load().get("profiles") or {}).keys()) or ["阿鱼"]
    for i, n in enumerate(names):
        try:
            s = socket.create_connection(("127.0.0.1", 8790 + i), timeout=0.4)
            s.close()
            out[n] = True
        except OSError:
            out[n] = False
    return out


def thresholds(name: str | None = None) -> dict:
    """阈值：默认值 → 全局 → 该角色的覆盖。

    两个角色可以有不同的"多久想一次""劲头下限"。角色没单独配就用全局，
    全局没配就用默认值 —— 所以老配置照常工作，不需要迁移。
    """
    base = dict(DEFAULTS["thresholds"])
    base.update(load().get("thresholds") or {})
    try:
        own = profile(name).get("thresholds") or {}
        base.update({k: v for k, v in own.items() if k in base})
    except Exception:
        pass
    return base


def switches() -> dict:
    return load().get("switches") or DEFAULTS["switches"]


IDENTITY_FILE = ROOT / "data" / "identity.json"


def _identity_from_file() -> tuple[str, str]:
    """真实 wxid 放 data/identity.json（在 data/ 下，不会被提交），不写进 config.json。"""
    try:
        if not IDENTITY_FILE.exists():
            return "", ""
        d = json.loads(IDENTITY_FILE.read_text(encoding="utf-8"))
        return str(d.get("self_wxid") or "").strip(), str(d.get("owner_wxid") or "").strip()
    except (OSError, json.JSONDecodeError):
        return "", ""


def ident() -> tuple[str, str, tuple[str, ...]]:
    """(她自己, 主人, 所有叫法)。开源包里是空的，由用户填。

    真实 wxid 优先读 data/identity.json；config.json 里留空即可（那里只放叫法）。
    角色可以在自己的 profiles.<名>.names 里覆盖叫法 —— 多个角色同时跑时，
    不覆盖就全用阿鱼的名字（这就是「夺舍」的来源）。
    """
    d = load().get("identity") or {}
    names = tuple(n for n in (d.get("names") or ["阿鱼"]) if n)
    try:
        own = tuple(n for n in (profile().get("names") or []) if n)
        if own:
            names = own
    except Exception:
        pass
    self_id, owner_id = _identity_from_file()
    return (self_id or d.get("self_wxid") or "",
            owner_id or d.get("owner_wxid") or "",
            (names or ("阿鱼",)))


def _mask(secret) -> tuple[bool, str]:
    s = str(secret or "").strip()
    return (bool(s), s[-4:] if s else "")


def public(profile_name: str | None = None) -> dict:
    """给面板/前端看的配置：密钥只留"是否已配置 + 尾 4 位"，原值不出内核。

    面板要能改配置，但不该把 api_key / cookie / token / wxid 送回浏览器。
    前端拿这个视图渲染，保存时只提交用户真正改动过的字段。
    thresholds 给的是**该角色生效后**的值（全局 + 角色覆盖），不是磁盘上的原始值。
    """
    cfg = json.loads(json.dumps(load()))          # 深拷贝，别动缓存
    cfg["thresholds"] = thresholds(profile_name)

    api = cfg.get("api") or {}
    ok, tail = _mask(api.get("api_key"))
    api["api_key"] = ""
    api["api_key_set"], api["api_key_tail"] = ok, tail
    cfg["api"] = api

    for ch in (cfg.get("channels") or {}).values():
        if isinstance(ch, dict) and "token" in ch:
            ok, tail = _mask(ch.get("token"))
            ch["token"] = ""
            ch["token_set"], ch["token_tail"] = ok, tail

    def _redact_bili(d) -> None:
        if isinstance(d, dict) and "cookie" in d:
            ok, tail = _mask(d.get("cookie"))
            d["cookie"] = ""
            d["cookie_set"], d["cookie_tail"] = ok, tail

    _redact_bili(cfg.get("bilibili"))
    for p in (cfg.get("profiles") or {}).values():
        if isinstance(p, dict):
            _redact_bili(p.get("bilibili"))

    ident_cfg = cfg.get("identity") or {}
    for k in ("self_wxid", "owner_wxid"):
        ok, tail = _mask(ident_cfg.get(k))
        ident_cfg[k] = ""
        ident_cfg[f"{k}_set"], ident_cfg[f"{k}_tail"] = ok, tail
    cfg["identity"] = ident_cfg
    return cfg


def channels() -> dict:
    return load().get("channels") or DEFAULTS["channels"]


def profile_channels(name: str | None = None) -> list[str]:
    """这个角色管哪些渠道 —— 没单独配就继承全局启用的那些。

    多个角色同时跑时靠它分流：各自只处理自己渠道的消息，不会串。
    """
    prof = profile(name)
    own = prof.get("channels")
    if own:
        return list(own)
    return [k for k, v in channels().items() if v.get("enabled")]


def scene_belongs_to(scene: str, name: str | None = None) -> bool:
    """这条消息归不归这个角色管。"""
    ch = channel_for(scene)
    if not ch:
        # 认不出渠道的（比如没前缀的老数据）交给默认角色
        return (name or load().get("active")) == (load().get("active"))
    return ch in profile_channels(name)


def channel_for(scene: str) -> str:
    s = (scene or "").lower()
    for name, ch in channels().items():
        if not ch.get("enabled"):
            continue
        for p in ch.get("prefixes") or []:
            if s.startswith(p.lower()) or p.lower() in s:
                return name
    # 兜底：长得像微信 id 就走微信（但微信没启用就别硬塞）
    if "wxid_" in s or "@chatroom" in s:
        wx = channels().get("wechat") or {}
        if wx.get("enabled"):
            return "wechat"
    return ""


def switch(name: str) -> bool:
    return bool(switches().get(name, False))


def ensure_file() -> None:
    """第一次跑：把默认配置写出来，好让 UI 能编辑。"""
    if not CONFIG.exists():
        save(_merge(DEFAULTS, {}))
