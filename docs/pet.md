# 桌宠窗口

给「她」加一个看得见的身体：一个透明置顶的大肥鱼，在桌面上走来走去，
想说话的时候头上冒气泡，你拖得动她，也可以让她变成「点不到」。

这是**可选模块**，主程序不装它照常跑。

---

## 1. 是什么 / 不是什么

| 是 | 不是 |
| --- | --- |
| 一个独立进程里的透明窗口 | 主程序的一部分（主程序不会因为它变慢） |
| 只会被人念、被拖动的「外观层」 | 会自己思考、自己对话的第二个她 |
| 由主程序通过本机 socket 喂指令 | 直连 DeepSeek API（这个能力我们**去掉了**） |

参考实现 [1190fasheqi/dafeiyu-pet](https://github.com/1190fasheqi/dafeiyu-pet)
里那一堆「自己连 API 聊天、查天气、喂食、CPU/显卡监控」的功能我们都删了：
在主程序里，说话的是 `core/` 那一套心智体，桌宠只负责**把她说出来的话显示出来**，
而不是再养一个会聊天的分身。

### 不装主程序也能用吗

能。`python tools/pet_window.py` 单独就能跑，她照样会走、会蹦、会自言自语。
只是此时没有心智体喂台词 —— 走的是内置台词库（见下面许可一节）。

---

## 2. 安装

主程序本身零第三方依赖，桌宠要 PySide6，所以**单独一个 requirements 文件**，
不装它的人不会被迫拉进一个 200MB 的 Qt：

```bash
pip install -r requirements-pet.txt
```

然后下素材（素材不进仓库，原因见第 5 节）：

```bash
python tools/fetch_pet_sprites.py
```

它会下 13 个 PNG 到 `assets/pet/sprites/`，并顺手在那个目录里写一个
`.gitignore`（防止有人 `git add .` 把美术图提交上去）。

先看看要下什么、不联网：

```bash
python tools/fetch_pet_sprites.py --dry-run
```

国内网络连不上 `raw.githubusercontent.com` 时，可以试试整包：

```bash
python tools/fetch_pet_sprites.py --tarball
```

两个都失败就手动下：脚本失败时会打印可点的 GitHub 页面地址和逐个文件的清单。

最后启动：

```bash
python tools/pet_window.py
```

Windows 想让它不带黑框，用 `pythonw tools/pet_window.py`。

### 装不上/跑不起来时

| 现象 | 原因 | 怎么办 |
| --- | --- | --- |
| `ModuleNotFoundError: PySide6` | 没装可选依赖 | `pip install -r requirements-pet.txt` |
| 窗口是个**彩色方块** | 素材没装或文件名不对 | 跑 `fetch_pet_sprites.py`；素材缺了不会崩，只是占位 |
| 托盘提示「指令通道没起来」 | 端口 8791 被占（多半已有一只鱼在跑） | 换端口：`--port 8792`，并同步改主程序配置 |
| 托盘图标不显示 | 系统没开托盘区 | 窗口本身还在，右键窗口也能出菜单 |

---

## 3. 怎么和主程序连

两边只认一个约定：**`127.0.0.1` 上，一行一个 JSON**。谁先起都行。

### 3.1 打开开关

在主程序 `config.json` 里加一段（没有就整段新增）：

```json
"pet": {
  "enabled": true,
  "host": "127.0.0.1",
  "port": 8791
}
```

`enabled` 不写或写 `false` 时，`make_pet_link()` 返回 `NullPetLink`，**什么都不做** ——
这是默认行为，装桌宠是加装件，不该改变主程序的既有行为。

### 3.2 在主程序里拿桥

```python
from core import pet_link

link = pet_link.make_pet_link(cfg)      # 没开就是 NullPetLink，调用方式完全一样
link.say("今天有点困", inner=True)       # 心声：灰色斜体，带括号
link.set_mood(0.3, "电量低")             # 精力 0~1，影响走多快、多久动一次
link.set_state("thinking")              # idle / walking / thinking / talking / sleeping
link.close()
```

`core/pet_link.py` **不 import PySide6**（纯标准库），所以即便没装桌宠也能
`import core.pet_link`，不会把主程序拖下水。

### 3.3 协议（版本 1，字段只增不改）

| 方向 | 报文 | 说明 |
| --- | --- | --- |
| → 桌宠 | `{"cmd":"say","text":"...","inner":false}` | 弹气泡；`inner=true` 是心声 |
| → 桌宠 | `{"cmd":"mood","energy":0.6,"mood":"平静"}` | 同步精力/心情 |
| → 桌宠 | `{"cmd":"state","state":"thinking"}` | 状态机 |
| → 桌宠 | `{"cmd":"ping"}` | 探活 |
| 桌宠 → | `{"ok":true,"pong":true}` | ping 的回应 |
| 桌宠 → | `{"ok":false,"error":"..."}` | 其它错误一律这样回，不抛异常 |

### 3.4 各指令在窗口里是什么效果

| 指令 | 效果 | 备注 |
| --- | --- | --- |
| `say` | 头顶气泡 2.8 秒 | 同一句连发会被忽略，防刷屏 |
| `set_mood(energy)` | 移动速度 = 380 × (0.45 + energy) 像素/秒；小动作频率也随之变化 | energy 会被钳到 0~1 |
| `set_mood(mood)` | 非空的 `mood` 会顺带当一句话弹出来 | 不然用户看不见同步过来的心情 |
| `set_state("sleeping")` | 停止走动、呼吸变慢 | **只有它会改变运动** |
| `set_state("thinking")` | 头顶三个点轮流亮 | 不影响走动安排 |
| 其它状态 | 记录一下，不改变模式 | 走路/跟随是用户的右键菜单说了算 |

### 3.5 端口与命令行

```bash
python tools/pet_window.py --port 8792          # 换端口（要和 config 里一致）
python tools/pet_window.py --no-server          # 不开端口，只当装饰
python tools/pet_window.py --shot out.png       # 启动后截图就退出（自动化验证用）
```

窗口自己的状态（位置、大小、模式、是否穿透）存在 `data/pet_window.json`，
和主程序的 `config.json` 分开 —— 免得两个进程互相覆盖配置。

### 3.6 右键菜单

- **模式**：自由散步 / 跟随鼠标 / 原地待着
- **大小**：小 / 中 / 大（187 / 238 / 306 像素高）
- **显示/隐藏**、**回到屏幕内**
- **鼠标穿透**：开了以后点击直接穿过去，点不到她；从托盘图标右键关掉
- **窗口置顶**
- **退出**

透明置顶的窗口一被穿透就再也点不到，所以托盘菜单是最后一道退路；
双击鱼身直接弹菜单，也给穿透留了个后门。

---

## 4. 文件

| 文件 | 作用 | 要不要 PySide6 |
| --- | --- | --- |
| `core/pet_link.py` | 通信桥，`PetLink` / `NullPetLink` / `DirectPetLink` | 不要（纯标准库） |
| `tools/pet_window.py` | 桌面窗口本体 + 本机 socket 监听 | 要 |
| `tools/fetch_pet_sprites.py` | 下素材（含 `--dry-run` / `--tarball`） | 不要（纯标准库） |
| `requirements-pet.txt` | 可选依赖，只有这个文件里出现 PySide6 | — |
| `assets/pet/sprites/` | 素材落地位置，**不进仓库** | — |

环境变量 `DAFEIYU_PET_SPRITES` 可以把素材目录指到别处（自备素材、便携版放 U 盘）。

---

## 5. 素材许可

代码和使用条款是分开的两件事，这里必须说清楚：

- **我们的代码**：跟本项目一致，MIT。
- **参考实现的代码**：`1190fasheqi/dafeiyu-pet`，MIT，我们借用了它的窗口思路和台词库。
- **角色美术**（`sprites/` 里那 13 个 PNG，包括托盘图标）：
  **版权归 `1190fasheqi/dafeiyu-pet` 的作者**，不适用 MIT。
  仓库里**不放**这些图，`tools/fetch_pet_sprites.py` 只是帮你从上游拉一份，
  **仅限个人使用**，不要再分发、不要拿去做商业用途或改个名字当自己的作品发。
- **角色形象本身**（「大肥鱼」设定）同样归原作者。

如果你要用在公开发布的东西里，请换成自己的素材 —— 把 `DAFEIYU_PET_SPRITES`
指到自己的目录即可，代码不需要改。

台词库（`LINES` / `REACT_LINES` / `INNER_LINES` / `DRAG_LINES`）原样搬自参考实现，
`tools/pet_window.py` 里对应的常量上方有来源标注。这些台词的语调属于那个角色，
换成自己的角色时记得一起换掉。

---

## 6. 已知限制

1. **只在 Windows 上验证过**。`_apply_passthrough()` 的 `WS_EX_TRANSPARENT` 那一段是
   Win32 API，macOS/Linux 上会退化成只有 Qt 那一层（能点不到，但仍会挡住下层窗口）。
2. **没装 PySide6 时桌宠相关代码无法验证**，`import core.pet_link` 能过是设计保证，
   窗口能不能起来只能装完试。
3. **开机自启没做**。参考实现里有，但它改的是注册表/启动目录，属于「不该悄悄做的事」。
4. **不会自己说话**。所有台词都来自主程序或内置库；她不会对屏幕内容做出反应
   （没有 OCR、没有窗口监听）。
5. **一个端口一只鱼**。重复启动时第二个会绑定失败（Windows 上 `SO_REUSEADDR`
   会骗人，已经特意关掉），此时它仍在动，只是不听指令。
6. **多显示器**：`wander` 模式在鼠标所在/当前屏幕内活动；换屏幕后不会自动搬过去。
7. **素材文件名写死**：必须是 `正面.png` / `侧面.png` / `背面.png` 加
   `_187` / `_238` / `_306` 后缀。自定义素材不改名的话，会先找预缩放版，
   找不到就现场缩放原图（效果差一点，但不崩）。
8. **DPI 缩放**：Qt6 默认按系统缩放，150% 下窗口会比设定值大，
   位置记忆按逻辑像素存，换缩放比之后可能偏一点点。
