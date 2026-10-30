"""第一次运行：给这套东西起一个人，并建好她的记忆目录。

    python tools/init_persona.py                    # 交互式问几个问题
    python tools/init_persona.py --name 阿鱼 --persona "二十一岁，女的，住在……"
    python tools/init_persona.py --force            # 已有 记忆/ 也重建（会备份）

做完这几件事：
  1. 没有 config.json 就从 config.example.json 复制一份
  2. 从 memory_template/ 复制出 记忆/
  3. 把「我是谁」写进 记忆/我是谁.md
  4. 同步 config.json 里的角色名、别名、记忆目录
  5. 建一张她自己的卡 记忆/我/<名字>.md
"""

from __future__ import annotations

import argparse
import json
import pathlib
import shutil
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TEMPLATE = ROOT / "memory_template"
DEFAULT_MEMORY = "记忆"


def ask(prompt: str, default: str = "") -> str:
    tip = f"（{default}）" if default else ""
    try:
        v = input(f"{prompt}{tip}: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        sys.exit(1)
    return v or default


def write_identity(memory: pathlib.Path, name: str, text: str) -> None:
    p = memory / "我是谁.md"
    body = [
        "# 我是谁",
        "",
        "> v2 每一拍都重读这一节。她可以自己用 `revise_self` 改，你也可以直接编辑这个文件。",
        "> 改完不用重启，下一轮「想」就生效。",
        "",
        f"你是{name}。",
        "",
        text.strip(),
        "",
    ]
    p.write_text("\n".join(body), encoding="utf-8")


def write_self_card(memory: pathlib.Path, name: str) -> None:
    d = memory / "我"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{name}.md"
    if p.exists():
        return
    p.write_text(
        f"# {name}\n\n"
        "- [f1] 身份：这就是她自己\n\n"
        "<!-- 她自己的卡。程序导出人物卡时会跳过她自己，这里留着方便你写设定。 -->\n",
        encoding="utf-8",
    )
    old = d / "_示例.md"
    if old.exists():
        old.unlink()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default="")
    ap.add_argument("--persona", default="")
    ap.add_argument("--aliases", default="", help="逗号分隔，别人怎么叫她")
    ap.add_argument("--memory", default=DEFAULT_MEMORY)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    name = args.name or ask("她叫什么", "阿鱼")
    aliases = args.aliases or ask("还有别的叫法吗（逗号分隔，可空）", "")
    persona = args.persona or ask(
        "一句话说她是谁（年龄/性别/底色，越短越稳）",
        "二十一岁，女的。话不多，但不冷；被问住了会老实说不知道。",
    )
    memory_dir = ROOT / args.memory

    # 1) config.json
    cfg_path = ROOT / "config.json"
    example = ROOT / "config.example.json"
    if not cfg_path.exists():
        if not example.exists():
            sys.exit("找不到 config.example.json，仓库不完整？")
        cfg_path.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"+ 生成 config.json（从示例复制）")
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))

    # 2) 记忆目录
    if memory_dir.exists() and any(memory_dir.iterdir()):
        if not args.force:
            print(f"= {memory_dir.name}/ 已存在，保留原内容（加 --force 重建）")
        else:
            bak = memory_dir.with_name(f"{memory_dir.name}.bak-{int(time.time())}")
            shutil.move(str(memory_dir), str(bak))
            print(f"+ 旧记忆备份到 {bak.name}")
    if not memory_dir.exists():
        if not TEMPLATE.exists():
            sys.exit("找不到 memory_template/，仓库不完整？")
        shutil.copytree(TEMPLATE, memory_dir)
        print(f"+ 建立 {memory_dir.name}/（来自 memory_template/）")

    # 3) 我是谁
    write_identity(memory_dir, name, persona)
    print(f"+ 写入 {memory_dir.name}/我是谁.md")

    # 4) config 同步
    names = [name] + [a.strip() for a in aliases.split(",") if a.strip()]
    profiles = cfg.setdefault("profiles", {})
    old_profile = profiles.pop(cfg.get("active") or name, None)
    cfg["active"] = name
    cfg["viewing"] = name
    prof = {"dir": "data", "memory": args.memory, "persona": "我是谁.md",
            "channels": (old_profile or {}).get("channels", ["qq"]),
            "thresholds": (old_profile or {}).get("thresholds", {})}
    profiles[name] = prof
    cfg.setdefault("identity", {})["names"] = names
    cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"+ config.json：角色 {name}，叫法 {'/'.join(names)}")

    # 5) 她自己的卡
    write_self_card(memory_dir, name)

    print()
    print("下一步：")
    print("  1. 填模型：python tools/panel.py   浏览器里打开 http://127.0.0.1:8788")
    print("     或者设置环境变量 DEEPSEEK_API_KEY / OPENAI_API_KEY")
    print("  2. 接通道：docs/channels.md（QQ 最快，微信要自备 hook）")
    print("  3. 起服务：双击 start.bat → 9 启动v2（自主模式）；或 tools/ctl.py start")


if __name__ == "__main__":
    main()
