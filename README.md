# KeyOverlay — 屏幕按键实时显示悬浮窗

> **opencode 制作声明**
> 本项目的全部代码、文档与打包流程均由 **opencode**（https://opencode.ai）辅助设计与实现。

一个用于**录制视频/直播/教程**时，在屏幕角落实时展示当前键盘按键的 Windows 悬浮窗工具。

按下 `Ctrl` 然后 `C`，悬浮窗就会显示 **Ctrl + C**，松开后约 1.5 秒自动清空。

---

## 功能特性

- **实时按键显示**：全局监听键盘，组合键按顺序拼接（如 `Ctrl + Alt + Tab`），释放后延迟 1~2 秒自动清空（可在设置中调整）。
- **鼠标穿透（默认）**：悬浮窗永远不拦截鼠标点击、不抢前台软件焦点，录制时完全无感。
- **编辑模式**：按 `Ctrl + Alt + L` 切换——可拖拽调整位置、显示青色虚线边框、**滚轮直接调字号**；再按一次回到穿透模式。
- **系统托盘**：右键菜单包含「设置 / 隐藏显示 / 退出」，双击托盘图标打开设置。
- **设置界面**：字体（名称、字号、加粗、斜体）、字体颜色、背景颜色、背景不透明度、释放后清空延迟、窗口坐标，保存立即生效。
- **配置持久化**：所有设置自动保存到同目录 `config.json`，下次启动自动恢复；拖拽位置也会实时保存。
- **底层实现**：通过 `ctypes` 调用 `user32.dll` 设置 `WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_NOACTIVATE`，穿透与不抢焦点均为 Windows 原生行为，稳定可靠。
- **防卡死设计**：按键监听线程只发 Qt 信号，全部 UI 更新都在主线程完成（无锁、无跨线程控件访问）；另有看门狗自动清理异常残留按键；未知的输入法注入键（如 0xFC）直接忽略不显示。

## 快捷键

| 快捷键 | 作用 |
| --- | --- |
| `Ctrl + Alt + L` | 切换「编辑模式 / 穿透模式」 |
| 编辑模式下拖拽 | 移动悬浮窗位置（自动保存） |
| 编辑模式下滚轮 | 调整字体大小（自动保存） |

## 下载与运行

1. 前往仓库 **Releases** 页面下载 `KeyOverlay.exe`（单文件，免安装）。
2. 双击运行，屏幕左上角出现悬浮窗，右下角出现托盘图标。
3. 右键托盘图标 →「设置」进行个性化配置。

> 若 exe 放在个别被特殊权限策略（如 Everyone 拒绝类 ACL）保护的目录中无法启动，换到其它目录（如桌面）即可。

## 从源码运行

```bash
pip install PyQt5 pynput
python key_overlay.py
```

## 打包为单文件 exe

```bash
pip install pyinstaller
pyinstaller -F -w -n KeyOverlay --icon icon.ico key_overlay.py
# 产物：dist\KeyOverlay.exe
```

## 测试

```bash
python _smoke_test.py    # 单元冒烟测试（配置、穿透、按键跟踪、热键）
python _e2e_test.py      # 端到端测试（真实键盘钩子 + 模拟按键）
```

## 配置文件

同目录下的 `config.json`（首次启动自动生成）：

```json
{
  "x": 80, "y": 80,
  "font_family": "微软雅黑", "font_size": 22,
  "bold": true, "italic": false,
  "font_color": "#FFFFFF",
  "bg_color": "#101820", "bg_alpha": 70,
  "clear_delay": 1.5
}
```

## 排查按键问题

设置环境变量 `KEYOVERLAY_DEBUG=1` 后启动，所有原始按键事件（含 `KeyCode(char=..., vk=...)`）会写入 `key_overlay_debug.log`。

## 已知限制

- 笔记本 **Fn 组合宏键**（如 Fn+F12 打开计算器）是键盘固件行为，程序如实显示固件发出的键值。
- 输入法/软件注入的未知虚拟键码不显示（避免出现「按键252」这类无意义内容）。
- 若需监听以管理员身份运行的目标程序中的按键，本程序也需以管理员身份运行。

## 技术栈

Python 3.8+ · PyQt5（GUI / 托盘 / 设置窗）· pynput（全局键盘钩子）· ctypes（Win32 扩展窗口样式）· PyInstaller（打包）

## 许可证

MIT License，详见 [LICENSE](LICENSE)。

---

*Made with opencode.*
