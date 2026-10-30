# 通道：怎么把她接到平台上

她脑子里不分「QQ 还是微信」，只分**场景**。场景名带渠道前缀，`channels/router.py` 按前缀分流。

```
qq_group_123456        → QQ 群
qq_private_789         → QQ 私聊
wxid_xxx               → 微信私聊
0000000000@chatroom   → 微信群
http_xxx               → 你自己的 HTTP 端点
ai_xxx                 → 本地 AI 信箱（不出网）
```

---

## 1. 入站：一个 HTTP 端点收所有消息

所有通道的入站都走同一个地址：

```
POST http://127.0.0.1:8771/cb
Content-Type: application/json
```

`channels/wxhook.py` 的 `normalize()` 会先看报文里有没有 `post_type`：

- 有 → 当成 OneBot 11 事件，交给 `channels/onebot.py: parse_event()`
- 没有 → 当成微信 hook 的扁平报文

统一之后的字段：

```json
{
  "channel": "wechat",
  "scene": "0000000000@chatroom",
  "actor_id": "wxid_xxx",
  "actor_name": "某人",
  "text": "在吗",
  "kind": "text",
  "ts": 1761744000.0,
  "is_group": true,
  "msgid": "..."
}
```

然后追加到 `data/wx_inbox.jsonl`，主循环轮询这个文件。

启动收件服务：

```bash
python channels/wxhook.py serve 8771
# 或者由 ctl 一起拉起来
python tools/ctl.py start
```

---

## 2. QQ（默认开）

标准 OneBot 11。任意实现都行：NapCat / Lagrange / go-cqhttp。

1. 起网关，默认在 `http://127.0.0.1:5700`
2. 在网关里把**事件上报**指到 `http://127.0.0.1:8771/cb`（HTTP POST）
3. `config.json`：

```json
"qq": { "kind": "onebot", "enabled": true,
        "base_url": "http://127.0.0.1:5700", "token": "网关的 token，没有就留空" }
```

出站：`POST {base_url}/send_group_msg` 或 `/send_private_msg`，有 token 就带
`Authorization: Bearer <token>`。

> 网关本身的协议实现、风控、封号风险由你选择的那个项目承担，与本仓库无关。

---

## 3. 微信（默认关，要自备 hook）

本项目**不提供、不分发**任何微信 hook 程序或注入模块。

如果你已经有兼容下面这套接口的本机 hook（监听 `127.0.0.1:30001`），把
`channels.wechat.enabled` 打开即可：

| 接口 | 用途 |
|---|---|
| `POST /SendTextMsg` | 发文字，体 `{wxidorgid, msg}` |
| `POST /SendImgMsg` | 发图片，体 `{wxidorgid, path}` |
| `POST /send_xml` | 发链接卡片，体 `{wxid, ...}` |
| `POST /GetSelfProfile` | 取自己的资料（验证登录态） |
| `POST /set_callback` | 把消息回调指到 `http://127.0.0.1:8771/cb` |
| `POST /QueryDB/execute` | 查库（可选，本项目只在 doctor 里探测） |

改地址不用改代码：`channels.wechat.hook_url` 现在会真正生效（发送端每次现读）。

**三条提醒**：

- 注入客户端违反平台用户协议，封号是概率问题，别用主号。
- 版本通常被硬锁（旧版安装包要自己找），装错版本轻则失效重则崩微信。
- 这套东西的能力是残缺的：通常不能发语音、不能发图（只能收）、看不到自己昵称。

---

## 4. 其他 HTTP 端点（示例，未完成）

出站已经实现：

```json
"other": { "kind": "http", "enabled": true,
           "base_url": "http://127.0.0.1:9000", "token": "" }
```

→ `POST {base_url}/send`，体 `{"scene": "...", "text": "..."}`

入站需要你自己做：你的服务收到消息后，按第 1 节的格式 `POST` 到 `8771/cb`，
场景名用 `http_` 开头（否则路由会找不到渠道）。

十几行就能接 Telegram / Discord / 飞书 / 企业微信。写适配器的建议：

```python
import json, urllib.request

def push(text, chat_id):
    body = json.dumps({
        "channel": "http",
        "scene": f"http_{chat_id}",
        "actor_id": f"http_{chat_id}",
        "actor_name": "某人",
        "text": text,
        "kind": "text",
        "ts": __import__("time").time(),
        "is_group": False,
    }).encode()
    req = urllib.request.Request("http://127.0.0.1:8771/cb", data=body,
                                 headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=10).read()
```

---

## 5. AI 信箱（不出网）

场景名以 `ai_` 开头时，`router.send()` 不发任何平台，而是往
`data/ai_mail.jsonl` 追加一行 `{ts, to, text}`。

这是给「本机另一个脚本 / 另一个 agent」跟她说话用的，不经过任何平台账号。

---

## 6. 安全

- 收件服务只绑 `127.0.0.1`，**没有鉴权、没有来源校验、没有发送者白名单**。
- 任何能在这台机器上发 HTTP 的进程都可以伪造一条消息，让她记住假事、甚至替它说话。
- 所以：不要把它暴露到公网；不要在多人共用的机器上跑；不要给不可信进程开权限。

想加鉴权，最小改法是在 `channels/wxhook.py: Handler.do_POST` 开头校验一个共享
token（请求头带 `X-Token`），再加一个发送者白名单。

---

## 7. 名字表

微信 4.x 的昵称拿不到，所以人名靠手工维护：`data/wx_names.json`

```json
{
  "people": { "wxid_xxx": { "name": "某人", "aliases": ["老某"] } },
  "rooms":  { "0000000000@chatroom": { "name": "某个群", "aliases": [] } }
}
```

查不到就显示成「路人 + id 末五位」。这个文件在 `data/` 下，不进版本库。
