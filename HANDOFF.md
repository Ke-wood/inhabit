# 交接文档

> 写给下一个接手的人（或 AI）。读这一份就能上手，不用翻代码。
> 最后更新：2026-11-02 · 版本 **v1.5.0**

---

## 一、这是什么

Inhabit 是一个**持续运行的 AI 心智体**。它有自己的记忆、人格、关系、作息，
每隔一会儿自己醒一次，想事情，想让谁看见就说一句，不想说就不说。

**不是**「收到消息 → 生成回复」的机器人。

两个入口，同一套内核：

| | 应答模式（v1） | 自主模式（v2） |
|---|---|---|
| 启动 | `python inhabit.py run` | `python inhabit.py run --v2` |
| 决策 | 概率门 + 频率闸，`always_reply` 一开就是每条都回 | 先想出一条念头，冲动压过犹豫才开口 |
| 适合 | 剧本杀 NPC、客服、陪聊 | 群友、长期陪伴 |
| 记忆 | 每轮固定注入 | 她自己决定要不要 `recall` |

**默认是 v1。** 直播/桌宠这类需要稳定响应的场景，v1 更合适。

---

## 二、项目在哪

| | 位置 |
|---|---|
| 本地开发目录 | `D:\plan\dafeiyu v1` |
| GitHub | <https://github.com/Ke-wood/inhabit> |
| 当前版本 | v1.5.0 |
| 许可 | MIT（代码）；第三方与素材见 `THIRD_PARTY.md` |

规模：核心代码约 13,000 行 Python，12 个测试文件共 130 项检查。

```
core/       32 个模块，6977 行  ← 心智本体
channels/    5 个模块，1066 行  ← QQ / 微信 / HTTP 通道
bili/        3 个模块，  553 行  ← B 站只读接口
tools/      21 个脚本， 4465 行  ← 面板、启停、更新、桌宠
tests/      12 个文件， 2722 行  ← 130 项检查
docs/       12 篇文档
presets/     现成人设（dafeiyu）
memory_template/  空白记忆模板
```

---

## 三、怎么跑起来

```bash
python inhabit.py                   # 第一次跑会引导初始化
python inhabit.py init --preset dafeiyu    # 或直接套现成人设
export DEEPSEEK_API_KEY=sk-...      # 任何 OpenAI 兼容端点都行
python inhabit.py panel             # 控制面板 → 127.0.0.1:8788
python inhabit.py doctor            # 体检
```

**零第三方依赖**是硬卖点：运行时全是标准库。`requirements.txt` 里 4 项都是可选的，
缺哪个就退回更简单的实现（没装 bs4 用正则抓网页，没装 edge-tts 就发文字不发语音）。

---

## 四、核心架构（读懂这四层就懂了）

```
┌─────────────────────────────────────────────────┐
│ 存在层 Heartbeat（2 秒轮询，纯本地，零 LLM）        │
│ 时间·精力·睡眠·念头发酵·注意力判断                 │
└────────────────────┬────────────────────────────┘
                     │ 有新消息 / 想开口 / 闲够久
┌────────────────────▼────────────────────────────┐
│ 心智层 Think（1 次 LLM）                          │
│ 产出「一条念头」：内容 + 冲动 + 犹豫 + 计划        │
└────────────────────┬────────────────────────────┘
                     │ 冲动压过犹豫
┌────────────────────▼────────────────────────────┐
│ 行动层 Act（最多 3 步工具循环）                    │
│ 说/发图/发语音/看图/看B站/搜/记/忘/改人设/睡/…     │
└────────────────────┬────────────────────────────┘
                     │
              结果回流到存在层
```

| 层 | 文件 | 入口 |
|---|---|---|
| 存在层 | `core/live.py` | `Live.tick()` |
| 心智层 | `core/mind.py` | `think()` |
| 行动层 | `core/act.py` | `act()` |
| 记忆 | `memory/store.py` | `recall()` / `add_event()` |

**一拍** = 一次 `Live.tick()`：
护栏 → 取输入 → 想 → 裁决（冲动 vs 犹豫）→ 做 → 落库。

细节见 `docs/architecture.md`。

---

## 五、这一版新加的四件事

### 1. 桌宠（`desk:` 场景）

只做三件事：**看屏幕**（值不值得看由注意力判断）、**收你的话**（输入栏）、
**用气泡回你**。不做语音、不散步、不蹦跳。

```bash
pip install -r requirements-pet.txt   # 只装 PySide6
python tools/fetch_pet_sprites.py     # 下载素材（不进仓库）
python tools/panel.py                 # 面板勾选「启用桌宠」
```

关键文件：`tools/pet_window.py`、`core/pet_link.py`、`core/pet_input.py`

**素材不入库**：美术版权归原作者（`1190fasheqi/dafeiyu-pet`），MIT 只覆盖代码。

### 2. 场景节奏（`core/scenes.py`）

| | chat | desk | live |
|---|---|---|---|
| min_think_gap | 20s | 60s | **0.5s** |
| recall_limit | 12 | 8 | **3** |
| facts_here | 8 | 6 | **2** |
| max_steps | 3 | 1 | 1 |

**chat 画像直接读 config，不抄常量** —— 这是 QQ 零回归的保证。

直播压上下文靠「少取」不是「少存」：她直播时不需要想起三天前的事。

### 3. 注意力判断（`core/attention.py`）

决定「这一刻值不值得动」：沉默时长 / 有没有人说话 / 环境有没有新东西 / 精力。
**零模型调用，纯算术**，接在时间闸之后（AND 关系，不是替代）。

### 4. 流式输出

`core/brain.py` 的 `speak_stream()` 真 SSE；`core/sentencer.py` 切句
（遇句末标点切、逗号不切、40/60 字兜底）。真机实测首字提前 214ms。

**`speak()` 行为完全不变** —— 6 个调用方依赖它。

---

## 六、要小心的地方（这些是踩过的）

### 🔴 改动前必读

**1. `channel_for()` 曾经是子串匹配**
`live:qq_room` 会被判成 QQ 渠道。现在是前缀精确匹配 + 本地场景短路。
改它之前先看 `tests/test_scenes.py` 的「场景路由不误判」。

**2. `scene_belongs_to()` 的连锁反应**
`channel_for` 一改前缀匹配，`desk:x` 就从「认不出渠道」变成「属于 desk 渠道」，
于是多角色同跑时桌宠消息被丢弃。已在 `config.py` 用 `LOCAL_SCENE_CHANNELS` 短路。

**3. `facts_here` 的 8 与 10 语义分裂**
config 说 `facts_here=8`，代码历史默认是 10，且调用方从不传 limit ——
**config 里这个字段一直是死配置**。现在 chat 仍走 10（不回归），没去修，
因为那会改 QQ 的 prompt 长度。要不要统一，是产品决策。

**4. `int()` 画图会卡顿**
`drawPixmap(int(x), int(y), pix)` 把浮点呼吸量化成整数像素，
40 帧只剩 2 个 y 值。必须用 `QRectF` 走亚像素插值。桌宠的呼吸就是这么修的。

**5. 防幻觉守卫会 fail-open**
nanoMuse 的一个真实 bug：`tokenize()` 对西里尔字母返回空集 →
「没有新词」→ 任何改写都放行。
**规则：所有守卫，无法判断时默认拒绝，不是默认通过。**

**6. 感知事件绝不能进 `events` 主表**
每秒 1 条 = 86,400 条/天，会把 `recall()` 的候选集挤爆，聊天记忆再也召不回来。

### 🟡 已知但没修

- `poll_sec` 改了 `run_wx.py` 的 v2 循环，但 `inhabit.py` 等其他入口若也有
  `time.sleep(2.0)`，live 场景仍快不起来（已 grep，目前只剩 `bili_login_tv.py` 有）
- 单场景模型：`Live` 只能有一个「当前场景」，同时挂 QQ 和直播间需要按场景分片调度
- `recall_limit` 只作用于每轮注入，`act.py:577` 的 `recall` 动作仍硬编码 6
- 桌宠多显示器不会自动跟随换屏；150% DPI 下位置会有偏差
- 桌面音频/麦克风（听觉）完全没做

---

## 七、明确没做的

| 功能 | 状态 | 为什么 |
|---|---|---|
| **Live2D 实时控制** | 🟡 代码就绪，未接通 | `core/avatar.py` 的 `VTSBackend` 已写好（认证/参数注入/热键），但需要用户在 VTube Studio 里打开 API（设置 → API → 允许），端口 8001。**本机未验证过真连** |
| 口型同步 | ❌ 未做 | 需要解码 TTS 的 MP3（Python 3.13 已移除 `audioop`），必须引入 `miniaudio` 之类依赖 |
| 直播弹幕 | ❌ 未做 | `bilibili-api` 已被律师函关停（4164★ 项目），`blivedm` 仍在但属灰色地带 |
| 视觉感知（看屏幕内容） | ❌ 未做 | `_novelty()` 现在恒返回 0，即「没检测到新东西」。要做需要截屏 + 变化检测 + 视觉模型 |
| 听觉 | ❌ 未做 | `soundcard` 在 macOS 不支持回环，BlackHole 是 GPL |
| 语音合成实时化 | 🟡 单条可用 | edge-tts 首字节 1.09s，够发语音，不够打电话 |

---

## 八、代码地图（找东西用）

| 想改 | 去哪 |
|---|---|
| 她的判断逻辑（什么时候说） | `core/live.py: tick()` |
| 她想什么（prompt） | `core/mind.py: THINK_SYSTEM` |
| 她能做什么（动作表） | `core/act.py: ACT_SYSTEM` + 动作分支 |
| 记忆怎么存取 | `memory/store.py: recall()` |
| 人格怎么读 | `core/persona.py` + `记忆/我是谁.md` |
| 别人叫她什么 | `core/address.py` + `记忆/称呼.md` |
| 答应的事 | `core/tasks.py` |
| 节奏/场景 | `core/scenes.py` |
| 值不值得动 | `core/attention.py` |
| 通道 | `channels/router.py` |
| 桌宠 | `tools/pet_window.py` |
| 面板 | `tools/panel.py` + `web/panel.html` |
| 更新 | `tools/update.py` + `core/version.py` |

---

## 九、怎么改一个功能（流程）

1. **先跑一遍现有测试**，确认基线是绿的
   ```bash
   for t in tests/*.py; do python "$t" | tail -1; done
   ```
   注：Windows GBK 控制台下 `test_smoke` 会因 `⚠` 字符报编码错，用 `PYTHONUTF8=1`

2. **改代码**，关键决策写「为什么」注释，不是「做了什么」

3. **补测试**，尤其是回归保护（改了 A 不能弄坏 B）

4. **跑全量**，130 项全绿才算完成

5. **升版本号**：`VERSION` 是唯一真相，改完同步 `core/config.py` 和 `config.example.json`

6. **提交**：一句话说清「修了什么、为什么」

7. **发版**：`git tag vX.Y.Z && git push --tags`，再在 GitHub 建 release
   （不建 release 也能更新，但用户看不到更新说明）

---

## 十、发版 SOP

```bash
# 1. 改版本号（三处要一致）
echo "1.6.0" > VERSION
# core/config.py 的 DEFAULTS["version"]
# config.example.json 的 version
# CHANGELOG.md 加一节

# 2. 跑全量测试
for t in tests/*.py; do python "$t" | tail -1; done

# 3. 提交 + 打 tag + 推
git add -A && git commit -m "feat: ..."
git tag -a v1.6.0 -m "..."
git push origin main v1.6.0

# 4. 建 release（用 GitHub API 或网页）
```

**更新的白名单在 `core/version.py` 的 `classify()`**：
只覆盖程序文件，`data/`、`记忆/`、`config.json`、`.env` 永不触碰。
改这个文件前先跑 `tests/test_update.py`。

---

## 十一、给下一个人的三句话

1. **不要动 `channel_for` 和 `scene_belongs_to`，除非你先读了它们的测试。**
   这两个函数连锁影响消息进不进得来，改错的表现是「她突然不理人」。

2. **她的行为靠 prompt，不靠代码。**
   想让她说话更像人，改 `core/mind.py` / `core/act.py` 的 SYSTEM 字符串，
   不用改逻辑。改完记得**重启**（Python 不热重载，旧 prompt 会继续产出旧格式记忆）。

3. **所有「让她更活跃」的改动，先问一句「用户想不想被这么打扰」。**
   这个项目最有价值的判断是「沉默是默认动作」——
   加功能容易，加克制难。

---

## 十二、紧急问题怎么办

| 症状 | 先看 |
|---|---|
| 她不说话 | `python inhabit.py doctor`；再看日志里有没有「没什么值得说的」 |
| 启动就退出 | 8790 端口被占（单例锁）/ 没 API key |
| 收不到消息 | `channels/wxhook.py serve 8771` 起了没；8871 端口通不通 |
| 面板打不开 | 8788 被占；另一个面板实例还活着 |
| 桌宠不见了 | 托盘图标右键「回到右下角」；或面板「桌宠」卡片看是不是 running |
| 记忆不涨 | 主循环真的在处理吗？看 `data/logs/` 和 `data/wx_inbox.jsonl` 行数 |
| 更新失败 | `python inhabit.py rollback` 退回；备份在 `data/backups/` |

日志位置：`data/logs/`。没有日志目录 = 进程根本没走到那一步。
