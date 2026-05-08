"""
鼠标坐标抓取工具（Windows GUI 版）
================================
功能：在屏幕左上角显示一个半透明窗口，实时展示鼠标的屏幕绝对坐标和窗口客户区坐标。
      按 F12 复制当前客户区坐标到剪贴板，并弹窗提示。
      按 Esc 退出程序。
用法：python get_coords.py    或    打包后的 .exe 直接运行
依赖：pyautogui, pynput, pyperclip, tkinter (标准库)
注意：客户区坐标基于当前鼠标所在窗口，仅在 Windows 上可用（使用了 Win32 API）。
"""

import sys
import tkinter as tk
from tkinter import messagebox          # 用于弹窗提示
import threading
import pyautogui
from pynput import keyboard as pynput_keyboard

# Windows 平台专属的 API 调用
if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

# ============================================================
# 获取客户区坐标的底层函数
# ============================================================
def get_client_coords():
    """
    返回当前鼠标所在窗口的客户区坐标。
    仅在 Windows 下有效，非 Windows 返回 (-1, -1)。
    """
    if sys.platform != "win32":
        return -1, -1
    try:
        # 获取鼠标屏幕坐标
        pt = wintypes.POINT()
        ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
        # 获取鼠标点所在的窗口句柄
        hwnd = ctypes.windll.user32.WindowFromPoint(pt)
        if hwnd:
            # 将屏幕坐标转换为客户区坐标
            ctypes.windll.user32.ScreenToClient(hwnd, ctypes.byref(pt))
            return pt.x, pt.y
    except Exception:
        pass
    return -1, -1

# ============================================================
# 主界面：半透明置顶窗口
# ============================================================
class CoordOverlay:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Mouse Coords")
        # 窗口始终置顶
        self.root.attributes("-topmost", True)
        # 去掉标题栏和边框，做成小工具样式
        self.root.overrideredirect(True)
        self.root.configure(bg="black")
        # 半透明（0.85 透明度）
        self.root.attributes("-alpha", 0.85)

        # 系统平台提示（非 Windows 无法获取客户区坐标）
        if sys.platform != "win32":
            self.client_label_text = "Client: N/A (Windows only)"
        else:
            self.client_label_text = "Client: -1, -1"

        # 屏幕坐标显示
        self.screen_label = tk.Label(
            self.root,
            text="Screen: 0, 0",
            fg="cyan",
            bg="black",
            font=("Consolas", 10),
            anchor="w"
        )
        self.screen_label.pack(fill="x", padx=5, pady=(5, 0))

        # 客户区坐标显示
        self.client_label = tk.Label(
            self.root,
            text=self.client_label_text,
            fg="lime",
            bg="black",
            font=("Consolas", 10, "bold"),
            anchor="w"
        )
        self.client_label.pack(fill="x", padx=5, pady=(0, 0))

        # 操作提示行
        hint_text = "F12: 复制客户区坐标 | Esc: 退出"
        self.hint_label = tk.Label(
            self.root,
            text=hint_text,
            fg="gray",
            bg="black",
            font=("Consolas", 8),
            anchor="w"
        )
        self.hint_label.pack(fill="x", padx=5, pady=(2, 5))

        # 设置窗口初始大小，放在屏幕左上角
        self.root.geometry("280x70+10+10")

        # 开始循环更新坐标显示
        self.update_coords()

    def update_coords(self):
        """每 100ms 刷新一次坐标显示"""
        screen_x, screen_y = pyautogui.position()
        client_x, client_y = get_client_coords()
        self.screen_label.config(text=f"Screen: {screen_x}, {screen_y}")
        if client_x != -1:
            self.client_label.config(text=f"Client: {client_x}, {client_y}")
        else:
            self.client_label.config(text=self.client_label_text)
        self.root.after(100, self.update_coords)

    def run(self):
        self.root.mainloop()

# ============================================================
# 全局热键处理
# ============================================================
def on_press(key):
    try:
        if key == pynput_keyboard.Key.f12:
            # 获取当前客户区坐标
            client_x, client_y = get_client_coords()
            if client_x == -1 and sys.platform == "win32":
                messagebox.showwarning("警告", "无法获取客户区坐标，请确认鼠标在窗口内。")
                return
            coords_str = f"{client_x}, {client_y}"
            # 复制到剪贴板
            try:
                import pyperclip
                pyperclip.copy(coords_str)
            except ImportError:
                # 如果没有 pyperclip，回退到 Windows 的 clip 命令
                import subprocess
                subprocess.run("clip", input=coords_str.encode(), check=True)
            # 弹窗提示
            messagebox.showinfo(
                "坐标已复制",
                f"客户区坐标已复制到剪贴板：\n{coords_str}"
            )
        elif key == pynput_keyboard.Key.esc:
            # 退出程序
            overlay.root.quit()
            return False   # 停止监听器
    except Exception as e:
        messagebox.showerror("错误", f"热键处理出错：{e}")

# ============================================================
# 主程序入口
# ============================================================
if __name__ == "__main__":
    # 仅在 Windows 上运行客户区坐标抓取，但界面仍可启动（其他系统显示 N/A）
    overlay = CoordOverlay()

    # 启动键盘监听（后台线程）
    listener = pynput_keyboard.Listener(on_press=on_press)
    listener.start()

    # 进入 GUI 主循环
    overlay.run()
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
