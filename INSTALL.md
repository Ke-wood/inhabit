# 安装

> 目标：从一台干净的机器，到「她能在终端里自言自语」。
> 大约 10 分钟，不需要 GPU，不需要 Docker。

---

## 1. 需要什么

| 项 | 要求 | 说明 |
|---|---|---|
| Python | 3.10+（3.12 / 3.14 都跑过） | 运行时纯标准库 |
| 系统 | Windows / macOS / Linux | 微信通道仅 Windows（且要自备 hook） |
| 磁盘 | 源码约 1 MB | 装 biliup 再 +74 MB（可选） |
| 网络 | 能访问你的模型服务 | 本地 Ollama 也行 |
| 模型 | 任意 OpenAI 兼容端点 | DeepSeek / OpenAI / Kimi / Ollama |

可选依赖（不装也能跑）：`pip install -r requirements.txt`

---

## 2. 六步

### 第 1 步：拿代码

```bash
git clone https://github.com/Ke-wood/inhabit.git
cd inhabit
```

路径尽量别含空格和中文以外的奇怪字符（不是硬性要求，但省事）。

### 第 2 步：建角色

```bash
python inhabit.py init
```

会问你：叫什么、还有别的叫法吗、一句话说她是谁。
然后生成 `config.json`、`记忆/`（从 `memory_template/` 复制）、`记忆/我是谁.md`。

想跳过交互：

```bash
python inhabit.py init --name 阿鱼 --persona "二十一岁，女的，话不多但不冷"
```

### 第 3 步：接大脑

二选一：

**A. 环境变量（最快）**

```bash
export DEEPSEEK_API_KEY=sk-...
# 或 OPENAI_API_KEY；换 base_url 见 docs/reference.md
```

**B. 面板里填**

```bash
python inhabit.py panel        # 打开 http://127.0.0.1:8788
```

「配置」页填 `base_url` / `api_key` / `model`，保存即生效。

### 第 4 步：先离线看一遍

```bash
python run_demo.py
```

不联网、不碰真数据，用 MockBrain 演一段群聊，看她的开口节奏。
看到「说 / 不说」交替出现，说明门控在正常工作。

### 第 5 步：接一个通道

最快是 QQ（OneBot 11）：

1. 起一个网关（NapCat / Lagrange / go-cqhttp）
2. 把事件上报指到 `http://127.0.0.1:8771/cb`
3. 确认 `config.json` 的 `channels.qq.base_url` 对得上

细节见 [docs/channels.md](docs/channels.md)。微信要自备 hook，见同一篇。

### 第 6 步：起服务

```bash
python channels/wxhook.py serve 8771     # 收件（或让 ctl 一起拉起）
python inhabit.py run --v2                    # 自主模式
# 或
python run_wx.py                         # 应答/门控模式
```

Windows 上也可以双击 `start.bat` 用菜单，或双击 `面板.bat` 开面板。

---

## 3. 验证

| 检查 | 怎么做 | 正常表现 |
|---|---|---|
| 能通模型 | `python run_wx.py --brain mock` 不报错；换真 key 后跑一次 | 日志里有念头/回复 |
| 收件正常 | 给自己发一条消息 | `data/wx_inbox.jsonl` 多一行 |
| 记忆在写 | 面板「记忆」页 | 事件数在涨 |
| 面板能开 | `127.0.0.1:8788` | 能看到状态、阈值、人物卡 |
| 冒烟测试 | `python tests/test_smoke.py` | 5 项全过 |

---

## 4. 十条实际踩过的坑

1. **只能跑一个主循环。**
   两个 `run_wx.py` 同时读写同一个 inbox，每条消息会被处理两遍，她像两个人一起说话。
   防护是启动时占住 `8790`（每角色 +1）。看到「已经有一个在跑」就先去结束残留进程。

2. **改了代码必须重启。**
   Python 不热重载。改了 `core/` / `memory/` 之后要停掉再起。
   特别提醒：改 prompt 不重启，旧 prompt 会继续产出旧格式的记忆。

3. **同一条消息可能被推两次。**
   收件端用 `msgid` 做去重，但那是个固定大小的集合，满了会整体清空（会周期性失效）。
   目前没有严格的入站去重，靠单例锁从源头杜绝。

4. **`data/pids/*.pid` 可能写坏。**
   历史遗留：两个进程同时写会拼成一串数字。现在判断是否在跑，优先看端口，pid 文件只作辅助。

5. **悬着的事必须主动关。**
   `open_loops` 里每条都会进 prompt。只增不减，她会反复问同一个问题。
   用 `resolve`（有答案）或 `drop`（不要答案了）关掉；相似的事按 bigram 重合度合并。

6. **事实要写第一人称。**
   早期规则让模型写「给某某调了五分钟」，导致她记忆里的自己像第三方，出现人格分裂感。
   现在规则是第一人称（「给我调了五分钟」）。历史数据要批量改写时小心，别误伤别人的话。

7. **文件传输助手 / 新闻号要挡掉。**
   它们的消息是结构化 XML，不是人话。已在两层过滤：收件端按 id 前缀和 XML 开头丢，
   主循环再兜底一次。又冒出怪消息，先看它的 `type` 和 `content` 开头。

8. **不要用测试数据污染记忆。**
   任何「试着调用一下」的动作，先用不存在的 id 验证。
   早期测试误发过真实视频和评论，痕迹留在库里很久。

9. **hook 的登录态不可信。**
   查库接口返回的 `IsLogin` 不一定是真的；真正的检查是 `GetSelfProfile` 能不能拿到资料。

10. **进程可能在沙箱里看不见。**
    如果通过某个 agent 环境启动，进程可能跨会话不可见。
    长期跑建议你自己开一个终端，或做成系统服务。

---

## 5. 排错

| 症状 | 先看哪 |
|---|---|
| 启动就退出 | 日志第一行；单例端口被占 / 没 API key |
| 收不到消息 | `python channels/wxhook.py serve 8771` 起了没；网关的 callback 指对了没 |
| 她不说话 | 是不是自主模式且 `urge` 一直很低？先 `--brain mock` 看流程 |
| 面板打不开 | 8788 被占 / 另一个面板实例还活着 |
| 记忆不涨 | 主循环真的在处理消息吗？看 `data/logs/` 和 `data/wx_inbox.jsonl` 行数 |
| 中文乱码（Windows 控制台） | 用 Windows Terminal，或 `chcp 65001`；文件本身是 UTF-8 |

日志位置：`data/logs/`。没有日志目录就说明进程根本没走到那一步。

---

## 6. 打包成 exe（可选）

`shiyu.py` 是打包入口（PyInstaller）：

```bash
pyinstaller -F -n inhabit shiyu.py
```

打包后 `ROOT` 会跟着 exe 走（`core/paths.py`），`data/` 与 `记忆/` 放在 exe 旁边。
打包版不含 biliup 与任何模型。
