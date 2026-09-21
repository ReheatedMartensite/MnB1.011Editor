#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
骑砍 1.011 工具箱 —— 启动器 (双击本文件即可)

用法:
    双击运行            -> 弹出选择窗口, 点按钮启动对应工具
    命令行              -> python run_all.py 1        (1..4 直接启动)
    指定模块目录        -> 设置环境变量 MB_MODULE_DIR, 或在工具里手动选择一次

四个工具 (两套独立程序, 各分中/英):
    1. 兵种/物品查看器 (中文)  编辑 troops.txt / item_kinds1.txt  —— 抽象模板
    2. Troop/Item Viewer (EN) 同上, 界面与名称均为英文
    3. 存档修改器 (中文)       编辑 .sav 二进制存档实例
    4. Save Editor (EN)       同上, 英文界面
"""
import os
import sys
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))

TOOLS = [
    ("兵种 / 物品查看器（中文）\n编辑 troops.txt / item_kinds1.txt（抽象模板）",
     "兵种物品查看器_v2.py"),
    ("Troop / Item Viewer (English)\nEdit troops.txt / item_kinds1.txt (templates)",
     "troop_item_viewer_v2_EN.py"),
    ("存档修改器（中文）\n编辑 .sav 存档（兵种/部队/阵营/背包/装备）",
     "mb_editor_gui.py"),
    ("Save Editor (English)\nEdit .sav saves (troops / parties / factions / inventory)",
     "mb_editor_gui_EN.py"),
]


def launch(idx):
    name = TOOLS[idx][1]
    path = os.path.join(HERE, name)
    if not os.path.exists(path):
        raise SystemExit("找不到文件: %s" % path)
    subprocess.Popen([sys.executable, path], cwd=HERE)


def main_gui():
    import tkinter as tk
    from tkinter import ttk

    root = tk.Tk()
    root.title("骑砍 1.011 工具箱 / Mount & Blade 1.011 Toolkit")
    root.geometry("620x380")
    ttk.Label(root, text="请选择要启动的工具 / Choose a tool:",
              font=("Microsoft YaHei UI", 11)).pack(pady=10)

    def mk(i):
        ttk.Button(root, text=TOOLS[i][0], command=lambda: launch(i)).pack(fill="x", padx=20, pady=5)

    for i in range(len(TOOLS)):
        mk(i)

    ttk.Label(root,
              text="提示: 首次运行若未找到模组目录, 工具会请你手动选择一次, 之后自动记住。",
              foreground="#666").pack(pady=8)
    ttk.Button(root, text="退出 / Quit", command=root.destroy).pack(pady=6)
    root.mainloop()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1].isdigit():
        n = int(sys.argv[1]) - 1
        if 0 <= n < len(TOOLS):
            launch(n)
            raise SystemExit(0)
    main_gui()
