# 更新

> 一条命令升级到最新版。**你的记忆、人设、配置、数据库一个字节都不会动。**

```bash
python inhabit.py update          # 检查并更新
python inhabit.py update --dry    # 只看会改哪些文件，不动手
python inhabit.py rollback        # 反悔了，退回上一版
```

面板上也有：顶部「检查更新」，或「版本与更新」卡片里的按钮。

---

## 1. 它安全在哪

更新最容易出的不是技术问题，是**把用户的东西覆盖掉**——
记忆、人设、config、日志、数据库，全都在同一个目录里。

所以规则是**白名单**，不是黑名单：

| 类别 | 处理 | 举例 |
|---|---|---|
| **程序文件** | 会被覆盖 | `core/` `channels/` `tools/` `web/` `docs/` `presets/` `memory_template/` `README.md` `VERSION` … |
| **用户数据** | **永不触碰** | `data/`、`记忆/`（以及你在 config 里给角色起的任何记忆目录名）、`config.json`、`.env` |
| 缓存/二进制 | 跳过 | `__pycache__/`、`*.pyc`、`*.db`、`*.log` |

判断逻辑在 `core/version.py: classify()`，测试里逐条钉住了。

> 特别注意：`记忆/` 的名字不是写死的。你如果在 `config.json` 里
> 把某个角色的记忆目录设成 `my_persona`，它一样受保护。

---

## 2. 三步走

```
① 查最新版      GitHub Releases API（没有 release 就退到 main 分支）
② 备份          把将被覆盖的文件复制到 data/backups/pre-<版本>-<时间>/
③ 原子替换      每个文件先写 .new 再 os.replace()，不会出现半截文件
                任何一步失败 → 自动回滚已写的文件
```

恢复：

```bash
python inhabit.py rollback        # 用最近一次备份
```

备份目录可以手动删，不会越来越大（每次更新一份）。

---

## 3. 它连的是哪个库

`config.json`：

```jsonc
"updater": {
  "repo": "Ke-wood/inhabit",   // owner/name
  "token": ""                  // 私有库或遇到 API 限流才需要
}
```

默认指向 `Ke-wood/inhabit`。想跟别人的分支走，改 `repo` 就行。

**token 不用填**，除非：
- 仓库是私有的
- 撞到 GitHub 的 API 限流（60 次/小时，未登录）

也可以直接用环境变量：`export GITHUB_TOKEN=...`。

---

## 4. 版本号怎么比

`VERSION` 文件是唯一真相：

```
1.4.0
```

比较是**按数字**，不是按字符串（`1.10.0 > 1.9.0`，字符串比会错）。
预发布版小于同号正式版（`1.4.0-beta < 1.4.0`）。

`python inhabit.py version` 会打印当前版本、角色、上次更新的时间。

---

## 5. 会失败的情况

| 情况 | 表现 |
|---|---|
| 断网 | `查不到远端：连不上 GitHub`，返回码 2，**不会假装成功** |
| 遇到 API 限流 | `GitHub API 返回 403`，提示配 token |
| 仓库没有 release | 自动退回 main 分支（会说明） |
| 压缩包结构变了 | `压缩包里没有可更新的程序文件`，不动任何文件 |
| 替换中途出错 | 自动回滚，回滚了多少个文件会说 |

**更新失败不会让程序起不来** —— 要么全成功，要么回到动手前的样子。

---

## 6. 自己发布新版

1. 改 `VERSION`（比如 `1.5.0`）
2. 提交推送
3. 打个 tag：`git tag v1.5.0 && git push --tags`
4. 在 GitHub 上建一个 release（标题写 `v1.5.0`，正文写更新说明）

有 release 时，`update` 会读 release 的 tag 和说明；
没有 release 时退到 main 分支，版本号从 `VERSION` 文件读。

---

## 7. 为什么不做成「覆盖整个目录」

因为那等于要求用户把所有东西放在一个能随时被推翻的地方——
而 `data/feiyu.db` 里是她的记忆，`记忆/我是谁.md` 是用户亲手写的。
一次错误的覆盖，就是几个月的关系数据没了。

宁可多写 200 行白名单逻辑，也不做「先删后拷」的更新。
