"""
2D CT 分块重叠扫描工具（Python + Tkinter GUI 版）
==================================================
功能：在图形界面中输入扫描范围、视野大小、重叠比例等参数，
      脚本自动计算所有扫描位置，并操控 CT 软件完成扫描和保存。
用法：1. 直接运行本脚本（或打包后的 exe）
      2. 在界面中填入参数
      3. 点击“开始扫描”
      4. 随时可按 Esc 键或点击“停止”按钮紧急中断
注意：本脚本的控件坐标、窗口标题等需要根据实际工业机修改。
"""

import tkinter as tk                # 自带GUI库，用于创建参数输入窗口
from tkinter import messagebox      # 弹出警告、确认、错误等对话框
import threading                     # 将扫描任务放到后台线程，避免界面卡死
import time                         # 用于等待控件响应、扫描延时等
import math                         # 计算扫描矩阵用到的 floor 取整

import pyautogui                    # 模拟键盘鼠标操作（点击、输入）
from pynput import keyboard as pynput_keyboard  # 监听全局热键（Esc停止扫描）

# ============================================================
# 用户配置区 —— 需要根据实际工业机上的CT软件界面修改
# ============================================================

# CT 软件的窗口标题（部分文字即可）以及进程名（用于精确查找窗口）
CT_WINDOW_TITLE = "Your CT Software"      # 例如 "XRay CT Scanner"
CT_PROCESS_NAME = "YourCTSoftware.exe"    # 备用，任务管理器里的进程名

# 软件界面内各个控件的坐标（都是窗口客户区的相对坐标，单位：像素）
# 后续会通过 client_to_screen() 转换成屏幕绝对坐标
xInputBox      = (150, 120)   # 填写 X 中心坐标的输入框位置
yInputBox      = (150, 160)   # 填写 Y 中心坐标的输入框位置
sizeInputBox   = (150, 200)   # 填写视野大小的输入框（如果软件不需要改视野，设为 None）
scanButton     = (400, 500)   # “开始扫描”按钮

# 保存文件涉及的三个控件
saveOpenBtn    = (320, 500)   # 主界面上的“保存”或“另存为”按钮
fileNameInput  = (200, 300)   # 保存对话框中的文件名输入框
saveConfirmBtn = (350, 400)   # 保存对话框中的“保存”按钮

# 窗口标题栏/菜单栏等造成的偏移量（像素）
# 原因：pygetwindow 返回的窗口坐标是包含标题栏的，而我们的客户区坐标是相对于内容区的
# 通常标题栏高度约 30~35px，根据你的软件实际微调
OFFSET_Y = 30

# ============================================================
# 全局控制变量
# ============================================================
stop_flag = False           # 是否按下了“停止”按钮或 Esc 键
window_region = None        # 存储当前目标窗口的位置和大小：(left, top, width, height)

# ============================================================
# 工具函数
# ============================================================

def get_window_region():
    """
    查找目标 CT 软件的窗口，并返回其屏幕位置和大小。
    会先尝试通过窗口标题查找，失败则用进程名查找。
    """
    windows = pyautogui.getWindowsWithTitle(CT_WINDOW_TITLE)
    if not windows:
        # 如果标题匹配不到，可以用进程名作为备选方案
        # 更健壮的做法是用 psutil 列举进程并找到对应窗口，这里先抛出异常
        raise Exception(f"未找到标题包含 '{CT_WINDOW_TITLE}' 的窗口，请检查配置")
    win = windows[0]                     # 取第一个匹配的窗口
    win.activate()                       # 把窗口提到最前面，确保后续操作落在上面
    time.sleep(0.3)                      # 等待窗口完全获得焦点
    return (win.left, win.top, win.width, win.height)


def client_to_screen(client_pos):
    """
    将窗口客户区（内容区域）坐标转换为屏幕绝对坐标。
    参数：client_pos - (x, y) 元组，表示控件在窗口客户区内的位置。
    返回：(screen_x, screen_y) 屏幕上的绝对坐标。
    """
    if window_region is None:
        raise Exception("窗口未定位，无法转换坐标")
    left, top, w, h = window_region
    # 屏幕 X = 窗口左上角 X + 控件客户区 X
    # 屏幕 Y = 窗口左上角 Y + 控件客户区 Y + 标题栏/菜单栏高度偏移
    return (left + client_pos[0], top + client_pos[1] + OFFSET_Y)


def set_text(pos, text):
    """
    在指定位置点击（应该是输入框），然后全选旧内容、删除，输入新文本。
    参数：
        pos  - 屏幕绝对坐标 (x, y)
        text - 要填入的字符串
    """
    pyautogui.click(pos[0], pos[1])      # 点击输入框，使其获得焦点
    time.sleep(0.1)
    pyautogui.hotkey('ctrl', 'a')        # 全选（Ctrl+A）
    pyautogui.press('backspace')         # 删除选中内容
    pyautogui.write(str(text))           # 输入新值

# ============================================================
# 扫描主逻辑（将在独立线程中运行）
# ============================================================

def run_scan(params):
    """
    根据传入的参数字典执行完整的扫描流程。
    参数 params 包含所有 GUI 中设置的变量，如 Xstart, Xend, FOV 等。
    该函数运行在后台线程，因此可以直接使用 time.sleep 而不阻塞界面。
    """
    global stop_flag, window_region
    stop_flag = False                    # 每次开始新扫描前重置停止标志

    # 从参数字典中提取各变量，方便后续使用
    xs = params['Xstart']                # X 轴第一个扫描中心坐标
    xe = params['Xend']                  # X 轴最后一个扫描中心坐标
    ys = params['Ystart']                # Y 轴第一个扫描中心坐标
    ye = params['Yend']                  # Y 轴最后一个扫描中心坐标
    fov = params['FOV']                  # 视野大小（正方形边长）
    ov = params['Overlap']               # 重叠比例（0~1，如 0.15 表示 15%）
    wait_sec = params['ScanSeconds']     # 每次扫描的固定等待时间（秒）
    prefix = params['FilePrefix']        # 文件名前缀

    try:
        # 1. 定位目标窗口并获取其位置
        window_region = get_window_region()

        # 2. 将所有配置的控件客户区坐标转换为屏幕绝对坐标
        xi = client_to_screen(xInputBox)
        yi = client_to_screen(yInputBox)
        si = client_to_screen(sizeInputBox) if sizeInputBox else None  # 视野输入框可能不需要
        sb = client_to_screen(scanButton)
        so = client_to_screen(saveOpenBtn)
        fi = client_to_screen(fileNameInput)
        sc = client_to_screen(saveConfirmBtn)

        # 3. 计算扫描矩阵
        step = fov * (1 - ov)            # 步进距离 = 视野大小 × (1 - 重叠比例)
        # X 方向扫描点数（列数）
        Nx = math.floor((xe - xs) / step) + 1
        # Y 方向扫描点数（行数）
        Ny = math.floor((ye - ys) / step) + 1
        total = Nx * Ny                  # 总扫描块数

        # 弹出确认对话框（注意：此处处于子线程，直接调用 messagebox 是安全的）
        if not messagebox.askyesno(
            "确认扫描",
            f"将扫描 {Nx} 列 × {Ny} 行 = {total} 个位置。\n"
            f"步距 = {step:.2f} mm\n\n是否开始？"
        ):
            return                      # 用户点“否”，放弃本次扫描

        # 4. 主循环：逐块扫描
        count = 0                       # 已经完成的块数
        for i in range(Nx):
            xc = xs + i * step          # 当前块的 X 中心坐标
            for j in range(Ny):
                # 每次循环前检查是否被要求停止
                if stop_flag:
                    return
                yc = ys + j * step      # 当前块的 Y 中心坐标
                count += 1

                # 更新主界面上的状态文字（通过 root.after 确保在 GUI 线程中更新）
                root.after(0, update_status, f"扫描 {count}/{total}  中心 X={xc:.1f}, Y={yc:.1f}")

                # 4.1 在软件中填入 X 坐标
                set_text(xi, xc)

                # 4.2 填入 Y 坐标
                set_text(yi, yc)

                # 4.3 填入视野大小（如果配置了该控件）
                if si:
                    set_text(si, fov)

                # 4.4 点击“开始扫描”按钮
                pyautogui.click(sb)

                # 4.5 等待扫描完成
                #     采用分段 sleep，这样可以在 wait_sec 期间仍然响应停止命令
                wait_remaining = wait_sec
                while wait_remaining > 0 and not stop_flag:
                    time.sleep(min(1, wait_remaining))  # 每次睡 1 秒，直到剩余时间小于 1 秒
                    wait_remaining -= 1
                if stop_flag:           # 如果是因为按下了停止，立刻退出
                    return

                # 4.6 保存文件（点击保存按钮 → 点击文件名输入框 → 输入编号 → 确认保存）
                pyautogui.click(so)                     # 打开保存对话框
                time.sleep(0.8)
                pyautogui.click(fi)                     # 聚焦文件名输入框
                time.sleep(0.2)
                pyautogui.hotkey('ctrl', 'a')           # 全选旧文件名
                pyautogui.press('backspace')            # 删除
                # 生成文件名：前缀 + 三位数字编号，例如 SampleA_001
                fname = f"{prefix}{count:03d}"
                pyautogui.write(fname)
                pyautogui.click(sc)                     # 点击保存
                time.sleep(1)                           # 等待文件写入磁盘

        # 全部扫描完成后的通知（注意切换到 GUI 线程）
        root.after(0, scan_complete, total)

    except Exception as e:
        root.after(0, lambda e=e: messagebox.showerror("错误", str(e)))

# ============================================================
# 界面更新回调（从子线程调用，实际在 GUI 主线程中执行）
# ============================================================

def update_status(msg):
    """更新 GUI 底部状态栏的文字"""
    status_var.set(msg)

def scan_complete(total):
    """扫描全部结束后弹出完成提示"""
    messagebox.showinfo("完成", f"全部扫描完成！共 {total} 块数据已保存。")
    status_var.set("就绪")              # 恢复状态栏文字

# ============================================================
# GUI 界面构建
# ============================================================

root = tk.Tk()
root.title("CT 分块扫描控制台")

# 状态栏变量（用于显示当前进度或提示信息）
status_var = tk.StringVar(value="就绪")

# 创建各项参数对应的 Tkinter 变量，并与输入框绑定
# 使用 DoubleVar 存储浮点数，StringVar 存储字符串
var_Xstart = tk.DoubleVar(value=10.0)
var_Xend   = tk.DoubleVar(value=78.0)
var_Ystart = tk.DoubleVar(value=5.0)
var_Yend   = tk.DoubleVar(value=55.0)
var_FOV    = tk.DoubleVar(value=20.0)
var_Overlap = tk.DoubleVar(value=0.15)
var_ScanSeconds = tk.DoubleVar(value=480)
var_FilePrefix  = tk.StringVar(value="SampleA_")

# ---- 参数输入区 ----
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

# 第四行：扫描时间
tk.Label(frame, text="扫描时间 (s):").grid(row=3, column=0, sticky="e")
tk.Entry(frame, textvariable=var_ScanSeconds, width=8).grid(row=3, column=1)

# ---- 文件保存配置 ----
frame2 = tk.LabelFrame(root, text="文件保存", padx=10, pady=10)
frame2.pack(padx=10, pady=5, fill="x")
tk.Label(frame2, text="文件名前缀:").pack(side="left")
tk.Entry(frame2, textvariable=var_FilePrefix, width=15).pack(side="left", padx=5)
tk.Label(frame2, text="(自动追加三位编号)").pack(side="left")

# ---- 操作按钮 ----
btn_frame = tk.Frame(root)
btn_frame.pack(pady=5)
tk.Button(btn_frame, text="开始扫描", width=12, command=lambda: start_scan_thread()).pack(side="left", padx=5)
tk.Button(btn_frame, text="停止", width=8, command=lambda: set_stop()).pack(side="left", padx=5)
tk.Button(btn_frame, text="退出", width=8, command=root.quit).pack(side="left", padx=5)

# ---- 状态栏 ----
tk.Label(root, textvariable=status_var, bd=1, relief="sunken", anchor="w").pack(
    fill="x", padx=10, pady=5
)

# ============================================================
# 停止处理函数
# ============================================================

def set_stop():
    """用户点击界面上的“停止”按钮时调用"""
    global stop_flag
    stop_flag = True
    status_var.set("正在停止...")

def start_scan_thread():
    """
    读取界面中的所有参数，进行基本合法性检查，
    然后在后台线程中启动扫描任务，避免长时间扫描导致 GUI 无响应。
    """
    # 收集参数
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

    # 合法性检查
    if params['Xend'] <= params['Xstart'] or params['Yend'] <= params['Ystart']:
        messagebox.showerror("参数错误", "终点必须大于起点")
        return
    if params['FOV'] <= 0 or params['Overlap'] < 0 or params['Overlap'] >= 1 or params['ScanSeconds'] <= 0:
        messagebox.showerror("参数错误", "视野>0，重叠比例0~1(不含1)，扫描时间>0")
        return

    # 创建并启动后台线程
    t = threading.Thread(target=run_scan, args=(params,), daemon=True)
    t.start()

# ============================================================
# 全局热键监听（Esc 键紧急停止）
# ============================================================

# 监听键盘按键的后台线程，按 Esc 时设置 stop_flag
def on_press(key):
    global stop_flag
    if key == pynput_keyboard.Key.esc:
        stop_flag = True
        # 通过 root.after 安全更新状态栏
        root.after(0, lambda: status_var.set("紧急停止！"))

# 启动监听器
listener = pynput_keyboard.Listener(on_press=on_press)
listener.start()

# ============================================================
# 启动 GUI 主循环
# ============================================================
root.mainloop()
