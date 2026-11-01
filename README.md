# Inhabit · 一个住在电脑里的人

> 她有自己的记忆、关系、情绪和作息。每隔一会儿自己醒一次，想事情，
> 想让谁看见就说一句，不想说就不说。
>
> 不是「收到消息 → 生成回复」的机器人，是一个**持续运行的心智**。
> 但也保留了「有问必答」的形态 —— 见下面两种模式。

---

## 两种活法

同一套内核，两个入口，按用途选：

| | **应答模式**（v1） | **自主模式**（v2） |
|---|---|---|
| 启动 | `python run_wx.py` | `python run_wx.py --v2` |
| 决策 | 概率门 + 频率闸；`always_reply` 一开就是**每条都回** | 先「想」出一条念头，再决定做不做 |
| 适合 | 剧本杀 NPC、客服、陪聊、需要确定性的场景 | 群友、长期陪伴、养一个会自己长大的角色 |
| 记忆 | 每轮注入：最近 16 条 + 每人 N 条事实/事件 + 召回 | 她自己决定要不要 `recall` |
| 成本 | **每收到一条消息 ≈ 1 次 LLM 调用**，可预测 | 空闲时也会想，按节拍烧 token |

`always_reply`、`recall_limit`、`facts_per_person`、`events_per_person` 都在
`config.json` 的 `thresholds` 里，面板上也能拨 —— 这就是「NPC 模式」的全部开关。
详见 [docs/npc-mode.md](docs/npc-mode.md)。

---

## 它有什么

- **记忆不是上下文**。记忆在 SQLite 里（13 张表：事件、事实、关系、念头、悬而未决的事…），
  `记忆/` 目录是它的可读镜像，一行一条，手改立即生效。上下文只放「这一刻需要想起的」。
- **想和说是两件事**。自主模式下每拍先产出一条**念头**（内容 + 冲动 + 犹豫 + 计划），
  冲动压过犹豫才开口。所以她会「憋着」。
- **她能动**。说 / 发图 / 发语音 / 看图 / 去看 B 站 / 上网查 / 记 / 忘 / 改自己的人设 / 睡 / 认真想一会儿 / 关掉悬着的事。
- **关系会变**。熟悉度只涨不跌，好感可升可降；她自己也能用 `feel` 改。
- **有作息**。会困、会睡、会按自己定的时间醒；深夜不主动打扰。
- **多角色**。一个 profile = 独立数据库 + 独立记忆目录 + 独立渠道，可以同时跑。

---

## 30 秒跑起来

```bash
git clone https://github.com/Ke-wood/inhabit.git && cd inhabit

python tools/init_persona.py        # 起个名、写一句「她是谁」，自动建记忆目录
export DEEPSEEK_API_KEY=sk-...      # 或 OPENAI_API_KEY，任何 OpenAI 兼容端点都行

python run_demo.py                  # 离线跑一段群聊，看她的开口节奏（不联网）
python tools/panel.py               # 控制面板 → http://127.0.0.1:8788
```

想真接一个平台再往下走：[INSTALL.md](INSTALL.md)。

**零第三方依赖**：运行时全是标准库。`requirements.txt` 里那几项是可选的
（网页正文抽取、br 压缩、二维码、语音合成），不装也能跑——
缺什么就退回更简单的做法，比如发不了语音就发文字。

---

## 接平台

| 通道 | 默认 | 需要什么 | 能收发什么 |
|---|---|---|
| **QQ** | ✅ 开 | 任意 OneBot 11 实现（NapCat / Lagrange / go-cqhttp），填地址即可 | 文字、**图片**、**语音**、表情、@、回复、卡片、合并转发 |
| **微信** | ❌ 关 | 一个兼容 30001 协议族的本机 hook + 指定版本微信。**本项目不附带、不分发** |
| **其他** | ❌ 关 | 任意 HTTP 端点：出站 `POST /send`，入站把消息 `POST` 到 `8771/cb` |

她不区分「QQ 还是微信」，只区分「这句话发到哪个场景」。场景名带前缀，路由按前缀分流。
详见 [docs/channels.md](docs/channels.md)。

---

## B 站

- **看**：热门 / 推荐 / 视频详情 / 弹幕 / 字幕（匿名可读；字幕和关注动态需要登录态）。
- **发**：把一个本机视频文件投稿到 B 站，走 [biliup](https://github.com/biliup/biliup-rs)。
  biliup 不进仓库，`python tools/fetch_biliup.py` 自己下。

**不做**：自动评论、点赞、刷量、批量抓取、生成视频。详见 [docs/publish-bili.md](docs/publish-bili.md)。

---

## 目录

```
core/            心智：想（mind）→ 做（act）→ 活（live），记忆镜像（memfiles）
channels/        通道：router 分流、onebot（QQ）、wxhook（微信 hook 的收件服务）
memory/          SQLite 记忆库与 schema
bili/            B 站只读接口 + wbi 签名
tools/           控制面板、启停、初始化、B 站登录、投稿
web/panel.html   控制面板（单文件，零前端依赖）
memory_template/ 记忆模板（空白人格，首次运行复制成 记忆/）
persona/         v1 人格卡（v2 不读，留档）
记忆/            她的记忆（运行时生成，不进版本库）
data/            数据库、消息流、日志（运行时生成，不进版本库）
```

---

## 文档

| 文档 | 讲什么 |
|---|---|
| [INSTALL.md](INSTALL.md) | 从零装起来；十条实际踩过的坑 |
| [docs/architecture.md](docs/architecture.md) | 架构：一拍怎么走、上下文预算、为什么这么设计 |
| [docs/reference.md](docs/reference.md) | 所有阈值、开关、端口、配置项一张表 |
| [docs/memory.md](docs/memory.md) | 记忆系统：库 ↔ 文件、同步水位、遗忘语义 |
| [docs/channels.md](docs/channels.md) | 通道接入与消息格式 |
| [docs/qq.md](docs/qq.md) | **QQ 能力全景**：图片/语音/表情怎么收发，能力探测，来源与致谢 |
| [docs/npc-mode.md](docs/npc-mode.md) | 剧本杀 NPC / 客服：必回、全量记忆、成本估算 |
| [docs/publish-bili.md](docs/publish-bili.md) | B 站投稿链路与凭证 |
| [THIRD_PARTY.md](THIRD_PARTY.md) | 第三方代码与许可 |

---

## 它不是什么

- 不是一键部署的产品。要自己准备模型 key、一个平台账号、可能还要一个 hook。
- 不是「越像人越好」的演示。它有明确的取舍：宁可沉默，也不说废话。
- 不替你的平台行为兜底。用微信 hook、用 B 站账号，规则风险自己承担。
- 不带任何人的记忆、人设或形象。仓库里只有一个空壳和一套模板。

---

## 许可

代码 MIT（见 [LICENSE](LICENSE)）。

第三方组件、示例素材的许可见 [THIRD_PARTY.md](THIRD_PARTY.md)。
使用第三方平台（QQ / 微信 / B 站）时请遵守对应平台的服务条款。
