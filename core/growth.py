"""生长：把"日常对话里值得改的东西"记成候选，而不是当场改人格。

快层（每轮状态）自动变；慢层（人格文本、发言系数、禁区）必须经过人审。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from core.paths import ROOT
CANDIDATES = ROOT / "persona" / "candidates.md"

HEADER = ("# 候选修订\n\n"
          "> 由对话自动追加。**不要自动合入**，人工审核后搬到 persona.md 并记进 CHANGELOG。\n\n")


def _ensure() -> None:
    if not CANDIDATES.exists():
        CANDIDATES.write_text(HEADER, encoding="utf-8")


def note(kind: str, text: str, *, why: str = "") -> None:
    """记一条候选。同一天同类同一句话只记一次。"""
    _ensure()
    body = CANDIDATES.read_text(encoding="utf-8")
    if f"· {kind} · {text}" in body:
        return
    stamp = datetime.now().strftime("%m-%d %H:%M")
    with CANDIDATES.open("a", encoding="utf-8") as f:
        f.write(f"- {stamp} · {kind} · {text}" + (f"（{why}）" if why else "") + "\n")
