# -*- coding: utf-8 -*-
"""端到端测试：真实 pynput 钩子 + 模拟按键，验证热键切换与显示。"""
import sys
from PyQt5.QtCore import QTimer, QEventLoop
from PyQt5.QtWidgets import QApplication
from pynput import keyboard
import key_overlay as ko

app = QApplication(sys.argv)
cfg = ko.load_config()
ov = ko.OverlayWindow(cfg)
ov.show()
tracker = ko.KeyTracker(0.5)
tracker.text_changed.connect(ov.set_text)
tracker.edit_toggled.connect(ov.toggle_edit_mode)

texts = []
tracker.text_changed.connect(texts.append)

listener = keyboard.Listener(
    on_press=lambda k: tracker.feed(ko.key_to_name(k), True),
    on_release=lambda k: tracker.feed(ko.key_to_name(k), False))
listener.daemon = True
listener.start()

loop = QEventLoop()
QTimer.singleShot(600, loop.quit)
loop.exec_()  # 等钩子就绪

ctl = keyboard.Controller()
with ctl.pressed(keyboard.Key.ctrl, keyboard.Key.alt):
    ctl.press("l")
    ctl.release("l")

QTimer.singleShot(800, loop.quit)
loop.exec_()

assert ov.edit_mode is True, "第一次 Ctrl+Alt+L 未进入编辑模式"
assert "VK" not in "".join(texts), "出现 VK 乱码: %r" % texts
assert not any("按键" in t for t in texts), "出现兜底名: %r" % texts
print("[8] 真实钩子 + Ctrl+Alt+L 进入编辑模式: 通过, 显示序列 =", texts)

texts.clear()
with ctl.pressed(keyboard.Key.ctrl, keyboard.Key.alt):
    ctl.press("l")
    ctl.release("l")
QTimer.singleShot(800, loop.quit)
loop.exec_()

assert ov.edit_mode is False, "第二次 Ctrl+Alt+L 未切回穿透模式"
print("[9] 再按一次切回穿透模式: 通过")

listener.stop()
ov.hide()
sys.exit(0)
