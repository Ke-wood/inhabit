"""后台整理工：没有自我，只做机械活。

职责只有四件，轮流做，一次一件，跑在没人说话的空档：
- 从最近消息里抽结构化事实（reflect.extract）
- 按互动更新好感度（relations.touch）
- 深夜写日记（diary.write）
- 把库导回「记忆」文件、归档旧事件

边界：
- 她自己 remember / feel 的结果优先，这里产生的事实 confidence 更低
- 不写口吻、不做判断、不改她的自我设定
- 一次只做一件，避免长时间占住主循环
"""

from __future__ import annotations

import time
from datetime import datetime

from core import diary, memfiles, reflect, relations
from core.brain import Brain
from memory import store


class Janitor:
    def __init__(self, con, brain: Brain, *, log=print, interval: float = 1500.0):
        self.con = con
        self.brain = brain
        self.log = log
        self.interval = interval          # 两次整理之间至少隔多久
        self.last_run = 0.0
        self.last_export = 0.0
        self.last_decay = 0.0
        self.cursor = 0
        self.fact_cursor = self._load_cursor("janitor_fact_cursor")
        self.rel_cursor = self._load_cursor("janitor_rel_cursor")
        self.stats = {"facts": 0, "relations": 0, "diary": 0, "export": 0, "decay": 0}

    def _load_cursor(self, key: str) -> int:
        """整理进度要能让重启接上，不然关机期间的事就永远不整理了。"""
        try:
            row = self.con.execute(
                "SELECT value FROM self_state WHERE key=?", (key,)).fetchone()
            if row:
                return int(row[0])
        except Exception:
            pass
        return self._max_event_id()

    def _save_cursor(self, key: str, value: int) -> None:
        try:
            store.set_state(self.con, key, str(value))
            self.con.commit()
        except Exception:
            pass

    def _max_event_id(self) -> int:
        try:
            return int(self.con.execute(
                "SELECT COALESCE(MAX(id),0) FROM events").fetchone()[0])
        except Exception:
            return 0

    # ---------------------------------------------------------------- 一拍
    def tick(self, now: float | None = None) -> dict:
        now = now or time.time()
        if now - self.last_run < self.interval:
            return {}
        self.last_run = now

        dt = datetime.fromtimestamp(now)
        if diary.should_write(self.con, dt):
            try:
                text = diary.write(self.con, self.brain, dt)
                if text:
                    self.stats["diary"] += 1
                    self.log(f"· 整理：写了今天的日记（{len(text)} 字）")
                    return {"diary": text}
            except Exception as e:
                self.log(f"· 整理：写日记失败 {type(e).__name__}: {e}")

        jobs = [self._extract, self._relations, self._export, self._decay]
        for _ in range(len(jobs)):
            job = jobs[self.cursor % len(jobs)]
            self.cursor += 1
            try:
                r = job(now)
                if r:
                    return r
            except Exception as e:
                self.log(f"· 整理：{job.__name__} 失败 {type(e).__name__}: {e}")
        return {}

    # ---------------------------------------------------------------- 四件事
    def _extract(self, now: float) -> dict:
        added, cursor = reflect.extract(self.con, self.brain, since_id=self.fact_cursor)
        self.fact_cursor = cursor
        self._save_cursor("janitor_fact_cursor", cursor)
        if added:
            self.stats["facts"] += len(added)
            brief = "；".join(f"{a['who']}{a['fact']}" for a in added[:3])
            self.log(f"· 整理：记住 {len(added)} 条（{brief}）")
            return {"facts": added}
        return {}

    def _relations(self, now: float) -> dict:
        rows = self.con.execute(
            "SELECT id, ts, actor_name, text, scene FROM events "
            "WHERE id > ? AND kind='message' AND actor_name IS NOT NULL "
            "AND actor_name NOT IN ('阿鱼','群里','某人','对方') "
            "ORDER BY id LIMIT 40", (self.rel_cursor,)).fetchall()
        if not rows:
            return {}

        touched = 0
        people: list[str] = []
        for r in rows:
            replied = self.con.execute(
                "SELECT 1 FROM events WHERE kind='speech' AND scene=? AND id > ? LIMIT 1",
                (r["scene"], r["id"])).fetchone()
            rel = relations.touch(self.con, r["actor_name"],
                                  replied=bool(replied), text=r["text"])
            if rel and abs(rel.get("delta", 0)) >= 0.1:
                touched += 1
                if r["actor_name"] not in people:
                    people.append(r["actor_name"])
            self.rel_cursor = r["id"]
        self._save_cursor("janitor_rel_cursor", self.rel_cursor)
        self.con.commit()
        if touched:
            self.stats["relations"] += touched
            who = "、".join(people[:4]) + ("等" if len(people) > 4 else "")
            self.log(f"· 整理：看了 {len(rows)} 条消息，{len(people)} 个人的好感有变动"
                     f"（{who}）")
            return {"relations": len(people)}
        return {}

    def _export(self, now: float) -> dict:
        if now - self.last_export < 1800:
            return {}
        try:
            n = memfiles.export_people(self.con)
        except Exception as e:
            self.log(f"· 整理：导出失败 {type(e).__name__}: {e}")
            return {}
        self.last_export = now
        self.stats["export"] += 1
        self.log(f"· 整理：把 {n} 个人物档案写回「记忆」")
        return {"export": n}

    def _decay(self, now: float) -> dict:
        if now - self.last_decay < 7 * 86400:
            return {}
        try:
            n = store.decay(self.con)
            self.con.commit()
        except Exception as e:
            self.log(f"· 整理：归档失败 {type(e).__name__}: {e}")
            return {}
        self.last_decay = now
        if n:
            self.stats["decay"] += n
            self.log(f"· 整理：归档了 {n} 条旧事件")
            return {"decay": n}
        return {}
