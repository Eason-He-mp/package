"""
鼠标屏幕坐标抓取工具（简化版）
==============================
功能：在屏幕左上角显示一个半透明窗口，实时展示鼠标的屏幕绝对坐标。
      按 F12 复制当前屏幕坐标到剪贴板，并弹窗提示。
      按 Esc 退出程序。
用法：python get_coords.py    或    打包后的 .exe 直接运行
依赖：pyautogui, pynput, pyperclip, tkinter (标准库)
"""

import tkinter as tk
from tkinter import messagebox
import pyautogui
from pynput import keyboard as pynput_keyboard

# ============================================================
# 坐标显示窗口
# ============================================================
class CoordOverlay:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Mouse Coords")
        # 始终置顶，无边框
        self.root.attributes("-topmost", True)
        self.root.overrideredirect(True)
        self.root.configure(bg="black")
        self.root.attributes("-alpha", 0.85)

        # 屏幕坐标标签
        self.screen_label = tk.Label(
            self.root,
            text="Screen: 0, 0",
            fg="cyan",
            bg="black",
            font=("Consolas", 11, "bold"),
            anchor="w"
        )
        self.screen_label.pack(fill="x", padx=8, pady=(6, 2))

        # 操作提示标签（红色加粗）
        hint_text = "F12: 复制屏幕坐标 | Esc: 退出"
        self.hint_label = tk.Label(
            self.root,
            text=hint_text,
            fg="red",                     # 改为红色
            bg="black",
            font=("Consolas", 8, "bold"), # 改为加粗
            anchor="w"
        )
        self.hint_label.pack(fill="x", padx=8, pady=(2, 6))

        # 设置窗口大小和位置（左上角）
        self.root.geometry("250x60+10+10")

        # 启动定时刷新坐标
        self.update_coords()

    def update_coords(self):
        """每 100ms 刷新屏幕坐标显示"""
        x, y = pyautogui.position()
        self.screen_label.config(text=f"Screen: {x}, {y}")
        self.root.after(100, self.update_coords)

    def run(self):
        self.root.mainloop()

# ============================================================
# 全局热键处理
# ============================================================
def on_press(key):
    try:
        if key == pynput_keyboard.Key.f12:
            # 获取当前屏幕坐标
            x, y = pyautogui.position()
            coords_str = f"{x}, {y}"
            # 复制到剪贴板
            try:
                import pyperclip
                pyperclip.copy(coords_str)
            except ImportError:
                import subprocess
                subprocess.run("clip", input=coords_str.encode(), check=True)
            # 弹窗提示
            messagebox.showinfo(
                "坐标已复制",
                f"屏幕坐标已复制到剪贴板：\n{coords_str}"
            )
        elif key == pynput_keyboard.Key.esc:
            overlay.root.quit()
            return False   # 停止监听器
    except Exception as e:
        messagebox.showerror("错误", f"热键处理出错：{e}")

# ============================================================
# 主程序入口
# ============================================================
if __name__ == "__main__":
    overlay = CoordOverlay()

    listener = pynput_keyboard.Listener(on_press=on_press)
    listener.start()

    overlay.run()
