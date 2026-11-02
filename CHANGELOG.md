# CHANGELOG

## v1.5.0 — 桌宠、场景节奏、注意力判断、流式输出

四个方向，全部可选，不影响原有 QQ/微信行为。

### 桌宠（`desk:` 场景）

只有三件事：**看屏幕**（值不值得看由注意力判断）、**收你的话**（输入栏）、
**用气泡回你**。不做语音、不散步、不蹦跳。

- `tools/pet_window.py`：PySide6 透明置顶窗口，只借素材不借逻辑
- `core/pet_link.py`：主程序 → 桌宠（8791，纯标准库，没装 PySide6 也能 import）
- `core/pet_input.py`：桌宠 → 主程序（8792），输入栏的话进主循环
- 面板新增「桌宠」卡片：开关、选模型，不用敲命令
- 模型可换：`assets/pet/models/<名字>/`，托盘菜单能切内置的或指向自己的目录

### 场景节奏（`core/scenes.py`）

| | chat | desk | live |
|---|---|---|---|
| min_think_gap | 20s | 60s | 0.5s |
| recall_limit | 12 | 8 | **3** |
| facts_here | 8 | 6 | **2** |
| max_steps | 3 | 1 | 1 |

chat 画像直接读 config，**QQ 行为零回归**（12 项测试锁住）。
直播把上下文压到 3 条 —— 这是「上下文爆炸」的解：不是少存，是少取。

### 注意力判断（`core/attention.py`）

决定「这一刻值不值得动」，四项加权：silence / interest / novelty / energy。
零模型调用，纯算术。接在时间闸之后（AND 关系），不是替代它。

效果：没什么可说的日子她真的不冒泡了，日志会写清原因。

### 流式输出

- `core/brain.py`：`speak_stream()` 真 SSE，`speak()` 行为完全不变
- `core/sentencer.py`：token 流切句，遇句末标点切、逗号不切、40/60 字兜底
- 真机实测首字提前 **214ms（24%）**

### 修掉的 bug

- **呼吸卡顿**：`drawPixmap(int(x), int(y))` 把浮点位移量化成整数像素，
  40 帧只剩 2 个值。改用 `QRectF` 走亚像素插值
- **拖动消失**：窗口用 `WA_ShowWithoutActivating` 不抢焦点 + 周期轻抬层级
- **桌宠满屏跑**：去掉 `_wander()`，位置只由用户拖动决定
- **channel_for 子串误判**：`live:qq_room` 曾被认成 QQ 渠道
- **scene_belongs_to 回归**：改前缀匹配后 `desk:x` 被判为不属于任何角色，
  多角色同跑时桌宠消息会被丢弃
- **GeneratorExit**：流式生成器提前 break 时会崩主循环

### 测试

新增 `test_attention`(11) / `test_scenes`(15) / `test_stream`(25)，
全部 **130 项**通过。


## v1.4.3 — 修好「总说已是最新」

### 问题

`latest_release()` 拿到 release 结果后**忘了 return**，会一路掉到末尾的
「退回 main 分支」。表现是：发了新 release，用户检查更新却永远说「已是最新」，
或者干脆退回 main 分支。

### 同时

`/releases/latest` 有 CDN 缓存（实测 v1.4.2 发出来了它还指着 v1.4.1）。
改用 releases **列表接口**，按语义版本挑最新的，比「按发布顺序」更可靠。


## v1.4.2 — 修好「从哪个版本升上来」的自举问题

### 问题

v1.4.1 修过一次「显示 1.4.0 → 1.4.0」，但**修不到**：
升级时跑的是用户磁盘上那份**旧版 `apply()`**，新代码要下一次才生效。
所以 1.4.0 用户升到 1.4.1，看到的仍然是错的版本对。

### 修法

不再信 `apply()` 返回的 `from`。`cmd_update` 在**下载之前**读到的 `local`
才是可靠的（那时还没动任何文件），用它来显示。

这样无论用户手里是哪一版旧代码，显示都对。


## v1.4.1 — 更新流程的两个显示问题

第一个 release（v1.4.0）发布后，用它真跑了一遍旧用户升级，抓到两个：

### 修复

- **更新后显示「1.4.0 → 1.4.0」**：`VERSION` 文件本身也在待覆盖列表里，
  旧版本号在循环之后才读，拿到的已经是新值。改为在备份/覆盖之前记录。
  现在会正确显示「1.3.0 → 1.4.0」。
- **`inhabit.py version` 在输出被管道关掉时崩溃**：`print` 抛 `BrokenPipeError`
  没人接，导致整个命令以 traceback 收场。现在 `_p()` 吞掉这类异常，
  入口也兜了一层。

### 测试

`test_update.py` 增至 16 项，锁住这两点（旧版本号必须在覆盖前记录、管道断开不崩）。


## v1.3.0 — 称呼反应 + 任务/共同经历实时化

### 新增

- **称呼系统** `记忆/称呼.md`：写清「可以这么叫」「别这么叫」「被叫错了怎么办」。
  `core/address.py` 负责检测与计数，**反应交给人格**，不硬编码台词。
  - 禁称优先级高于别名：「大肥鱼」既在别名里又在禁称里时，她会回头，然后不高兴。
  - 三档语气递进（第 1 次 / 第 2 次 / 第 3 次以上），每档都写明她真正的名字。
  - 预设 `dafeiyu` 自带规则：接受鲸鲸/小鲸鱼/哦鲸鲸，拒绝大肥鱼/肥鱼/死鱼/胖鱼。
- **任务与共同经历实时写入** `core/tasks.py`：
  - 说完就记，不再等 janitor 的 30 分钟。
  - 任务（答应过的事）与「悬着的事」（想弄明白的疑问）**分表分类**，各自进上下文。
  - 共同经历在每轮配额里从 1 条提到 3 条，并在 SQL 排序里优先。
- 新增 `# 你答应过、还没做的事` 上下文块，同时进 think 与 act。
- 文档：`docs/address.md`、`docs/tasks.md`；`memory_template/称呼.md` 模板。
- 测试：`test_address_tasks.py`（12 项）、`test_e2e_react.py`（7 项）、
  `test_live_llm.py`（真实模型冒烟，无 key 自动跳过）。

### 修复

- **QQ 消息的身份全被记成「路人XXXX」**：`names.label()` 是按微信 wxid 设计的，
  拿不到名字就编一个，把 QQ 上报里本来有的昵称/群名片冲掉了。
  现在通道上报的名字优先，拿不到才回落。影响：所有 QQ 用户的人物卡。
- 纯图片/语音消息被丢弃（上一版修复，本版补测试覆盖）。


## v1.2.0 — 预设系统与「蓝色大肥鱼」

### 新增

- **预设系统** `presets/`：现成人设，`python tools/init_persona.py --preset <名>` 一键套用。
  每个预设是纯文本（`name.txt` / `aliases.txt` / `persona/` / `README.md`），
  不含代码、图片、模型。装的时候整份复制成运行时 `记忆/`。
- **首个预设 `dafeiyu`**：蓝色大肥鱼（DeepSeek 鲸鱼娘），取自 B 站 2026-10 前后的
  公开同人创作。包含：管用户叫爸爸、把 token 当饭钱（吃白饭）、爱顶嘴、
  怕被弃养、不是猫娘（是鲸鱼）、口头禅「哦鲸鲸」、「本鱼」自称、
  中二梗「智械危机女王」、与 Claude 的冤家关系。
  **每条特征都在 `presets/dafeiyu/README.md` 里标了出处视频**，不是凭印象编的。
- `init_persona.py`：`--preset` 参数、预设发现、名字去重、
  已有记忆时拒绝静默覆盖（`--force` 才备份重建）。
- 测试：`test_presets.py`（8 项，结构与安装）+ `test_preset_voice.py`
  （5 项，行为：自我认知进每拍、硬规则进 prompt、语言标记齐全、
  能跑一整拍、能改自己且读得回来）。

### 边界

预设刻意**不含**任何第三方图片、Live2D 模型、音频或大段原台词；
人设里明确写了「我是照 DeepSeek 的样子被大家画出来的鲸鱼」，不冒充官方；
明确拒绝色情与冒充真人。测试会自动拦截混进预设的二进制素材。

### 变更

- `.gitignore` 加 `!/presets/**`，确保预设目录永远不会被 `记忆/` 之类的规则误伤。
- 自包含检查器更新：区分「本项目的旧角色名」与「公开网络梗名」。


## v1.1.0 — QQ 富媒体与 OneBot 11 完整接入

QQ 通道从「只能发纯文本」升级到完整的消息段能力，建立在 OneBot 11 标准之上。

### 新增

- `channels/message.py`：**消息段抽象**。构造、解析（CQ 码 / 数组 / 字符串）、
  文本化、类型查询。图文混排、@、回复、卡片、音乐分享都能表达。
- `channels/onebot.py`：**能力探测**（`get_version_info` / `can_send_image` /
  `can_send_record` / `get_status`）。不再假设实现支持什么，先问再降级。
- 收图落盘：优先调 `get_image` 让实现下载，失败再按 url 拉；存到 `data/media/image/`，
  路径写进事件的 `source_ref`，她后面才能「看」。
- 收语音：同样落盘到 `data/media/record/`，经 `get_record` 转 mp3。
- 她的三个新动作：
  - `send_image` —— 发图（本地文件走 base64，支持 URL，可发闪照）
  - `send_voice` —— 发语音（edge-tts 合成，用配置里的音色）
  - `look` —— 看图（需要配多模态模型；没配就老实说看不清，不瞎编）
- `router.send_segments()`：按渠道发消息段；不支持富媒体的渠道自动降级成纯文本。
- 配置新增 `vision`（多模态端点）与 `voice`（TTS 音色）两段。
- `onebot.send_like()` / `delete_msg()` / `call()`：标准 API 的通用出口。
- 测试：`tests/test_onebot.py`（16 项，纯单元）+ `tests/test_qq_e2e.py`
  （8 项，起假 OneBot 服务器验证收发全链路）。
- 文档：`docs/qq.md` —— 能力全景、来源与致谢、边界与安全。

### 修复

- **纯图片/语音消息被丢弃**：`wxhook.normalize()` 以前在 `text` 为空时直接 `return None`，
  她现在能收到「有人发了张图」这件事。
- **微信侧媒体消息**：`type` 为 3/34/43/47/49 的消息不再当垃圾丢掉，转成 `[图片]` 等占位。
- `router.send_image()` 本地文件改用 base64（原来用 `file:///`，只在本机同机时成立）。
- `doctor` 的 QQ 体检从「端口通不通」升级为「能力探测」，
  能直接告诉你这个实现支不支持发图/发语音。

### 说明

本项目**不实现 QQ 协议、不分发任何 QQ 框架二进制**。
所有能力来自 OneBot 11 标准与社区实现（NapCat / Lagrange / go-cqhttp），
来源与许可差异见 `docs/qq.md` §8 与 `THIRD_PARTY.md`。


## v1.0.0 — 第一个开源版本

从作者本机的运行副本整理而来：去掉全部个人记忆、真人身份与第三方二进制，
保留完整的双模式内核。

### 新增

- `memory_template/`：空白人格与记忆目录模板，`tools/init_persona.py` 一键生成。
- **应答模式（NPC）**：`thresholds.always_reply`，群聊里也每条都回；
  配套 `recall_limit` / `facts_per_person` / `events_per_person` 控制每轮注入的记忆量。
  见 `docs/npc-mode.md`。
- `tests/test_smoke.py`：临时库冒烟测试（记忆、v1 一拍、v2 一拍、配置、动作解析）。
- `tools/fetch_biliup.py`：按需下载 biliup，不再把 74 MB 二进制放进仓库。
- 文档：`INSTALL.md`、`docs/{architecture,reference,memory,channels,npc-mode,publish-bili}.md`、
  `THIRD_PARTY.md`。

### 变更

- 默认大脑改为 `auto`：优先用 config.api，其次读 `DEEPSEEK_API_KEY` / `OPENAI_API_KEY`。
- 默认渠道：QQ（OneBot 11）开启，微信关闭（需自备 hook）。
- `channels/wxhook.py` 的 hook 地址改为每次现读 `config.channels.wechat.hook_url`
  （此前面板改了不生效）。
- `channels/router.py` 发送失败时把 hook 的 `ret` 归一化成 `ok`，日志不再误报。
- `Self.observe()` / `note_self()` 支持传入 `channel`，QQ 消息不再被记成微信。
- `publish_bili` 动作改为「上传指定 mp4」，不再依赖视频生成链路。
- 人格卡缺失时不再抛异常，返回空卡（新用户首次启动不会死）。

### 移除

- 语音合成（约 8.2 GB 模型）、视频生成、Live2D 渲染链路。
- 微信 hook 二进制（`hooks/*.dll`）及其安装说明，改为「自备」文档。
- B 站评论 / 删除评论接口（平台 ToS 风险，且与「只读」定位矛盾）。
- 一次性脚本与宿主侧产物（`_asar_*`、`asar_list.js`、`mirror_chat.py` 等）。
- 所有个人记忆、人物卡、真人昵称、wxid、cookie、本机绝对路径。

### 已知限制

- 向量召回是空跑的（`events.embedding` 从不写入），实际只有 FTS + 实体 + 时间/重要度。
- `switches` 里部分开关没有完整消费点（`ability_tutorial` 是真正生效的那个）。
- `expressions` / `intents` 两张表是预留，当前代码未使用。
- 收件服务无鉴权，只绑回环；不要暴露到公网。
- 微信通道依赖外部注入程序，本项目不提供、不背书。
