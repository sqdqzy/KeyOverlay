# -*- coding: utf-8 -*-
"""临时冒烟测试：验证配置读写、按键跟踪逻辑、悬浮窗与穿透样式。"""
import sys, time
from PyQt5.QtCore import QTimer, QEventLoop
from PyQt5.QtWidgets import QApplication
import key_overlay as ko

app = QApplication(sys.argv)

# 1. 配置读写
cfg = ko.load_config()
ko.save_config(cfg)
cfg2 = ko.load_config()
assert cfg == cfg2, "配置读写不一致"
print("[1] 配置读写: 通过")

# 2. 悬浮窗 + 穿透样式
ov = ko.OverlayWindow(cfg)
ov.show()
app.processEvents()
ko.apply_exstyle(int(ov.winId()), passthrough=True)
style = ko.user32.GetWindowLongW(int(ov.winId()), ko.GWL_EXSTYLE)
assert style & ko.WS_EX_TRANSPARENT, "穿透标志未生效"
assert style & ko.WS_EX_NOACTIVATE, "NOACTIVATE 未生效"
ov.set_edit_mode(True)
style = ko.user32.GetWindowLongW(int(ov.winId()), ko.GWL_EXSTYLE)
assert not (style & ko.WS_EX_TRANSPARENT), "编辑模式未清除穿透"
ov.set_edit_mode(False)
style = ko.user32.GetWindowLongW(int(ov.winId()), ko.GWL_EXSTYLE)
assert style & ko.WS_EX_TRANSPARENT, "恢复穿透失败"
print("[2] 鼠标穿透/编辑模式切换: 通过")

# 3. 按键跟踪逻辑
texts = []
t = ko.KeyTracker(clear_delay=0.3)
t.text_changed.connect(texts.append)
t.feed("Ctrl", True); t.feed("C", True); t.feed("C", False); t.feed("Ctrl", False)
assert texts == ["Ctrl", "Ctrl + C"], texts
loop = QEventLoop()
QTimer.singleShot(500, loop.quit)
loop.exec_()
assert texts[-1] == "", texts
print("[3] 按键显示与延迟清空: 通过")

# 4. 热键触发
texts.clear(); toggles = []
t2 = ko.KeyTracker(1.0)
t2.text_changed.connect(texts.append)
t2.edit_toggled.connect(lambda: toggles.append(1))
t2.feed("Ctrl", True); t2.feed("Alt", True); t2.feed("L", True)
t2.feed("L", False); t2.feed("Alt", False); t2.feed("Ctrl", False)
assert toggles == [1], toggles
assert texts == ["Ctrl", "Ctrl + Alt", ""], texts
t2.feed("A", True)
assert texts[-1] == "A", texts
print("[4] 全局热键 Ctrl+Alt+L: 通过")

# 5. 按键名转换
from pynput import keyboard
assert ko.key_to_name(keyboard.Key.ctrl_l) == "Ctrl"
assert ko.key_to_name(keyboard.Key.alt_r) == "Alt"
assert ko.key_to_name(keyboard.KeyCode.from_char("a")) == "A"
assert ko.key_to_name(keyboard.Key.f5) == "F5"
assert ko.key_to_name(keyboard.Key.space) == "Space"
assert ko.key_to_name(keyboard.Key.up) == "↑"
# 组合键下的控制字符 -> 字母
assert ko.key_to_name(keyboard.KeyCode(char="\x03", vk=0x43)) == "C"
assert ko.key_to_name(keyboard.KeyCode(char="\x01", vk=0x41)) == "A"
# 无字符（Ctrl+Alt+L 场景）-> 必须得到 L 而不是 VK76
assert ko.key_to_name(keyboard.KeyCode(vk=0x4C)) == "L"
assert ko.key_to_name(keyboard.KeyCode(vk=0x74)) == "F5"
assert ko.key_to_name(keyboard.KeyCode(vk=0x20)) == "Space"
print("[5] 按键名规范化: 通过")

# 5b. 用户报告的异常按键：小键盘 1（vk=97）与主键盘数字
assert ko.key_to_name(keyboard.KeyCode(vk=97)) in ("1", "End"), "小键盘1映射错误"
assert ko.key_to_name(keyboard.KeyCode(vk=0x31)) == "1"
assert ko.key_to_name(keyboard.KeyCode(vk=0x61, char="1")) in ("1", "End")  # 优先 vk
assert ko.key_to_name(keyboard.KeyCode(vk=0x7B)) == "F12"           # F12
assert ko.key_to_name(keyboard.Key.f12) == "F12"
# char 与 vk 不一致时必须以 vk 为准（杜绝 VK 乱码）
_bad = ko.key_to_name(keyboard.KeyCode(vk=97, char="\x03"))
assert _bad in ("1", "End"), _bad
assert "VK" not in _bad and "按键" not in _bad
# 输入法注入的未知 vk（微信语音输入 0xFC=252）必须被忽略
assert ko.key_to_name(keyboard.KeyCode(vk=252)) == "", "未知 vk 应返回空串"
print("[5b] 小键盘/数字键/F12 映射: 通过")

# 5c. 未知键被 KeyTracker 忽略，不影响正常显示
texts_c = []
tc = ko.KeyTracker(0.2)
tc.text_changed.connect(texts_c.append)
tc.feed("", True)          # 按下未知键
tc.feed("A", True)
tc.feed("", False)         # 松开未知键
assert texts_c == ["A"], texts_c
tc.feed("A", False)
print("[5c] 未知注入键被忽略: 通过")

# 6. 热键：模拟真实 Ctrl+Alt+L（char=None, vk=0x4C）
texts2, toggles2 = [], []
t3 = ko.KeyTracker(1.0)
t3.text_changed.connect(texts2.append)
t3.edit_toggled.connect(lambda: toggles2.append(1))
t3.feed("Ctrl", True)
t3.feed("Alt", True)
t3.feed(ko.key_to_name(keyboard.KeyCode(vk=0x4C)), True)   # 按下 L
t3.feed(ko.key_to_name(keyboard.KeyCode(vk=0x4C)), False)  # 释放 L
t3.feed("Alt", False)
t3.feed("Ctrl", False)
assert toggles2 == [1], toggles2
assert texts2 == ["Ctrl", "Ctrl + Alt", ""], texts2
assert t3.down == [] and not t3.suppress, (t3.down, t3.suppress)
print("[6] Ctrl+Alt+L 热键（char=None 场景）: 通过")

# 7. 残留按键看门狗（release 名字异常导致按下列表清不掉的情况）
import time as _t
texts3 = []
t4 = ko.KeyTracker(1.0)
t4.text_changed.connect(texts3.append)
t4.feed("Ctrl", True)
t4.feed("VK76异常残留", True)
t4._last_event = _t.monotonic() - 5   # 模拟 5 秒无事件
t4._check_stale()
assert t4.down == [], t4.down
print("[7] 残留按键看门狗清理: 通过")

ov.hide()
print("全部测试通过")
sys.exit(0)
