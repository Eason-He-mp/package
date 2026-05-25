"""
2D CT 分块重叠扫描工具（快捷键 + 自动采集等待版）
===================================================
功能：
  - 读取 config.json 获取窗口标题、控件屏幕绝对坐标
  - GUI 中输入扫描范围、视野、重叠、移动等待时间、文件名前缀
  - 新增：曝光时间与平均张数自动设置，采集等待时间 = 曝光时间(s) * 平均张数 + 1s（只读）
  - Live（F2）和 Capture（F3）改用键盘快捷键
  - 行优先扫描：外层 Y 递增，内层 X 递减；每行开始时设置 Y 并确认移动
  - 视野大小、曝光时间、平均张数仅在开始时设置一次
  - Esc 紧急停止，停止后鼠标移至 (1200, 600)
  - 扫描时隐藏主窗口
依赖：pyautogui, pynput, tkinter
"""

import sys
import json
import os
import tkinter as tk
from tkinter import messagebox, ttk
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
        required_buttons = ["exposure_menu_button", "integration_menu_button",
                            "move_confirm_button"]
        for btn in required_buttons:
            if btn not in config["controls"] or config["controls"][btn] is None:
                raise KeyError(f"配置缺少控件坐标: {btn}")
        integ_keys = ["integration_dropdown_left", "integration_dropdown_right",
                      "integration_dropdown_top", "integration_dropdown_bottom"]
        for k in integ_keys:
            if k not in config["controls"] or config["controls"][k] is None:
                raise KeyError(f"配置缺少平均张数下拉区域坐标: {k}")
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
moveConfirmBtn   = tuple(controls["move_confirm_button"])
saveOpenBtn      = tuple(controls["save_open_button"])
fileNameInput    = tuple(controls["file_name_input"])
saveConfirmBtn   = tuple(controls["save_confirm_button"])

exposureMenuBtn  = tuple(controls["exposure_menu_button"])
integrationMenuBtn = tuple(controls["integration_menu_button"])
integLeft   = controls["integration_dropdown_left"]
integRight  = controls["integration_dropdown_right"]
integTop    = controls["integration_dropdown_top"]
integBottom = controls["integration_dropdown_bottom"]

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

def set_exposure(value_index):
    """设置曝光时间：点击菜单按钮，然后按数字键 (0~7)"""
    pyautogui.click(exposureMenuBtn[0], exposureMenuBtn[1])
    time.sleep(0.3)
    pyautogui.press(str(value_index))
    time.sleep(0.2)

def set_integration(value_index):
    """设置平均张数：点击菜单按钮，然后点击下拉菜单中对应索引的矩形中心"""
    pyautogui.click(integrationMenuBtn[0], integrationMenuBtn[1])
    time.sleep(0.3)

    total_height = integBottom - integTop
    item_height = total_height / 13.0
    center_x = (integLeft + integRight) // 2
    center_y = int(integTop + (value_index + 0.5) * item_height)

    pyautogui.click(center_x, center_y)
    time.sleep(0.2)

# ============================================================
# 4. 自动计算采集等待时间
# ============================================================
# 曝光时间字符串 -> 秒数的映射
EXPOSURE_TO_SEC = {
    "33ms": 0.033,
    "67ms": 0.067,
    "100ms": 0.1,
    "200ms": 0.2,
    "333ms": 0.333,
    "1000ms": 1.0,
    "2000ms": 2.0,
    "5000ms": 5.0,
}

def calc_capture_wait(exposure_str, integ_str):
    """返回 曝光秒数 * 平均张数 + 1 秒"""
    exp_sec = EXPOSURE_TO_SEC.get(exposure_str, 0.033)
    integ_num = int(integ_str)
    return round(exp_sec * integ_num + 1.0, 2)

def update_capture_wait_display(*args):
    """当曝光时间或平均张数改变时，更新采集等待时间标签"""
    exp_str = var_Exposure.get()
    integ_str = var_Integration.get()
    wait = calc_capture_wait(exp_str, integ_str)
    var_CaptureWait.set(f"{wait:.2f} 秒")

# ============================================================
# 5. 扫描主逻辑
# ============================================================
def run_scan(params):
    global stop_flag
    stop_flag = False
    root.withdraw()

    xs = params['Xstart']
    xe = params['Xend']
    ys = params['Ystart']
    ye = params['Yend']
    fov = params['FOV']
    ov = params['Overlap']
    capture_wait = params['CaptureWait']   # 自动计算好的值
    move_wait    = params['MoveWait']
    prefix       = params['FilePrefix']
    exposure_idx = params['Exposure']
    integ_idx    = params['Integration']

    try:
        activate_target_window()

        xi, yi = xInputBox, yInputBox
        si = sizeInputBox
        mc = moveConfirmBtn
        so, fi, sc = saveOpenBtn, fileNameInput, saveConfirmBtn

        # ---------- 生成扫描中心序列 ----------
        step_abs = fov * (1 - ov)

        step_x = -step_abs
        Xcenters = []
        xc = xs
        while xc >= xe:
            Xcenters.append(xc)
            xc += step_x
        Xcenters.append(xc)

        step_y = step_abs
        Ycenters = []
        yc = ys
        while yc <= ye:
            Ycenters.append(yc)
            yc += step_y
        Ycenters.append(yc)

        Nx = len(Xcenters)
        Ny = len(Ycenters)
        total = Nx * Ny

        if not messagebox.askyesno("确认扫描",
                                   f"将扫描 {Nx} 列 × {Ny} 行 = {total} 个位置。\n"
                                   f"X 步距 = {step_abs:.2f} mm（递减）\n"
                                   f"Y 步距 = {step_abs:.2f} mm（递增）\n"
                                   f"采集等待 = {capture_wait:.2f} 秒  |  移动等待 = {move_wait} 秒\n\n是否开始？"):
            root.deiconify()
            return

        # ---------- 一次性设置 ----------
        if si:
            set_text(si, fov)
            time.sleep(0.3)

        set_exposure(exposure_idx)
        set_integration(integ_idx)

        activate_target_window()
        time.sleep(0.2)

        # ===== 主扫描循环 =====
        count = 0
        for yc in Ycenters:
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

                pyautogui.press('f2')
                time.sleep(0.2)

                set_text(xi, xc)
                pyautogui.click(mc)
                time.sleep(0.2)

                if move_wait > 0:
                    remaining = move_wait
                    while remaining > 0 and not stop_flag:
                        time.sleep(min(1, remaining))
                        remaining -= 1
                if stop_flag:
                    break

                pyautogui.press('f3')
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

                pyautogui.press('f2')
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
# 6. GUI 更新
# ============================================================
def update_status(msg):
    status_var.set(msg)

def scan_complete(total):
    messagebox.showinfo("完成", f"全部扫描完成！共 {total} 块数据已保存。")
    status_var.set("就绪")

# ============================================================
# 7. 构建界面（采集等待时间改为只读计算值）
# ============================================================
root = tk.Tk()
root.title("CT 分块扫描控制台")
status_var = tk.StringVar(value="就绪")

var_Xstart = tk.StringVar(value="")
var_Xend   = tk.StringVar(value="")
var_Ystart = tk.StringVar(value="")
var_Yend   = tk.StringVar(value="")
var_FOV    = tk.StringVar(value="64")
var_Overlap = tk.DoubleVar(value=0.15)
var_MoveWait    = tk.DoubleVar(value=5)
var_FilePrefix  = tk.StringVar(value="SampleA_")

# 曝光时间与平均张数变量
exposure_options = ["33ms", "67ms", "100ms", "200ms", "333ms", "1000ms", "2000ms", "5000ms"]
integ_options    = ["1", "2", "4", "8", "16", "32", "64", "128", "256", "512", "1024", "2048", "4096"]
var_Exposure     = tk.StringVar(value=exposure_options[3])   # 默认 200ms
var_Integration  = tk.StringVar(value=integ_options[4])      # 默认 16

# 自动计算的采集等待时间（只读显示）
var_CaptureWait = tk.StringVar(value="")
# 初始显示
update_capture_wait_display()

# 当曝光时间或平均张数变更时，自动更新采集等待时间
var_Exposure.trace_add('write', update_capture_wait_display)
var_Integration.trace_add('write', update_capture_wait_display)

frame = tk.LabelFrame(root, text="扫描范围与参数", padx=10, pady=10)
frame.pack(padx=10, pady=5, fill="x")

# X 轴
tk.Label(frame, text="X 轴起点 (mm):").grid(row=0, column=0, sticky="e")
tk.Entry(frame, textvariable=var_Xstart, width=8).grid(row=0, column=1)
tk.Label(frame, text="X 轴终点 (mm):").grid(row=0, column=2, sticky="e", padx=(20,0))
tk.Entry(frame, textvariable=var_Xend, width=8).grid(row=0, column=3)

# Y 轴
tk.Label(frame, text="Y 轴起点 (mm):").grid(row=1, column=0, sticky="e")
tk.Entry(frame, textvariable=var_Ystart, width=8).grid(row=1, column=1)
tk.Label(frame, text="Y 轴终点 (mm):").grid(row=1, column=2, sticky="e", padx=(20,0))
tk.Entry(frame, textvariable=var_Yend, width=8).grid(row=1, column=3)

# 视野与重叠
tk.Label(frame, text="视野大小 (mm):").grid(row=2, column=0, sticky="e")
tk.Entry(frame, textvariable=var_FOV, width=8).grid(row=2, column=1)
tk.Label(frame, text="重叠比例 (0~1):").grid(row=2, column=2, sticky="e", padx=(20,0))
tk.Entry(frame, textvariable=var_Overlap, width=8).grid(row=2, column=3)

# 移动等待时间
tk.Label(frame, text="移动等待时间 (s):").grid(row=3, column=0, sticky="e")
tk.Entry(frame, textvariable=var_MoveWait, width=8).grid(row=3, column=1)

# 采集参数区域
frame_exp = tk.LabelFrame(root, text="采集参数", padx=10, pady=10)
frame_exp.pack(padx=10, pady=5, fill="x")

tk.Label(frame_exp, text="曝光时间:").grid(row=0, column=0, sticky="e")
exp_combo = ttk.Combobox(frame_exp, textvariable=var_Exposure, values=exposure_options,
                         state="readonly", width=10)
exp_combo.grid(row=0, column=1, padx=5)

tk.Label(frame_exp, text="平均张数:").grid(row=0, column=2, sticky="e", padx=(20,0))
integ_combo = ttk.Combobox(frame_exp, textvariable=var_Integration, values=integ_options,
                           state="readonly", width=10)
integ_combo.grid(row=0, column=3, padx=5)

# 自动计算的采集等待时间（只读）
tk.Label(frame_exp, text="采集等待时间:").grid(row=1, column=0, sticky="e", pady=(10,0))
tk.Label(frame_exp, textvariable=var_CaptureWait, relief="sunken", width=15,
         anchor="w").grid(row=1, column=1, padx=5, pady=(10,0), sticky="w")

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
    move_wait = var_MoveWait.get()
    prefix = var_FilePrefix.get().strip()
    if not prefix:
        prefix = "Scan"

    if xs <= xe:
        messagebox.showerror("参数错误", "X 轴起点必须大于终点（起点为视野左侧坐标，较大值）。")
        return
    if ys >= ye:
        messagebox.showerror("参数错误", "Y 轴起点必须小于终点（起点为视野下侧坐标，较小值）。")
        return
    if fov <= 0 or ov < 0 or ov >= 1 or move_wait < 0:
        messagebox.showerror("参数错误", "视野>0，重叠比例0~1(不含1)，移动等待时间>=0")
        return

    # 获取曝光与积分索引，并计算采集等待时间（保证一致性）
    exp_str = var_Exposure.get()
    integ_str = var_Integration.get()
    exp_idx = exposure_options.index(exp_str)
    integ_idx = integ_options.index(integ_str)
    capture_wait = calc_capture_wait(exp_str, integ_str)

    params = {
        'Xstart': xs,
        'Xend': xe,
        'Ystart': ys,
        'Yend': ye,
        'FOV': fov,
        'Overlap': ov,
        'CaptureWait': capture_wait,
        'MoveWait': move_wait,
        'FilePrefix': prefix,
        'Exposure': exp_idx,
        'Integration': integ_idx
    }

    t = threading.Thread(target=run_scan, args=(params,), daemon=True)
    t.start()

# ============================================================
# 8. Esc 热键
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
