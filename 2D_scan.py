"""
2D CT 分块重叠扫描工具（配置文件版）
====================================
读取 config.json 获取窗口标题、控件坐标等参数。
GUI 界面的扫描范围、重叠比例等仍由用户输入。
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

# ============================================================
# 1. 配置文件加载
# ============================================================
CONFIG_FILE = "config.json"

def load_config():
    """从 config.json 读取窗口标题和控件坐标，返回字典"""
    if not os.path.exists(CONFIG_FILE):
        messagebox.showerror("配置文件缺失",
                             f"未找到 {CONFIG_FILE}，请将配置文件与本程序放在同一目录。")
        sys.exit(1)
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            config = json.load(f)
        # 简单验证必要字段
        required_keys = ["window", "controls"]
        for key in required_keys:
            if key not in config:
                raise KeyError(f"缺少必需字段: {key}")
        return config
    except Exception as e:
        messagebox.showerror("配置文件错误", f"读取 {CONFIG_FILE} 失败: {e}")
        sys.exit(1)

# 加载配置
config = load_config()

# 窗口相关配置
CT_WINDOW_TITLE = config["window"]["title"]
OFFSET_Y = config["window"].get("offset_y", 30)   # 提供默认值

# 控件坐标（转换为元组，便于后续使用）
controls = config["controls"]
xInputBox      = tuple(controls["x_input"])
yInputBox      = tuple(controls["y_input"])
sizeInputBox   = tuple(controls["size_input"]) if controls.get("size_input") else None
scanButton     = tuple(controls["scan_button"])
saveOpenBtn    = tuple(controls["save_open_button"])
fileNameInput  = tuple(controls["file_name_input"])
saveConfirmBtn = tuple(controls["save_confirm_button"])

# ============================================================
# 2. 全局控制变量（不变）
# ============================================================
stop_flag = False
window_region = None

# ============================================================
# 3. 工具函数（与之前相同，但使用已加载的坐标）
# ============================================================
def get_window_region():
    windows = pyautogui.getWindowsWithTitle(CT_WINDOW_TITLE)
    if not windows:
        raise Exception(f"未找到标题包含 '{CT_WINDOW_TITLE}' 的窗口")
    win = windows[0]
    win.activate()
    time.sleep(0.3)
    return (win.left, win.top, win.width, win.height)

def client_to_screen(client_pos):
    if window_region is None:
        raise Exception("窗口未定位")
    left, top, w, h = window_region
    return (left + client_pos[0], top + client_pos[1] + OFFSET_Y)

def set_text(pos, text):
    pyautogui.click(pos[0], pos[1])
    time.sleep(0.1)
    pyautogui.hotkey('ctrl', 'a')
    pyautogui.press('backspace')
    pyautogui.write(str(text))

# ============================================================
# 4. 扫描主逻辑（与之前完全一致，仅引用的坐标变量已改为配置值）
# ============================================================
def run_scan(params):
    global stop_flag, window_region
    stop_flag = False

    xs = params['Xstart']
    xe = params['Xend']
    ys = params['Ystart']
    ye = params['Yend']
    fov = params['FOV']
    ov = params['Overlap']
    wait_sec = params['ScanSeconds']
    prefix = params['FilePrefix']

    try:
        window_region = get_window_region()
        xi = client_to_screen(xInputBox)
        yi = client_to_screen(yInputBox)
        si = client_to_screen(sizeInputBox) if sizeInputBox else None
        sb = client_to_screen(scanButton)
        so = client_to_screen(saveOpenBtn)
        fi = client_to_screen(fileNameInput)
        sc = client_to_screen(saveConfirmBtn)

        step = fov * (1 - ov)
        Nx = math.floor((xe - xs) / step) + 1
        Ny = math.floor((ye - ys) / step) + 1
        total = Nx * Ny

        if not messagebox.askyesno("确认扫描",
                                   f"将扫描 {Nx} 列 × {Ny} 行 = {total} 个位置。\n"
                                   f"步距 = {step:.2f} mm\n\n是否开始？"):
            return

        count = 0
        for i in range(Nx):
            xc = xs + i * step
            for j in range(Ny):
                if stop_flag:
                    return
                yc = ys + j * step
                count += 1
                root.after(0, update_status, f"扫描 {count}/{total}  中心 X={xc:.1f}, Y={yc:.1f}")

                set_text(xi, xc)
                set_text(yi, yc)
                if si:
                    set_text(si, fov)

                pyautogui.click(sb)

                wait_remaining = wait_sec
                while wait_remaining > 0 and not stop_flag:
                    time.sleep(min(1, wait_remaining))
                    wait_remaining -= 1
                if stop_flag:
                    return

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

        root.after(0, scan_complete, total)

    except Exception as e:
        root.after(0, lambda err=str(e): messagebox.showerror("错误", err))

def update_status(msg):
    status_var.set(msg)

def scan_complete(total):
    messagebox.showinfo("完成", f"全部扫描完成！共 {total} 块数据已保存。")
    status_var.set("就绪")

# ============================================================
# 5. GUI 界面构建（与之前完全相同，仅参数输入部分不变）
# ============================================================
root = tk.Tk()
root.title("CT 分块扫描控制台")
status_var = tk.StringVar(value="就绪")

var_Xstart = tk.DoubleVar(value=10.0)
var_Xend = tk.DoubleVar(value=78.0)
var_Ystart = tk.DoubleVar(value=5.0)
var_Yend = tk.DoubleVar(value=55.0)
var_FOV = tk.DoubleVar(value=20.0)
var_Overlap = tk.DoubleVar(value=0.15)
var_ScanSeconds = tk.DoubleVar(value=480)
var_FilePrefix = tk.StringVar(value="SampleA_")

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

tk.Label(frame, text="扫描时间 (s):").grid(row=3, column=0, sticky="e")
tk.Entry(frame, textvariable=var_ScanSeconds, width=8).grid(row=3, column=1)

frame2 = tk.LabelFrame(root, text="文件保存", padx=10, pady=10)
frame2.pack(padx=10, pady=5, fill="x")
tk.Label(frame2, text="文件名前缀:").pack(side="left")
tk.Entry(frame2, textvariable=var_FilePrefix, width=15).pack(side="left", padx=5)
tk.Label(frame2, text="(自动追加三位编号)").pack(side="left")

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
        'ScanSeconds': var_ScanSeconds.get(),
        'FilePrefix': var_FilePrefix.get()
    }
    if params['Xend'] <= params['Xstart'] or params['Yend'] <= params['Ystart']:
        messagebox.showerror("参数错误", "终点必须大于起点")
        return
    if params['FOV'] <= 0 or params['Overlap'] < 0 or params['Overlap'] >= 1 or params['ScanSeconds'] <= 0:
        messagebox.showerror("参数错误", "视野>0，重叠比例0~1(不含1)，扫描时间>0")
        return
    t = threading.Thread(target=run_scan, args=(params,), daemon=True)
    t.start()

# 热键
def on_press(key):
    global stop_flag
    if key == pynput_keyboard.Key.esc:
        stop_flag = True
        root.after(0, lambda: status_var.set("紧急停止！"))

listener = pynput_keyboard.Listener(on_press=on_press)
listener.start()

root.mainloop()
