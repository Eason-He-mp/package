"""
鼠标坐标抓取工具（Windows 版）
==============================
功能：在屏幕左上角实时显示鼠标的屏幕绝对坐标和窗口客户区坐标。
     按 F12 复制当前窗口客户区坐标到剪贴板，按 Esc 退出。
用法：python get_coords.py    或者    打包后的 .exe 直接运行
依赖：pyautogui, pynput, tkinter
注意：客户区坐标基于当前鼠标所在窗口，无需安装额外库即可在 Windows 上运行。
"""

import tkinter as tk
import threading
import time
import ctypes
import sys

# 如果要在 Windows 上获取客户区坐标，需要 ctypes 调用系统 API
from ctypes import wintypes

# 使用 pynput 进行全局热键监听（不依赖焦点）
from pynput import keyboard as pynput_keyboard

# 使用 pyautogui 获取鼠标屏幕坐标（和主程序一致，打包时会一并包含）
import pyautogui

# ============================================================
# Windows API 函数声明（用于获取客户区坐标）
# ============================================================
# 获取当前鼠标位置的屏幕坐标
GetCursorPos = ctypes.windll.user32.GetCursorPos
# 获取鼠标所在窗口的句柄（从屏幕坐标得到窗口句柄）
WindowFromPoint = ctypes.windll.user32.WindowFromPoint
# 获取指定窗口的客户区矩形（左上角坐标相对于屏幕）
GetClientRect = ctypes.windll.user32.GetClientRect
# 将屏幕坐标转换为窗口客户区坐标
ScreenToClient = ctypes.windll.user32.ScreenToClient

def get_client_coords():
    """
    获取当前鼠标所在窗口的客户区坐标。
    返回 (client_x, client_y) 整数元组，若无法获取则返回 (-1, -1)。
    """
    try:
        # 获取鼠标屏幕坐标
        pt = wintypes.POINT()
        GetCursorPos(ctypes.byref(pt))
        screen_x, screen_y = pt.x, pt.y

        # 获取该点所在的窗口句柄
        hwnd = WindowFromPoint(pt)
        if hwnd:
            # 转换为客户区坐标（直接修改 pt 结构）
            ScreenToClient(hwnd, ctypes.byref(pt))
            return pt.x, pt.y
    except Exception:
        pass
    return -1, -1

# ============================================================
# 坐标显示窗口（Tkinter）
# ============================================================
class CoordOverlay:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Mouse Coords")
        # 窗口置顶，无标题栏（工具窗口外观）
        self.root.attributes("-topmost", True)
        self.root.overrideredirect(True)  # 去掉边框，防止误移动
        # 窗口背景色和透明度（可选）
        self.root.configure(bg="black")
        self.root.attributes("-alpha", 0.85)

        # 显示标签：屏幕坐标和客户区坐标
        self.screen_label = tk.Label(
            self.root,
            text="Screen: 0, 0",
            fg="cyan",
            bg="black",
            font=("Consolas", 10),
            anchor="w"
        )
        self.screen_label.pack(fill="x", padx=5, pady=(5, 0))

        self.client_label = tk.Label(
            self.root,
            text="Client: -1, -1",
            fg="lime",
            bg="black",
            font=("Consolas", 10, "bold"),
            anchor="w"
        )
        self.client_label.pack(fill="x", padx=5, pady=(0, 5))

        # 将窗口放在屏幕左上角（避免遮挡操作区域）
        self.root.geometry("200x50+10+10")

        # 启动坐标更新循环
        self.update_coords()

    def update_coords(self):
        """每 100ms 更新一次坐标显示"""
        # 获取屏幕坐标
        screen_x, screen_y = pyautogui.position()
        # 获取窗口客户区坐标
        client_x, client_y = get_client_coords()

        # 更新标签文字
        self.screen_label.config(text=f"Screen: {screen_x}, {screen_y}")
        self.client_label.config(text=f"Client: {client_x}, {client_y}")

        # 100ms 后再次调用自身
        self.root.after(100, self.update_coords)

    def run(self):
        self.root.mainloop()

# ============================================================
# 全局热键处理（使用 pynput 监听）
# ============================================================
def on_press(key):
    try:
        # F12 复制当前客户区坐标到剪贴板
        if key == pynput_keyboard.Key.f12:
            client_x, client_y = get_client_coords()
            coords_str = f"{client_x}, {client_y}"
            # 复制到剪贴板
            import pyperclip  # 简便复制库，若不想依赖可改用 subprocess 调用 clip 命令
            pyperclip.copy(coords_str)
            # 在显示窗口上临时提示
            overlay.client_label.config(text=f"Copied: {coords_str}")
            # 1.5 秒后恢复显示
            threading.Timer(1.5, lambda: overlay.client_label.config(
                text=f"Client: {client_x}, {client_y}"
            )).start()
        # Esc 退出程序
        elif key == pynput_keyboard.Key.esc:
            overlay.root.quit()
            return False  # 停止监听器
    except Exception as e:
        print(f"热键处理错误: {e}")

# ============================================================
# 主程序入口
# ============================================================
if __name__ == "__main__":
    # 检查系统平台
    if sys.platform != "win32":
        print("此工具仅支持 Windows 系统（需要调用 Windows API 获取客户区坐标）")
        sys.exit(1)

    # 创建坐标显示窗口
    overlay = CoordOverlay()

    # 启动键盘监听器（后台线程）
    listener = pynput_keyboard.Listener(on_press=on_press)
    listener.start()

    # 运行 Tkinter 主循环
    overlay.run()
