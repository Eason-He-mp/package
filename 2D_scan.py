"""
2D CT 分块重叠扫描与拼接控制软件
==================================
功能：
  - 读取 config.json 获取窗口标题、控件屏幕绝对坐标、Fiji 路径
  - GUI 中输入扫描范围、视野、重叠、移动等待时间、文件名前缀、图像保存目录
  - 曝光时间与平均张数自动设置，采集等待时间自动计算
  - Live（F2）和 Capture（F3）用键盘快捷键
  - 行优先扫描：Y 递增，X 递减，中心越过终点
  - 扫描完成后可选择立即拼接或稍后拼接（使用 ImageJ/Fiji）
  - 拼接时动态生成宏，调用 Fiji 执行，拼接结果由用户选择保存路径
  - Esc 紧急停止，停止后鼠标移至 (1200, 600)
  - 扫描时隐藏主窗口
依赖：pyautogui, pynput, tkinter, subprocess, tempfile, shutil, json, os
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
        # 检查必要字段
        required_sections = ["window", "controls"]
        for sec in required_sections:
            if sec not in config:
                raise KeyError(f"缺少 '{sec}' 字段")
        # 检查 Fiji 可执行文件（可以不存在，但拼接时需要）
        if "fiji_executable" not in config:
            config["fiji_executable"] = ""
        return config
    except Exception as e:
        messagebox.showerror("配置文件错误", f"读取 {CONFIG_FILE} 失败: {e}")
        sys.exit(1)

config = load_config()

CT_WINDOW_TITLE = config["window"]["title"]
FIJI_EXECUTABLE = config.get("fiji_executable", "")

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
last_scan_params = None   # 保存最近一次扫描的参数，供拼接使用

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
    pyautogui.click(exposureMenuBtn[0], exposureMenuBtn[1])
    time.sleep(0.3)
    pyautogui.press(str(value_index))
    time.sleep(0.2)

def set_integration(value_index):
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
    exp_sec = EXPOSURE_TO_SEC.get(exposure_str, 0.033)
    integ_num = int(integ_str)
    return round(exp_sec * integ_num + 1.0, 2)

def update_capture_wait_display(*args):
    exp_str = var_Exposure.get()
    integ_str = var_Integration.get()
    wait = calc_capture_wait(exp_str, integ_str)
    var_CaptureWait.set(f"{wait:.2f} 秒")

# ============================================================
# 5. 扫描主逻辑（保持原有功能）
# ============================================================
def run_scan(params):
    global stop_flag, last_scan_params
    stop_flag = False
    root.withdraw()

    xs = params['Xstart']
    xe = params['Xend']
    ys = params['Ystart']
    ye = params['Yend']
    fov = params['FOV']
    ov = params['Overlap']
    capture_wait = params['CaptureWait']
    move_wait    = params['MoveWait']
    prefix       = params['FilePrefix']
    exposure_idx = params['Exposure']
    integ_idx    = params['Integration']
    image_dir    = params['ImageDir']   # 用户输入的图像保存目录

    try:
        activate_target_window()

        xi, yi = xInputBox, yInputBox
        si = sizeInputBox
        mc = moveConfirmBtn
        so, fi, sc = saveOpenBtn, fileNameInput, saveConfirmBtn

        # 生成扫描中心序列
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

        # 一次性设置
        if si:
            set_text(si, fov)
            time.sleep(0.3)
        set_exposure(exposure_idx)
        set_integration(integ_idx)
        activate_target_window()
        time.sleep(0.2)

        # 主扫描循环
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
            root.after(0, root.deiconify)
            return

        # 扫描完成
        root.after(0, scan_complete, total)

        # 保存最后一次扫描参数（供拼接使用）
        last_scan_params = {
            'image_dir': image_dir,
            'prefix': prefix,
            'Nx': Nx,
            'Ny': Ny,
            'overlap': ov,
            'total': total
        }

        # 询问是否立即拼接
        root.after(0, ask_stitch_after_scan)

    except Exception as e:
        root.after(0, lambda err=str(e): messagebox.showerror("错误", err))
    finally:
        root.after(0, root.deiconify)
      
def ask_stitch_after_scan():
    """扫描完成后弹窗询问是否立即拼接"""
    if not last_scan_params:
        return
    ans = messagebox.askyesno("拼接", "扫描已完成。\n是否立即进行图像拼接？")
    if ans:
        stitch_images()
    else:
        # 激活手动拼接按钮
        btn_stitch.config(state="normal")
        status_var.set("就绪（可点击“拼接图像”按钮进行拼接）")
# ============================================================
# 6. 拼接相关函数
# ============================================================
def stitch_images():
    """图形化选择拼接范围，自动丢弃边缘冗余块（修复保存窗口问题）"""
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

    # ---------- 路径处理 ----------
    prefix = params['prefix']
    overlap = params['overlap']
    safe_dir = image_dir.replace('\\', '/')
    if ' ' in safe_dir:
        dir_for_macro = f'"{safe_dir}"'
    else:
        dir_for_macro = safe_dir

    file_template = f"{prefix}{{iii}}.tif"

    # 计算缺失tile
    missing = []
    for j in range(orig_Ny):
        for i in range(orig_Nx):
            if i >= Nx_stitch or j >= Ny_stitch:
                missing.append(j * orig_Nx + i + 1)
    missing_str = ",".join(str(t) for t in missing) if missing else ""

    # ---------- 生成宏（修复保存）----------
    macro_content = f"""
// Auto-generated stitching macro
run("Grid/Collection stitching", 
    "type=[Grid: row-by-row] 
     order=[Right & Down] 
     grid_size_x={orig_Nx} 
     grid_size_y={orig_Ny} 
     tile_overlap={int(overlap*100)} 
     first_file_index_i=1 
     directory=[{dir_for_macro}] 
     file_names={file_template} 
     output_textfile_name=TileConfiguration.txt 
     fusion_method=[Linear Blending]
     regression_threshold=0.30 
     max/avg_displacement_threshold=2.50 
     absolute_displacement_threshold=3.50 
     computation_parameters=[Save memory (but be slower)] 
     image_output=[Write to disk] 
     output_directory=[{dir_for_macro}]
     {"missing_tiles=[" + missing_str + "]" if missing_str else ""});
waitFor("Stitching");
selectWindow("Fused");
saveAs("Tiff", "{safe_dir}/Stitched_Result.tif");
run("Quit");
"""

    # ---------- 执行 Fiji ----------
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
        subprocess.run(cmd, check=True, timeout=600)
    except subprocess.TimeoutExpired:
        messagebox.showerror("拼接超时", "拼接进程超过10分钟未完成。")
        return
    except subprocess.CalledProcessError as e:
        messagebox.showerror("拼接失败", f"Fiji 返回错误码 {e.returncode}。\n请检查图像文件格式或目录权限。")
        return
    except Exception as e:
        messagebox.showerror("运行 Fiji 出错", str(e))
        return
    finally:
        if macro_file and os.path.exists(macro_file):
            os.remove(macro_file)

    # ---------- 检查结果 ----------
    result_temp = os.path.join(image_dir, "Stitched_Result.tif")
    if not os.path.isfile(result_temp):
        messagebox.showerror("拼接结果丢失",
                             f"未生成拼接文件。\n预期位置：{result_temp}\n"
                             "请确认图像文件名是否匹配，或手动运行Fiji宏检查窗口标题。")
        return

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
# 7. GUI 更新
# ============================================================
def update_status(msg):
    status_var.set(msg)

def scan_complete(total):
    messagebox.showinfo("完成", f"全部扫描完成！共 {total} 块数据已保存。")
    status_var.set("就绪")

# ============================================================
# 8. 构建界面（增加图像保存目录、拼接按钮）
# ============================================================
root = tk.Tk()
root.title("CT 分块扫描与拼接控制台")
status_var = tk.StringVar(value="就绪")

var_Xstart = tk.StringVar(value="")
var_Xend   = tk.StringVar(value="")
var_Ystart = tk.StringVar(value="")
var_Yend   = tk.StringVar(value="")
var_FOV    = tk.StringVar(value="64")
var_Overlap = tk.DoubleVar(value=0.15)
var_MoveWait    = tk.DoubleVar(value=5)
var_FilePrefix  = tk.StringVar(value="SampleA_")
var_ImageDir    = tk.StringVar(value="")   # 图像保存目录

exposure_options = ["33ms", "67ms", "100ms", "200ms", "333ms", "1000ms", "2000ms", "5000ms"]
integ_options    = ["1", "2", "4", "8", "16", "32", "64", "128", "256", "512", "1024", "2048", "4096"]
var_Exposure     = tk.StringVar(value=exposure_options[3])
var_Integration  = tk.StringVar(value=integ_options[4])
var_CaptureWait = tk.StringVar(value="")
update_capture_wait_display()
var_Exposure.trace_add('write', update_capture_wait_display)
var_Integration.trace_add('write', update_capture_wait_display)

frame = tk.LabelFrame(root, text="扫描范围与参数", padx=10, pady=10)
frame.pack(padx=10, pady=5, fill="x")

# X/Y 轴输入
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

tk.Label(frame, text="移动等待时间 (s):").grid(row=3, column=0, sticky="e")
tk.Entry(frame, textvariable=var_MoveWait, width=8).grid(row=3, column=1)

# 采集参数（曝光、积分、自动计算等待时间）
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
tk.Label(frame_exp, text="采集等待时间:").grid(row=1, column=0, sticky="e", pady=(10,0))
tk.Label(frame_exp, textvariable=var_CaptureWait, relief="sunken", width=15,
         anchor="w").grid(row=1, column=1, padx=5, pady=(10,0), sticky="w")

# 图像保存目录
frame_dir = tk.LabelFrame(root, text="图像保存路径", padx=10, pady=10)
frame_dir.pack(padx=10, pady=5, fill="x")
tk.Label(frame_dir, text="图像目录:").pack(side="left")
tk.Entry(frame_dir, textvariable=var_ImageDir, width=40).pack(side="left", padx=5)
tk.Button(frame_dir, text="浏览", command=lambda: var_ImageDir.set(filedialog.askdirectory())).pack(side="left", padx=5)

# 文件保存前缀
frame2 = tk.LabelFrame(root, text="文件保存", padx=10, pady=10)
frame2.pack(padx=10, pady=5, fill="x")
tk.Label(frame2, text="文件名前缀:").pack(side="left")
tk.Entry(frame2, textvariable=var_FilePrefix, width=15).pack(side="left", padx=5)
tk.Label(frame2, text="(自动追加三位编号)").pack(side="left")

# 按钮区域
btn_frame = tk.Frame(root)
btn_frame.pack(pady=5)
tk.Button(btn_frame, text="开始扫描", width=12, command=lambda: start_scan_thread()).pack(side="left", padx=5)
tk.Button(btn_frame, text="停止", width=8, command=lambda: set_stop()).pack(side="left", padx=5)
# 拼接按钮，初始禁用
btn_stitch = tk.Button(btn_frame, text="拼接图像", width=10, command=stitch_images, state="disabled")
btn_stitch.pack(side="left", padx=5)
tk.Button(btn_frame, text="退出", width=8, command=root.quit).pack(side="left", padx=5)

tk.Label(root, textvariable=status_var, bd=1, relief="sunken", anchor="w").pack(fill="x", padx=10, pady=5)

def set_stop():
    global stop_flag
    stop_flag = True
    status_var.set("正在停止...")

def start_scan_thread():
    # 校验输入
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

    if xs <= xe:
        messagebox.showerror("参数错误", "X 轴起点必须大于终点。")
        return
    if ys >= ye:
        messagebox.showerror("参数错误", "Y 轴起点必须小于终点。")
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
# 9. Esc 热键
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
