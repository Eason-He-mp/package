"""
2D CT 分块重叠扫描工具（屏幕绝对坐标版 + 移动等待参数）
==========================================================
读取 config.json 获取窗口标题、控件屏幕坐标等参数。
扫描流程：Live → 设置 X/Y → 等待 CNC 移动 → Capture → 等待采集 → 保存 → Live
启动时如 config.json 缺失或格式错误会弹窗提示并退出。
"""

import sys
import json
import os
import tkinter as tk
from tkinter import messagebox
import threading
import time
import math
import pyautogui
from pynput import keyboard as pynput_keyboard

# 禁用 fail-safe，使用 Esc 热键停止
pyautogui.FAILSAFE = False

# ============================================================
# 1. 配置文件加载
# ============================================================
CONFIG_FILE = "config.json"

def load_config():
    if not os.path.exists(CONFIG_FILE):
        messagebox.showerror("配置文件缺失",
                             f"未找到 {CONFIG_FILE}，请将配置文件与本程序放在同一目录。")
        sys.exit(1)
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8-sig") as f:   # 兼容 BOM
            config = json.load(f)
        # 检查必要字段
        if "window" not in config or "controls" not in config:
            raise KeyError("缺少 'window' 或 'controls' 字段")
        # 检查 live/capture 按钮
        for key in ["live_button", "capture_button"]:
            if key not in config["controls"] or config["controls"][key] is None:
                raise KeyError(f"配置缺少控件坐标: {key}")
        return config
    except Exception as e:
        messagebox.showerror("配置文件错误", f"读取 {CONFIG_FILE} 失败: {e}")
        sys.exit(1)

config = load_config()

# 窗口配置（仅用于激活窗口）
CT_WINDOW_TITLE = config["window"]["title"]

# 控件坐标（直接使用屏幕绝对坐标，不再转换）
controls = config["controls"]
xInputBox      = tuple(controls["x_input"])
yInputBox      = tuple(controls["y_input"])
sizeInputBox   = tuple(controls["size_input"]) if controls.get("size_input") else None
liveButton     = tuple(controls["live_button"])
captureButton  = tuple(controls["capture_button"])
saveOpenBtn    = tuple(controls["save_open_button"])
fileNameInput  = tuple(controls["file_name_input"])
saveConfirmBtn = tuple(controls["save_confirm_button"])

# ============================================================
# 2. 全局变量
# ============================================================
stop_flag = False

# ============================================================
# 3. 窗口激活函数
# ============================================================
def activate_target_window():
    """激活 CT 软件窗口，确保操作落在上面"""
    windows = pyautogui.getWindowsWithTitle(CT_WINDOW_TITLE)
    if not windows:
        raise Exception(f"未找到标题包含 '{CT_WINDOW_TITLE}' 的窗口")
    windows[0].activate()
    time.sleep(0.3)

# ============================================================
# 4. 输入文本辅助函数
# ============================================================
def set_text(pos, text):
    """点击文本框，清空并输入新内容"""
    pyautogui.click(pos[0], pos[1])
    time.sleep(0.1)
    pyautogui.hotkey('ctrl', 'a')
    pyautogui.press('backspace')
    pyautogui.write(str(text))

# ============================================================
# 5. 扫描主逻辑（增加移动等待）
# ============================================================
def run_scan(params):
    global stop_flag
    stop_flag = False

    xs = params['Xstart']
    xe = params['Xend']
    ys = params['Ystart']
    ye = params['Yend']
    fov = params['FOV']
    ov = params['Overlap']
    capture_wait = params['CaptureWait']     # 采集等待时间 (s)
    move_wait = params['MoveWait']           # CNC 移动等待时间 (s)
    prefix = params['FilePrefix']

    try:
        # 激活窗口
        activate_target_window()

        # 取用屏幕绝对坐标
        xi, yi = xInputBox, yInputBox
        si = sizeInputBox
        lb, cb = liveButton, captureButton
        so, fi, sc = saveOpenBtn, fileNameInput, saveConfirmBtn

        # 计算扫描矩阵
        step = fov * (1 - ov)
        Nx = math.floor((xe - xs) / step) + 1
        Ny = math.floor((ye - ys) / step) + 1
        total = Nx * Ny

        if not messagebox.askyesno("确认扫描",
                                   f"将扫描 {Nx} 列 × {Ny} 行 = {total} 个位置。\n"
                                   f"步距 = {step:.2f} mm\n"
                                   f"采集等待 = {capture_wait} s  |  移动等待 = {move_wait} s\n\n是否开始？"):
            return

        count = 0
        for i in range(Nx):
            xc = xs + i * step
            for j in range(Ny):
                if stop_flag:
                    return
                yc = ys + j * step
                count += 1
                root.after(0, update_status, f"第 {count}/{total} 块  X={xc:.1f}, Y={yc:.1f}")

                # ---------- 新流程 ----------
                # 1. 切换到 Live 模式（如果需要，也可以放在移动之后，按你软件特性）
                pyautogui.click(lb)
                time.sleep(0.2)

                # 2. 设置 X 和 Y 坐标
                set_text(xi, xc)
                set_text(yi, yc)
                if si:
                    set_text(si, fov)

                # 3. 等待 CNC 移动到位（可自定义参数）
                if move_wait > 0:
                    # 分段等待，允许中途停止
                    remaining = move_wait
                    while remaining > 0 and not stop_flag:
                        time.sleep(min(1, remaining))
                        remaining -= 1
                    if stop_flag:
                        return

                # 4. 点击 Capture 抓图
                pyautogui.click(cb)

                # 5. 等待采集完成（用户设定的采集时间）
                remaining = capture_wait
                while remaining > 0 and not stop_flag:
                    time.sleep(min(1, remaining))
                    remaining -= 1
                if stop_flag:
                    return

                # 6. 保存文件
                pyautogui.click(so)
                time.sleep(0.8)
                pyautogui.click(fi)
                time.sleep(0.2)
                pyautogui.hotkey('ctrl', 'a')
                pyautogui.press('backspace')
                fname = f"{prefix}{count:03d}"
                pyautogui.write(fname)
                pyautogui.click(sc)
                time.sleep(1)

                # 7. 再次点击 Live，为下一次移动做准备（或保持实时状态）
                pyautogui.click(lb)
                time.sleep(0.2)

        root.after(0, scan_complete, total)

    except Exception as e:
        root.after(0, lambda err=str(e): messagebox.showerror("错误", err))

def update_status(msg):
    status_var.set(msg)

def scan_complete(total):
    messagebox.showinfo("完成", f"全部扫描完成！共 {total} 块数据已保存。")
    status_var.set("就绪")

# ============================================================
# 6. GUI 界面（增加“移动等待时间”输入框）
# ============================================================
root = tk.Tk()
root.title("CT 分块扫描控制台")
status_var = tk.StringVar(value="就绪")

# 变量绑定
var_Xstart = tk.DoubleVar(value=10.0)
var_Xend   = tk.DoubleVar(value=78.0)
var_Ystart = tk.DoubleVar(value=5.0)
var_Yend   = tk.DoubleVar(value=55.0)
var_FOV    = tk.DoubleVar(value=20.0)
var_Overlap = tk.DoubleVar(value=0.15)
var_CaptureWait = tk.DoubleVar(value=2.0)      # 采集等待
var_MoveWait    = tk.DoubleVar(value=2.0)      # 移动等待
var_FilePrefix  = tk.StringVar(value="SampleA_")

# 参数区域
frame = tk.LabelFrame(root, text="扫描范围与参数", padx=10, pady=10)
frame.pack(padx=10, pady=5, fill="x")

tk.Label(frame, text="X 轴起点 (mm):").grid(row=0, column=0, sticky="e")
tk.Entry(frame, textvariable=var_Xstart, width=8).grid(row=0, column=1)
tk.Label(frame, text="X 轴终点 (mm):").grid(row=0, column=2, sticky="e", padx=(20,0))
tk.Entry(frame, textvariable=var_Xend, width=8).grid(row=0, column=3)

tk.Label(frame, text="Y 轴起点 (mm):").grid(row=1, column=0, sticky="e")
tk.Entry(frame, textvariable=var_Ystart, width=8).grid(row=1, column=1)
tk.Label(frame, text="Y 轴终点 (mm):").grid(row=1, column=2, sticky="e", padx=(20,0))
tk.Entry(frame, textvariable=var_Yend, width=8).grid(row=1, column=3)

tk.Label(frame, text="视野大小 (mm):").grid(row=2, column=0, sticky="e")
tk.Entry(frame, textvariable=var_FOV, width=8).grid(row=2, column=1)
tk.Label(frame, text="重叠比例 (0~1):").grid(row=2, column=2, sticky="e", padx=(20,0))
tk.Entry(frame, textvariable=var_Overlap, width=8).grid(row=2, column=3)

tk.Label(frame, text="采集等待时间 (s):").grid(row=3, column=0, sticky="e")
tk.Entry(frame, textvariable=var_CaptureWait, width=8).grid(row=3, column=1)
tk.Label(frame, text="移动等待时间 (s):").grid(row=3, column=2, sticky="e", padx=(20,0))
tk.Entry(frame, textvariable=var_MoveWait, width=8).grid(row=3, column=3)

# 文件保存
frame2 = tk.LabelFrame(root, text="文件保存", padx=10, pady=10)
frame2.pack(padx=10, pady=5, fill="x")
tk.Label(frame2, text="文件名前缀:").pack(side="left")
tk.Entry(frame2, textvariable=var_FilePrefix, width=15).pack(side="left", padx=5)
tk.Label(frame2, text="(自动追加三位编号)").pack(side="left")

# 按钮
btn_frame = tk.Frame(root)
btn_frame.pack(pady=5)
tk.Button(btn_frame, text="开始扫描", width=12, command=lambda: start_scan_thread()).pack(side="left", padx=5)
tk.Button(btn_frame, text="停止", width=8, command=lambda: set_stop()).pack(side="left", padx=5)
tk.Button(btn_frame, text="退出", width=8, command=root.quit).pack(side="left", padx=5)

tk.Label(root, textvariable=status_var, bd=1, relief="sunken", anchor="w").pack(fill="x", padx=10, pady=5)

def set_stop():
    global stop_flag
    stop_flag = True
    status_var.set("正在停止...")

def start_scan_thread():
    params = {
        'Xstart': var_Xstart.get(),
        'Xend': var_Xend.get(),
        'Ystart': var_Ystart.get(),
        'Yend': var_Yend.get(),
        'FOV': var_FOV.get(),
        'Overlap': var_Overlap.get(),
        'CaptureWait': var_CaptureWait.get(),
        'MoveWait': var_MoveWait.get(),
        'FilePrefix': var_FilePrefix.get()
    }
    # 合法性检查
    if params['Xend'] <= params['Xstart'] or params['Yend'] <= params['Ystart']:
        messagebox.showerror("参数错误", "终点必须大于起点")
        return
    if params['FOV'] <= 0 or params['Overlap'] < 0 or params['Overlap'] >= 1 or params['CaptureWait'] < 0 or params['MoveWait'] < 0:
        messagebox.showerror("参数错误", "视野>0，重叠比例0~1(不含1)，等待时间>=0")
        return
    t = threading.Thread(target=run_scan, args=(params,), daemon=True)
    t.start()

# 全局 Esc 热键
def on_press(key):
    global stop_flag
    if key == pynput_keyboard.Key.esc:
        stop_flag = True
        root.after(0, lambda: status_var.set("紧急停止！"))

listener = pynput_keyboard.Listener(on_press=on_press)
listener.start()

root.mainloop()
