# CHANGELOG

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
