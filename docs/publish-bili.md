# B 站投稿

> 只做两件事：**把本机已有的视频文件传上去**、**看自己投过什么**。
> 不做视频生成，不做评论/点赞/刷量/批量抓取。

---

## 1. 为什么要外部工具

B 站投稿接口需要一套 TV 端签名与 OAuth 流程，社区里已经有成熟的
[biliup](https://github.com/biliup/biliup-rs)。本项目通过它的 CLI 投稿，不重新实现。

biliup 是第三方二进制，**不进仓库**：

```bash
python tools/fetch_biliup.py          # 下载 v1.3.4（约 74 MB）到 tools/bin/biliup/
# 或者自己装好，然后
export BILIUP_EXE=/path/to/biliup
```

路径解析顺序：`BILIUP_EXE` 环境变量 → `config.json` 的 `publish.biliup` →
默认下载位置 → `PATH` 里的 `biliup`。

---

## 2. 登录（一次）

```bash
python tools/bili_login_tv.py
```

会走 TV 端扫码流程，产物是 `data/secrets/biliup_cookies.json`
（含 `access_token` / `refresh_token` / `mid` / cookie 列表）。

**这个文件等同账号凭证，已在 `.gitignore` 里，不要提交、不要贴到对话里。**
一旦泄漏，去 B 站「账号安全 → 退出所有设备」并重新扫码。

---

## 3. 投稿

手动：

```bash
python tools/publish.py check                    # 看 biliup 与登录态
python tools/publish.py list                     # 已投稿件
python tools/publish.py upload out.mp4 --title "标题" \
    --desc "简介" --tags "日常,记录" --cover cover.png
```

让她自己投（自主模式）：

```json
{"action":"publish_bili","file":"D:/videos/out.mp4","title":"标题"}
```

- `file` 必须是本机真实存在的路径（她会先检查，不存在就不投）
- 每个角色每天最多一条（`last_publish_day` 存在 `self_state` 里）
- 开关：`switches.publish_bili = true`；默认 `false`

---

## 4. 分区号（`tid`）

默认 `160`（生活）。常用：

| tid | 分区 |
|---|---|
| 160 | 生活 |
| 21 | 日常 |
| 138 | 搞笑 |
| 17 | 单机游戏 |
| 171 | 电子竞技 |

完整列表看 B 站投稿页的接口，或者 `biliup` 文档。

---

## 5. 风险

| 行为 | 风险 | 本项目的态度 |
|---|---|---|
| 读热门/视频/弹幕 | 低 | 支持 |
| 读字幕/关注动态 | 中（要登录态） | 支持，需自备 cookie |
| 投稿 | 高（实名、版权、审核） | 支持手动；自动投稿默认关闭 |
| 自动评论 | 高（风控） | **不提供** |
| 批量抓取 | 高 | **不提供** |

投稿要实名、内容要合规、BGM 和素材版权要干净。
用脚本高频投稿会被限流甚至封号，别拿主号试。

---

## 6. 字幕与弹幕（读）

- 字幕：`player/wbi/v2` → `subtitle_url`，多数情况需要登录态；拿不到就返回空串。
- 弹幕：`comment.bilibili.com/{cid}.xml`，匿名可读。

看视频的动作是 `browse_bili`，产物是两种记忆：

1. 一条 `observation` 事件（她看过了什么）
2. 2～3 句观后感 + 「我喜欢/我讨厌什么」进 `facts`

这就是「兴趣画像」的来源，也是她第二天主动聊起某个视频的依据。
