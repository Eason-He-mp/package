"""
2D CT 分块重叠扫描工具（X 递减·Y 递增·中心越过终点）
====================================================
功能：
  - 读取 config.json 获取窗口标题、控件屏幕绝对坐标
  - GUI 中输入扫描范围：
      X 起点（大值）→ X 终点（小值），从左到右递减扫描
      Y 起点（小值）→ Y 终点（大值），从下到上递增扫描
  - 步长计算：绝对步长 = 视野 × (1 - 重叠)，X 取负值，Y 取正值
  - 循环生成中心序列，保证最后一个中心：
      X 轴：中心坐标 < X 终点（覆盖左边界）
      Y 轴：中心坐标 > Y 终点（覆盖上边界）
  - 行优先扫描：外层 Y 递增，内层 X 递减；每行开始时设置 Y 并确认移动，内层只设 X
  - 视野大小仅在开始时设置一次
  - Esc 紧急停止，停止后鼠标移至 (1200, 600)
  - 扫描时隐藏主窗口
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
        with open(CONFIG_FILE, "r", encoding="utf-8-sig") as f:
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

CT_WINDOW_TITLE = config["window"]["title"]

controls = config["controls"]
xInputBox        = tuple(controls["x_input"])
yInputBox        = tuple(controls["y_input"])
sizeInputBox     = tuple(controls["size_input"]) if controls.get("size_input") else None
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
    windows = pyautogui.getWindowsWithTitle(CT_WINDOW_TITLE)
    if not windows:
        raise Exception(f"未找到标题包含 '{CT_WINDOW_TITLE}' 的窗口")
    win = windows[0]
    if win.isMinimized:
        win.restore()
    win.activate()
    time.sleep(0.5)

def set_text(pos, text):
    pyautogui.click(pos[0], pos[1])
    time.sleep(0.1)
    pyautogui.write(str(text))

# ============================================================
# 4. 扫描主逻辑（中心越过终点）
# ============================================================
def run_scan(params):
    global stop_flag
    stop_flag = False
    root.withdraw()

    xs = params['Xstart']               # X 起点（较大值）
    xe = params['Xend']                 # X 终点（较小值）
    ys = params['Ystart']               # Y 起点（较小值）
    ye = params['Yend']                 # Y 终点（较大值）
    fov = params['FOV']
    ov = params['Overlap']
    capture_wait = params['CaptureWait']
    move_wait    = params['MoveWait']
    prefix       = params['FilePrefix']

    try:
        activate_target_window()

        xi, yi = xInputBox, yInputBox
        si = sizeInputBox
        lb, cb = liveButton, captureButton
        mc = moveConfirmBtn
        so, fi, sc = saveOpenBtn, fileNameInput, saveConfirmBtn

        # ---------- 生成扫描中心序列 ----------
        step_abs = fov * (1 - ov)       # 绝对步长

        # ---- X 方向：递减（步长取负值）----
        step_x = -step_abs              # 负值，使坐标逐渐减小
        Xcenters = []
        xc = xs
        # 只要中心还未低于终点就继续添加
        while xc >= xe:
            Xcenters.append(xc)
            xc += step_x                # xc 减小
        # 追加越过终点的那个中心（此时 xc < xe）
        Xcenters.append(xc)

        # ---- Y 方向：递增（步长取正值）----
        step_y = step_abs               # 正值，使坐标逐渐增大
        Ycenters = []
        yc = ys
        # 只要中心还未超过终点就继续添加
        while yc <= ye:
            Ycenters.append(yc)
            yc += step_y                # yc 增大
        # 追加越过终点的那个中心（此时 yc > ye）
        Ycenters.append(yc)

        Nx = len(Xcenters)
        Ny = len(Ycenters)
        total = Nx * Ny

        if not messagebox.askyesno("确认扫描",
                                   f"将扫描 {Nx} 列 × {Ny} 行 = {total} 个位置。\n"
                                   f"X 步距 = {step_abs:.2f} mm（递减，末中心 < 终点）\n"
                                   f"Y 步距 = {step_abs:.2f} mm（递增，末中心 > 终点）\n"
                                   f"采集等待 = {capture_wait} s  |  移动等待 = {move_wait} s\n\n是否开始？"):
            root.deiconify()
            return

        # ---------- 一次性设置 ----------
        if si:
            set_text(si, fov)
            time.sleep(0.3)

        pyautogui.click(lb)          # 初始 Live
        time.sleep(0.2)

        # ===== 主扫描循环：行优先（Y 递增，X 递减）=====
        count = 0
        for yc in Ycenters:
            # 设置本行 Y 坐标
            set_text(yi, yc)
            pyautogui.click(mc)
            time.sleep(0.2)

            if move_wait > 0:
                remaining = move_wait
                while remaining > 0 and not stop_flag:
                    time.sleep(min(1, remaining))
                    remaining -= 1
            if stop_flag:
                break

            for xc in Xcenters:
                if stop_flag:
                    break
                count += 1
                root.after(0, update_status, f"第 {count}/{total} 块  X={xc:.1f}, Y={yc:.1f}")

                # Live
                pyautogui.click(lb)
                time.sleep(0.2)

                # 设置 X 坐标
                set_text(xi, xc)

                # 确认移动
                pyautogui.click(mc)
                time.sleep(0.2)

                if move_wait > 0:
                    remaining = move_wait
                    while remaining > 0 and not stop_flag:
                        time.sleep(min(1, remaining))
                        remaining -= 1
                if stop_flag:
                    break

                # Capture
                pyautogui.click(cb)
                remaining = capture_wait
                while remaining > 0 and not stop_flag:
                    time.sleep(min(1, remaining))
                    remaining -= 1
                if stop_flag:
                    break

                # 保存文件
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

                # 再次 Live
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
        root.after(0, root.deiconify)

# ============================================================
# 5. GUI 更新函数
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

var_Xstart = tk.StringVar(value="")
var_Xend   = tk.StringVar(value="")
var_Ystart = tk.StringVar(value="")
var_Yend   = tk.StringVar(value="")
var_FOV    = tk.StringVar(value="")
var_Overlap = tk.DoubleVar(value=0.15)
var_CaptureWait = tk.DoubleVar(value=10)
var_MoveWait    = tk.DoubleVar(value=5)
var_FilePrefix  = tk.StringVar(value="SampleA_")

frame = tk.LabelFrame(root, text="扫描范围与参数", padx=10, pady=10)
frame.pack(padx=10, pady=5, fill="x")

# X 轴：起点（大值），终点（小值）
tk.Label(frame, text="X 轴起点 (mm):").grid(row=0, column=0, sticky="e")
tk.Entry(frame, textvariable=var_Xstart, width=8).grid(row=0, column=1)
tk.Label(frame, text="X 轴终点 (mm):").grid(row=0, column=2, sticky="e", padx=(20,0))
tk.Entry(frame, textvariable=var_Xend, width=8).grid(row=0, column=3)

# Y 轴：起点（小值），终点（大值）
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
    try:
        xs = float(var_Xstart.get())
        xe = float(var_Xend.get())
        ys = float(var_Ystart.get())
        ye = float(var_Yend.get())
        fov = float(var_FOV.get())
    except ValueError:
        messagebox.showerror("输入错误", "X/Y 起点、终点和视野大小必须为有效数字，且不能为空。")
        return

    ov = var_Overlap.get()
    capture_wait = var_CaptureWait.get()
    move_wait = var_MoveWait.get()
    prefix = var_FilePrefix.get().strip()
    if not prefix:
        prefix = "Scan"

    # X 轴检查：起点必须大于终点（从左到右递减）
    if xs <= xe:
        messagebox.showerror("参数错误", "X 轴起点必须大于终点（起点为视野左侧坐标，较大值）。")
        return

    # Y 轴检查：起点必须小于终点（从下到上递增）
    if ys >= ye:
        messagebox.showerror("参数错误", "Y 轴起点必须小于终点（起点为视野下侧坐标，较小值）。")
        return

    if fov <= 0 or ov < 0 or ov >= 1 or capture_wait < 0 or move_wait < 0:
        messagebox.showerror("参数错误", "视野>0，重叠比例0~1(不含1)，等待时间>=0")
        return

    params = {
        'Xstart': xs,
        'Xend': xe,
        'Ystart': ys,
        'Yend': ye,
        'FOV': fov,
        'Overlap': ov,
        'CaptureWait': capture_wait,
        'MoveWait': move_wait,
        'FilePrefix': prefix
    }

    t = threading.Thread(target=run_scan, args=(params,), daemon=True)
    t.start()

# ============================================================
# 7. Esc 热键
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
