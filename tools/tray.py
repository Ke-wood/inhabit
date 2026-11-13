"""面板托盘图标：让用户有一个明确的地方关掉面板。

为什么需要这个
--------------
面板是个后台进程，关了浏览器窗口它还在跑。用户没有「退出」的入口，
只能去任务管理器杀 pythonw —— 这既不安全（可能杀错进程），也很劝退。
托盘图标是最符合直觉的出口：右下角一个小鲸鱼，右键「打开面板 / 退出」。

设计约束
--------
- **没有 PySide6 也要能跑**：面板是核心功能，托盘只是锦上添花。
  装不上 Qt 就静默退化成「只开浏览器」，绝不因此让面板起不来。
- **必须在主线程用托盘**：Qt 的事件循环要占住主线程，而 HTTP 服务要
  在后台线程。所以这里有意的分工是：serve_forever 在子线程，
  托盘（或降级后的 sleep 循环）在主线程。
"""

from __future__ import annotations

import threading


def available() -> bool:
    """装没装 PySide6 / 有没有图形环境。"""
    try:
        import PySide6.QtWidgets  # noqa: F401
        return True
    except Exception:
        return False


def _icon():
    """小鲸鱼图标。找不到图标文件就画一个蓝圆点，不让托盘变成空白。"""
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QBrush, QColor, QIcon, QPainter, QPixmap

    px = QPixmap(64, 64)
    px.fill(Qt.transparent)
    p = QPainter(px)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QBrush(QColor("#2f81f7")))
    p.setPen(Qt.NoPen)
    p.drawEllipse(QRectF(6, 10, 52, 44))
    # 尾巴
    p.drawEllipse(QRectF(44, 26, 18, 16))
    # 眼睛
    p.setBrush(QBrush(QColor("#ffffff")))
    p.drawEllipse(QRectF(18, 26, 9, 9))
    p.drawEllipse(QRectF(35, 26, 9, 9))
    p.setBrush(QBrush(QColor("#0b0e12")))
    p.drawEllipse(QRectF(21, 29, 4, 4))
    p.drawEllipse(QRectF(38, 29, 4, 4))
    p.end()
    return QIcon(px)


def serve_with_tray(url: str, port: int, *, on_open=None) -> int:
    """在托盘里跑面板。返回退出码。

    调用方负责：HTTP 服务跑在后台线程，这里占住主线程跑 Qt。
    """
    from PySide6.QtGui import QAction
    from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

    app = QApplication.instance() or QApplication([])
    # 关掉最后一个窗口就退出 —— 但托盘不是窗口，所以要显式禁止
    app.setQuitOnLastWindowClosed(False)

    tray = QSystemTrayIcon(_icon())
    tray.setToolTip(f"控制面板 · {url}")

    menu = QMenu()

    def _open():
        if on_open:
            on_open()
        else:
            import webbrowser
            webbrowser.open(url)

    act_open = QAction("打开面板", menu)
    act_open.triggered.connect(_open)
    menu.addAction(act_open)

    menu.addSeparator()

    act_quit = QAction("退出面板", menu)
    act_quit.triggered.connect(lambda: _quit())
    menu.addAction(act_quit)

    tray.setContextMenu(menu)
    tray.activated.connect(
        lambda reason: _open() if reason == QSystemTrayIcon.Trigger else None)
    tray.show()

    def _show_hint():
        tray.showMessage("控制面板已在后台运行",
                         f"右键这个小图标可以打开页面或退出：{url}",
                         QSystemTrayIcon.Information, 4000)

    # 起好之后再冒个提示，让用户知道图标在哪
    from PySide6.QtCore import QTimer
    QTimer.singleShot(600, _show_hint)

    def _quit():
        tray.hide()
        app.quit()

    # 第一次自动打开页面，和以前的行为一致
    from PySide6.QtCore import QTimer as _T
    _T.singleShot(800, _open)

    return app.exec()


def serve_headless(url: str) -> int:
    """没有图形环境时的退路：不装托盘，主线程就等着。

    HTTP 服务在别的线程已经跑起来了，这里只需要别让进程退出。
    """
    try:
        while True:
            threading.Event().wait(3600)
    except KeyboardInterrupt:
        return 0
