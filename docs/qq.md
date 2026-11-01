# QQ：能力全景

> 本项目不自己实现 QQ 协议，而是接在 **OneBot 11** 标准上。
> 这样一个人写的通道代码，能跑 NapCat / Lagrange / go-cqhttp 等多种实现，
> 也能直接用生态里别人已经做好的能力（图片、语音、表情、管理接口）。

---

## 1. 它接在哪一层

```
        ┌────────────────────────────────────────────┐
        │  QQ 客户端（你自己的号）                     │
        └───────────────────┬────────────────────────┘
                            │ 私有协议
        ┌───────────────────▼────────────────────────┐
        │  OneBot 11 实现（别人做的）                   │
        │  NapCat / Lagrange / go-cqhttp              │
        │  对外暴露 HTTP API + 事件上报                 │
        └───────────────────┬────────────────────────┘
                            │ 标准 HTTP / JSON
        ┌───────────────────▼────────────────────────┐
        │  本项目 channels/onebot.py（200 行）          │
        │  发：send_group_msg / send_private_msg       │
        │  收：POST 到 8771/cb                         │
        └────────────────────────────────────────────┘
```

**关键取舍**：我们不碰 QQ 协议，也不改 OneBot 实现的代码。
所有「灵动」的能力都从标准消息段和标准 API 拼出来，换实现不用改代码。

---

## 2. 能收发什么

| 类型 | 收 | 发 | 说明 |
|---|---|---|---|
| 文字 | ✅ | ✅ | `text` 段 |
| 图片 | ✅ | ✅ | 收到自动落盘；发送支持本地文件（base64）、URL；可发闪照 |
| 语音 | ✅ | ✅ | 收到可下成 mp3；发送配 edge-tts，用你的音色念 |
| QQ 表情 | ✅ | ✅ | `face` 段，按 id |
| @ 某人 | ✅ | ✅ | `at` 段，`all` = 全体 |
| 回复引用 | ✅ | ✅ | `reply` 段 |
| 链接卡片 | ✅ | ✅ | `share` 段 |
| 音乐分享 | — | ✅ | `music` 段，qq / 163 / xm |
| 视频 | ✅ | ✅ | `video` 段 |
| 合并转发 | ✅ | ✅ | 收 `forward`，发 `node` |
| 卡片/XML | ✅ | ✅ | 文本化成 `[卡片]`，不解析内容 |
| 戳一戳 | ✅ | ✅ | `poke` 段 |

规范：<https://github.com/botuniverse/onebot-11/blob/master/message/segment.md>

---

## 3. 她多了三个动作

在自主模式里，她可以自己决定：

| 动作 | 写法 | 说明 |
|---|---|---|
| 发图 | `{"action":"send_image","path":"D:/pics/a.png","text":"看这个"}` | path 可以是本地文件或 http(s) 地址；配文可选 |
| 发语音 | `{"action":"send_voice","text":"喂，在吗"}` | 用 `voice.tts_voice` 指定的音色念；也可以给 `path` 直接发已有的音频 |
| 仔细看 | `{"action":"look"}` | 只在真的收到图时才有意义；需要配 `vision` 模型 |

**没有视觉模型时**：`look` 会老实返回「看不出」，不会瞎编图里有什么。
她仍然会记住「某人发了张图」（记忆里是 `[图片]`，并带上本地文件路径）。

**没有 edge-tts 时**：`send_voice` 报错并退回文字，进程不会崩。

---

## 4. 能力探测：不假设，先问

不同实现对「能不能发图/发语音」的支持不一样。硬编码踩过坑，所以启动时问一次：

```python
from channels import onebot
onebot.capabilities()      # {'image': True, 'record': True, 'app': 'NapCat', ...}
onebot.describe_caps()     # "QQ：NapCat 4.x · 可发图 · 可发语音"
```

调用的标准接口：`get_version_info` / `can_send_image` / `can_send_record` / `get_status`。
结果进程内缓存；探测失败时**保守地当作支持**（宁可试一次失败，也不无谓地关掉能力）。

---

## 5. 图片怎么进她的记忆

```
QQ 群里有人发图
   ↓ OneBot 上报 {"type":"image","data":{"file":"abc.jpg","url":"https://..."}}
channels/onebot.py: parse_event()
   ↓ 优先调 get_image 让实现自己下载；没有再按 url 拉
data/media/image/abc.jpg          ← 落盘
   ↓
Live.feed() → store.bind_source(event_id, 本地路径)
   ↓
事件内容是 "[图片]"，source_ref 是本地路径
   ↓ 她想看的时候
action=look → _look_image() → 视觉模型
```

落盘目录：`data/media/{image,record,tts}`，已在 `.gitignore` 里。

---

## 6. 配起来

```jsonc
// config.json
"channels": {
  "qq": {
    "kind": "onebot",
    "enabled": true,
    "base_url": "http://127.0.0.1:5700",
    "token": "你的 OneBot access token，没有就留空"
  }
},
"vision": { "base_url": "", "api_key": "", "model": "" },   // 想让她看图才填
"voice":  { "tts_voice": "zh-CN-XiaoxiaoNeural" }
```

OneBot 端要做两件事：

1. 开 **HTTP API**（本项目调它发消息）
2. 事件上报指到 `http://127.0.0.1:8771/cb`（**HTTP POST**，不是 WebSocket 反向）

⚠️ **不要开 `auto_escape`**：那会让消息段被当纯文本发出去，图片会变成一串 CQ 码。

---

## 7. 安全

- 收件服务 `8771/cb` **无鉴权**，只绑回环。任何本机进程都能伪造消息。
- OneBot 的 `access_token` 只在出站请求里带，入站不校验。
- 想加固：在 `channels/wxhook.py: Handler.do_POST` 开头加共享 token 校验，
  并在 OneBot 端配置同样的 token。
- 别把 `5700` / `8771` 暴露到公网。别用主号。被封是小号的事，但也会连累群友。

---

## 8. 来源与致谢

本通道完全建立在别人的工作上，来源如下：

| 项目 | 我们用了什么 | 许可 / 说明 |
|---|---|---|
| [OneBot 11 标准](https://github.com/botuniverse/onebot-11) | 消息段格式、API 语义、事件结构 | 社区标准；本仓库不复制其代码，仅实现其协议 |
| [NapCatQQ](https://github.com/NapNeko/NapCatQQ) | 推荐默认实现；`get_image` / `can_send_*` 实测可用 | 上游混合许可，**本项目不分发其任何二进制** |
| [Lagrange.Core](https://github.com/LagrangeDev/Lagrange.Core) | 备选实现（.NET） | 同上，不由本项目分发 |
| [go-cqhttp](https://github.com/Mrs4s/go-cqhttp) | 备选实现（已停更，仅兼容性参考） | 同上 |
| [edge-tts](https://github.com/rany2/edge-tts) | 文字转语音（`pip install edge-tts`） | **LGPLv3**，作为可选依赖安装，不 vendored 进仓库 |
| 微软 Edge 在线 TTS | edge-tts 背后的服务 | 服务条款归 Microsoft；高频使用可能被限流 |

**本仓库不含任何 QQ 协议实现、不含注入模块、不含第三方二进制。**
你跑的是别人的实现，出了问题找上游，别找这里。

---

## 9. 边界：我们故意不做的

| 不做 | 为什么 |
|---|---|
| 自己实现 QQ 协议 | 逆向协议风险高、维护成本大，且没有意义——生态里已经有更好的 |
| 群管理（踢人/禁言/改名片） | 能力在 `onebot.call()` 里都能调，但不给她这个权力。要就自己加 |
| 自动通过好友/入群请求 | 同上。这是账号安全边界，不该由模型决定 |
| 批量加好友 / 群发 | 平台风控红线 |
| 解析卡片/XML 里的链接去抓取 | 不可信输入，容易变成 SSRF/钓鱼跳板 |

`onebot.call()` 是通用出口：想加什么能力，写一行就行，不用改本项目的架构。
