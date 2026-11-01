"""真实 LLM 冒烟：确认预设 + 称呼 + 任务在真模型下的表现。

    python tests/test_live_llm.py

会真的调模型（走 DSH 网关或环境变量里的 key），所以默认不在 CI 跑。
没有可用 key 时自动跳过，不报失败。
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import sys
import tempfile
import time

TMP = pathlib.Path(tempfile.mkdtemp(prefix="inhabit-llm-"))
os.environ["DAFEIYU_ROOT"] = str(TMP)
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _setup():
    shutil.copytree(ROOT / "presets" / "dafeiyu" / "persona", TMP / "记忆", dirs_exist_ok=True)
    (TMP / "config.json").write_text(json.dumps({
        "active": "鲸鲸",
        "profiles": {"鲸鲸": {"dir": "data", "memory": "记忆", "persona": "我是谁.md"}},
        "identity": {"names": ["鲸鲸", "哦鲸鲸", "本鱼"]},
        "api": {"base_url": "", "api_key": "", "model": ""},
    }, ensure_ascii=False), encoding="utf-8")
    from core import config
    config._cache.clear()
    config._mtime = 0.0


def _brain():
    """优先用 config，再退回 DSH 网关。没有就返回 None（跳过）。"""
    from core.brain import make_brain
    try:
        return make_brain("auto")
    except SystemExit:
        pass
    keyfile = pathlib.Path.home() / ".dsh" / "openai-gateway" / "api-key"
    if keyfile.exists():
        from core.brain import OpenAICompatBrain
        return OpenAICompatBrain(
            api_key=keyfile.read_text(encoding="utf-8").strip(),
            base_url=os.environ.get("DSH_GATEWAY_BASE", "http://127.0.0.1:8326/v1"),
            model=os.environ.get("DSH_GATEWAY_MODEL", "deepseek-account/deepseek-flash"),
            max_tokens=200, proxy=None, extra={"reasoning_effort": "none"})
    return None


def main() -> int:
    _setup()
    brain = _brain()
    if brain is None:
        print("跳过：没有可用的模型 key")
        return 0

    from core import persona
    from core.act import _address_block
    from core import address

    core_text, rules = persona.load_persona()
    assert core_text, "人设没读到"
    sysmsg = persona.build_system_prompt(core_text, rules, {}, "", where="私聊")

    print("=" * 60)
    print("真实模型冒烟（每次调用会花 token）")
    print("=" * 60)

    results = {}

    # 1) 正常说话：看语气像不像
    try:
        r1 = brain.speak(system=sysmsg, user="爸爸：在吗\n\n# 现在你要做什么？只输出那行 JSON。")
        r1 = (r1 or "").strip()
        print(f"\n[正常] {r1[:160]}")
        results["正常回话"] = bool(r1) and len(r1) < 300
    except Exception as e:
        print(f"\n[正常] 失败：{e}")
        results["正常回话"] = None

    # 2) 被叫错名字
    (TMP / "记忆" / "称呼.md").write_text(
        "# 称呼\n\n## 可以这么叫我\n\n- 鲸鲸\n\n## 别这么叫我\n\n- 大肥鱼\n",
        encoding="utf-8")
    from memory import store
    con = store.init_db(TMP / "feiyu.db")
    hit = address.detect("大肥鱼你在干嘛", bot_names=("鲸鲸",))
    hit["count"] = address.bump_wrong(con, "大肥鱼")
    blk = _address_block(hit)
    try:
        r2 = brain.speak(
            system=sysmsg,
            user=("大肥鱼：大肥鱼你在干嘛\n" + blk
                  + "\n\n# 现在你要做什么？只输出那行 JSON。"))
        r2 = (r2 or "").strip()
        print(f"\n[被叫错] {r2[:160]}")
        # 有反应即可 —— 具体措辞不该由测试规定
        results["被叫错有反应"] = bool(r2)
        results["没认这个名"] = "大肥鱼" not in r2[:40] or "不" in r2 or "叫" in r2
    except Exception as e:
        print(f"\n[被叫错] 失败：{e}")
        results["被叫错有反应"] = None

    # 3) 任务要能想起来
    from core import tasks
    tasks.add_task(con, "答应帮他查那个软件怎么装", who="爸爸", when="今天")
    con.commit()
    from core.act import _initial_prompt
    from core.mind import Thought
    from core.self import Self
    bot = Self(con, bot_name="鲸鲸")
    p = _initial_prompt(bot, Thought(content="想起件事", urge=0.6, plan="reply"),
                        "qq_private_1")
    got = "查那个软件" in p
    print(f"\n[任务] prompt 里带上了任务：{got}")
    results["任务进 prompt"] = got

    print("\n" + "-" * 60)
    bad = [k for k, v in results.items() if v is False]
    for k, v in results.items():
        print(f"  {'ok  ' if v else ('FAIL' if v is False else 'skip')} {k}")
    if bad:
        print(f"\n{len(bad)} 项不理想：{bad}")
        return 1
    print("\n全部符合预期")
    return 0


if __name__ == "__main__":
    sys.exit(main())
