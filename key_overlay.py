# -*- coding: utf-8 -*-
"""
屏幕按键实时显示悬浮窗 (KeyOverlay)
=====================================

功能：
1. 无边框、置顶、背景半透明的悬浮窗，实时显示全局键盘按键。
2. 默认“穿透模式”（鼠标穿透，不抢焦点），全局热键 Ctrl+Alt+L 切换到“编辑模式”
   （可拖拽、显示虚线边框）。
3. 系统托盘图标 + 右键菜单（设置 / 隐藏显示 / 退出）。
4. 设置窗口（字体、颜色、背景透明度、坐标、清空延迟），保存立即生效。
5. 配置持久化到同目录 config.json，启动自动恢复。

依赖：PyQt5、pynput
"""

import ctypes
import json
import os
import signal
import sys
import time

from PyQt5.QtCore import Qt, QObject, QTimer, pyqtSignal
from PyQt5.QtGui import (
    QColor, QFont, QFontDatabase, QFontMetrics, QIcon, QPainter, QPen, QPixmap,
)
from PyQt5.QtWidgets import (
    QApplication, QColorDialog, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QDoubleSpinBox, QFormLayout, QHBoxLayout, QLabel, QPushButton, QSlider,
    QSpinBox, QSystemTrayIcon, QMenu, QWidget,
)

from pynput import keyboard


# ---------------------------------------------------------------------------
# 一、配置持久化（JSON）
# ---------------------------------------------------------------------------

# 默认配置：字体、颜色、位置、清空延迟、背景不透明度
DEFAULT_CONFIG = {
    "x": 80,               # 悬浮窗左上角 X 坐标
    "y": 80,               # 悬浮窗左上角 Y 坐标
    "font_family": "微软雅黑",
    "font_size": 22,
    "bold": True,
    "italic": False,
    "font_color": "#FFFFFF",   # 字体颜色 #RRGGBB
    "bg_color": "#101820",     # 背景颜色 #RRGGBB
    "bg_alpha": 70,            # 背景不透明度 0~100（百分比，越高越不透明）
    "clear_delay": 1.5,        # 按键释放后自动清空的延迟（秒）
}


def config_path() -> str:
    """返回 config.json 的绝对路径（与脚本/exe 同目录）。"""
    if getattr(sys, "frozen", False):
        # PyInstaller 打包后：取 exe 所在目录
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, "config.json")


def load_config() -> dict:
    """读取配置文件；文件缺失或损坏时返回默认配置的副本。"""
    cfg = dict(DEFAULT_CONFIG)
    path = config_path()
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                for k in cfg:
                    if k in data:
                        cfg[k] = data[k]
    except Exception as exc:  # 配置损坏不应导致程序崩溃
        print("[警告] 读取配置失败，已使用默认配置：", exc)
    return cfg


def save_config(cfg: dict) -> None:
    """原子化写入配置（先写临时文件再替换，避免写一半损坏）。"""
    path = config_path()
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception as exc:
        print("[警告] 保存配置失败：", exc)


# ---------------------------------------------------------------------------
# 二、Windows 底层 API：鼠标穿透 / 不抢焦点
# ---------------------------------------------------------------------------

user32 = ctypes.windll.user32

GWL_EXSTYLE = -20            # 扩展窗口样式在 Get/SetWindowLong 中的索引
WS_EX_LAYERED = 0x00080000   # 分层窗口（半透明所必需）
WS_EX_TRANSPARENT = 0x00000020  # 鼠标穿透：点击会“漏”给下层窗口
WS_EX_NOACTIVATE = 0x08000000  # 窗口被点击时不会获得输入焦点


def apply_exstyle(hwnd: int, passthrough: bool) -> None:
    """
    核心：调整窗口的扩展样式（WS_EX_*）。

    - 无论哪种模式都保留 WS_EX_LAYERED + WS_EX_NOACTIVATE：
        * LAYERED：配合 WA_TranslucentBackground 实现背景半透明；
        * NOACTIVATE：悬浮窗永远不抢走前台软件的焦点。
    - 穿透模式：追加 WS_EX_TRANSPARENT，所有鼠标事件直接穿透到下层窗口，
      悬浮窗收不到任何点击，因此也无法被拖拽。
    - 编辑模式：清除 WS_EX_TRANSPARENT，悬浮窗重新接收鼠标事件，
      于是可以响应 mousePressEvent / mouseMoveEvent 完成拖拽。
    """
    ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    ex |= WS_EX_LAYERED | WS_EX_NOACTIVATE
    if passthrough:
        ex |= WS_EX_TRANSPARENT
    else:
        ex &= ~WS_EX_TRANSPARENT
    user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ex)


# ---------------------------------------------------------------------------
# 三、按键名称规范化
# ---------------------------------------------------------------------------

# 特殊按键 -> 显示名称
_NAME_MAP = {
    "ctrl": "Ctrl", "alt": "Alt", "shift": "Shift", "cmd": "Win",
    "alt_gr": "AltGr", "caps_lock": "CapsLock", "tab": "Tab",
    "enter": "Enter", "space": "Space", "backspace": "Backspace",
    "esc": "Esc", "delete": "Del", "insert": "Ins",
    "home": "Home", "end": "End", "page_up": "PgUp", "page_down": "PgDn",
    "up": "↑", "down": "↓", "left": "←", "right": "→",
    "print_screen": "PrtSc", "scroll_lock": "ScrLk", "pause": "Pause",
    "num_lock": "NumLk", "menu": "Menu",
}

# 虚拟键码(VK) -> 显示名称。用于 char 不可用时的稳定命名。
# 注意：同一个物理键无论 press/release、无论按住什么修饰键，
# 都必须映射成同一个名字，否则按键列表会残留导致显示卡死。
_VK_NAME = {
    0x08: "Backspace", 0x09: "Tab", 0x0D: "Enter", 0x13: "Pause",
    0x14: "CapsLock", 0x1B: "Esc", 0x20: "Space", 0x21: "PgUp",
    0x22: "PgDn", 0x23: "End", 0x24: "Home", 0x25: "←", 0x26: "↑",
    0x27: "→", 0x28: "↓", 0x2C: "PrtSc", 0x2D: "Ins", 0x2E: "Del",
    0x5B: "Win", 0x5C: "Win", 0x90: "NumLk", 0x91: "ScrLk",
    0xA0: "Shift", 0xA1: "Shift", 0xA2: "Ctrl", 0xA3: "Ctrl",
    0xA4: "Alt", 0xA5: "Alt",
    # 常见 OEM 键（英文标点）
    0xBA: ";", 0xBB: "=", 0xBC: ",", 0xBD: "-", 0xBE: ".",
    0xBF: "/", 0xC0: "`", 0xDB: "[", 0xDC: "\\", 0xDD: "]",
    0xDE: "'", 0xE2: "\\",
    # 小键盘运算符
    0x6A: "*", 0x6B: "+", 0x6D: "-", 0x6E: ".", 0x6F: "/",
    # 小键盘在 NumLock 关闭时对应的导航功能
    0x60: "Ins", 0x61: "End", 0x62: "↓", 0x63: "PgDn",
    0x64: "←", 0x65: "5", 0x66: "→", 0x67: "Home",
    0x68: "↑", 0x69: "PgUp",
}


def _numlock_on() -> bool:
    """读取 NumLock 当前是否点亮（低位置 1 表示开启）。"""
    try:
        return bool(user32.GetKeyState(0x90) & 0x0001)
    except Exception:
        return True


def _vk_name(vk: int):
    """虚拟键码转显示名；无对应项时返回 None。"""
    if 0x30 <= vk <= 0x39:          # 主键盘数字 0-9
        return chr(vk)
    if 0x41 <= vk <= 0x5A:          # 主键盘字母 A-Z
        return chr(vk)
    if 0x60 <= vk <= 0x69:          # 小键盘 0-9：受 NumLock 状态影响
        return chr(vk - 0x30) if _numlock_on() else _VK_NAME.get(vk)
    if 0x70 <= vk <= 0x87:          # F1-F24
        return "F%d" % (vk - 0x6F)
    return _VK_NAME.get(vk)


def key_to_name(key) -> str:
    """把 pynput 的 Key / KeyCode 对象转换为友好显示名。"""
    if isinstance(key, keyboard.Key):
        name = key.name  # 例如 'ctrl_l'、'f5'、'space'
        # 左右修饰键归一化：ctrl_l / ctrl_r -> ctrl
        if name.endswith("_l") or name.endswith("_r"):
            base = name[:-2]
            if base in ("ctrl", "alt", "shift", "cmd"):
                name = base
        if name in _NAME_MAP:
            return _NAME_MAP[name]
        # F1~F24
        if len(name) > 1 and name[0] == "f" and name[1:].isdigit():
            return name.upper()
        return name

    if isinstance(key, keyboard.KeyCode):
        ch = key.char
        # 1) 优先用虚拟键码 vk 命名。
        #    char 在组合键（Ctrl/Alt/Fn）和输入法下极不稳定：
        #    同一次按键 press 可能拿到 'l'、下一次自动重复拿到 None，
        #    Ctrl+C 拿到控制字符 '\x03'。名字一旦不一致，按下列表
        #    就会残留、显示卡死，甚至冒出 VK97 这类数字乱码。
        #    vk 是驱动层给的物理键编号，永远稳定。
        if key.vk:
            name = _vk_name(key.vk)
            if name:
                return name

        # 2) vk 无映射时（如输入法/软件注入的 VK_PACKET 字符）退回 char
        if ch and ch.isprintable() and ch.isascii():
            return ch.upper() if ch.isalpha() else ch
        if ch and ch.isprintable():
            return ch

        # 3) 未知的虚拟键码（如微信输入法语音输入注入的 0xFC=252）：
        #    这类码不属于任何物理键，显示成“按键252”只会吓到用户，
        #    因此返回空串表示“忽略此键”，不参与显示与状态跟踪。
        if key.vk:
            return ""
        return ""


# ---------------------------------------------------------------------------
# 三·补充、按键原始事件调试日志（排查异常按键时使用）
# ---------------------------------------------------------------------------
# 用法：设置环境变量 KEYOVERLAY_DEBUG=1 后再启动程序，
# 所有原始按键会追加写入 key_overlay_debug.log，便于确认
# 某个组合键（如 Fn+F12）到底发来了什么 vk / char。
DEBUG_LOG = os.environ.get("KEYOVERLAY_DEBUG", "") == "1"


def log_key(raw, pressed: bool, name: str) -> None:
    """把原始按键对象与转换结果写入调试日志（失败静默）。"""
    if not DEBUG_LOG:
        return
    try:
        path = os.path.join(os.path.dirname(config_path()), "key_overlay_debug.log")
        with open(path, "a", encoding="utf-8") as f:
            f.write("%s  press=%-5s  raw=%r  -> name=%s\n" % (
                time.strftime("%H:%M:%S"), "按下" if pressed else "松开", raw, name))
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 四、按键状态跟踪（运行在 Qt 主线程，通过信号接收监听线程数据）
# ---------------------------------------------------------------------------

class KeyTracker(QObject):
    """
    维护“当前按下的按键顺序”并生成显示文本。

    线程模型：pynput 监听线程只负责 emit 信号（raw_key），
    真正的业务逻辑全部在主线程的槽函数里执行 —— 这样完全不需要锁，
    也绝不会出现跨线程操作控件导致的界面卡死。
    """

    raw_key = pyqtSignal(str, bool)      # 来自监听线程：按键名, 是否按下
    text_changed = pyqtSignal(str)       # 通知悬浮窗更新显示文本
    edit_toggled = pyqtSignal()          # 通知悬浮窗切换编辑/穿透模式

    HOTKEY = ("Ctrl", "Alt", "L")        # 全局热键：Ctrl + Alt + L

    def __init__(self, clear_delay: float = 1.5, parent=None):
        super().__init__(parent)
        self.down = []          # 当前按下的按键（保持按下的先后顺序）
        self.suppress = False   # 热键触发期间抑制显示
        self.clear_delay = int(clear_delay * 1000)
        self._last_event = 0.0  # 最后一次收到按键事件的时间（monotonic）

        # 释放后延迟清空的计时器（单次触发）
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(lambda: self.text_changed.emit(""))

        # 看门狗：清理因 press/release 名字不一致等原因残留的按键，
        # 防止“按下列表永远不为空”导致文本卡死不更新。
        # 依据：长按时 Windows 会持续产生自动重复事件不断刷新时间戳，
        # 因此真正长按的键不会被误清理。
        self.watchdog = QTimer(self)
        self.watchdog.setInterval(1000)
        self.watchdog.timeout.connect(self._check_stale)
        self.watchdog.start()

        self.raw_key.connect(self._on_raw_key)   # 自动建立跨线程队列连接

    # ---- 供监听线程调用（只发信号，不碰任何 UI） ----
    def feed(self, name: str, pressed: bool) -> None:
        self.raw_key.emit(name, pressed)

    def _check_stale(self) -> None:
        """超过 2 秒没有任何按键事件却仍显示“按住”状态 -> 判定为残留。"""
        if self.down and (time.monotonic() - self._last_event) > 2.0:
            self.down.clear()
            self.suppress = False
            if not self.timer.isActive():
                self.timer.start(self.clear_delay)

    # ---- 主线程槽函数 ----
    def _on_raw_key(self, name: str, pressed: bool) -> None:
        if not name:
            return  # 未知/注入型虚拟键（如输入法的 0xFC）：直接忽略，不显示
        self._last_event = time.monotonic()  # 自动重复事件也刷新时间戳
        if pressed:
            if name in self.down:
                return  # 键盘自动重复触发，忽略
            self.down.append(name)

            # 判定全局热键 Ctrl+Alt+L
            if (name == "L" and all(k in self.down for k in ("Ctrl", "Alt"))):
                self.suppress = True      # 抑制热键本身及后续按住期间的显示
                self.timer.stop()
                self.text_changed.emit("")
                self.edit_toggled.emit()
                return

            if self.suppress:
                return
            self.timer.stop()             # 新按键到来 -> 取消待清空计时
            self.text_changed.emit(" + ".join(self.down))
        else:
            if name in self.down:
                self.down.remove(name)
            if self.suppress:
                # 热键组合全部松开后才恢复显示
                if not self.down:
                    self.suppress = False
                return
            # 冻结当前文本：只在第一次释放时启动清空计时（后续释放不打断）
            if not self.timer.isActive():
                self.timer.start(self.clear_delay)

    def set_clear_delay(self, seconds: float) -> None:
        self.clear_delay = int(seconds * 1000)


# ---------------------------------------------------------------------------
# 五、悬浮窗本体
# ---------------------------------------------------------------------------

class OverlayWindow(QWidget):
    """无边框、置顶、透明背景的按键显示悬浮窗。"""

    def __init__(self, config: dict, parent=None):
        # Frameless：无边框；StaysOnTop：置顶；Tool：不出现在任务栏
        super().__init__(parent, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)

        self.config = config
        self.edit_mode = False          # False=穿透模式，True=编辑模式
        self.text = ""                  # 当前显示文本
        self._drag_offset = None        # 拖拽偏移量

        # 关键属性：
        # WA_TranslucentBackground -> 背景可半透明（任意像素 alpha）
        # WA_ShowWithoutActivating -> 显示时不抢焦点
        # NoFocus                  -> Qt 层面也拒绝获得焦点（双保险）
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setFocusPolicy(Qt.NoFocus)
        self.setMouseTracking(True)

        self._apply_style_from_config()
        self.move(config["x"], config["y"])
        self._resize_to_content()

    # ---------- 样式 ----------
    def _apply_style_from_config(self) -> None:
        cfg = self.config
        f = QFont(cfg["font_family"])
        f.setPointSize(int(cfg["font_size"]))
        f.setBold(bool(cfg["bold"]))
        f.setItalic(bool(cfg["italic"]))
        self.setFont(f)

    def apply_config(self, cfg: dict) -> None:
        """设置保存后立即应用（字体/颜色/透明度/位置/延迟）。"""
        self.config = cfg
        self._apply_style_from_config()
        self.move(int(cfg["x"]), int(cfg["y"]))
        self._resize_to_content()
        self.update()

    def set_text(self, text: str) -> None:
        """更新显示文本并按内容自适应调整窗口大小（保持左上角不动）。"""
        if text == self.text:
            return
        self.text = text
        self._resize_to_content()
        self.update()

    def _resize_to_content(self) -> None:
        fm = QFontMetrics(self.font())
        w = fm.horizontalAdvance(self.text) + 48 if self.text else 90
        h = fm.height() + 30
        self.resize(max(w, 96), max(h, 52))

    # ---------- 编辑模式 / 穿透模式 ----------
    def set_edit_mode(self, on: bool) -> None:
        if self.edit_mode == on:
            return
        self.edit_mode = on
        # 通过 Windows API 切换 WS_EX_TRANSPARENT（穿透 <-> 可点击）
        apply_exstyle(int(self.winId()), passthrough=not on)
        self.update()  # 触发重绘，画出/隐藏虚线边框

    def toggle_edit_mode(self) -> None:
        self.set_edit_mode(not self.edit_mode)

    def showEvent(self, event) -> None:
        """窗口首次显示后立即应用扩展样式（必须在有 HWND 之后调用）。"""
        super().showEvent(event)
        apply_exstyle(int(self.winId()), passthrough=not self.edit_mode)

    # ---------- 绘制 ----------
    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)

        # 1) 半透明圆角背景
        bg = QColor(self.config["bg_color"])
        bg.setAlpha(int(255 * int(self.config["bg_alpha"]) / 100))
        body = self.rect().adjusted(2, 2, -2, -2)
        p.setPen(Qt.NoPen)
        p.setBrush(bg)
        p.drawRoundedRect(body, 14, 14)

        # 2) 按键文本
        if self.text:
            p.setFont(self.font())
            p.setPen(QColor(self.config["font_color"]))
            p.drawText(body, Qt.AlignCenter, self.text)

        # 3) 编辑模式下画虚线边框作为提示
        if self.edit_mode:
            pen = QPen(QColor(0, 200, 255), 2, Qt.DashLine)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(self.rect().adjusted(2, 2, -2, -2), 14, 14)

    # ---------- 编辑模式下的拖拽 ----------
    def mousePressEvent(self, event) -> None:
        # 只有编辑模式（已移除 WS_EX_TRANSPARENT）才可能收到鼠标事件
        if self.edit_mode and event.button() == Qt.LeftButton:
            self._drag_offset = event.globalPos() - self.pos()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._drag_offset is not None and event.buttons() & Qt.LeftButton:
            self.move(event.globalPos() - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._drag_offset is not None:
            self._drag_offset = None
            # 拖拽结束，立即持久化新坐标
            self.config["x"] = self.x()
            self.config["y"] = self.y()
            save_config(self.config)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    # ---------- 编辑模式下滚轮直接调字号 ----------
    def wheelEvent(self, event) -> None:
        # 穿透模式下窗口收不到鼠标事件（WS_EX_TRANSPARENT），
        # 所以滚轮只有在编辑模式（Ctrl+Alt+L 切换）下才可用。
        if not self.edit_mode:
            super().wheelEvent(event)
            return
        delta = event.angleDelta().y()
        if delta:
            step = 1 if delta > 0 else -1
            size = max(8, min(120, int(self.config["font_size"]) + step))
            if size != int(self.config["font_size"]):
                self.config["font_size"] = size
                self._apply_style_from_config()
                self._resize_to_content()
                self.update()
                save_config(self.config)   # 立即持久化
        event.accept()


# ---------------------------------------------------------------------------
# 六、设置窗口
# ---------------------------------------------------------------------------

class SettingsDialog(QDialog):
    """设置界面；点击“保存”时把新配置通过 saved 信号发出去。"""

    saved = pyqtSignal(dict)

    def __init__(self, config: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("按键悬浮窗 - 设置")
        self.setModal(True)
        self.config = dict(config)

        form = QFormLayout(self)

        # 字体名称
        self.ed_family = QComboBox()
        self.ed_family.setEditable(True)
        families = QFontDatabase().families()
        self.ed_family.addItems(families)
        idx = self.ed_family.findText(config["font_family"])
        self.ed_family.setCurrentIndex(idx if idx >= 0 else 0)
        form.addRow("字体：", self.ed_family)

        # 字号
        self.ed_size = QSpinBox()
        self.ed_size.setRange(8, 120)
        self.ed_size.setValue(int(config["font_size"]))
        form.addRow("字号：", self.ed_size)

        # 加粗 / 斜体
        row = QHBoxLayout()
        self.ck_bold = QCheckBox("加粗")
        self.ck_bold.setChecked(bool(config["bold"]))
        self.ck_italic = QCheckBox("斜体")
        self.ck_italic.setChecked(bool(config["italic"]))
        row.addWidget(self.ck_bold)
        row.addWidget(self.ck_italic)
        form.addRow("字形：", row)

        # 字体颜色
        self.btn_font_color = QPushButton()
        self._font_color = QColor(config["font_color"])
        self.btn_font_color.clicked.connect(self._pick_font_color)
        form.addRow("字体颜色：", self.btn_font_color)

        # 背景颜色
        self.btn_bg_color = QPushButton()
        self._bg_color = QColor(config["bg_color"])
        self.btn_bg_color.clicked.connect(self._pick_bg_color)
        form.addRow("背景颜色：", self.btn_bg_color)

        # 背景不透明度滑动条
        alpha_row = QHBoxLayout()
        self.sl_alpha = QSlider(Qt.Horizontal)
        self.sl_alpha.setRange(0, 100)
        self.sl_alpha.setValue(int(config["bg_alpha"]))
        self.lb_alpha = QLabel("%d%%" % int(config["bg_alpha"]))
        self.sl_alpha.valueChanged.connect(
            lambda v: self.lb_alpha.setText("%d%%" % v))
        alpha_row.addWidget(self.sl_alpha)
        alpha_row.addWidget(self.lb_alpha)
        form.addRow("背景不透明度：", alpha_row)

        # 清空延迟
        self.sp_delay = QDoubleSpinBox()
        self.sp_delay.setRange(0.2, 10.0)
        self.sp_delay.setSingleStep(0.1)
        self.sp_delay.setSuffix(" 秒")
        self.sp_delay.setValue(float(config["clear_delay"]))
        form.addRow("释放后清空延迟：", self.sp_delay)

        # 窗口坐标
        pos_row = QHBoxLayout()
        self.sp_x = QSpinBox()
        self.sp_x.setRange(-3000, 8000)
        self.sp_x.setValue(int(config["x"]))
        self.sp_y = QSpinBox()
        self.sp_y.setRange(-3000, 8000)
        self.sp_y.setValue(int(config["y"]))
        pos_row.addWidget(QLabel("X:"))
        pos_row.addWidget(self.sp_x)
        pos_row.addWidget(QLabel("Y:"))
        pos_row.addWidget(self.sp_y)
        form.addRow("窗口位置：", pos_row)

        # 提示
        form.addRow("", QLabel("提示：按 Ctrl+Alt+L 可切换编辑模式，直接拖拽悬浮窗。"))

        # 保存 / 取消
        self.buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        self.buttons.accepted.connect(self._on_save)
        self.buttons.rejected.connect(self.reject)
        form.addRow(self.buttons)

        self._refresh_color_buttons()

    # ---------- 颜色选择 ----------
    def _refresh_color_buttons(self) -> None:
        self.btn_font_color.setText(self._font_color.name())
        self.btn_font_color.setStyleSheet(
            "background-color:%s; color:%s; padding:6px;"
            % (self._font_color.name(),
               "black" if self._font_color.lightness() > 128 else "white"))
        self.btn_bg_color.setText(self._bg_color.name())
        self.btn_bg_color.setStyleSheet(
            "background-color:%s; color:%s; padding:6px;"
            % (self._bg_color.name(),
               "black" if self._bg_color.lightness() > 128 else "white"))

    def _pick_font_color(self) -> None:
        c = QColorDialog.getColor(self._font_color, self, "选择字体颜色")
        if c.isValid():
            self._font_color = c
            self._refresh_color_buttons()

    def _pick_bg_color(self) -> None:
        c = QColorDialog.getColor(self._bg_color, self, "选择背景颜色")
        if c.isValid():
            self._bg_color = c
            self._refresh_color_buttons()

    # ---------- 保存 ----------
    def _on_save(self) -> None:
        new_cfg = dict(self.config)
        new_cfg.update({
            "font_family": self.ed_family.currentText(),
            "font_size": self.ed_size.value(),
            "bold": self.ck_bold.isChecked(),
            "italic": self.ck_italic.isChecked(),
            "font_color": self._font_color.name(),
            "bg_color": self._bg_color.name(),
            "bg_alpha": self.sl_alpha.value(),
            "clear_delay": round(self.sp_delay.value(), 2),
            "x": self.sp_x.value(),
            "y": self.sp_y.value(),
        })
        self.saved.emit(new_cfg)
        self.accept()


# ---------------------------------------------------------------------------
# 七、托盘图标
# ---------------------------------------------------------------------------

def make_tray_icon() -> QIcon:
    """用 QPainter 程序化绘制一个托盘图标，避免依赖外部图片文件。"""
    pm = QPixmap(64, 64)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(30, 144, 255))
    p.drawRoundedRect(4, 4, 56, 56, 14, 14)
    p.setPen(QColor(255, 255, 255))
    f = QFont("Arial", 34, QFont.Bold)
    p.setFont(f)
    p.drawText(pm.rect(), Qt.AlignCenter, "K")
    p.end()
    return QIcon(pm)


# ---------------------------------------------------------------------------
# 八、主流程
# ---------------------------------------------------------------------------

def main() -> None:
    # 隐藏模式运行（无控制台窗口时仍然可用），托盘程序必须关闭“最后窗口退出”
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("KeyOverlay")

    config = load_config()

    # --- 悬浮窗 ---
    overlay = OverlayWindow(config)
    overlay.show()

    # --- 按键跟踪（主线程） ---
    tracker = KeyTracker(config["clear_delay"])
    tracker.text_changed.connect(overlay.set_text)
    tracker.edit_toggled.connect(overlay.toggle_edit_mode)

    # --- 全局键盘监听（独立线程） ---
    def on_press(key):
        name = key_to_name(key)
        log_key(key, True, name)
        tracker.feed(name, True)

    def on_release(key):
        name = key_to_name(key)
        log_key(key, False, name)
        tracker.feed(name, False)

    listener = keyboard.Listener(on_press=on_press, on_release=on_release)
    listener.daemon = True
    listener.start()

    # --- 系统托盘 ---
    tray = QSystemTrayIcon(make_tray_icon())
    tray.setToolTip("按键悬浮窗 (Ctrl+Alt+L 切换编辑模式)")
    menu = QMenu()
    act_settings = menu.addAction("设置")
    act_toggle = menu.addAction("隐藏悬浮窗")
    menu.addSeparator()
    act_quit = menu.addAction("退出")
    tray.setContextMenu(menu)
    tray.show()

    settings_dialog = None  # 保持引用，防止被垃圾回收

    def open_settings():
        nonlocal settings_dialog
        if settings_dialog is not None and settings_dialog.isVisible():
            settings_dialog.raise_()
            settings_dialog.activateWindow()
            return
        settings_dialog = SettingsDialog(overlay.config, None)
        settings_dialog.saved.connect(apply_settings)
        settings_dialog.exec_()
        settings_dialog = None

    def apply_settings(cfg: dict) -> None:
        """设置保存：立即生效 + 写盘。"""
        overlay.apply_config(cfg)
        tracker.set_clear_delay(cfg["clear_delay"])
        save_config(cfg)

    def toggle_visible():
        if overlay.isVisible():
            overlay.hide()
            act_toggle.setText("显示悬浮窗")
        else:
            overlay.show()
            act_toggle.setText("隐藏悬浮窗")

    def quit_app() -> None:
        """完全退出：停监听、藏托盘、退出事件循环。"""
        try:
            listener.stop()
        except Exception:
            pass
        tray.hide()
        app.quit()

    act_settings.triggered.connect(open_settings)
    act_toggle.triggered.connect(toggle_visible)
    act_quit.triggered.connect(quit_app)
    tray.activated.connect(
        lambda reason: open_settings() if reason == QSystemTrayIcon.DoubleClick else None)

    # ------------------------------------------------------------------
    # Ctrl+C 处理：第一次按下 -> 优雅退出；随后立即把 SIGINT 设为“忽略”，
    # 这样“按过猛”连敲的后续 Ctrl+C 不会再打断清理流程，
    # 也就不会出现连环 KeyboardInterrupt 把 Python/PowerShell 打崩的情况。
    # ------------------------------------------------------------------
    def _on_sigint(signum, frame):
        signal.signal(signal.SIGINT, signal.SIG_IGN)  # 先屏蔽，防重入
        print("\n已收到 Ctrl+C，正在退出……（继续按 Ctrl+C 无效）")
        quit_app()

    try:
        signal.signal(signal.SIGINT, _on_sigint)
    except (ValueError, OSError):
        pass  # 非主线程或平台不支持时忽略

    code = 0
    try:
        code = app.exec_()
    except KeyboardInterrupt:
        # 兜底：万一 handler 未拦截到（例如发生在 exec 之前）
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        print("\n已收到 Ctrl+C，正在退出……")
        quit_app()
    finally:
        try:
            listener.stop()
        except Exception:
            pass
        tray.hide()
    sys.exit(code)


if __name__ == "__main__":
    main()
