"""wxid / roomid -> 人、群。带别名。

微信 4.x 的数据库是加密的，hook 也抓不到句柄，所以名字靠人给。
格式（v2）：
{
  "people": {"wxid_x": {"name": "蛋", "aliases": ["璃香"]}},
  "rooms":  {"123@chatroom": {"name": "群名"}}
}
旧格式（值是字符串）会自动迁移。
"""

from __future__ import annotations

import json
import pathlib

from core.paths import ROOT
NAMES = ROOT / "data" / "wx_names.json"


def _normalize(d: dict) -> dict:
    people, rooms = {}, {}
    for wxid, v in (d.get("people") or {}).items():
        if isinstance(v, str):
            people[wxid] = {"name": v, "aliases": []}
        elif isinstance(v, dict):
            people[wxid] = {"name": v.get("name") or wxid,
                            "aliases": list(v.get("aliases") or [])}
    for rid, v in (d.get("rooms") or {}).items():
        rooms[rid] = v if isinstance(v, dict) else {"name": str(v), "aliases": []}
    return {"people": people, "rooms": rooms}


def load() -> dict:
    if not NAMES.exists():
        NAMES.parent.mkdir(parents=True, exist_ok=True)
        NAMES.write_text(json.dumps({"people": {}, "rooms": {}}, ensure_ascii=False, indent=2),
                         encoding="utf-8")
        return {"people": {}, "rooms": {}}
    try:
        raw = json.loads(NAMES.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        raw = {}
    return _normalize(raw)


def save(d: dict) -> None:
    NAMES.write_text(json.dumps(_normalize(d), ensure_ascii=False, indent=2), encoding="utf-8")


def person(wxid: str | None) -> dict | None:
    return load()["people"].get(wxid or "")


def label(wxid: str | None) -> str:
    p = person(wxid)
    if p:
        return p["name"]
    if not wxid:
        return "某人"
    tail = wxid.split("@")[0][-5:]
    return f"路人{tail}" if len(tail) > 2 else "路人"


def aliases(wxid: str | None) -> list[str]:
    p = person(wxid)
    return list(p["aliases"]) if p else []


def all_people() -> dict[str, dict]:
    return load()["people"]


def room(roomid: str | None) -> str | None:
    r = load()["rooms"].get(roomid or "")
    return r["name"] if r else None


def all_rooms() -> dict[str, dict]:
    return load()["rooms"]


def ensure_room(roomid: str) -> str:
    """没登记过的群，先给个临时名（群+后四位）并记下来，免得跟别的群混。

    以后知道真名了，直接改 wx_names.json 里的 name 就行。
    """
    if not roomid or not roomid.endswith("@chatroom"):
        return roomid or ""
    d = load()
    r = d["rooms"].get(roomid)
    if r and r.get("name"):
        return r["name"]
    name = "群" + roomid.split("@")[0][-4:]
    d["rooms"][roomid] = {"name": name, "aliases": list((r or {}).get("aliases") or [])}
    save(d)
    return name


def find_room(text: str | None) -> str | None:
    """按群名 / 别名 / roomid 反查房间 id，找不到返回 None。"""
    if not text:
        return None
    t = text.strip().strip("「」『』\"'《》 ")
    # 口语尾巴：「〇〇〇那个群」「群里」—— 去掉再认
    for suf in ("那个群", "这个群", "群里", "的群", "群"):
        if t.endswith(suf) and len(t) > len(suf):
            t = t[: -len(suf)]
            break
    if not t:
        return None
    rooms = load()["rooms"]
    if t in rooms:
        return t
    for rid, r in rooms.items():
        name = r.get("name") or ""
        if name and (t == name or t in name or name in t):
            return rid
        for a in r.get("aliases") or []:
            if a and (t == a or a in t):
                return rid
    return None


def find_person(text: str | None) -> str | None:
    """按名字 / 别名反查 wxid。"""
    if not text:
        return None
    t = text.strip().strip("「」『』\"'《》 ")
    if not t:
        return None
    for wxid, p in load()["people"].items():
        name = p.get("name") or ""
        if name and (t == name or t in name or name in t):
            return wxid
        for a in p.get("aliases") or []:
            if a and (t == a or a in t):
                return wxid
    return None


def find_any(text: str | None) -> str | None:
    """群或人都行 —— 她说"去找某人"和"去群里冒个泡"应该是同一类动作。"""
    return find_room(text) or find_person(text)
