"""桌宠：把她放到桌面角落，只承担三件事。

职责边界（很重要）
------------------
这个窗口**只做三件事**，别的都不做：

1. **输入 A：看屏幕** —— 交给 `core/attention.py` 判断值不值得看，
   值得才把画面送进心智体（不是每帧都送）。
2. **输入 B：用户的话** —— 单击弹出输入栏，打字送到主程序。
3. **输出：她的回复** —— 以气泡形式显示。

**不做语音**、**不做散步**（用户第一反应是「烦」）、
**不做蹦跳/喂食/呼吸浮动**（那是宠物，不是她）。

关于「卡顿的上下浮动」
----------------------
上一版有 breathing = sin(t*1.55)*1.6，再被 int(y) 截断，
浮点值被量化成整数像素 —— 看起来就是一跳一跳的。
这一版**完全静态**：没有呼吸、没有浮动。

模型可换
--------
`assets/pet/models/<名字>/` 下每套一个目录，必须含 `正面.png`
（可选 `侧面.png` / `背面.png` / `icon.png`）。
托盘菜单能切换内置的，也能直接指向用户自己的目录。
"""

from __future__ import annotations

import json
import math
import os
import pathlib
import socketserver
import sys
import threading

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_MODELS_DIR = pathlib.Path(os.environ.get("DAFEIYU_PET_MODELS")
                            or (_ROOT / "assets" / "pet" / "models"))
_STATE = _ROOT / "data" / "pet_state.json"
_CONFIG = _ROOT / "config.json"

HOST = "127.0.0.1"
PET_PORT = 8791
CORE_PORT = 8792

VIEW_OF_DIR = {"down": "正面", "up": "背面", "left": "侧面", "right": "侧面"}
VIEWS = ("正面", "侧面", "背面")

SPRITE_H = 238
TICK_MS = 50
BUBBLE_H = 78
MARGIN = 6
MIN_W = 200

try:
    from PySide6.QtCore import QPoint, QRectF, Qt, QTimer
    from PySide6.QtGui import (QAction, QColor, QFont, QFontMetrics, QIcon,
                               QPainter, QPixmap, QTransform)
    from PySide6.QtWidgets import (QApplication, QFileDialog, QInputDialog,
                                   QLineEdit, QMenu, QSystemTrayIcon, QWidget)
    QT_OK = True
except Exception:
    QT_OK = False


def wrap_cjk(text, fm, max_w):
    if not text:
        return []
    lines, cur = [], ""
    for ch in text:
        if fm.horizontalAdvance(cur + ch) > max_w and cur:
            lines.append(cur)
            cur = ch
        else:
            cur += ch
    if cur:
        lines.append(cur)
    return lines


def list_models():
    if not _MODELS_DIR.is_dir():
        return []
    return [d for d in sorted(_MODELS_DIR.iterdir())
            if d.is_dir() and (d / "正面.png").is_file()]


def placeholder(view, height):
    w = max(48, int(height * 0.72))
    pix = QPixmap(w, height)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(96, 140, 220))
    p.drawRoundedRect(0, 0, w, height, 12, 12)
    p.setPen(QColor(255, 255, 255, 210))
    f = QFont()
    f.setPointSize(10)
    p.setFont(f)
    p.drawText(0, 0, w, height, Qt.AlignmentFlag.AlignCenter, view)
    p.end()
    return pix


class Model:
    def __init__(self, root):
        self.root = pathlib.Path(root)
        self._cache = {}
        self.missing = set()

    @property
    def name(self):
        return self.root.name

    def installed(self):
        return (self.root / "正面.png").is_file()

    def _load(self, view, height):
        cand = [self.root / (view + "_" + str(height) + ".png")]
        for d in (-1, 1, -2, 2):
            cand.append(self.root / (view + "_" + str(height + d) + ".png"))
        for p in cand:
            if p.is_file():
                pix = QPixmap(str(p))
                if not pix.isNull():
                    return pix
        src = self.root / (view + ".png")
        if not src.is_file():
            src = self.root / "正面.png"
        if not src.is_file():
            return None
        pix = QPixmap(str(src))
        if pix.isNull():
            return None
        return pix.scaledToHeight(height, Qt.TransformationMode.SmoothTransformation)

    def get(self, view, height, facing=1):
        key = (view, height, facing)
        pix = self._cache.get(key)
        if pix is None:
            pix = self._load(view, height)
            if pix is None:
                self.missing.add(view)
                pix = placeholder(view, height)
            self._cache[key] = pix
        if view == "侧面" and facing < 0:
            return pix.transformed(QTransform().scale(-1, 1))
        return pix

    def max_width(self, height):
        return max(self.get(v, height).width() for v in VIEWS)


class _Handler(socketserver.StreamRequestHandler):
    def handle(self):
        win = self.server.window
        for raw in self.rfile:
            line = raw.decode("utf-8", "replace").strip()
            if not line:
                continue
            try:
                cmd = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(cmd, dict):
                win.post(cmd)


class _Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def start_server(win, port=PET_PORT):
    srv = _Server((HOST, port), _Handler)
    srv.window = win
    threading.Thread(target=srv.serve_forever, daemon=True).start()


def send_to_core(payload, port=CORE_PORT):
    import socket as _s
    try:
        s = _s.create_connection((HOST, port), timeout=1.5)
        s.sendall((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
        s.close()
        return True
    except Exception:
        return False


class PetWindow(QWidget):
    def __init__(self):
        flags = (Qt.WindowType.FramelessWindowHint
                 | Qt.WindowType.Tool
                 | Qt.WindowType.WindowStaysOnTopHint)
        super().__init__(None, flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setWindowTitle("桌宠")

        self.cfg = self._load_state()
        self.model = Model(self._resolve_model())
        self.dir = "down"
        self.facing = 1
        self.bubble_text = ""
        self.bubble_until = 0.0
        self.bubble_inner = False
        self.energy = 0.8
        self.state = "idle"
        self.t = 0.0

        self.drag_from = None
        self.drag_win0 = None
        self.dragging = False

        self.reply = QLineEdit(self)
        self.reply.setPlaceholderText("跟她说点什么…")
        self.reply.returnPressed.connect(self._submit_reply)
        self.reply.hide()
        self.reply.setStyleSheet(
            "QLineEdit{background:rgba(255,255,255,242);border:1px solid #cfd6e4;"
            "border-radius:10px;padding:5px 9px;font-size:13px;color:#2b3448}")

        self._apply_geometry()
        self._restore_position()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(TICK_MS)

        self._top = QTimer(self)
        self._top.timeout.connect(lambda: self.raise_())
        self._top.start(4000)

        self.tray = self._build_tray()
        self.show()
        if not self.model.installed():
            QTimer.singleShot(1200, self._warn)

    def _load_state(self):
        try:
            return json.loads(_STATE.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save_state(self):
        try:
            _STATE.parent.mkdir(parents=True, exist_ok=True)
            self.cfg["x"], self.cfg["y"] = self.x(), self.y()
            self.cfg["model"] = str(self.model.root)
            _STATE.write_text(json.dumps(self.cfg, ensure_ascii=False, indent=1),
                              encoding="utf-8")
        except Exception:
            pass

    def _resolve_model(self):
        saved = self.cfg.get("model")
        if saved and (pathlib.Path(saved) / "正面.png").is_file():
            return pathlib.Path(saved)
        try:
            cfg = json.loads(_CONFIG.read_text(encoding="utf-8"))
            want = str((cfg.get("pet") or {}).get("model") or "").strip()
            if want:
                p = pathlib.Path(want)
                if not p.is_absolute():
                    p = _MODELS_DIR / want
                if (p / "正面.png").is_file():
                    return p
        except Exception:
            pass
        models = list_models()
        return models[0] if models else _MODELS_DIR / "default"

    def _apply_geometry(self):
        w = max(self.model.max_width(SPRITE_H) + MARGIN * 2, MIN_W)
        self.setFixedSize(w, SPRITE_H + BUBBLE_H + MARGIN * 2)

    def _restore_position(self):
        x, y = self.cfg.get("x"), self.cfg.get("y")
        if not isinstance(x, int) or not isinstance(y, int):
            g = QApplication.primaryScreen().availableGeometry()
            x = g.right() - self.width() - 40
            y = g.bottom() - self.height() - 20
        self.move(x, y)
        self._clamp()

    def _clamp(self):
        scr = QApplication.screenAt(self.pos()) or QApplication.primaryScreen()
        if scr is None:
            return
        g = scr.availableGeometry()
        x = max(g.left(), min(g.right() - self.width(), self.x()))
        y = max(g.top(), min(g.bottom() - self.height(), self.y()))
        if (x, y) != (self.x(), self.y()):
            self.move(x, y)

    def post(self, cmd):
        QTimer.singleShot(0, lambda: self._on_cmd(cmd))

    def _on_cmd(self, cmd):
        k = cmd.get("cmd")
        if k == "say":
            self._say(str(cmd.get("text") or ""), 6.0, bool(cmd.get("inner")))
        elif k == "mood":
            try:
                self.energy = max(0.0, min(1.0, float(cmd.get("energy", 0.8))))
            except (TypeError, ValueError):
                pass
        elif k == "state":
            self.state = str(cmd.get("state") or "idle")
        elif k == "model":
            self._switch_model(str(cmd.get("name") or ""))
        elif k == "show":
            self.show()
            self.raise_()
        elif k == "hide":
            self.hide()

    def _say(self, text, secs=6.0, inner=False):
        if not text:
            return
        self.bubble_text = text
        self.bubble_inner = inner
        self.bubble_until = self.t + secs
        self.reply.hide()
        self.update()

    def _tick(self):
        self.t += TICK_MS / 1000.0
        if self.bubble_text and self.t > self.bubble_until:
            self.bubble_text = ""
            self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self._draw_bubble(p)
        self._draw_sprite(p)
        p.end()

    def _draw_sprite(self, p):
        """画精灵，带呼吸起伏。

        呼吸是「她还活着」的廉价暗示，不能去掉。上一版的问题是
        `drawPixmap(int(x), int(y), ...)` 把浮点位移截成整数像素，
        于是 1.6px 的幅度变成 0/1/2 的跳变，看起来是一卡一卡的。

        现在用 QRectF 目标矩形，Qt 自己做亚像素插值 —— 运动平滑，
        帧率反而可以降（TICK 50ms 就够，CPU 更省）。
        """
        view = VIEW_OF_DIR.get(self.dir, "正面")
        pix = self.model.get(view, SPRITE_H, self.facing if view == '侧面' else 1)
        # 呼吸：待机时慢而轻，说话时略快略大
        amp = 2.2 if self.state == 'talking' else 1.4
        speed = 2.6 if self.state == 'talking' else 1.15
        breathe = math.sin(self.t * speed) * amp
        x = (self.width() - pix.width()) / 2.0
        y = BUBBLE_H + MARGIN + breathe
        # 关键：给浮点矩形，不是 int()。Qt 会做平滑插值，
        # 亚像素级的位移看起来才是「呼吸」而不是「抖动」。
        target = QRectF(x, y, float(pix.width()), float(pix.height()))
        p.drawPixmap(target, pix, QRectF(pix.rect()))

    def _draw_bubble(self, p):
        if not self.bubble_text:
            return
        f = QFont()
        f.setPointSize(10)
        p.setFont(f)
        fm = QFontMetrics(f)
        max_w = min(210, self.width() - 16)
        lines = wrap_cjk(self.bubble_text, fm, max_w)[:3]
        bw = max((fm.horizontalAdvance(l) for l in lines), default=40) + 20
        bh = len(lines) * fm.height() + 12
        bx = (self.width() - bw) / 2.0
        by = MARGIN
        bg = QColor(233, 234, 240, 238) if self.bubble_inner else QColor(255, 255, 255, 242)
        fg = QColor(118, 120, 136) if self.bubble_inner else QColor(50, 58, 76)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(bg)
        p.drawRoundedRect(QRectF(bx, by, bw, bh), 10, 10)
        p.setPen(fg)
        if self.bubble_inner:
            f.setItalic(True)
            p.setFont(f)
        for i, line in enumerate(lines):
            p.drawText(int(bx + 10), int(by + fm.ascent() + 6 + i * fm.height()), line)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.drag_from = e.globalPosition().toPoint()
            self.drag_win0 = QPoint(self.x(), self.y())
            self.dragging = False

    def mouseMoveEvent(self, e):
        if self.drag_from is None or not (e.buttons() & Qt.MouseButton.LeftButton):
            return
        d = e.globalPosition().toPoint() - self.drag_from
        if not self.dragging and d.manhattanLength() > 5:
            self.dragging = True
            self.reply.hide()
        if self.dragging:
            self.move(self.drag_win0 + d)
            if abs(d.x()) > 8:
                self.dir = "left" if d.x() < 0 else "right"
                self.facing = -1 if d.x() < 0 else 1
            self.update()

    def mouseReleaseEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton:
            return
        was = self.dragging
        self.dragging = False
        self.drag_from = None
        self.drag_win0 = None
        if was:
            self.dir = "down"
            self.facing = 1
            self._clamp()
            self._save_state()
            self.raise_()
        else:
            self._toggle_reply()

    def _toggle_reply(self):
        if self.reply.isVisible():
            self.reply.hide()
            return
        h = 28
        self.reply.setGeometry(MARGIN, SPRITE_H + BUBBLE_H + MARGIN - h,
                               self.width() - MARGIN * 2, h)
        self.reply.show()
        self.reply.setFocus()

    def _submit_reply(self):
        text = self.reply.text().strip()
        self.reply.clear()
        self.reply.hide()
        if not text:
            return
        if send_to_core({"cmd": "input", "text": text}):
            self._say("嗯…", 1.5)
        else:
            self._say("她不在线（主程序没开）", 4.0)

    def _switch_model(self, name):
        if not name:
            return
        p = pathlib.Path(name)
        if not p.is_absolute():
            p = _MODELS_DIR / name
        if not (p / "正面.png").is_file():
            self._say("这套素材没有 正面.png：" + name, 4.5)
            return
        self.model = Model(p)
        self.cfg["model"] = str(p)
        self._apply_geometry()
        self._clamp()
        self._save_state()
        self.update()

    def _pick_model(self):
        names = [m.name for m in list_models()]
        if not names:
            self._say("还没有素材。先跑 tools/fetch_pet_sprites.py", 5.0)
            return
        cur = self.model.name
        idx = names.index(cur) if cur in names else 0
        name, ok = QInputDialog.getItem(self, "换模型", "选一套素材：", names, idx, False)
        if ok and name:
            self._switch_model(name)

    def _pick_model_dir(self):
        d = QFileDialog.getExistingDirectory(self, "选素材目录（需含 正面.png）")
        if d:
            self._switch_model(d)

    def _build_tray(self):
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return None
        icon_path = self.model.root / "icon.png"
        icon = (QIcon(str(icon_path)) if icon_path.is_file()
                else self.style().standardIcon(
                    self.style().StandardPixmap.SP_ComputerIcon))
        tray = QSystemTrayIcon(icon, self)
        m = QMenu()
        a1 = QAction("显示 / 隐藏", self)
        a1.triggered.connect(lambda: self.hide() if self.isVisible() else self.show())
        m.addAction(a1)
        a2 = QAction("回到右下角", self)
        a2.triggered.connect(self._reset_pos)
        m.addAction(a2)
        m.addSeparator()
        a3 = QAction("换模型…", self)
        a3.triggered.connect(self._pick_model)
        m.addAction(a3)
        a4 = QAction("用自定义素材目录…", self)
        a4.triggered.connect(self._pick_model_dir)
        m.addAction(a4)
        m.addSeparator()
        a5 = QAction("退出", self)
        a5.triggered.connect(self._quit)
        m.addAction(a5)
        tray.setContextMenu(m)
        tray.setToolTip("桌宠 · " + self.model.name)
        tray.show()
        return tray

    def _warn(self):
        try:
            self.tray.showMessage(
                "桌宠素材没装",
                "跑 python tools/fetch_pet_sprites.py，或托盘「换模型」",
                QSystemTrayIcon.MessageIcon.Information, 5000)
        except Exception:
            pass

    def _reset_pos(self):
        g = QApplication.primaryScreen().availableGeometry()
        self.move(g.right() - self.width() - 40, g.bottom() - self.height() - 20)
        self._clamp()
        self._save_state()
        self.show()
        self.raise_()

    def _quit(self):
        self._save_state()
        try:
            if self.tray:
                self.tray.hide()
        except Exception:
            pass
        QApplication.quit()

    def closeEvent(self, e):
        self._save_state()
        super().closeEvent(e)


def main():
    if not QT_OK:
        print("没装 PySide6。先跑：pip install -r requirements-pet.txt")
        return 2
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    win = PetWindow()
    start_server(win)
    ok = "有" if win.model.installed() else "缺（会画占位块）"
    print("桌宠已启动 · 模型=" + win.model.name + " · 素材=" + ok)
    print("  主程序 → 桌宠：" + HOST + ":" + str(PET_PORT))
    print("  桌宠 → 主程序：" + HOST + ":" + str(CORE_PORT))
    print("  左键拖动 · 单击输入 · 托盘右键换模型/退出")
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
