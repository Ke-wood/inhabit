"""调参台：拿一段真实聊天记录，扫不同的发言阈值，看它会不会变成话痨或哑巴。

    python tools/tune.py                 # 用内置演示脚本
    python tools/tune.py --transcript x.jsonl

transcript 每行：{"name": "...", "text": "...", "offset": -120, "expect": true}
expect=true 表示这条"应该接"（人工标注的期望），用于算命中率。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.brain import make_brain
from core.gate import Gate
from core.loop import HeartFlow, Msg
from memory import store

DEMO_EXPECT = {"@阿鱼", "好烦啊"}


def load(path: str | None) -> list[tuple[str, str, float, bool]]:
    if not path:
        from run_demo import SCRIPT
        return [(n, t, float(o), any(k in t for k in DEMO_EXPECT)) for o, n, t in SCRIPT]
    rows = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        d = json.loads(line)
        rows.append((d["name"], d["text"], float(d.get("offset", 0)), bool(d.get("expect"))))
    return rows


def run_once(rows, threshold: float, seed: int = 11):
    db = store.DB_PATH.parent / "_tune.db"
    if db.exists():
        db.unlink()
    con = store.init_db(db)
    from run_demo import seed as seed_memory
    seed_memory(con)
    gate = Gate(threshold=threshold)
    gate.rng.seed(seed)
    flow = HeartFlow(con, scene="g1", bot_name="阿鱼", brain=make_brain("mock"),
                     gate=gate, log_candidates=False)
    now0 = time.time()
    out = []
    for name, text, off, expect in rows:
        ts = now0 + off
        d = flow.tick([Msg(name, name, text, ts)], ts)
        out.append((name, text, expect, d.speak, d.prob))
    con.close()
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--transcript", default=None)
    ap.add_argument("--lo", type=float, default=0.44)
    ap.add_argument("--hi", type=float, default=0.66)
    ap.add_argument("--step", type=float, default=0.02)
    args = ap.parse_args()

    rows = load(args.transcript)
    n = len(rows)
    n_expect = sum(1 for r in rows if r[3])
    print(f"聊天记录 {n} 条，其中标注应回 {n_expect} 条\n")
    print(f"{'阈值':>5} {'开口':>4} {'应回命中':>8} {'误开口':>6}  判断")
    print("-" * 52)

    thr = args.lo
    while thr <= args.hi + 1e-9:
        res = run_once(rows, round(thr, 2))
        spoke = [r for r in res if r[3]]
        hit = sum(1 for r in spoke if r[2])
        false_pos = sum(1 for r in spoke if not r[2])
        rate = len(spoke) / n
        if rate > 0.45:
            verdict = "太吵"
        elif rate < 0.10 and n_expect:
            verdict = "太闷"
        else:
            verdict = "可以"
        print(f"{thr:>5.2f} {len(spoke):>4} {hit:>4}/{n_expect:<3} {false_pos:>6}  {verdict}  (占比{rate:.0%})")
        thr = round(thr + args.step, 2)

    print("\n最近一次运行的逐条概率：")
    for name, text, expect, speak, p in run_once(rows, round(args.hi, 2)):
        flag = "★" if expect else " "
        mark = "说" if speak else "默"
        print(f"  {flag}{mark} p={p:.2f}  {name}：{text[:28]}")


if __name__ == "__main__":
    main()
