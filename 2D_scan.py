"""
2D CT 分块重叠扫描工具（最终整合版）
====================================
功能：
  - 读取 config.json 获取窗口标题、控件屏幕绝对坐标。
  - GUI 输入扫描范围、视野、重叠比例、采集等待、移动等待、文件名前缀。
  - 行优先扫描：先固定 Y，遍历 X；每行结束后切换 Y。
  - 视野大小仅设置一次（循环外）。
  - 设置坐标后点击“确认移动”按钮，等待 CNC 移动到位。
  - 流程：Live → 设置坐标 → 确认移动 → 等待移动 → Capture → 等待采集 → 保存 → Live
  - Esc 热键紧急停止，停止后鼠标移动到 (1200, 600)。
  - 扫描时隐藏主窗口，防止焦点错乱。
依赖：pyautogui, pynput, pyperclip, tkinter（标准库）
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

# 禁用 PyAutoGUI 的故障安全机制（原为鼠标移到角落触发停止）
# 因为使用 Esc 热键手动停止，故关闭此功能避免误触发
pyautogui.FAILSAFE = False

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
        # 使用 utf-8-sig 编码自动跳过可能存在的 BOM 头
        with open(CONFIG_FILE, "r", encoding="utf-8-sig") as f:
            config = json.load(f)
        # 验证必要字段
        if "window" not in config or "controls" not in config:
            raise KeyError("缺少 'window' 或 'controls' 字段")
        # 检查必须的按钮坐标
        required_buttons = ["live_button", "capture_button", "move_confirm_button"]
        for btn in required_buttons:
            if btn not in config["controls"] or config["controls"][btn] is None:
                raise KeyError(f"配置缺少控件坐标: {btn}")
        return config
    except Exception as e:
        messagebox.showerror("配置文件错误", f"读取 {CONFIG_FILE} 失败: {e}")
        sys.exit(1)

# 加载配置
config = load_config()

# 窗口标题（用于查找并激活 CT 软件窗口）
CT_WINDOW_TITLE = config["window"]["title"]

# 提取所有控件的屏幕绝对坐标（由 GetCoords 抓取，直接用于点击）
controls = config["controls"]
xInputBox        = tuple(controls["x_input"])
yInputBox        = tuple(controls["y_input"])
sizeInputBox     = tuple(controls["size_input"]) if controls.get("size_input") else None   # 可能为 null
liveButton       = tuple(controls["live_button"])
captureButton    = tuple(controls["capture_button"])
moveConfirmBtn   = tuple(controls["move_confirm_button"])   # 移动确认按钮
saveOpenBtn      = tuple(controls["save_open_button"])
fileNameInput    = tuple(controls["file_name_input"])
saveConfirmBtn   = tuple(controls["save_confirm_button"])

# ============================================================
# 2. 全局控制变量
# ============================================================
stop_flag = False          # 是否触发紧急停止

# ============================================================
# 3. 窗口激活与输入辅助函数
# ============================================================
def activate_target_window():
    """激活 CT 软件窗口，确保后续操作落在正确的窗口上"""
    windows = pyautogui.getWindowsWithTitle(CT_WINDOW_TITLE)
    if not windows:
        raise Exception(f"未找到标题包含 '{CT_WINDOW_TITLE}' 的窗口")
    win = windows[0]
    if win.isMinimized:
        win.restore()          # 如果最小化则先恢复
    win.activate()
    time.sleep(0.5)            # 等待窗口完全获取焦点

def set_text(pos, text):
    """
    点击指定屏幕坐标的输入框，并输入文本。
    假设：软件在点击输入框后会自动全选原有内容，因此直接写入即可覆盖。
    """
    pyautogui.click(pos[0], pos[1])
    time.sleep(0.1)            # 等待自动全选生效
    pyautogui.write(str(text)) # 直接输入新值

# ============================================================
# 4. 扫描主逻辑（行优先，Y 固定时遍历 X）
# ============================================================
def run_scan(params):
    global stop_flag
    stop_flag = False
    # 隐藏主窗口，防止焦点落在参数输入框导致误输入
    root.withdraw()

    # 从 GUI 传递的参数中读取扫描范围、等待时间等
    xs = params['Xstart']
    xe = params['Xend']
    ys = params['Ystart']
    ye = params['Yend']
    fov = params['FOV']                # 视野大小（正方形）
    ov = params['Overlap']             # 重叠比例（0~1）
    capture_wait = params['CaptureWait']   # 采集等待时间 (秒)
    move_wait = params['MoveWait']         # CNC 移动等待时间 (秒)
    prefix = params['FilePrefix']          # 文件名前缀

    try:
        # --- 激活目标窗口 ---
        activate_target_window()

        # 所有按钮/输入框的屏幕坐标（直接使用，不再转换）
        xi, yi = xInputBox, yInputBox
        si = sizeInputBox
        lb, cb = liveButton, captureButton
        mc = moveConfirmBtn
        so, fi, sc = saveOpenBtn, fileNameInput, saveConfirmBtn

        # --- 计算扫描矩阵 ---
        step = fov * (1 - ov)          # 步距 = 视野 × (1 - 重叠)
        Nx = math.floor((xe - xs) / step) + 1   # X 方向扫描点数
        Ny = math.floor((ye - ys) / step) + 1   # Y 方向扫描点数
        total = Nx * Ny

        # 弹出确认对话框，让用户检查参数
        if not messagebox.askyesno("确认扫描",
                                   f"将扫描 {Nx} 列 × {Ny} 行 = {total} 个位置。\n"
                                   f"步距 = {step:.2f} mm\n"
                                   f"采集等待 = {capture_wait} s  |  移动等待 = {move_wait} s\n\n是否开始？"):
            root.deiconify()   # 用户取消，恢复主窗口
            return

        # --- 一次性设置（视野大小）---
        if si:                           # 如果配置了视野输入框（不为 null）
            set_text(si, fov)            # 输入视野大小
            # 如果软件需要点击确认才能应用视野，可以在这里加一次 mc 点击
            # pyautogui.click(mc)
            time.sleep(0.3)

        # 可选的初始 Live 模式激活
        pyautogui.click(lb)
        time.sleep(0.2)

        # ===== 主扫描循环：行优先（Y 外层，X 内层）=====
        count = 0   # 已采集的块数计数器
        for j in range(Ny):
            yc = ys + j * step                    # 当前行的 Y 中心坐标

            # --- 设置本行 Y 坐标（仅一次）---
            set_text(yi, yc)                      # 填写 Y 坐标
            pyautogui.click(mc)                   # 点击“确认移动”，让 CNC 移到该行 Y 位置
            time.sleep(0.2)

            # 等待 CNC 移动到目标 Y 位置
            if move_wait > 0:
                remaining = move_wait
                while remaining > 0 and not stop_flag:
                    time.sleep(min(1, remaining))
                    remaining -= 1
            if stop_flag:
                break

            for i in range(Nx):
                if stop_flag:
                    break
                xc = xs + i * step                # 当前列的 X 中心坐标
                count += 1
                # 更新主界面状态栏
                root.after(0, update_status, f"第 {count}/{total} 块  X={xc:.1f}, Y={yc:.1f}")

                # ---------- 每块图像的扫描流程 ----------
                # 1. 点击 Live 按钮（切换到实时预览，为下一张图做准备）
                pyautogui.click(lb)
                time.sleep(0.2)

                # 2. 设置 X 坐标（Y 已固定）
                set_text(xi, xc)

                # 3. 确认移动（沿 X 轴）
                pyautogui.click(mc)
                time.sleep(0.2)

                # 4. 等待 CNC X 轴移动到位
                if move_wait > 0:
                    remaining = move_wait
                    while remaining > 0 and not stop_flag:
                        time.sleep(min(1, remaining))
                        remaining -= 1
                if stop_flag:
                    break

                # 5. 点击 Capture 按钮抓取静态图像
                pyautogui.click(cb)

                # 6. 等待采集完成
                remaining = capture_wait
                while remaining > 0 and not stop_flag:
                    time.sleep(min(1, remaining))
                    remaining -= 1
                if stop_flag:
                    break

                # 7. 保存图像文件
                pyautogui.click(so)               # 打开保存对话框
                time.sleep(0.8)
                pyautogui.click(fi)               # 点击文件名输入框
                time.sleep(0.2)
                # 清空旧文件名并写入新文件名（前缀 + 三位编号）
                pyautogui.hotkey('ctrl', 'a')
                pyautogui.press('backspace')
                fname = f"{prefix}{count:03d}"    # 例如 SampleA_001
                pyautogui.write(fname)
                pyautogui.click(sc)               # 点击“保存”按钮
                time.sleep(1)

                # 8. 再次点击 Live，恢复实时预览状态（为下一次移动做准备）
                pyautogui.click(lb)
                time.sleep(0.2)

            if stop_flag:
                break   # 跳出外层循环

        # 扫描完成后更新界面
        if stop_flag:
            root.after(0, lambda: status_var.set("已停止"))
        else:
            root.after(0, scan_complete, total)

    except Exception as e:
        # 异常处理：将错误信息显示在弹窗中
        root.after(0, lambda err=str(e): messagebox.showerror("错误", err))
    finally:
        # 无论成功或失败，最终都恢复主程序窗口
        root.after(0, root.deiconify)

# ============================================================
# 5. GUI 状态更新函数
# ============================================================
def update_status(msg):
    """更新状态栏文字"""
    status_var.set(msg)

def scan_complete(total):
    """扫描全部完成后的提示"""
    messagebox.showinfo("完成", f"全部扫描完成！共 {total} 块数据已保存。")
    status_var.set("就绪")

# ============================================================
# 6. 构建图形用户界面
# ============================================================
root = tk.Tk()
root.title("CT 分块扫描控制台")
status_var = tk.StringVar(value="就绪")

# 用户输入变量（默认值可按实际修改）
var_Xstart = tk.DoubleVar(value=10.0)
var_Xend   = tk.DoubleVar(value=78.0)
var_Ystart = tk.DoubleVar(value=5.0)
var_Yend   = tk.DoubleVar(value=55.0)
var_FOV    = tk.DoubleVar(value=20.0)
var_Overlap = tk.DoubleVar(value=0.15)
var_CaptureWait = tk.DoubleVar(value=2.0)     # 采集等待时间 (秒)
var_MoveWait    = tk.DoubleVar(value=2.0)     # 移动等待时间 (秒)
var_FilePrefix  = tk.StringVar(value="SampleA_")

# 参数分组框
frame = tk.LabelFrame(root, text="扫描范围与参数", padx=10, pady=10)
frame.pack(padx=10, pady=5, fill="x")

# 第一行：X 起点 + X 终点
tk.Label(frame, text="X 轴起点 (mm):").grid(row=0, column=0, sticky="e")
tk.Entry(frame, textvariable=var_Xstart, width=8).grid(row=0, column=1)
tk.Label(frame, text="X 轴终点 (mm):").grid(row=0, column=2, sticky="e", padx=(20,0))
tk.Entry(frame, textvariable=var_Xend, width=8).grid(row=0, column=3)

# 第二行：Y 起点 + Y 终点
tk.Label(frame, text="Y 轴起点 (mm):").grid(row=1, column=0, sticky="e")
tk.Entry(frame, textvariable=var_Ystart, width=8).grid(row=1, column=1)
tk.Label(frame, text="Y 轴终点 (mm):").grid(row=1, column=2, sticky="e", padx=(20,0))
tk.Entry(frame, textvariable=var_Yend, width=8).grid(row=1, column=3)

# 第三行：视野大小 + 重叠比例
tk.Label(frame, text="视野大小 (mm):").grid(row=2, column=0, sticky="e")
tk.Entry(frame, textvariable=var_FOV, width=8).grid(row=2, column=1)
tk.Label(frame, text="重叠比例 (0~1):").grid(row=2, column=2, sticky="e", padx=(20,0))
tk.Entry(frame, textvariable=var_Overlap, width=8).grid(row=2, column=3)

# 第四行：采集等待 + 移动等待
tk.Label(frame, text="采集等待时间 (s):").grid(row=3, column=0, sticky="e")
tk.Entry(frame, textvariable=var_CaptureWait, width=8).grid(row=3, column=1)
tk.Label(frame, text="移动等待时间 (s):").grid(row=3, column=2, sticky="e", padx=(20,0))
tk.Entry(frame, textvariable=var_MoveWait, width=8).grid(row=3, column=3)

# 文件保存设置
frame2 = tk.LabelFrame(root, text="文件保存", padx=10, pady=10)
frame2.pack(padx=10, pady=5, fill="x")
tk.Label(frame2, text="文件名前缀:").pack(side="left")
tk.Entry(frame2, textvariable=var_FilePrefix, width=15).pack(side="left", padx=5)
tk.Label(frame2, text="(自动追加三位编号)").pack(side="left")

# 操作按钮
btn_frame = tk.Frame(root)
btn_frame.pack(pady=5)
tk.Button(btn_frame, text="开始扫描", width=12, command=lambda: start_scan_thread()).pack(side="left", padx=5)
tk.Button(btn_frame, text="停止", width=8, command=lambda: set_stop()).pack(side="left", padx=5)
tk.Button(btn_frame, text="退出", width=8, command=root.quit).pack(side="left", padx=5)

# 状态栏
tk.Label(root, textvariable=status_var, bd=1, relief="sunken", anchor="w").pack(fill="x", padx=10, pady=5)

# 停止按钮回调
def set_stop():
    global stop_flag
    stop_flag = True
    status_var.set("正在停止...")

# 启动扫描线程
def start_scan_thread():
    # 收集 GUI 输入参数
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
    # 在后台线程中运行扫描，避免阻塞 GUI
    t = threading.Thread(target=run_scan, args=(params,), daemon=True)
    t.start()

# ============================================================
# 7. 全局 Esc 热键：紧急停止并移动鼠标到屏幕中心
# ============================================================
def on_press(key):
    global stop_flag
    if key == pynput_keyboard.Key.esc:
        stop_flag = True
        try:
            pyautogui.moveTo(1200, 600)   # 鼠标回到坐标 (1200, 600)
        except Exception:
            pass
        root.after(0, lambda: status_var.set("紧急停止！鼠标已移到 (1200,600)"))
        # 确保主窗口恢复可见（如果之前被隐藏）
        root.after(0, root.deiconify)

listener = pynput_keyboard.Listener(on_press=on_press)
listener.start()

# ============================================================
# 8. 启动 GUI 主循环
# ============================================================
root.mainloop()
