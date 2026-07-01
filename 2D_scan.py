"""
2D CT 分块重叠扫描与拼接控制软件（最终整合版）
================================================
功能：
  - 读取 config.json 获取窗口标题、控件屏幕绝对坐标、Fiji 路径
  - GUI 输入扫描范围、视野、重叠、移动等待时间、文件名前缀、图像保存目录
  - 曝光时间与平均张数自动设置，采集等待时间自动计算（曝光时间*平均张数+1秒）
  - Live（F2）和 Capture（F3）使用键盘快捷键
  - 行优先扫描：Y 递增，X 递减，中心越过终点保证覆盖
  - 扫描完成后可选择立即拼接或稍后拼接（使用 ImageJ/Fiji）
  - 拼接时动态生成宏，调用 Fiji 命令行执行，通过 fused.tif 重命名获取结果
  - 图形化矩阵选择：保留左上角区域，丢弃边缘冗余块
  - Esc 紧急停止，停止后鼠标移至 (1200, 600)
  - 扫描时隐藏主窗口
依赖：pyautogui, pynput, tkinter, subprocess, tempfile, shutil, json, os, threading, time, math
"""

import sys
import json
import os
import tempfile
import shutil
import subprocess
import threading
import time
import math
import tkinter as tk
from tkinter import messagebox, ttk, filedialog
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
    """从 config.json 读取窗口标题、控件坐标和 Fiji 可执行文件路径"""
    if not os.path.exists(CONFIG_FILE):
        messagebox.showerror("配置文件缺失",
                             f"未找到 {CONFIG_FILE}，请将配置文件与本程序放在同一目录。")
        sys.exit(1)
    try:
        # 使用 utf-8-sig 编码自动跳过可能存在的 BOM 头
        with open(CONFIG_FILE, "r", encoding="utf-8-sig") as f:
            config = json.load(f)
        # 检查必要字段
        if "window" not in config or "controls" not in config:
            raise KeyError("缺少 'window' 或 'controls' 字段")
        # 确保必须的按钮坐标存在
        required_buttons = ["exposure_menu_button", "integration_menu_button",
                            "move_confirm_button"]
        for btn in required_buttons:
            if btn not in config["controls"] or config["controls"][btn] is None:
                raise KeyError(f"配置缺少控件坐标: {btn}")
        # 检查平均张数下拉区域坐标
        integ_keys = ["integration_dropdown_left", "integration_dropdown_right",
                      "integration_dropdown_top", "integration_dropdown_bottom"]
        for k in integ_keys:
            if k not in config["controls"] or config["controls"][k] is None:
                raise KeyError(f"配置缺少平均张数下拉区域坐标: {k}")
        # Fiji 可执行文件路径（可选，但拼接时需要）
        if "fiji_executable" not in config:
            config["fiji_executable"] = ""
        return config
    except Exception as e:
        messagebox.showerror("配置文件错误", f"读取 {CONFIG_FILE} 失败: {e}")
        sys.exit(1)

# 加载配置
config = load_config()

# 窗口标题
CT_WINDOW_TITLE = config["window"]["title"]
# Fiji 可执行文件路径（默认为空字符串）
FIJI_EXECUTABLE = config.get("fiji_executable", "")

# 控件屏幕绝对坐标（由 GetCoords 抓取，直接用于点击）
controls = config["controls"]
xInputBox        = tuple(controls["x_input"])
yInputBox        = tuple(controls["y_input"])
sizeInputBox     = tuple(controls["size_input"]) if controls.get("size_input") else None   # 可能为 null
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
stop_flag = False          # 是否触发紧急停止
last_scan_params = None    # 保存最近一次扫描的参数，供拼接使用

# ============================================================
# 3. 工具函数
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
    """点击指定屏幕坐标的输入框，并输入文本"""
    pyautogui.click(pos[0], pos[1])
    time.sleep(0.1)            # 等待软件自动全选生效
    pyautogui.write(str(text))

def set_exposure(value_index):
    """设置曝光时间：点击菜单按钮，然后按数字键 (0~7)"""
    pyautogui.click(exposureMenuBtn[0], exposureMenuBtn[1])
    time.sleep(0.3)            # 等待菜单弹出
    pyautogui.press(str(value_index))
    time.sleep(0.2)

def set_integration(value_index):
    """设置平均张数：点击菜单按钮，然后点击下拉菜单中对应索引的矩形中心"""
    pyautogui.click(integrationMenuBtn[0], integrationMenuBtn[1])
    time.sleep(0.3)            # 等待菜单弹出
    total_height = integBottom - integTop
    item_height = total_height / 13.0
    center_x = (integLeft + integRight) // 2
    center_y = int(integTop + (value_index + 0.5) * item_height)
    pyautogui.click(center_x, center_y)
    time.sleep(0.2)

# ============================================================
# 4. 曝光时间与采集等待时间计算
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
    global stop_flag, last_scan_params
    stop_flag = False
    root.withdraw()                     # 隐藏主窗口，防止焦点落在参数输入框

    xs = params['Xstart']               # X 起点（较大值）
    xe = params['Xend']                 # X 终点（较小值）
    ys = params['Ystart']               # Y 起点（较小值）
    ye = params['Yend']                 # Y 终点（较大值）
    fov = params['FOV']
    ov = params['Overlap']
    capture_wait = params['CaptureWait']    # 自动计算好的采集等待时间
    move_wait    = params['MoveWait']       # CNC 移动等待时间
    prefix       = params['FilePrefix']
    exposure_idx = params['Exposure']       # 曝光时间索引 (0~7)
    integ_idx    = params['Integration']    # 平均张数索引 (0~12)
    image_dir    = params['ImageDir']       # 图像保存目录

    try:
        activate_target_window()

        xi, yi = xInputBox, yInputBox
        si = sizeInputBox
        mc = moveConfirmBtn
        so, fi, sc = saveOpenBtn, fileNameInput, saveConfirmBtn

        # ---------- 生成扫描中心序列（X递减，Y递增，中心越过终点）----------
        step_abs = fov * (1 - ov)       # 绝对步长

        # X 方向：递减（步长取负值）
        step_x = -step_abs
        Xcenters = []
        xc = xs
        while xc >= xe:                 # 只要中心还在终点右侧，继续扫描
            Xcenters.append(xc)
            xc += step_x
        Xcenters.append(xc)             # 追加越过终点的中心，保证覆盖

        # Y 方向：递增（步长取正值）
        step_y = step_abs
        Ycenters = []
        yc = ys
        while yc <= ye:                 # 只要中心还在终点下侧，继续扫描
            Ycenters.append(yc)
            yc += step_y
        Ycenters.append(yc)             # 追加越过终点的中心

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

        # ---------- 一次性设置：视野大小、曝光时间、平均张数 ----------
        if si:
            set_text(si, fov)
            time.sleep(0.3)
        set_exposure(exposure_idx)
        set_integration(integ_idx)

        # 再次激活窗口，确保焦点正确
        activate_target_window()
        time.sleep(0.2)

        # ===== 主扫描循环：行优先（Y 递增，X 递减）=====
        count = 0
        for yc in Ycenters:
            # --- 设置本行 Y 坐标并确认移动 ---
            set_text(yi, yc)
            pyautogui.click(mc)         # 确认移动，让 CNC 移到该行 Y 位置
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

                # 1. Live (F2)
                pyautogui.press('f2')
                time.sleep(0.2)

                # 2. 设置 X 坐标
                set_text(xi, xc)

                # 3. 确认移动
                pyautogui.click(mc)
                time.sleep(0.2)

                if move_wait > 0:
                    remaining = move_wait
                    while remaining > 0 and not stop_flag:
                        time.sleep(min(1, remaining))
                        remaining -= 1
                if stop_flag:
                    break

                # 4. Capture (F3)
                pyautogui.press('f3')
                # 5. 等待采集完成
                remaining = capture_wait
                while remaining > 0 and not stop_flag:
                    time.sleep(min(1, remaining))
                    remaining -= 1
                if stop_flag:
                    break

                # 6. 保存文件
                pyautogui.click(so)      # 打开保存对话框
                time.sleep(0.8)
                fname = f"{prefix}{count:03d}"
                pyautogui.write(fname)
                pyautogui.press('enter')      # 保存按钮
                time.sleep(1)

                # 7. 再次 Live，准备下一块
                pyautogui.press('f2')
                time.sleep(0.2)

            # 退出内层循环后，检查是否需要退出外层循环
            if stop_flag:
                break

        # 外层循环结束后，判断是中途停止还是正常完成
        if stop_flag:
            root.after(0, lambda: status_var.set("已停止"))
        else:
            # 扫描正常完成，先保存参数（供拼接使用）
            last_scan_params = {
                'image_dir': image_dir,
                'prefix': prefix,
                'Nx': Nx,
                'Ny': Ny,
                'overlap': ov,
                'total': total
            }
            # 然后调度 scan_complete（其中会调用 ask_stitch_after_scan）
            root.after(0, scan_complete, total)

    except Exception as e:
        root.after(0, lambda err=str(e): messagebox.showerror("错误", err))
    finally:
        root.after(0, root.deiconify)   # 确保主窗口恢复显示



# ============================================================
# 6. 拼接相关函数
# ============================================================
def ask_stitch_after_scan():
    """扫描完成后弹窗询问是否立即拼接"""
    if not last_scan_params:
        return
    ans = messagebox.askyesno("拼接", "扫描已完成。\n是否立即进行图像拼接？")
    if ans:
        stitch_images()
    else:
        btn_stitch.config(state="normal")
        status_var.set("就绪（可点击“拼接图像”按钮进行拼接）")

def stitch_images():
    """执行拼接操作（headless + Write to disk，自动重命名结果文件）"""
    if not last_scan_params:
        messagebox.showwarning("无扫描参数", "请先执行一次完整扫描。")
        return

    fiji_exe = FIJI_EXECUTABLE
    if not fiji_exe or not os.path.isfile(fiji_exe):
        messagebox.showerror("Fiji 未找到",
                             "请在 config.json 中配置正确的 fiji_executable 路径，\n"
                             "并确保 Fiji 已安装。")
        return

    params = last_scan_params
    image_dir = params['image_dir']
    if not os.path.isdir(image_dir):
        messagebox.showerror("图像目录不存在",
                             f"目录 {image_dir} 不存在，\n请检查主界面中的“图像目录”设置。")
        return

    orig_Nx = params['Nx']
    orig_Ny = params['Ny']

    # ---------- 矩阵选择 GUI ----------
    dlg = tk.Toplevel(root)
    dlg.title("选择拼接范围（保留左上角区域）")
    dlg.resizable(False, False)
    dlg.grab_set()

    ctrl_frame = tk.Frame(dlg)
    ctrl_frame.pack(pady=10)

    tk.Label(ctrl_frame, text="保留列数:").grid(row=0, column=0, padx=5)
    col_var = tk.IntVar(value=orig_Nx)
    col_spin = tk.Spinbox(ctrl_frame, from_=1, to=orig_Nx, textvariable=col_var, width=5)
    col_spin.grid(row=0, column=1, padx=5)

    tk.Label(ctrl_frame, text="保留行数:").grid(row=0, column=2, padx=5)
    row_var = tk.IntVar(value=orig_Ny)
    row_spin = tk.Spinbox(ctrl_frame, from_=1, to=orig_Ny, textvariable=row_var, width=5)
    row_spin.grid(row=0, column=3, padx=5)

    preview_frame = tk.Frame(dlg, bg="white", relief="ridge", bd=2)
    preview_frame.pack(padx=10, pady=5)

    cell_size = 25
    canvas = tk.Canvas(preview_frame,
                       width=orig_Nx * cell_size + 2,
                       height=orig_Ny * cell_size + 2,
                       highlightthickness=0, bg="white")
    canvas.pack()

    def draw_grid():
        canvas.delete("all")
        keep_cols = col_var.get()
        keep_rows = row_var.get()
        for j in range(orig_Ny):
            for i in range(orig_Nx):
                x1 = i * cell_size + 1
                y1 = j * cell_size + 1
                x2 = x1 + cell_size - 2
                y2 = y1 + cell_size - 2
                if i < keep_cols and j < keep_rows:
                    color, outline = "#4CAF50", "#2E7D32"
                else:
                    color, outline = "#CCCCCC", "#999999"
                canvas.create_rectangle(x1, y1, x2, y2, fill=color, outline=outline)
                idx = j * orig_Nx + i + 1
                canvas.create_text(x1 + cell_size//2 - 1, y1 + cell_size//2 - 1,
                                   text=str(idx), font=("Arial", 7), fill="black")

    col_var.trace_add('write', lambda *a: draw_grid())
    row_var.trace_add('write', lambda *a: draw_grid())
    draw_grid()

    btn_frame = tk.Frame(dlg)
    btn_frame.pack(pady=10)
    result = {"confirmed": False, "nx": orig_Nx, "ny": orig_Ny}

    def confirm():
        result["nx"] = col_var.get()
        result["ny"] = row_var.get()
        result["confirmed"] = True
        dlg.destroy()

    def cancel():
        dlg.destroy()

    tk.Button(btn_frame, text="确定", command=confirm).pack(side="left", padx=10)
    tk.Button(btn_frame, text="取消", command=cancel).pack(side="left", padx=10)

    root.wait_window(dlg)
    if not result["confirmed"]:
        status_var.set("拼接已取消")
        return

    Nx_stitch, Ny_stitch = result["nx"], result["ny"]

    # ---------- 路径和文件模板 ----------
    prefix = params['prefix']
    overlap = params['overlap']
    safe_dir = image_dir.replace('\\', '/')          # 转为正斜杠

    # 文件模板（根据实际修改，这里假设三位补零 .tif）
    file_template = f"{prefix}{{iii}}.jpg"

    # 计算缺失的 tile
    missing = []
    for j in range(orig_Ny):
        for i in range(orig_Nx):
            if i >= Nx_stitch or j >= Ny_stitch:
                missing.append(j * orig_Nx + i + 1)
    missing_str = ",".join(str(t) for t in missing) if missing else ""

    # ---------- 生成 ImageJ 宏（Write to disk，无窗口操作）----------
    macro_args = (
        f"type=[Grid: row-by-row] "
        f"order=[Right & Down                ] "
        f"grid_size_x={orig_Nx} "
        f"grid_size_y={orig_Ny} "
        f"tile_overlap={int(overlap*100)} "
        f"first_file_index_i=1 "
        f"directory=[{safe_dir}] "
        f"file_names={file_template} "
        f"output_textfile_name=TileConfiguration.txt "
        f"fusion_method=[Linear Blending] "
        f"regression_threshold=0.30 "
        f"max/avg_displacement_threshold=2.50 "
        f"absolute_displacement_threshold=3.50 "
        f"computation_parameters=[Save memory (but be slower)] "
        f"image_output=[Write to disk] "
        f"output_directory=[{safe_dir}] "   # 末尾空格防粘连
    )
    if missing_str:
        macro_args += f"missing_tiles=[{missing_str}]"

    # 简洁宏，只运行拼接，然后退出（不需要 waitFor 等）
    macro_content = (
        f'run("Grid/Collection stitching", "{macro_args}");\n'
        f'run("Quit");\n'
    )

    # ---------- 执行 Fiji (headless) ----------
    macro_file = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".ijm", delete=False, encoding="utf-8") as f:
            f.write(macro_content)
            macro_file = f.name
    except Exception as e:
        messagebox.showerror("宏生成失败", str(e))
        return

    status_var.set("正在拼接图像，请稍候...")
    root.update_idletasks()

    try:
        cmd = [fiji_exe, "--headless", "--console", "-macro", macro_file]
        log_path = os.path.join(image_dir, "fiji_output.log")

        with open(log_path, "w", encoding="utf-8") as log:
            result = subprocess.run(
                cmd,
                stdout=log,
                stderr=subprocess.STDOUT,
                timeout=600
            )

        if result.returncode != 0:
            # 尝试读取日志前500字符供显示
            try:
                with open(log_path, "r", encoding="utf-8") as lf:
                    log_preview = lf.read(500)
            except:
                log_preview = "无法读取日志"
            messagebox.showerror("拼接失败",
                                 f"Fiji 返回错误码 {result.returncode}。\n\n"
                                 f"日志前500字符：\n{log_preview}\n\n"
                                 f"完整日志：{log_path}")
            return
    except subprocess.TimeoutExpired:
        messagebox.showerror("拼接超时", "拼接进程超过10分钟未完成。")
        return
    except FileNotFoundError:
        messagebox.showerror("Fiji 未找到", f"找不到可执行文件：\n{fiji_exe}")
        return
    except Exception as e:
        messagebox.showerror("运行 Fiji 出错", str(e))
        return
    finally:
        if macro_file and os.path.exists(macro_file):
            os.remove(macro_file)

    # ---------- 查找并重命名结果文件 ----------
    # 实际输出文件名可能为 img_t1_z1_c1.tif（有时也是 fused.tif）
    possible_names = ["img_t1_z1_c1.file", "fused.tif"]
    found_file = None
    for name in possible_names:
        candidate = os.path.join(image_dir, name)
        if os.path.isfile(candidate):
            found_file = candidate
            break

    if found_file is None:
        messagebox.showerror("拼接结果丢失",
                             f"未找到拼接结果文件。\n"
                             f"尝试的文件名：{possible_names}\n"
                             f"请检查 Fiji 日志或手动运行宏。")
        status_var.set("就绪")
        return

    # 重命名为统一的结果文件
    result_temp = os.path.join(image_dir, "Stitched_Result.tif")
    if os.path.exists(result_temp):
        os.remove(result_temp)
    os.rename(found_file, result_temp)

    # ---------- 另存为对话框 ----------
    save_path = filedialog.asksaveasfilename(
        title="保存拼接图像",
        defaultextension=".tif",
        filetypes=[("TIFF files", "*.tif"), ("All files", "*.*")]
    )
    if save_path:
        try:
            shutil.copy2(result_temp, save_path)
            messagebox.showinfo("拼接完成", f"拼接图像已保存至：\n{save_path}")
            os.remove(result_temp)
        except Exception as e:
            messagebox.showerror("保存失败", str(e))
    else:
        messagebox.showwarning("未保存", f"拼接结果保留在：\n{result_temp}")

    status_var.set("就绪")
# ============================================================
# 7. GUI 更新函数
# ============================================================
def update_status(msg):
    """更新主界面状态栏文字"""
    status_var.set(msg)

def scan_complete(total):
    """扫描完成弹窗，然后自动询问是否拼接"""
    messagebox.showinfo("完成", f"全部扫描完成！共 {total} 块数据已保存。")
    status_var.set("就绪")
    # 弹窗关闭后，立即询问拼接
    ask_stitch_after_scan()

# ============================================================
# 8. 构建图形用户界面
# ============================================================
root = tk.Tk()
root.title("CT 分块扫描与拼接控制台")
status_var = tk.StringVar(value="就绪")

# --- 用户输入变量 ---
var_Xstart = tk.StringVar(value="")
var_Xend   = tk.StringVar(value="")
var_Ystart = tk.StringVar(value="")
var_Yend   = tk.StringVar(value="")
var_FOV    = tk.StringVar(value="64")          # 默认视野 64mm
var_Overlap = tk.DoubleVar(value=0.15)
var_MoveWait    = tk.DoubleVar(value=5)        # 移动等待时间（秒）
var_FilePrefix  = tk.StringVar(value="SampleA_")
var_ImageDir    = tk.StringVar(value="")       # 图像保存目录

# 曝光时间与平均张数选项
exposure_options = ["33ms", "67ms", "100ms", "200ms", "333ms", "1000ms", "2000ms", "5000ms"]
integ_options    = ["1", "2", "4", "8", "16", "32", "64", "128", "256", "512", "1024", "2048", "4096"]
var_Exposure     = tk.StringVar(value=exposure_options[3])   # 默认 200ms
var_Integration  = tk.StringVar(value=integ_options[4])      # 默认 16

# 自动计算的采集等待时间（只读显示）
var_CaptureWait = tk.StringVar(value="")
update_capture_wait_display()
# 当曝光时间或平均张数变更时，自动刷新等待时间
var_Exposure.trace_add('write', update_capture_wait_display)
var_Integration.trace_add('write', update_capture_wait_display)

# --- 参数输入区 ---
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

# 第四行：移动等待时间
tk.Label(frame, text="移动等待时间 (s):").grid(row=3, column=0, sticky="e")
tk.Entry(frame, textvariable=var_MoveWait, width=8).grid(row=3, column=1)

# --- 采集参数区 ---
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

# 自动计算的采集等待时间（只读标签）
tk.Label(frame_exp, text="采集等待时间:").grid(row=1, column=0, sticky="e", pady=(10,0))
tk.Label(frame_exp, textvariable=var_CaptureWait, relief="sunken", width=15,
         anchor="w").grid(row=1, column=1, padx=5, pady=(10,0), sticky="w")

# --- 图像保存路径 ---
frame_dir = tk.LabelFrame(root, text="图像保存路径", padx=10, pady=10)
frame_dir.pack(padx=10, pady=5, fill="x")
tk.Label(frame_dir, text="图像目录:").pack(side="left")
tk.Entry(frame_dir, textvariable=var_ImageDir, width=40).pack(side="left", padx=5)
tk.Button(frame_dir, text="浏览", command=lambda: var_ImageDir.set(filedialog.askdirectory())).pack(side="left", padx=5)

# --- 文件保存前缀 ---
frame2 = tk.LabelFrame(root, text="文件保存", padx=10, pady=10)
frame2.pack(padx=10, pady=5, fill="x")
tk.Label(frame2, text="文件名前缀:").pack(side="left")
tk.Entry(frame2, textvariable=var_FilePrefix, width=15).pack(side="left", padx=5)
tk.Label(frame2, text="(自动追加三位编号)").pack(side="left")

# --- 操作按钮 ---
btn_frame = tk.Frame(root)
btn_frame.pack(pady=5)
tk.Button(btn_frame, text="开始扫描", width=12, command=lambda: start_scan_thread()).pack(side="left", padx=5)
tk.Button(btn_frame, text="停止", width=8, command=lambda: set_stop()).pack(side="left", padx=5)
# 拼接按钮，初始禁用（扫描完成后启用）
btn_stitch = tk.Button(btn_frame, text="拼接图像", width=10, command=stitch_images, state="disabled")
btn_stitch.pack(side="left", padx=5)
tk.Button(btn_frame, text="退出", width=8, command=root.quit).pack(side="left", padx=5)

# 状态栏
tk.Label(root, textvariable=status_var, bd=1, relief="sunken", anchor="w").pack(fill="x", padx=10, pady=5)

# --- 停止按钮与启动扫描的回调函数 ---
def set_stop():
    global stop_flag
    stop_flag = True
    status_var.set("正在停止...")

def start_scan_thread():
    # 读取并校验用户输入
    try:
        xs = float(var_Xstart.get())
        xe = float(var_Xend.get())
        ys = float(var_Ystart.get())
        ye = float(var_Yend.get())
        fov = float(var_FOV.get())
    except ValueError:
        messagebox.showerror("输入错误", "X/Y 起点、终点和视野大小必须为有效数字。")
        return

    ov = var_Overlap.get()
    move_wait = var_MoveWait.get()
    prefix = var_FilePrefix.get().strip()
    if not prefix:
        prefix = "Scan"
    image_dir = var_ImageDir.get().strip()
    if not image_dir:
        messagebox.showerror("输入错误", "请指定图像保存目录。")
        return

    # 合法性检查
    if xs <= xe:
        messagebox.showerror("参数错误", "X 轴起点必须大于终点（起点为视野左侧坐标，较大值）。")
        return
    if ys >= ye:
        messagebox.showerror("参数错误", "Y 轴起点必须小于终点（起点为视野下侧坐标，较小值）。")
        return
    if fov <= 0 or ov < 0 or ov >= 1 or move_wait < 0:
        messagebox.showerror("参数错误", "视野>0，重叠比例0~1(不含1)，移动等待时间>=0")
        return

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
        'Integration': integ_idx,
        'ImageDir': image_dir
    }

    # 禁用拼接按钮（新一轮扫描）
    btn_stitch.config(state="disabled")
    t = threading.Thread(target=run_scan, args=(params,), daemon=True)
    t.start()

# ============================================================
# 9. 全局 Esc 热键：紧急停止 + 鼠标回中
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

# 启动 GUI 主循环
root.mainloop()
