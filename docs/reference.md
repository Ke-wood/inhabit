# 参数与接口速查

> 以代码为准：`core/config.py` 的 `DEFAULTS` 是唯一真相源。
> 这里列的是「你会想改的那些」，以及改了会影响什么。

---

## 1. 启动方式

| 命令 | 干什么 |
|---|---|
| `python run_wx.py` | v1：门控 / 应答模式 |
| `python run_wx.py --v2` | v2：自主模式 |
| `python run_wx.py --brain mock` | 不联网，用固定台词（流程调试） |
| `python run_wx.py --profile 某角色` | 跑指定角色 |
| `python run_wx.py --replay` | 从头重放收件箱（会重复回复旧消息，调试用） |
| `python channels/wxhook.py serve 8771` | 只起收件服务 |
| `python tools/panel.py` | 控制面板 `127.0.0.1:8788` |
| `python tools/ctl.py start` | 起收件服务 + 配回调 + 起主循环 |
| `python run_demo.py` | 离线演示一段群聊 |
| `python tools/init_persona.py` | 第一次运行：建人设与记忆目录 |
| `python tests/test_smoke.py` | 冒烟测试（临时库，不碰真数据） |

---

## 2. 端口

| 端口 | 用途 | 在哪改 |
|---|---|---|
| 30001 | 微信 hook（**你自己的外部程序**） | `channels.wechat.hook_url` |
| 5700 | OneBot 11 网关 | `channels.qq.base_url` |
| 8771 | 收件服务（所有通道的入站都 POST 到这里） | `channels/wxhook.py: serve(port)` |
| 8790 | 单例锁（每个角色 +1） | `run_wx.py: _singleton_port()` |
| 8788 | 控制面板 | `tools/panel.py: serve(port)` |

Agent 侧还有可选的 AI 信箱：场景名 `ai_` 开头时，`channels/router.py` 直接写
`data/ai_mail.jsonl`，不经任何平台——给本机脚本/另一个 agent 说话用。

---

## 3. 阈值（`thresholds`）

### 3.1 自主模式节拍

| 键 | 默认 | 作用 |
|---|---|---|
| `min_think_gap` | 20 秒 | 两次「想」的最短间隔 |
| `idle_think_gap` | 60 秒 | 没人说话时多久自己想一次 |
| `focus_gap` | 20 秒 | 「思考时刻」里的间隔 |
| `speak_gap` | 0 秒 | 说完话后多久能再说 |
| `browse_gap` | 3600 秒 | 两次自己刷 B 站的最短间隔 |
| `search_gap` | 600 秒 | 两次自己上网查的最短间隔 |
| `urge_floor` | 0.2 | 劲头低于这个，只想不做 |
| `score_band` | 0.15 | 冲动/犹豫差值在此区间内 → 掷骰子 |
| `quiet_hours` | `[2,3,4,5,6]` | 这些小时不主动开口（思考时刻除外） |
| `sleep_hours` | `[0,1,2,3,4]` | 倾向睡觉的时段 |
| `max_sleep_hours` | 10 | 最长一觉 |
| `focus_max_minutes` | 60 | 「思考时刻」最长多久 |

### 3.2 记忆

| 键 | 默认 | 作用 |
|---|---|---|
| `facts_here` | 8 | 每轮带出几条「眼前这几人」的事实 |
| `keep_loops` | 20 | 最多保留几件悬而未决的事 |
| `decay_days` | 30 | 低于 importance 阈值的旧事件多久后归档（不删） |
| `recall_limit` | 12 | **v1** 每轮召回几条旧事 |
| `facts_per_person` | 15 | **v1** 每人带几条事实 |
| `events_per_person` | 6 | **v1** 每人带几条最近事件 |

### 3.3 应答模式

| 键 | 默认 | 作用 |
|---|---|---|
| `always_reply` | `false` | `true` = 群聊里也每条都回（NPC / 客服） |

v1 另有写死在 `core/gate.py` 的频率闸（改它要动代码）：
`threshold 0.52`、`min_gap_seconds 60`、`max_per_10min 3`、
`direct_min_gap_seconds 0.5`、`direct_max_per_10min 60`。

---

## 4. 开关（`switches`）

| 键 | 默认 | 作用 |
|---|---|---|
| `browse_bili` | `true` | 能不能自己去看 B 站 |
| `publish_bili` | `false` | 能不能往 B 站投稿 |
| `focus_mode` | `true` | 能不能自己开「思考时刻」 |
| `auto_sleep` | `true` | 会不会困、自己睡 |
| `share` | `true` | 会不会忍不住分享 |
| `judge_relations` | `true` | 会不会自己给人改好感 |
| `ability_tutorial` | `true` | 每轮告诉她「你能改自己」这份说明 |

> 注意：`switches` 目前在代码里的落实程度不一致——`ability_tutorial` 是真正逐轮生效的，
> 其余几项有的只在部分路径判断。改之前先 `grep` 一下你关心的那个键。

---

## 5. 配置结构

```jsonc
{
  "edition": "open",              // open = 开源版（QQ 默认）；local = 本机自用
  "active": "阿鱼",               // 现在跑谁（主循环启动时写）
  "viewing": "阿鱼",              // 面板正在看谁
  "profiles": {                   // 每个角色 = 独立库 + 独立记忆 + 独立渠道
    "阿鱼": {
      "dir": "data",              // 库在 <dir>/feiyu.db
      "memory": "记忆",           // 记忆目录（md 镜像）
      "persona": "我是谁.md",      // 自我认知文件
      "channels": ["qq"],         // 这个角色挂哪些渠道（可选，不填=全部）
      "thresholds": {}            // 按角色覆盖阈值
    }
  },
  "thresholds": { },              // 全局阈值
  "switches": { },
  "api": { "base_url": "", "api_key": "", "model": "" },   // 留空读环境变量
  "identity": {
    "self_wxid": "",              // 她自己的账号 id（用来忽略自己发的）
    "owner_wxid": "",             // 主人 id（特权行为用，不是访问控制）
    "names": ["阿鱼"]             // 她认得的称呼
  },
  "bilibili": { "cookie": "" },   // 留空读 data/secrets/bili_cookie.txt
  "publish": { "biliup": "" },    // biliup 路径；留空自动找
  "channels": {
    "wechat": { "kind": "wechat_hook", "enabled": false, "hook_url": "http://127.0.0.1:30001" },
    "qq":     { "kind": "onebot", "enabled": true, "base_url": "http://127.0.0.1:5700", "token": "" },
    "other":  { "kind": "http", "enabled": false, "base_url": "" }
  }
}
```

优先级：`profiles.<角色>.thresholds` > 顶层 `thresholds` > `core/config.py: DEFAULTS`。

---

## 6. 环境变量

| 变量 | 作用 |
|---|---|
| `DAFEIYU_ROOT` | 项目根（打包/多实例时用；旧名 `FEIYU_ROOT` 仍兼容） |
| `DEEPSEEK_API_KEY` | 没在 config 里填 api 时用它 |
| `OPENAI_API_KEY` | 同上（DeepSeek 没填时兜底） |
| `DEEPSEEK_BASE_URL` | 默认 `https://api.deepseek.com/v1` |
| `DEEPSEEK_MODEL` | 默认 `deepseek-chat` |
| `LOCAL_BASE_URL` / `LOCAL_MODEL` / `LOCAL_API_KEY` | `--brain local`（Ollama / LM Studio） |
| `HTTPS_PROXY` / `https_proxy` | 走代理访问模型 |
| `BILIUP_EXE` | biliup 可执行文件路径 |

---

## 7. 关键常量（改代码才能改）

| 常量 | 值 | 位置 |
|---|---|---|
| 单例端口基址 | 8790 + 角色序号 | `run_wx.py` / `core/config.py` |
| 收件服务端口 | 8771 | `channels/wxhook.py` |
| 面板端口 | 8788 | `tools/panel.py` |
| 每场景保留窗口 | 40 条 | `core/self.py: WINDOW_KEEP` |
| 「我自己的记忆」注入上限 | 520 字（落盘 1040） | `core/memfiles.py` |
| 分条发送上限 | 3 段 | `core/brain.py: split_segments` |
| 行动步数 | 3 | `core/act.py` |
| 活跃念头上限 | 20 | `memory/store.py` |

---

## 8. 数据库

`<profile.dir>/feiyu.db`，13 张表：

| 表 | 是什么 |
|---|---|
| `events` + `events_fts` | 情景记忆流（说过的、看到的、做过的），带 importance |
| `facts` | 语义记忆（稳定认知），带 evidence 与 confidence |
| `entities` / `relations` | 人与关系：昵称、熟悉度、好感 |
| `self_state` | 她的状态（心情、精力、睡到几点、身份缓存） |
| `reflections` | 日记与反思 |
| `thoughts` | 念头池（v2 的核心） |
| `open_loops` | 悬而未决的事 |
| `search_pool` | 搜到但还没决定记不记的暂存区 |
| `expressions` / `intents` | **预留表，当前代码未使用**，可以忽略 |

表结构见 `memory/schema.sql`。
