"""
2D CT 分块重叠扫描工具（最终覆盖完整版）
========================================
功能：
  - 读取 config.json 获取窗口标题、控件屏幕绝对坐标
  - GUI 输入样品范围（物理坐标）、视野、重叠比例、采集/移动等待时间、文件名前缀
  - 自动计算所有扫描位置，保证完全覆盖样品区域（边缘不留空白）
  - 行优先扫描：先固定 Y，遍历 X；每个扫描块独立设置 X、Y 坐标并确认移动
  - 视野大小仅在开始时设置一次
  - 流程：Live → 设置 X/Y → 确认移动 → 等待移动 → Capture → 等待采集 → 保存 → Live
  - Esc 热键紧急停止，停止后鼠标移至 (1200, 600)
  - 扫描时隐藏主窗口，防止焦点错乱
依赖：pyautogui, pynput, tkinter
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

pyautogui.FAILSAFE = False          # 关闭鼠标触角停止，改用 Esc 控制

# ============================================================
# 1. 配置文件加载
# ============================================================
CONFIG_FILE = "config.json"

def load_config():
    """从 config.json 读取窗口标题和所有控件的屏幕绝对坐标"""
    if not os.path.exists(CONFIG_FILE):
        messagebox.showerror("配置文件缺失",
                             f"未找到 {CONFIG_FILE}，请将配置文件与本程序放在同一目录。")
        sys.exit(1)
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8-sig") as f:   # 兼容 UTF-8 BOM
            config = json.load(f)
        if "window" not in config or "controls" not in config:
            raise KeyError("缺少 'window' 或 'controls' 字段")
        required_buttons = ["live_button", "capture_button", "move_confirm_button"]
        for btn in required_buttons:
            if btn not in config["controls"] or config["controls"][btn] is None:
                raise KeyError(f"配置缺少控件坐标: {btn}")
        return config
    except Exception as e:
        messagebox.showerror("配置文件错误", f"读取 {CONFIG_FILE} 失败: {e}")
        sys.exit(1)

config = load_config()

# 窗口标题
CT_WINDOW_TITLE = config["window"]["title"]

# 控件屏幕绝对坐标
controls = config["controls"]
xInputBox        = tuple(controls["x_input"])
yInputBox        = tuple(controls["y_input"])
sizeInputBox     = tuple(controls["size_input"]) if controls.get("size_input") else None   # 可为 null
liveButton       = tuple(controls["live_button"])
captureButton    = tuple(controls["capture_button"])
moveConfirmBtn   = tuple(controls["move_confirm_button"])
saveOpenBtn      = tuple(controls["save_open_button"])
fileNameInput    = tuple(controls["file_name_input"])
saveConfirmBtn   = tuple(controls["save_confirm_button"])

# ============================================================
# 2. 全局变量
# ============================================================
stop_flag = False

# ============================================================
# 3. 工具函数
# ============================================================
def activate_target_window():
    """激活 CT 软件窗口"""
    windows = pyautogui.getWindowsWithTitle(CT_WINDOW_TITLE)
    if not windows:
        raise Exception(f"未找到标题包含 '{CT_WINDOW_TITLE}' 的窗口")
    win = windows[0]
    if win.isMinimized:
        win.restore()
    win.activate()
    time.sleep(0.5)

def set_text(pos, text):
    """点击输入框（软件自动全选），然后直接写入新值"""
    pyautogui.click(pos[0], pos[1])
    time.sleep(0.1)            # 等待自动全选生效
    pyautogui.write(str(text))

# ============================================================
# 4. 扫描主逻辑（行优先，每个块都设 X/Y，保证完全覆盖）
# ============================================================
def run_scan(params):
    global stop_flag
    stop_flag = False
    root.withdraw()                     # 隐藏主窗口

    xs = params['Xstart']               # 样品 X 起点（物理坐标）
    xe = params['Xend']                 # 样品 X 终点
    ys = params['Ystart']
    ye = params['Yend']
    fov = params['FOV']
    ov = params['Overlap']
    capture_wait = params['CaptureWait']
    move_wait    = params['MoveWait']
    prefix       = params['FilePrefix']

    try:
        activate_target_window()

        # 按钮坐标（屏幕绝对坐标）
        xi, yi = xInputBox, yInputBox
        si = sizeInputBox
        lb, cb = liveButton, captureButton
        mc = moveConfirmBtn
        so, fi, sc = saveOpenBtn, fileNameInput, saveConfirmBtn

        # ---------- 计算扫描矩阵（保证边界完全覆盖）----------
        step = fov * (1 - ov)            # 步距

        # X 方向
        if (xe - xs) <= fov:
            Nx = 1
            Xcenters = [(xs + xe) / 2.0]
        else:
            Nx = math.ceil((xe - xs - fov) / step) + 1
            Xcenters = [xs + fov/2.0 + i * step for i in range(Nx)]

        # Y 方向
        if (ye - ys) <= fov:
            Ny = 1
            Ycenters = [(ys + ye) / 2.0]
        else:
            Ny = math.ceil((ye - ys - fov) / step) + 1
            Ycenters = [ys + fov/2.0 + j * step for j in range(Ny)]

        total = Nx * Ny

        if not messagebox.askyesno("确认扫描",
                                   f"将扫描 {Nx} 列 × {Ny} 行 = {total} 个位置。\n"
                                   f"步距 = {step:.2f} mm\n"
                                   f"采集等待 = {capture_wait} s  |  移动等待 = {move_wait} s\n\n是否开始？"):
            root.deiconify()
            return

        # ---------- 一次性设置 ----------
        # 设置视野大小（仅一次）
        if si:
            set_text(si, fov)
            time.sleep(0.3)

        # 初始 Live 激活
        pyautogui.click(lb)
        time.sleep(0.2)

        # ===== 主扫描循环：行优先（Y 外层，X 内层）=====
        count = 0
        for yc in Ycenters:
            for xc in Xcenters:
                if stop_flag:
                    break
                count += 1
                root.after(0, update_status, f"第 {count}/{total} 块  X={xc:.1f}, Y={yc:.1f}")

                # --- 扫描一个块 ---
                # 1. Live
                pyautogui.click(lb)
                time.sleep(0.2)

                # 2. 设置 X 坐标
                set_text(xi, xc)
                # 3. 设置 Y 坐标
                set_text(yi, yc)

                # 4. 确认移动（CNC 移动到 xc, yc）
                pyautogui.click(mc)
                time.sleep(0.2)

                # 5. 等待移动到位
                if move_wait > 0:
                    remaining = move_wait
                    while remaining > 0 and not stop_flag:
                        time.sleep(min(1, remaining))
                        remaining -= 1
                if stop_flag:
                    break

                # 6. Capture 抓图
                pyautogui.click(cb)
                # 7. 等待采集
                remaining = capture_wait
                while remaining > 0 and not stop_flag:
                    time.sleep(min(1, remaining))
                    remaining -= 1
                if stop_flag:
                    break

                # 8. 保存文件
                pyautogui.click(so)          # 打开保存对话框
                time.sleep(0.8)
                pyautogui.click(fi)          # 文件名输入框
                time.sleep(0.2)
                pyautogui.hotkey('ctrl', 'a')
                pyautogui.press('backspace')
                fname = f"{prefix}{count:03d}"
                pyautogui.write(fname)
                pyautogui.click(sc)          # 保存按钮
                time.sleep(1)

                # 9. 再次 Live，准备下一块
                pyautogui.click(lb)
                time.sleep(0.2)

            if stop_flag:
                break

        if stop_flag:
            root.after(0, lambda: status_var.set("已停止"))
        else:
            root.after(0, scan_complete, total)

    except Exception as e:
        root.after(0, lambda err=str(e): messagebox.showerror("错误", err))
    finally:
        root.after(0, root.deiconify)   # 恢复主窗口

# ============================================================
# 5. GUI 更新
# ============================================================
def update_status(msg):
    status_var.set(msg)

def scan_complete(total):
    messagebox.showinfo("完成", f"全部扫描完成！共 {total} 块数据已保存。")
    status_var.set("就绪")

# ============================================================
# 6. 构建界面
# ============================================================
root = tk.Tk()
root.title("CT 分块扫描控制台")
status_var = tk.StringVar(value="就绪")

# 界面变量
var_Xstart = tk.DoubleVar(value=0.0)
var_Xend   = tk.DoubleVar(value=100.0)
var_Ystart = tk.DoubleVar(value=0.0)
var_Yend   = tk.DoubleVar(value=80.0)
var_FOV    = tk.DoubleVar(value=20.0)
var_Overlap = tk.DoubleVar(value=0.15)
var_CaptureWait = tk.DoubleVar(value=2.0)
var_MoveWait    = tk.DoubleVar(value=2.0)
var_FilePrefix  = tk.StringVar(value="SampleA_")

frame = tk.LabelFrame(root, text="扫描范围与参数", padx=10, pady=10)
frame.pack(padx=10, pady=5, fill="x")

# 第一行：X 起点/终点
tk.Label(frame, text="X 轴起点 (mm):").grid(row=0, column=0, sticky="e")
tk.Entry(frame, textvariable=var_Xstart, width=8).grid(row=0, column=1)
tk.Label(frame, text="X 轴终点 (mm):").grid(row=0, column=2, sticky="e", padx=(20,0))
tk.Entry(frame, textvariable=var_Xend, width=8).grid(row=0, column=3)

# 第二行：Y 起点/终点
tk.Label(frame, text="Y 轴起点 (mm):").grid(row=1, column=0, sticky="e")
tk.Entry(frame, textvariable=var_Ystart, width=8).grid(row=1, column=1)
tk.Label(frame, text="Y 轴终点 (mm):").grid(row=1, column=2, sticky="e", padx=(20,0))
tk.Entry(frame, textvariable=var_Yend, width=8).grid(row=1, column=3)

# 第三行：视野大小 + 重叠比例
tk.Label(frame, text="视野大小 (mm):").grid(row=2, column=0, sticky="e")
tk.Entry(frame, textvariable=var_FOV, width=8).grid(row=2, column=1)
tk.Label(frame, text="重叠比例 (0~1):").grid(row=2, column=2, sticky="e", padx=(20,0))
tk.Entry(frame, textvariable=var_Overlap, width=8).grid(row=2, column=3)

# 第四行：等待时间
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

# 回调函数
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

# ============================================================
# 7. Esc 热键：停止 + 鼠标回中
# ============================================================
def on_press(key):
    global stop_flag
    if key == pynput_keyboard.Key.esc:
        stop_flag = True
        try:
            pyautogui.moveTo(1200, 600)
        except Exception:
            pass
        root.after(0, lambda: status_var.set("紧急停止！鼠标已移到 (1200,600)"))
        root.after(0, root.deiconify)

listener = pynput_keyboard.Listener(on_press=on_press)
listener.start()

root.mainloop()
