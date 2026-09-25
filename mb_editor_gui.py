#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
骑砍 1.011 存档修改器  --  GUI 版 (兵种 Troops + 部队 Parties)
================================================================
功能:
  [兵种] 浏览/编辑全部兵种(troop/hero)记录 (0..793, 共 794 条)
        - 四维属性 / 七项武器熟练 / 48 项技能 / 等级 / 经验 / 未用点数
        - 64 格物品栏 + 10 格装备槽 (修改"该兵种当前携带物品")
        - 性格(personality / 兵种槽128, 对应战团 reputation_type) 编辑
        - 简介(bio)
  [部队] 浏览/编辑全部部队(party)记录 (模块部队 + 动态部队)
        - 显示并修改每条部队的兵种堆叠 (兵种 + 数量)
        - 支持增删堆叠、改兵种类型、改数量
  - 写回存档(自动备份), 就地修改, 安全(变长操作带重定位+回滚校验)
依赖: mb_model.py (已验证可精确遍历 794 兵种 + 577+ 部队)
"""
import os
import sys
import json
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
import mb_model as M


def enable_dpi_awareness():
    """Windows 高 DPI: 声明系统 DPI 感知, 避免非整数缩放下文本漂移/点击错位。"""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)   # system DPI aware
        except Exception:
            ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

# 候选 Modules 根目录 (仅作兜底; 实际用 M.modules_roots(), 它还包含
# 环境变量 MB_MODULES_DIR、注册表/Steam 库探测, 便于拷到他人机器上使用)
STANDARD_MB = [
    r"E:\Program Files\Game\Mount&Blade\Modules",
    r"D:\Program Files\Game\Mount&Blade\Modules",
    r"C:\Program Files\Game\Mount&Blade\Modules",
    r"E:\Steam\steamapps\common\MountBlade\Modules",
]
CFG_FILE = Path(__file__).with_name("mb_editor_module.json")   # 旧配置, 仅作迁移读取


# ----------------------------------------------------------------------------
# 可滚动容器
# ----------------------------------------------------------------------------
class ScrolledFrame(ttk.Frame):
    """内部放一个可垂直滚动的 Frame."""
    def __init__(self, master, **kw):
        super().__init__(master, **kw)
        self.canvas = tk.Canvas(self, highlightthickness=0)
        self.vsb = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.vsb.set)
        self.vsb.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.inner = ttk.Frame(self.canvas)
        self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", lambda e: self.canvas.configure(
            scrollregion=self.canvas.bbox("all")))


def safe_int(var, default=None):
    """安全读取 tk 数值变量 (IntVar / StringVar), 空值或非法值返回 default, 绝不抛异常。

    背景: ttk.Spinbox 在用户「清空输入框准备重填」的瞬间会把 textvariable 置为 ""，
    此时 IntVar.get() 会抛 _tkinter.TclError: expected integer but got ""，
    而 trace_add("write") 回调随即炸出一整页 traceback（控制台刷屏）。
    这里统一吸收: 空/非法 → default（调用方据此跳过本次写入, 而不是写成 0）。
    """
    try:
        v = var.get()
    except Exception:
        return default          # TclError / 变量已失效
    if isinstance(v, str):
        v = v.strip()
        if v == "":
            return default
    try:
        return int(v)
    except Exception:
        return default


# ----------------------------------------------------------------------------
# 主应用
# ----------------------------------------------------------------------------
class App(tk.Tk):
    def __init__(self, module_arg=None):
        super().__init__()
        # 兜底: 未捕获的 Tk 回调异常只报一行, 不再往控制台刷整页 traceback
        self.report_callback_exception = self._on_tk_error
        self.title("骑砍 1.011 存档修改器 v3.4  (WD - Minuet 适配)")
        self.geometry("1200x780")
        self._forced_module = module_arg      # --module 指定 / 环境变量
        self.doc = M.SaveDoc()
        self.cur_idx = None          # 当前选中的兵种索引
        self.cur_party = None        # 当前选中的部队索引
        self.inv_widgets = []        # 物品栏槽控件
        self.equip_widgets = []      # 装备槽控件
        self.skill_vals = [0] * M.NSKILL
        self.pstack_widgets = []      # 部队堆叠行控件
        self.item_options = ["空 (-1)"]
        self.troop_options = ["空 (-1)"]
        self._building = False
        self._build_ui()
        self._set_status("未载入存档。点击「打开存档」。")

    # ---------------- UI 构建 ----------------
    def _build_ui(self):
        # 工具栏
        bar = ttk.Frame(self)
        bar.pack(side="top", fill="x", padx=4, pady=4)
        ttk.Button(bar, text="打开存档", command=self.open_save).pack(side="left")
        ttk.Button(bar, text="保存", command=self.do_save).pack(side="left", padx=2)
        ttk.Button(bar, text="另存为", command=self.do_save_as).pack(side="left", padx=2)
        ttk.Button(bar, text="备份", command=self.do_backup).pack(side="left", padx=2)
        self.mod_label = ttk.Label(bar, text="模块: (未载入)")
        self.mod_label.pack(side="left", padx=8)
        self.rec_label = ttk.Label(bar, text="兵种: 0  部队: 0")
        self.rec_label.pack(side="left")

        # 顶层: 兵种 / 部队 两个并列页签
        self.mode = ttk.Notebook(self)
        self.mode.pack(side="top", fill="both", expand=True, padx=4, pady=4)
        self.troop_tab = ttk.Frame(self.mode)
        self.party_tab = ttk.Frame(self.mode)
        self.mode.add(self.troop_tab, text="兵种 (Troops)")
        self.mode.add(self.party_tab, text="部队 (Parties)")
        self._build_troop_tab(self.troop_tab)
        self._build_party_tab(self.party_tab)

        # 状态栏
        self.status = ttk.Label(self, text="", relief="sunken", anchor="w")
        self.status.pack(side="bottom", fill="x")

    # ---- 兵种页 ----
    def _build_troop_tab(self, parent):
        main = ttk.PanedWindow(parent, orient="horizontal")
        main.pack(fill="both", expand=True, padx=4, pady=4)
        left = ttk.Frame(main, width=330)
        left.pack_propagate(False)     # 固定宽度, 防止内容变化引起窗格/分隔条漂移
        main.add(left, weight=0)

        f = ttk.Frame(left); f.pack(side="top", fill="x", padx=2, pady=2)
        ttk.Label(f, text="筛选:").pack(side="left")
        self.filter_var = tk.StringVar()
        self._filter_job = None
        self.filter_var.trace_add("write", lambda *a: self._debounce(self.refresh_list, "_filter_job"))
        ttk.Entry(f, textvariable=self.filter_var).pack(side="left", fill="x", expand=True)

        jf = ttk.Frame(left); jf.pack(side="top", fill="x", padx=2, pady=2)
        ttk.Label(jf, text="跳转索引:").pack(side="left")
        self.jump_var = tk.StringVar()
        ej = ttk.Entry(jf, textvariable=self.jump_var, width=10); ej.pack(side="left")
        ej.bind("<Return>", lambda *a: self.jump_to_troop())
        ttk.Button(jf, text="跳转", command=self.jump_to_troop).pack(side="left")

        lf = ttk.Frame(left); lf.pack(side="top", fill="both", expand=True, padx=2, pady=2)
        self.listbox = tk.Listbox(lf, font=("Consolas", 9), exportselection=False)
        self.lyscroll = ttk.Scrollbar(lf, orient="vertical", command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=self.lyscroll.set)
        self.listbox.pack(side="left", fill="both", expand=True)
        self.lyscroll.pack(side="right", fill="y")
        self.listbox.bind("<<ListboxSelect>>", self.on_select)
        # 禁用 Shift+滚轮横向滚动 —— 横向视图一旦被推走, 文本起点左移且视觉与选择错位
        self.listbox.bind("<Shift-MouseWheel>", lambda e: "break")
        self.count_label = ttk.Label(left, text="")
        self.count_label.pack(side="bottom", fill="x")

        right = ttk.Frame(main, width=620)
        main.add(right, weight=1)
        self.nb = ttk.Notebook(right)
        self.nb.pack(fill="both", expand=True)
        self.tab_basic = ttk.Frame(self.nb)
        self.tab_skill = ttk.Frame(self.nb)
        self.tab_inv = ttk.Frame(self.nb)
        self.tab_equip = ttk.Frame(self.nb)
        self.tab_bio = ttk.Frame(self.nb)
        self.nb.add(self.tab_basic, text="基础")
        self.nb.add(self.tab_skill, text="技能(48)")
        self.nb.add(self.tab_inv, text="物品栏(64)")
        self.nb.add(self.tab_equip, text="装备(10)")
        self.nb.add(self.tab_bio, text="简介")
        self._build_basic()
        self.skill_scroll = ScrolledFrame(self.tab_skill)
        self.skill_scroll.pack(fill="both", expand=True)
        self.inv_scroll = ScrolledFrame(self.tab_inv)
        self.inv_scroll.pack(fill="both", expand=True)
        self.equip_scroll = ScrolledFrame(self.tab_equip)
        self.equip_scroll.pack(fill="both", expand=True)
        self.bio_text = scrolledtext.ScrolledText(self.tab_bio, wrap="word", font=("Consolas", 10))
        self.bio_text.pack(fill="both", expand=True)
        self.bio_text.bind("<<Modified>>", self.on_bio_modified)

    def _build_basic(self):
        f = self.tab_basic
        self.attr_vars = [tk.IntVar() for _ in range(4)]
        self.prof_vars = [tk.IntVar() for _ in range(7)]
        self.level_var = tk.IntVar()
        self.xp_var = tk.IntVar()
        self.pts_var = tk.IntVar()
        row = ttk.Frame(f); row.pack(fill="x", padx=6, pady=6)
        ttk.Label(row, text="四维属性", font=("SimSun", 11, "bold")).pack(anchor="w")
        names = ["力量(STR)", "敏捷(AGI)", "智力(INT)", "魅力(CHA)"]
        for i, nm in enumerate(names):
            r = ttk.Frame(f); r.pack(fill="x", padx=16, pady=1)
            ttk.Label(r, text=nm, width=14).pack(side="left")
            sp = ttk.Spinbox(r, from_=0, to=300, width=8,
                             textvariable=self.attr_vars[i],
                             command=lambda i=i: self.on_attr(i))
            sp.pack(side="left")
            self.attr_vars[i].trace_add("write", lambda *a, i=i: self.on_attr(i))
        r2 = ttk.Frame(f); r2.pack(fill="x", padx=6, pady=(10, 2))
        ttk.Label(r2, text="武器熟练 (0-700)", font=("SimSun", 11, "bold")).pack(anchor="w")
        pnames = ["单手", "双手", "长杆", "弓", "弩", "投掷", "火器"]
        for i, nm in enumerate(pnames):
            r = ttk.Frame(f); r.pack(fill="x", padx=16, pady=1)
            ttk.Label(r, text=nm, width=14).pack(side="left")
            sp = ttk.Spinbox(r, from_=0, to=700, width=8,
                             textvariable=self.prof_vars[i],
                             command=lambda i=i: self.on_prof(i))
            sp.pack(side="left")
            self.prof_vars[i].trace_add("write", lambda *a, i=i: self.on_prof(i))
        r3 = ttk.Frame(f); r3.pack(fill="x", padx=6, pady=(10, 2))
        ttk.Label(r3, text="其它", font=("SimSun", 11, "bold")).pack(anchor="w")
        for nm, var, lo, hi, cb in [
            ("等级 (1-63)", self.level_var, 1, 63, self.on_level),
            ("经验 (XP)", self.xp_var, 0, 2147483647, self.on_xp),
            ("未用点数", self.pts_var, 0, 2147483647, self.on_pts),
        ]:
            r = ttk.Frame(f); r.pack(fill="x", padx=16, pady=1)
            ttk.Label(r, text=nm, width=14).pack(side="left")
            sp = ttk.Spinbox(r, from_=lo, to=hi, width=12, textvariable=var, command=cb)
            sp.pack(side="left")
            var.trace_add("write", lambda *a, c=cb: c())

        # ---- 标识信息: 阵营 / 标志位(tf_hero 等) / 槽 ----
        r4 = ttk.Frame(f); r4.pack(fill="x", padx=6, pady=(12, 2))
        ttk.Label(r4, text="标识信息", font=("SimSun", 11, "bold")).pack(anchor="w")
        self.info_faction = tk.StringVar(value="—")
        self.info_flags = tk.StringVar(value="—")
        self.info_hero = tk.StringVar(value="—")
        self.info_slots = tk.StringVar(value="—")
        # 阵营(模板, 只读) + 存档当前阵营(可改 —— 即领主跳槽)
        r = ttk.Frame(f); r.pack(fill="x", padx=16, pady=1)
        ttk.Label(r, text="阵营(模板)", width=14).pack(side="left")
        ttk.Label(r, textvariable=self.info_faction, wraplength=300,
                  justify="left").pack(side="left", anchor="w")
        r = ttk.Frame(f); r.pack(fill="x", padx=16, pady=1)
        ttk.Label(r, text="存档阵营", width=14).pack(side="left")
        self.fac_combo = ttk.Combobox(r, state="readonly", width=28)
        self.fac_combo.pack(side="left")
        self.fac_apply_btn = ttk.Button(r, text="仅改归属", command=self._apply_faction)
        self.fac_apply_btn.pack(side="left", padx=2)
        self.fac_defect_btn = ttk.Button(r, text="执行跳槽", command=self._do_defect)
        self.fac_defect_btn.pack(side="left", padx=2)
        self.fac_hint = ttk.Label(r, text="", foreground="#888")
        self.fac_hint.pack(side="left", padx=6)
        ttk.Label(f, text="『执行跳槽』会同时改领主归属与其部队归属(等价于游戏跳槽脚本); "
                          "『仅改归属』只改领主记录, 部队仍会替旧阵营打仗。",
                  foreground="#a06a00", wraplength=760,
                  justify="left").pack(fill="x", padx=16, pady=(2, 4))
        # 提拔为领主 (复刻 create_kingdom_hero_party + give_center_to_lord): 给无部队英雄创建部队/封地
        r = ttk.Frame(f); r.pack(fill="x", padx=16, pady=1)
        ttk.Label(r, text="提拔为领主", width=14).pack(side="left")
        self.center_combo = ttk.Combobox(r, state="readonly", width=32)
        self.center_combo.pack(side="left")
        self.promote_btn = ttk.Button(r, text="提拔为领主", command=self._do_promote)
        self.promote_btn.pack(side="left", padx=2)
        ttk.Label(r, text="(给选中英雄建部队, 可选封地; 等价于游戏复国/分封脚本)",
                  foreground="#888").pack(side="left", padx=6)
        for nm, var in (("英雄", self.info_hero), ("槽(尾部)", self.info_slots)):
            r = ttk.Frame(f); r.pack(fill="x", padx=16, pady=1)
            ttk.Label(r, text=nm, width=14).pack(side="left")
            ttk.Label(r, textvariable=var, wraplength=620, justify="left").pack(side="left", anchor="w")
        # 性格(personality / 槽128): 1.011 领主与战团一样有性格, 存于兵种槽128
        # (= 战团 slot_troop_reputation_type)。非活跃领主内联可读可改;
        # 活跃领主实时槽在存档另一区段(尚未解码), 此处只能显示提示。
        self.pers_options = ["（未设置 0）"] + ["%d %s" % (k, n)
                                                for k, n in M.SaveDoc.PERSONALITY_NAMES.items()]
        r = ttk.Frame(f); r.pack(fill="x", padx=16, pady=1)
        ttk.Label(r, text="性格(slot128)", width=14).pack(side="left")
        self.pers_combo = ttk.Combobox(r, state="readonly", width=30)
        self.pers_combo["values"] = self.pers_options
        self.pers_combo.pack(side="left")
        self.pers_combo.bind("<<ComboboxSelected>>", lambda ev: self.on_personality())
        self.pers_note = ttk.Label(r, text="", foreground="#888")
        self.pers_note.pack(side="left", padx=6)
        # 活跃状态(occupation / 槽2) + 囚禁状态(槽8): 槽号与枚举由本模组
        # simple_triggers 重生触发器 + game_start 实证得出。
        self.occ_options = ["0 %s" % M.SaveDoc.OCCUPATION_NAMES[0]] + [
            "%d %s" % (k, n) for k, n in sorted(M.SaveDoc.OCCUPATION_NAMES.items()) if k != 0]
        r = ttk.Frame(f); r.pack(fill="x", padx=16, pady=1)
        ttk.Label(r, text="活跃状态(槽2)", width=14).pack(side="left")
        self.occ_combo = ttk.Combobox(r, state="readonly", width=34)
        self.occ_combo["values"] = self.occ_options
        self.occ_combo.pack(side="left")
        self.occ_combo.bind("<<ComboboxSelected>>", lambda ev: self.on_occupation())
        ttk.Label(r, text="囚禁(槽8)").pack(side="left", padx=(8, 0))
        self.pris_var = tk.StringVar(value="-1")
        self.pris_spin = ttk.Spinbox(r, from_=-1, to=2147483647, width=8,
                                     textvariable=self.pris_var)
        self.pris_spin.pack(side="left", padx=2)
        self.pris_spin.bind("<Return>", lambda ev: self.on_prisoner())
        self.pris_spin.bind("<FocusOut>", lambda ev: self.on_prisoner())
        ttk.Button(r, text="设为囚禁/释放", command=self.on_prisoner).pack(side="left", padx=2)
        self.occ_note = ttk.Label(r, text="", foreground="#888")
        self.occ_note.pack(side="left", padx=6)
        # 标志位: 只读解码 + 可编辑十六进制
        r = ttk.Frame(f); r.pack(fill="x", padx=16, pady=1)
        ttk.Label(r, text="标志位", width=14).pack(side="left")
        ttk.Label(r, textvariable=self.info_flags, wraplength=470,
                  justify="left").pack(side="left", anchor="w")
        rf = ttk.Frame(f); rf.pack(fill="x", padx=16, pady=1)
        ttk.Label(rf, text="改写(16进制)", width=14).pack(side="left")
        self.flags_var = tk.StringVar()
        e = ttk.Entry(rf, textvariable=self.flags_var, width=14)
        e.pack(side="left")
        e.bind("<Return>", lambda ev: self.on_flags())
        ttk.Button(rf, text="应用", command=self.on_flags).pack(side="left", padx=4)
        ttk.Button(rf, text="切换英雄(tf_hero)",
                   command=self.toggle_hero).pack(side="left", padx=4)
        # ---- 标志位勾选面板 (程序在 勾选项 ↔ 数值 间自动转换) ----
        # 编辑对象 = 存档实例的兵种 flags (与兵种物品查看器编辑 troops.txt 模板是两回事)
        tff = ttk.LabelFrame(f, text="标志位 tf_* 勾选 (存档实例, 改完即时写入)")
        tff.pack(fill="x", padx=16, pady=(2, 4))
        self.flag_calc = ttk.Label(tff, text="当前值: 0x00000000 (十进制 0)", foreground="#555")
        self.flag_calc.pack(side="top", fill="x", padx=6, pady=(3, 0))
        fgrid = ttk.Frame(tff)
        fgrid.pack(side="top", fill="x", padx=6, pady=2)
        self.tf_vars = []
        for k, (mask, name) in enumerate(M.TF_NAMES):
            var = tk.BooleanVar()
            cb = ttk.Checkbutton(fgrid, text=name, variable=var,
                                 command=self._on_flags_check)
            cb.grid(row=k // 3, column=k % 3, sticky="w", padx=4, pady=1)
            self.tf_vars.append((mask, var))
        ttk.Button(tff, text="应用勾选", command=self._on_flags_check_apply).pack(side="top", padx=6, pady=(2, 4))

    def on_flags(self):
        """改写兵种标志位 (支持 0x 十六进制或十进制)。"""
        if not self._can_edit(): return
        s = self.flags_var.get().strip()
        if not s: return
        try:
            v = int(s, 16) if s.lower().startswith("0x") else int(s)
        except ValueError:
            self._set_status("标志位必须是整数或 0x 开头的十六进制数", warn=True)
            return
        self.doc.set_flags(self.cur_idx, v)
        self._fill_basic(self.cur_idx)
        self._set_status("标志位已改为 0x%08X" % v, ok=True)

    def toggle_hero(self):
        """切换 tf_hero —— 英雄不会被杀死、可独立加点、队伍技能生效、显示血量百分比。"""
        if not self._can_edit(): return
        cur = self.doc.get_flags(self.cur_idx)
        new = cur ^ M.TF_HERO
        self.doc.set_flags(self.cur_idx, new)
        self._fill_basic(self.cur_idx)
        self._set_status(("已设为英雄" if new & M.TF_HERO else "已取消英雄") + " (0x%08X)" % new, ok=True)

    def _on_flags_check(self):
        """勾选变化: 实时刷新『当前值』显示, 并同步十六进制输入框 (不改写文件)。
        保留不在勾选表里的未知位, 避免丢失数据。"""
        if not getattr(self, "tf_vars", None):
            return
        known_all = 0
        for mask, _ in self.tf_vars:
            known_all |= mask
        base = 0
        try:
            base = self.doc.get_flags(self.cur_idx) & ~known_all & 0xFFFFFFFF
        except Exception:
            base = 0
        v = base
        for mask, var in self.tf_vars:
            if var.get():
                v |= mask
        try:
            self.flags_var.set("0x%08X" % (v & 0xFFFFFFFF))
            self.flag_calc.config(
                text="当前值: 0x%08X (十进制 %d)  解析: %s"
                % (v & 0xFFFFFFFF, v & 0xFFFFFFFF, ", ".join(M.decode_tf(v)) or "—"))
        except Exception:
            pass

    def _on_flags_check_apply(self):
        """把勾选结果写入存档兵种标志位 (即时生效)。
        不在勾选表里的未知位原样保留。"""
        if not self._can_edit(): return
        if not getattr(self, "tf_vars", None): return
        known_all = 0
        for mask, _ in self.tf_vars:
            known_all |= mask
        base = self.doc.get_flags(self.cur_idx) & ~known_all & 0xFFFFFFFF
        v = base
        for mask, var in self.tf_vars:
            if var.get():
                v |= mask
        try:
            self.doc.set_flags(self.cur_idx, v)
        except Exception as ex:
            self._set_status("标志位写入失败: %s" % ex, warn=True)
            return
        self._fill_basic(self.cur_idx)
        self._set_status("标志位已改为 0x%08X (存档即时生效)" % (v & 0xFFFFFFFF), ok=True)

    # ---- 部队页 ----
    def _build_party_tab(self, parent):
        main = ttk.PanedWindow(parent, orient="horizontal")
        main.pack(fill="both", expand=True, padx=4, pady=4)
        left = ttk.Frame(main, width=360)
        left.pack_propagate(False)     # 固定宽度, 防止窗格漂移
        main.add(left, weight=0)

        f = ttk.Frame(left); f.pack(side="top", fill="x", padx=2, pady=2)
        ttk.Label(f, text="筛选:").pack(side="left")
        self.pfilter_var = tk.StringVar()
        self._pfilter_job = None
        self.pfilter_var.trace_add("write", lambda *a: self._debounce(self.refresh_party_list, "_pfilter_job"))
        ttk.Entry(f, textvariable=self.pfilter_var).pack(side="left", fill="x", expand=True)

        jf = ttk.Frame(left); jf.pack(side="top", fill="x", padx=2, pady=2)
        ttk.Label(jf, text="跳转索引:").pack(side="left")
        self.pjump_var = tk.StringVar()
        ej = ttk.Entry(jf, textvariable=self.pjump_var, width=10); ej.pack(side="left")
        ej.bind("<Return>", lambda *a: self.jump_to_party())
        ttk.Button(jf, text="跳转", command=self.jump_to_party).pack(side="left")

        lf = ttk.Frame(left); lf.pack(side="top", fill="both", expand=True, padx=2, pady=2)
        self.plistbox = tk.Listbox(lf, font=("Consolas", 9), exportselection=False)
        self.pyscroll = ttk.Scrollbar(lf, orient="vertical", command=self.plistbox.yview)
        self.plistbox.configure(yscrollcommand=self.pyscroll.set)
        self.plistbox.pack(side="left", fill="both", expand=True)
        self.pyscroll.pack(side="right", fill="y")
        self.plistbox.bind("<<ListboxSelect>>", self.on_party_select)
        self.plistbox.bind("<Shift-MouseWheel>", lambda e: "break")
        self.pcount_label = ttk.Label(left, text="")
        self.pcount_label.pack(side="bottom", fill="x")

        right = ttk.Frame(main, width=600)
        main.add(right, weight=1)
        self._build_party_detail(right)

    def _build_party_detail(self, right):
        self.party_detail = ttk.Frame(right)
        self.party_detail.pack(fill="both", expand=True)
        self.pheader = ttk.Frame(self.party_detail)
        self.pheader.pack(side="top", fill="x", padx=6, pady=4)
        self.plabel_id = ttk.Label(self.pheader, text="(未选择部队)", font=("SimSun", 10, "bold"))
        self.plabel_id.pack(anchor="w")
        # 归属阵营 (party fields[4]) —— 城池/城堡/村庄改此即转移领地归属
        fr = ttk.Frame(self.pheader); fr.pack(anchor="w", pady=(2, 0))
        ttk.Label(fr, text="归属阵营:").pack(side="left")
        self.pfac_combo = ttk.Combobox(fr, state="readonly", width=30)
        self.pfac_combo.pack(side="left", padx=2)
        self.pfac_btn = ttk.Button(fr, text="应用", command=self._apply_party_faction)
        self.pfac_btn.pack(side="left", padx=2)
        self.pfac_hint = ttk.Label(fr, text="", foreground="#888")
        self.pfac_hint.pack(side="left", padx=6)
        ttk.Label(fr, text="(城池/城堡/村庄改此可转移领地归属)",
                  foreground="#a06a00", font=("SimSun", 9)).pack(side="left", padx=6)
        ttk.Label(self.pheader,
                  text="每条堆叠 = 兵种 + 数量 (flag 为内部标记, 一般保持 0)",
                  font=("SimSun", 9)).pack(anchor="w")
        self.pstack_scroll = ScrolledFrame(self.party_detail)
        self.pstack_scroll.pack(fill="both", expand=True, padx=4, pady=2)
        # 新增堆叠行
        af = ttk.Frame(self.party_detail); af.pack(side="bottom", fill="x", padx=6, pady=4)
        ttk.Label(af, text="新增堆叠 兵种:").pack(side="left")
        self.padd_cmb = ttk.Combobox(af, values=self.troop_options, width=42, state="readonly")
        self.padd_cmb.pack(side="left", padx=2)
        ttk.Label(af, text="数量:").pack(side="left")
        self.padd_num = tk.IntVar(value=1)
        ttk.Spinbox(af, from_=1, to=100000, width=8, textvariable=self.padd_num).pack(side="left", padx=2)
        ttk.Button(af, text="添加", command=self.add_party_stack_ui).pack(side="left", padx=2)

    # ---------------- 载入 ----------------
    def _guess_folder(self, p):
        """定位存档所属模块目录 (可移植优先级):
           1) --module 参数 / 环境变量 MB_MODULE_DIR
           2) 由存档路径推断 (候选目录名 x 所有候选 Modules 根)
           3) 上次记忆 (mb_module_path.json, 兼容旧 mb_editor_module.json)
           4) 自动探测 (唯一命中才用, 多个则交给用户选, 避免猜错)
        """
        forced = getattr(self, "_forced_module", None) or os.environ.get("MB_MODULE_DIR")
        if forced and (Path(forced) / "troops.txt").exists():
            return Path(forced)

        cands = []
        if p.parent.name.lower() == "save":
            cands.append(p.parent.parent.name)
        cands.append(p.parent.name)
        cands.append(p.parent.parent.name)
        for base in M.modules_roots():
            for nm in cands:
                if nm and nm.lower() not in ("", "save"):
                    cand = Path(base) / nm
                    if (cand / "troops.txt").exists():
                        return cand

        last = M.get_last_module()
        if last:
            return Path(last)

        found = M.discover_modules()
        if len(found) == 1:
            return Path(list(found.values())[0])
        return None

    def _load_cfg(self):
        return M.get_last_module()

    def _save_cfg(self, folder):
        M.remember_module(folder)

    def open_save(self):
        init = M.get_last_save_dir() or M.initial_dir_for_module_dialog()
        path = filedialog.askopenfilename(
            title="选择骑砍 1.011 存档 (.sav)", initialdir=init,
            filetypes=[("Save files", "*.sav"), ("All", "*.*")])
        if not path:
            return
        M.remember_save_dir(path)
        self.load_save(path)

    def load_save(self, path):
        folder = self._guess_folder(Path(path))
        if folder and not self.doc.mod.loaded:
            self.doc.load_module(folder)
            self.mod_label.config(text="模块: " + os.path.basename(str(folder)))
            self._save_cfg(folder)
        elif not self.doc.mod.loaded:
            mf = filedialog.askdirectory(title="选择模块目录 (含 troops.txt)",
                                         initialdir=M.initial_dir_for_module_dialog())
            if mf:
                self.doc.load_module(mf)
                self._save_cfg(mf)
                self.mod_label.config(text="模块: " + os.path.basename(mf))
        if not self.doc.mod.loaded:
            messagebox.showerror("错误", "无法载入模块数据 (troops.txt 等)。")
            return
        self.doc.path = Path(path)
        self.doc.load_save(path)
        self._build_item_options()
        # 换存档/模组后阵营下拉选项需重建
        self.faction_options = None
        # 槽位行只建一次: 若已存在, 仅同步下拉选项列表
        if getattr(self, "_rows_built", False):
            for w in self.inv_widgets + self.equip_widgets:
                w["cmb"]["values"] = self.item_options
        self._party_infos = None    # 部队信息缓存失效
        n_t = len(self.doc.starts)
        n_p = self.doc.party_count()
        self.rec_label.config(text="兵种: %d  部队: %d" % (n_t, n_p))
        if n_t != (len(self.doc.mod.troops) or 794):
            self._set_status("警告: 兵种记录数=%d (期望 %d)" % (n_t, len(self.doc.mod.troops) or 794), warn=True)
        else:
            self._set_status("已载入: %s  兵种=%d 部队=%d  英雄完整记录=%d"
                         % (os.path.basename(path), n_t, n_p, len(self.doc.hero_offs)))
        self.refresh_list()
        self.refresh_party_list()
        self._select(0)
        if n_p > 0:
            self._select_party(0)

    def _build_item_options(self):
        # 物品下拉选项: 0 = 空(-1), 其后 0..nitems
        opts = ["空 (-1)"]
        for i in range(self.doc.nitems + 1):
            opts.append("%d  %s" % (i, self.doc.mod.item_name(i)))
        self.item_options = opts
        # 兵种下拉选项 (部队堆叠用): 0 = 空(-1), 其后 0..ntroops-1
        topts = ["空 (-1)"]
        for i in range(len(self.doc.mod.troops)):
            topts.append("%d  %s" % (i, self.doc.mod.troop_name(i)))
        self.troop_options = topts
        # 同步部队"新增堆叠"下拉框 (它在 UI 构建时用的占位列表)
        if hasattr(self, "padd_cmb"):
            self.padd_cmb["values"] = self.troop_options
        # 技能名称
        self.skill_names = []
        for k in range(M.NSKILL):
            sname = "技能#%d" % k
            if k < len(self.doc.mod.skills):
                sname = self.doc.mod.skill_names.get(self.doc.mod.skills[k], self.doc.mod.skills[k][4:])
            self.skill_names.append(sname)

    # ---------------- 兵种列表 ----------------
    def _debounce(self, fn, job_attr, delay_ms=250):
        """筛选输入防抖: 停止输入 250ms 后才执行重建, 避免每个按键全量刷新造成卡顿。"""
        if getattr(self, job_attr, None):
            self.after_cancel(getattr(self, job_attr))
        setattr(self, job_attr, self.after(delay_ms, fn))

    def refresh_list(self):
        if not self.doc.starts:
            return
        self.listbox.delete(0, tk.END)
        self._idx_map = []
        q = self.filter_var.get().strip().lower()
        for i in range(len(self.doc.starts)):
            nm = self.doc.mod.troop_name(i)
            if q and q not in nm.lower() and q not in str(i):
                continue
            lv = self.doc.view_level(i)
            # 只按 troops.txt 的 tf_hero 标志分类 —— 索引区间(1~201/203+)并不可靠:
            # 实测 idx0~5,134,141~146 等 46 个非同伴单位也带 tf_hero,
            # 而 idx672+ 有 93 个"领主"其实不是英雄。
            kind = ""
            if i == 0:
                kind = " [玩家]"
            elif self.doc.mod.troops[i].get("flags", 0) & M.TF_HERO:
                kind = " [英雄]"
            if not self.doc.is_reliable(i):
                kind += " (未生成)"
            self.listbox.insert(tk.END, "#%03d  Lv.%-3d  %s%s" % (i, lv, nm, kind))
            self._idx_map.append(i)
        self.count_label.config(text="显示 %d / %d" % (len(self._idx_map), len(self.doc.starts)))
        self.listbox.xview_moveto(0)   # 防横向漂移

    def on_select(self, ev):
        sel = self.listbox.curselection()
        if not sel:
            return
        self._select(self._idx_map[sel[0]])

    def jump_to_troop(self):
        try:
            idx = int(self.jump_var.get())
        except Exception:
            return
        if 0 <= idx < len(self.doc.starts):
            self._select(idx)
            self._set_status("已跳转到兵种 #%d" % idx)

    def _select(self, idx):
        if idx is None or idx >= len(self.doc.starts):
            return
        self.cur_idx = idx
        self._building = True
        try:
            self._fill_basic(idx)
            self._fill_skills(idx)
            self._fill_inventory(idx)
            self._fill_equipment(idx)
            self._fill_bio(idx)
        finally:
            self._building = False
        if getattr(self, "_idx_map", None):
            for pos, j in enumerate(self._idx_map):
                if j == idx:
                    self.listbox.selection_clear(0, tk.END)
                    self.listbox.selection_set(pos)
                    self.listbox.see(pos)
                    # 关键修复: see() 对比列表框更宽的条目会持续右推横向视图,
                    # 导致文本起点不断左移直至移出可视区 (视觉与点击错位的根源)
                    self.listbox.xview_moveto(0)
                    break
        self._set_status("已选中: #%d  %s" % (idx, self.doc.mod.troop_name(idx)))

    def _can_edit(self):
        """编辑守卫: 未选中 / 正在填充 / 定位不可靠(存档中无该兵种完整记录)时禁止。"""
        if self._building or self.cur_idx is None: return False
        if not self.doc.is_reliable(self.cur_idx):
            self._set_status("⚠ #%d %s: 存档中没有该兵种的完整记录(游戏尚未生成它), "
                             "无法可靠定位, 已禁止编辑以防写坏存档。"
                             % (self.cur_idx, self.doc.mod.troop_name(self.cur_idx)), warn=True)
            return False
        return True

    # ---------------- 兵种 基础页 ----------------
    def _fill_basic(self, idx):
        a, p, lv, xp, pts, fl = self.doc.view_basic(idx)
        for i in range(4): self.attr_vars[i].set(a[i])
        for i in range(7): self.prof_vars[i].set(p[i])
        self.level_var.set(lv)
        self.xp_var.set(xp)
        self.pts_var.set(pts)
        # ---- 标识信息 ----
        mod = self.doc.mod
        t = mod.troops[idx] if idx < len(mod.troops) else {}
        fi = t.get("faction", -1)
        if isinstance(fi, int) and 0 <= fi < len(mod.factions):
            fn = mod.factions[fi]
            self.info_faction.set("[%d] %s" % (fi, mod.faction_names.get(fn, fn)))
        else:
            self.info_faction.set("— (无/未知)")
        self._sync_faction_combo(idx, fi)
        self.flags_var.set("0x%08X" % fl)
        self.info_flags.set("0x%08X   %s" % (fl, ", ".join(M.decode_tf(fl))))
        # 用勾选面板反映当前存档实例 flags (程序 ↔ 数值 自动同步)
        try:
            for mask, var in getattr(self, "tf_vars", []):
                var.set(bool(fl & mask))
            self.flag_calc.config(
                text="当前值: 0x%08X (十进制 %d)  解析: %s"
                % (fl & 0xFFFFFFFF, fl & 0xFFFFFFFF, ", ".join(M.decode_tf(fl)) or "—"))
        except Exception:
            pass
        if self.doc.is_reliable(idx):
            self.info_hero.set("是" if self.doc.is_hero(idx) else "否")
            try:
                nz = self.doc.get_tail_nonzero(idx)
            except Exception:
                nz = []
            self.info_slots.set(("%d 个非零: %s" % (len(nz), nz[:10])) if nz
                                else "空 (普通兵种通常没有槽数据)")
        else:
            self.info_hero.set("⚠ 存档中无该兵种的完整记录(游戏尚未生成它); "
                               "以上显示的是 troops.txt 模板值, 仅供参考且禁止编辑")
            self.info_slots.set("⚠ 无完整记录, 无槽数据可读")
        self._fill_personality(idx)
        self._fill_occupation(idx)

    def _fill_personality(self, idx):
        """填充『性格(slot128)』下拉框 —— 1.011 领主性格槽。
        内联可读可改(非活跃领主); 活跃领主实时槽在另一区段(尚未解码), 仅提示。"""
        if getattr(self, "pers_combo", None) is None:
            return
        if not self.doc.is_reliable(idx):
            self.pers_combo.set("")
            self.pers_combo.config(state="disabled")
            self.pers_note.config(text="无完整记录, 不可读")
            return
        if not self.doc.has_inline_slots(idx):
            self.pers_combo.set("")
            self.pers_combo.config(state="disabled")
            self.pers_note.config(text="活跃领主: 性格槽在存档另一区段(尚未解码)")
            return
        v = self.doc.get_personality(idx)
        if v == 0:
            self.pers_combo.current(0)
        else:
            found = False
            for i, opt in enumerate(self.pers_options):
                if opt.startswith("%d " % v):
                    self.pers_combo.current(i); found = True; break
            if not found:
                self.pers_combo.set("（未知 %d）" % v)
        self.pers_combo.config(state="readonly")
        self.pers_note.config(text="原始槽值=%d" % v)

    def _fill_occupation(self, idx):
        """填充『活跃状态(槽2)』与『囚禁(槽8)』控件。

        枚举(由脚本实证): 0 不活跃 / 2 领主活跃 / 3 玩家同伴(在主角队伍) /
        4 宫廷贵妇 / 8 强盗骑士 / 11 已退休。

        可读性: 2026-09-20 起, 活跃领主的内联槽区已能在记录内定位(外观块之后、
        结束于记录尾), occupation(槽2) 可直接读取 —— 此前"全灰"的 bug 已解决。
        写安全性: 仅 slot_region_trusted() 通过(活跃领主须在其定位起点读到
        occupation==2)才允许改 occupation; 锚点不符的少数领主仍禁用并显推断值。
        囚禁(槽8)在活跃领主身上的槽位语义尚未验证, 一律禁用写。"""
        if getattr(self, "occ_combo", None) is None:
            return
        if (not self.doc.is_reliable(idx)) or (not self.doc.slot_region_trusted(idx)):
            if not self.doc.is_reliable(idx):
                shown, reason = "", "无完整记录, 不可读"
            else:
                # 区域未通过可信锚点(多为活跃领主但 X 偏移算错那几个): 显推断值。
                # 有部队(led_party) ⇒ occupation 必为 2(王国英雄)。
                reason = "槽区定位未通过可信校验 —— 显示为推断值, 不可写"
                if self.doc.find_lord_party(idx):
                    shown = "2 王国英雄 · 作为领主活跃 (slto_kingdom_hero)"
                else:
                    shown = "（未知 — 槽区未可信定位）"
            self.occ_combo.set(shown)
            self.occ_combo.config(state="disabled")
            self.pris_spin.config(state="disabled")
            self.occ_note.config(text=reason)
            return
        v = self.doc.get_occupation(idx)
        found = False
        for i, opt in enumerate(self.occ_options):
            if opt.startswith("%d " % v):
                self.occ_combo.current(i); found = True; break
        if not found:
            self.occ_combo.set("（未知 %d）" % v)
        self.occ_combo.config(state="readonly")
        pv = self.doc.get_prisoner_of_party(idx)
        self.pris_var.set(str(pv))
        lp = self.doc.get_led_party(idx)
        if self.doc.is_active_lord(idx):
            # 活跃领主: occupation 可写(已锚定); 但囚禁槽语义未验证, 禁用。
            self.pris_spin.config(state="disabled")
            self.occ_note.config(text="槽值=%d | 囚禁=%d(槽未验证·禁写) | 率部队=%d" % (v, pv, lp))
        else:
            self.pris_spin.config(state="normal")
            self.occ_note.config(text="槽值=%d | 囚禁=%d | 率部队=%d" % (v, pv, lp))

    def on_occupation(self):
        """把下拉选择写入兵种槽2 (活跃状态 / slot_troop_occupation)。"""
        if not self._can_edit():
            return
        if not self.doc.slot_region_trusted(self.cur_idx):
            self._set_status("该兵种槽区定位未通过可信校验(活跃领主锚点不符), 暂无法修改 occupation 以防写坏存档。", warn=True)
            return
        sel = self.occ_combo.current()
        if sel is None or sel < 0:
            return
        v = int(self.occ_options[sel].split()[0])
        try:
            self.doc.set_occupation(self.cur_idx, v)
            self._set_status("已将 #%d %s 的活跃状态设为 %d %s (保存存档后生效)"
                             % (self.cur_idx, self.doc.mod.troop_name(self.cur_idx),
                                v, M.SaveDoc.OCCUPATION_NAMES.get(v, "")))
            self.occ_note.config(text="槽值=%d" % v)
        except Exception as ex:
            self._set_status("活跃状态修改失败: %s" % ex, warn=True)

    def on_prisoner(self):
        """把输入框写入兵种槽8 (囚禁 / slot_troop_prisoner_of_party; -1=释放)。"""
        if not self._can_edit():
            return
        if not self.doc.slot_region_trusted(self.cur_idx) or self.doc.is_active_lord(self.cur_idx):
            self._set_status("囚禁(槽8)在活跃领主身上的槽位语义尚未验证, 暂禁止修改以防写坏存档。", warn=True)
            return
        try:
            v = int(self.pris_var.get())
        except Exception:
            self._set_status("囚禁值必须是整数 (-1=释放, >=0=关押方部队号)", warn=True)
            return
        try:
            self.doc.set_prisoner_of_party(self.cur_idx, v)
            self._set_status("已将 #%d %s 的囚禁状态设为 %d (保存存档后生效)"
                             % (self.cur_idx, self.doc.mod.troop_name(self.cur_idx), v))
        except Exception as ex:
            self._set_status("囚禁状态修改失败: %s" % ex, warn=True)

    def on_personality(self):
        """把下拉选择写入兵种槽128 (性格 / reputation_type)。"""
        if not self._can_edit():
            return
        if not self.doc.slot_region_trusted(self.cur_idx):
            self._set_status("该兵种槽区定位未通过可信校验, 性格槽暂无法修改以防写坏存档。", warn=True)
            return
        if self.doc.is_active_lord(self.cur_idx):
            self._set_status("活跃领主的性格(槽128)在存档中的槽位尚未验证, 暂禁止修改。", warn=True)
            return
        sel = self.pers_combo.current()
        if sel is None or sel < 0:
            return
        # 选项 0 = 未设置(值0); 其余格式 "%d 名称"
        v = 0 if sel == 0 else int(self.pers_options[sel].split()[0])
        try:
            self.doc.set_personality(self.cur_idx, v)
            self._set_status("已将 #%d %s 的性格设为 %s (保存存档后生效)"
                             % (self.cur_idx, self.doc.mod.troop_name(self.cur_idx),
                                M.SaveDoc.PERSONALITY_NAMES.get(v, "未设置")))
            self.pers_note.config(text="原始槽值=%d" % v)
        except Exception as ex:
            self._set_status("性格修改失败: %s" % ex, warn=True)

    def _sync_faction_combo(self, idx, tpl_fi):
        """同步『存档阵营』下拉框 (记录 +0x05C) —— 领主跳槽的编辑入口。"""
        if getattr(self, "fac_combo", None) is None:
            return
        mod = self.doc.mod
        if not getattr(self, "faction_options", None):
            self.faction_options = ["[%d] %s" % (i, mod.faction_names.get(f, f))
                                    for i, f in enumerate(mod.factions)]
            self.fac_combo["values"] = self.faction_options
        if not self.doc.is_reliable(idx):
            self.fac_combo.set("")
            self.fac_combo.config(state="disabled")
            self.fac_apply_btn.config(state="disabled")
            self.fac_defect_btn.config(state="disabled")
            self.fac_hint.config(text="存档无完整记录, 不可改")
            try:
                self.promote_btn.config(state="disabled")
                self.center_combo.config(state="disabled")
            except Exception:
                pass
            return
        v = self.doc.get_faction(idx)
        self.fac_combo.config(state="readonly")
        if 0 <= v < len(self.faction_options):
            self.fac_combo.current(v)
        self.fac_apply_btn.config(state="normal")
        # 跳槽按钮: 需能找到该领主所率部队
        try:
            pys = self.doc.find_lord_party(idx)
        except Exception:
            pys = []
        self.fac_defect_btn.config(state="normal" if pys else "disabled")
        pf = ""
        if pys:
            try:
                pf = "  部队#%d=%s" % (pys[0], self.doc.get_party_faction(pys[0]))
            except Exception:
                pf = ""
        self.fac_hint.config(text=("已跳槽(与模板不同)" if v != tpl_fi else "与模板一致")
                                  + pf + ("" if pys else "  (未找到其部队, 不能跳槽)"))
        # 提拔为领主: 城池下拉 + 按钮(仅英雄可用)
        if not getattr(self, "center_options", None):
            opts = ["（无）"]
            for pi in range(self.doc.party_count()):
                cinfo, _ = self.doc.get_party(pi)
                if cinfo["id"].startswith(("p_town_", "p_castle_", "p_village_")):
                    opts.append("%s (#%d)" % (cinfo["id"], pi))
            self.center_options = opts
            self.center_combo["values"] = opts
        try:
            self.center_combo.set("（无）")
            is_hero = bool(self.doc.get_flags(idx) & M.TF_HERO)
            # 已拥有部队的英雄(本就是领主/已上场)不能再提拔 —— 与模型 raise 一致
            has_party = False
            try:
                has_party = bool(self.doc.find_lord_party(idx))
            except Exception:
                has_party = False
            can_promote = is_hero and not has_party
            self.promote_btn.config(state="normal" if can_promote else "disabled")
            self.center_combo.config(state="readonly" if can_promote else "disabled")
        except Exception:
            pass

    def _apply_faction(self):
        """把选中阵营写入存档 (记录 +0x05C) —— 令该领主归属/跳槽到该阵营。"""
        if not self._can_edit():
            return
        sel = self.fac_combo.current()
        if sel is None or sel < 0:
            self._set_status("请先选择一个阵营。", warn=True)
            return
        try:
            self.doc.set_faction(self.cur_idx, sel)
            self._set_status("已把 #%d %s 的阵营改为 [%d] %s (保存存档后生效)"
                             % (self.cur_idx, self.doc.mod.troop_name(self.cur_idx),
                                sel, self.doc.get_faction_name(self.cur_idx)))
            self._fill_basic(self.cur_idx)
        except Exception as ex:
            self._set_status("阵营修改失败: %s" % ex, warn=True)

    def _do_defect(self):
        """执行跳槽脚本链: 领主归属 + 其部队归属一并改为选中的阵营。"""
        if not self._can_edit():
            return
        sel = self.fac_combo.current()
        if sel is None or sel < 0:
            self._set_status("请先选择目标阵营。", warn=True)
            return
        idx = self.cur_idx
        name = self.doc.mod.troop_name(idx)
        try:
            changes = self.doc.defect_lord(idx, sel)
        except Exception as ex:
            self._set_status("跳槽失败: %s" % ex, warn=True)
            return
        if not changes:
            self._set_status("#%d %s 已是该阵营, 无需改动。" % (idx, name))
            return
        detail = "; ".join("%s %s→%s" % (d, o, nv) for d, o, nv in changes)
        self._set_status("已执行跳槽: #%d %s —— %s (保存存档后生效)"
                         % (idx, name, detail))
        self._fill_basic(idx)

    def _do_promote(self):
        """提拔为领主: 给选中的英雄兵种从无到有创建部队(并可选封地),
        复刻游戏 create_kingdom_hero_party + give_center_to_lord 的运行时行为。
        与『执行跳槽』相反 —— 跳槽改"已有领主"的归属, 提拔是给"原本无部队的英雄"建部队。"""
        if not self._can_edit():
            return
        sel = self.fac_combo.current()
        if sel is None or sel < 0:
            self._set_status("请先在『存档阵营』下拉框选择该领主的目标阵营。", warn=True)
            return
        idx = self.cur_idx
        name = self.doc.mod.troop_name(idx)
        # 解析所选封地: 格式 "p_town_1 (#19)" / "（无）"
        center = None
        cval = self.center_combo.get()
        if cval and cval != "（无）":
            h = cval.rfind("#")
            if h >= 0:
                tail = cval[h + 1:].rstrip(")")
                if tail.isdigit():
                    center = int(tail)
                else:
                    self._set_status("无法解析封地选择: %s" % cval, warn=True)
                    return
            else:
                self._set_status("无法解析封地选择: %s" % cval, warn=True)
                return
        try:
            new_pi, changes = self.doc.promote_to_lord(idx, sel, center=center)
        except Exception as ex:
            self._set_status("提拔失败: %s" % ex, warn=True)
            return
        detail = "; ".join("%s %s→%s" % (d, o if o is not None else "-", nv)
                           for d, o, nv in changes)
        self._set_status("已提拔 #%d %s 为领主: 新建部队#%d —— %s (保存存档后生效)"
                         % (idx, name, new_pi, detail))
        self._fill_basic(idx)

    def on_attr(self, i):
        if not self._can_edit(): return
        vals = [safe_int(v) for v in self.attr_vars]
        if any(v is None for v in vals):
            return      # 有输入框被清空(正在重填), 本次不写入, 避免把 0 灌进存档
        self.doc.set_attrs(self.cur_idx, vals)

    def on_prof(self, i):
        if not self._can_edit(): return
        vals = [safe_int(v) for v in self.prof_vars]
        if any(v is None for v in vals):
            return
        self.doc.set_profs(self.cur_idx, vals)

    def on_level(self):
        if not self._can_edit(): return
        v = safe_int(self.level_var)
        if v is None: return
        self.doc.set_level(self.cur_idx, v)

    def on_xp(self):
        if not self._can_edit(): return
        v = safe_int(self.xp_var)
        if v is None: return
        self.doc.set_xp(self.cur_idx, v)

    def on_pts(self):
        if not self._can_edit(): return
        v = safe_int(self.pts_var)
        if v is None: return
        self.doc.set_pts(self.cur_idx, v)

    # ---------------- 兵种 技能页 ----------------
    def _build_skill_labels(self):
        if getattr(self, "_skill_built", False):
            return
        parent = self.skill_scroll.inner
        self._skill_vars = []
        for k in range(M.NSKILL):
            r = ttk.Frame(parent)
            r.pack(fill="x", padx=8, pady=1)
            ttk.Label(r, text="%02d  %s" % (k, self.skill_names[k]), width=28).pack(side="left")
            var = tk.IntVar()
            sp = ttk.Spinbox(r, from_=0, to=15, width=6, textvariable=var,
                             command=lambda k=k: self.on_skill(k))
            sp.pack(side="left")
            var.trace_add("write", lambda *a, k=k: self.on_skill(k))
            self._skill_vars.append(var)
        self._skill_built = True

    def _fill_skills(self, idx):
        if not getattr(self, "_skill_built", False):
            self._build_skill_labels()
        self.skill_vals = self.doc.view_skills(idx)
        for k in range(M.NSKILL):
            self._skill_vars[k].set(self.skill_vals[k])

    def on_skill(self, k):
        if not self._can_edit(): return
        v = safe_int(self._skill_vars[k])
        if v is None:
            return      # 技能框被清空(正在重填), 忽略本次, 不写存档
        self.skill_vals[k] = v
        self.doc.set_skills(self.cur_idx, self.skill_vals)

    # ---------------- 兵种 物品栏 / 装备 ----------------
    def _make_slot_row(self, parent, k, is_equip):
        r = ttk.Frame(parent)
        r.pack(fill="x", padx=6, pady=1)
        ttk.Label(r, text="槽 %02d" % k, width=6).pack(side="left")
        cmb = ttk.Combobox(r, values=self.item_options, width=46, state="readonly")
        cmb.pack(side="left", padx=2)
        ttk.Label(r, text="修饰原始值", width=8).pack(side="left")
        mod_var = tk.StringVar()
        mod_ent = ttk.Entry(r, textvariable=mod_var, width=12)
        mod_ent.pack(side="left", padx=2)
        dec = ttk.Label(r, text="", width=26)
        dec.pack(side="left", padx=2)
        ttype = tk.IntVar(); tamount = tk.IntVar()
        ttk.Label(r, text="修饰符").pack(side="left")
        sp_t = ttk.Spinbox(r, from_=0, to=255, width=5, textvariable=ttype)
        sp_t.pack(side="left", padx=1)
        ttk.Label(r, text="耐久/数量").pack(side="left")
        sp_a = ttk.Spinbox(r, from_=0, to=16777215, width=9, textvariable=tamount)
        sp_a.pack(side="left", padx=1)
        btn = ttk.Button(r, text="清空", command=lambda k=k: self.clear_slot(k, is_equip))
        btn.pack(side="left", padx=2)

        def on_item(*a):
            sel = cmb.current()
            item_id = -1 if sel <= 0 else sel - 1
            self.apply_slot(k, is_equip, item_id=item_id)
        def on_mod(*a):
            try: m = int(mod_var.get()) & 0xFFFFFFFF
            except Exception: return
            self.apply_slot(k, is_equip, modifier=m)
        def on_type_amt(*a):
            # 存档 u32 = (修饰符 << 24) | 耐久/数量
            t = safe_int(ttype); am = safe_int(tamount)
            if t is None or am is None:
                return      # 输入框被清空(正在重填), 忽略本次
            m = ((t & 0xFF) << 24) | (am & 0xFFFFFF)
            mod_var.set(str(m))
            self.apply_slot(k, is_equip, modifier=m)
        cmb.bind("<<ComboboxSelected>>", on_item)
        mod_ent.bind("<FocusOut>", on_mod)
        mod_ent.bind("<Return>", on_mod)
        sp_t.bind("<FocusOut>", on_type_amt); sp_t.bind("<Return>", on_type_amt)
        sp_a.bind("<FocusOut>", on_type_amt); sp_a.bind("<Return>", on_type_amt)
        return dict(cmb=cmb, mod_var=mod_var, dec=dec, ttype=ttype, tamount=tamount)

    def _ensure_slot_rows(self):
        """性能关键: 74 行槽控件 (物品栏 64 + 装备 10) 只创建一次。
        此前每次选中兵种都销毁重建 ~670 个控件, 是界面严重卡顿的根源。"""
        if getattr(self, "_rows_built", False):
            return
        for k in range(M.INV_SLOTS):
            self.inv_widgets.append(self._make_slot_row(self.inv_scroll.inner, k, False))
        for k in range(M.EQUIP_SLOTS):
            self.equip_widgets.append(self._make_slot_row(self.equip_scroll.inner, k, True))
        self._rows_built = True

    def _fill_inventory(self, idx):
        self._ensure_slot_rows()
        inv = self.doc.view_inventory(idx)
        for k in range(M.INV_SLOTS):
            item_id, mod = inv[k]
            self._set_slot_widget(self.inv_widgets[k], item_id, mod)

    def _fill_equipment(self, idx):
        self._ensure_slot_rows()
        eq = self.doc.get_equipment(idx) if self.doc.is_reliable(idx) else [(-1, 0)] * M.EQUIP_SLOTS
        for k in range(M.EQUIP_SLOTS):
            item_id, mod = eq[k]
            self._set_slot_widget(self.equip_widgets[k], item_id, mod)

    def _set_slot_widget(self, w, item_id, mod):
        if item_id == -1:
            w["cmb"].current(0)
        else:
            if 0 <= item_id <= self.doc.nitems:
                w["cmb"].current(item_id + 1)
            else:
                w["cmb"].current(0)
        w["mod_var"].set(str(mod))
        self._show_mod_decode(w, mod)

    def _show_mod_decode(self, w, mod):
        """存档 u32 = (修饰符 imod << 24) | 耐久/数量。"""
        mt, amt = (mod >> 24) & 0xFF, mod & 0xFFFFFF
        nm = M.IMOD_NAMES[mt] if 0 <= mt < len(M.IMOD_NAMES) else "?"
        w["ttype"].set(mt)
        w["tamount"].set(amt)
        w["dec"].config(text="(修饰符=%d %s, 耐久/数量=%d)" % (mt, nm, amt))

    def apply_slot(self, k, is_equip, item_id=None, modifier=None):
        if not self._can_edit(): return
        widgets = self.equip_widgets if is_equip else self.inv_widgets
        w = widgets[k]
        sel = w["cmb"].current()
        cur_id = -1 if sel <= 0 else sel - 1
        try: cur_mod = int(w["mod_var"].get()) & 0xFFFFFFFF
        except Exception: cur_mod = 0
        if item_id is not None: cur_id = item_id
        if modifier is not None: cur_mod = modifier
        if is_equip:
            self.doc.set_equipment_slot(self.cur_idx, k, cur_id, cur_mod)
        else:
            self.doc.set_inventory_slot(self.cur_idx, k, cur_id, cur_mod)
        self._show_mod_decode(w, cur_mod)

    def clear_slot(self, k, is_equip):
        if self.cur_idx is None: return
        self.apply_slot(k, is_equip, item_id=-1, modifier=0)

    # ---------------- 兵种 简介 ----------------
    def _fill_bio(self, idx):
        self.bio_text.edit_modified(False)
        self.bio_text.delete("1.0", tk.END)
        if self.doc.is_reliable(idx):
            self.bio_text.insert("1.0", self.doc.get_bio(idx))
        else:
            self.bio_text.insert("1.0", "（存档中无该兵种的完整记录, 无简介可读）")
        self.bio_text.edit_modified(False)

    def on_bio_modified(self, ev):
        if not self._can_edit(): return
        if not self.bio_text.edit_modified():
            return
        self.bio_text.edit_modified(False)
        txt = self.bio_text.get("1.0", tk.END).rstrip("\n")
        truncated = self.doc.set_bio(self.cur_idx, txt)
        if truncated:
            self._set_status("简介已截断到原长度(不允许变长, 以免破坏存档结构)。", warn=True)

    # ---------------- 部队列表 ----------------
    def refresh_party_list(self):
        if self.doc.party_count() == 0:
            return
        self.plistbox.delete(0, tk.END)
        self._pidx_map = []
        # 缓存部队信息: 此前筛选时每个按键都重新二进制解析全部 569+ 部队, 非常耗时
        n = self.doc.party_count()
        if getattr(self, "_party_infos", None) is None or len(self._party_infos) != n:
            self._party_infos = [self.doc.get_party(i)[0] for i in range(n)]
        q = self.pfilter_var.get().strip().lower()
        for i, info in enumerate(self._party_infos):
            nm = (info["id"] + " " + info["name"]).lower()
            if q and q not in nm:
                continue
            tag = "" if info.get("index_b") is None else " [#%d]" % info["index_b"]
            self.plistbox.insert(tk.END, "#%03d  %s%s  [%s]  %s" % (
                i, info["id"], tag, info["kind"], info["name"]))
            self._pidx_map.append(i)
        self.pcount_label.config(text="显示 %d / %d" % (len(self._pidx_map), self.doc.party_count()))
        self.plistbox.xview_moveto(0)   # 防横向漂移

    def on_party_select(self, ev):
        sel = self.plistbox.curselection()
        if not sel:
            return
        self._select_party(self._pidx_map[sel[0]])

    def jump_to_party(self):
        try:
            idx = int(self.pjump_var.get())
        except Exception:
            return
        if 0 <= idx < self.doc.party_count():
            self._select_party(idx)
            self._set_status("已跳转到部队 #%d" % idx)

    def _select_party(self, idx):
        if idx is None or idx >= self.doc.party_count():
            return
        self.cur_party = idx
        info, stacks = self.doc.get_party(idx)
        self._building = True
        try:
            tag = info["id"] + ("" if info.get("index_b") is None else " [#%d]" % info["index_b"])
            self.plabel_id.config(text="ID: %s   名称: %s   类型: %s   堆叠数: %d" % (
                tag, info["name"], info["kind"], info["num_stacks"]))
            self._fill_party_stacks(stacks)
            self._sync_party_faction(idx)
        finally:
            self._building = False
        if getattr(self, "_pidx_map", None):
            for pos, j in enumerate(self._pidx_map):
                if j == idx:
                    self.plistbox.selection_clear(0, tk.END)
                    self.plistbox.selection_set(pos)
                    self.plistbox.see(pos)
                    self.plistbox.xview_moveto(0)   # 防横向漂移
                    break
        self._set_status("已选中部队: %s" % info["id"])

    def _sync_party_faction(self, idx):
        """同步部队页的『归属阵营』下拉框 (party fields[4])。"""
        if getattr(self, "pfac_combo", None) is None:
            return
        mod = self.doc.mod
        if not getattr(self, "pfaction_options", None):
            self.pfaction_options = ["[%d] %s" % (i, mod.faction_names.get(f, f))
                                     for i, f in enumerate(mod.factions)]
            self.pfac_combo["values"] = self.pfaction_options
        try:
            v = self.doc.get_party_faction(idx)
        except Exception:
            v = -1
        if 0 <= v < len(self.pfaction_options):
            self.pfac_combo.config(state="readonly")
            self.pfac_combo.current(v)
            self.pfac_btn.config(state="normal")
            self.pfac_hint.config(text=self.doc.get_party_faction_name(idx))
        else:
            self.pfac_combo.set("")
            self.pfac_combo.config(state="disabled")
            self.pfac_btn.config(state="disabled")
            self.pfac_hint.config(text="未知阵营 (%s)" % v)

    def _apply_party_faction(self):
        """改该部队/城池的归属阵营 (fields[4])。"""
        if self.cur_party is None:
            return
        sel = self.pfac_combo.current()
        if sel is None or sel < 0:
            self._set_status("请先选择一个阵营。", warn=True)
            return
        try:
            old = self.doc.get_party_faction(self.cur_party)
            self.doc.set_party_faction(self.cur_party, sel)
            info, _ = self.doc.get_party(self.cur_party)
            self._set_status("已把 %s (%s) 的归属阵营由 [%d] 改为 [%d] %s (保存存档后生效)"
                             % (info["id"], info["name"], old, sel,
                                self.doc.mod.faction_names.get(
                                    self.doc.mod.factions[sel], "?")), )
            self._sync_party_faction(self.cur_party)
            self._select_party(self.cur_party)
        except Exception as ex:
            self._set_status("归属阵营修改失败: %s" % ex, warn=True)

    def _fill_party_stacks(self, stacks):
        for w in self.pstack_widgets:
            w["frame"].destroy()
        self.pstack_widgets = []
        parent = self.pstack_scroll.inner
        for sidx, st in enumerate(stacks):
            r = ttk.Frame(parent)
            r.pack(fill="x", padx=4, pady=1)
            ttk.Label(r, text="堆叠 %02d" % sidx, width=8).pack(side="left")
            cmb = ttk.Combobox(r, values=self.troop_options, width=42, state="readonly")
            cmb.pack(side="left", padx=2)
            if st["troop"] == -1:
                cmb.current(0)
            elif 0 <= st["troop"] < len(self.troop_options) - 1:
                cmb.current(st["troop"] + 1)
            else:
                cmb.current(0)
            ttk.Label(r, text="数量", width=5).pack(side="left")
            num_var = tk.IntVar(value=st["num"])
            sp = ttk.Spinbox(r, from_=0, to=100000, width=8, textvariable=num_var)
            sp.pack(side="left", padx=2)
            ttk.Label(r, text="flag=%d" % st["f4"], width=10).pack(side="left")
            ttk.Button(r, text="删除", command=lambda s=sidx: self.remove_party_stack_ui(s)).pack(side="left", padx=2)

            def on_troop(ev, sidx=sidx, cmb=cmb):
                self.on_party_stack_troop(sidx, cmb.current())
            def on_num(ev, sidx=sidx, num_var=num_var):
                try: v = int(num_var.get())
                except Exception: return
                self.doc.set_party_stack_count(self.cur_party, sidx, v)
            cmb.bind("<<ComboboxSelected>>", on_troop)
            sp.bind("<FocusOut>", on_num)
            sp.bind("<Return>", on_num)
            self.pstack_widgets.append(dict(frame=r, cmb=cmb, num_var=num_var))

    def on_party_stack_troop(self, sidx, sel):
        if self._building or self.cur_party is None: return
        troop = -1 if sel <= 0 else sel - 1
        self.doc.set_party_stack_troop(self.cur_party, sidx, troop)

    def add_party_stack_ui(self):
        if self.cur_party is None:
            messagebox.showinfo("提示", "请先在左侧选择一个部队。")
            return
        sel = self.padd_cmb.current()
        troop = -1 if sel <= 0 else sel - 1
        try:
            num = int(self.padd_num.get())
        except Exception:
            num = 1
        try:
            self.doc.add_party_stack(self.cur_party, troop, num, 0, 0)
            self._select_party(self.cur_party)
            self._set_status("已添加堆叠: 兵种 #%d x %d" % (troop, num))
        except Exception as e:
            messagebox.showerror("添加失败", str(e))

    def remove_party_stack_ui(self, sidx):
        if self.cur_party is None:
            return
        try:
            self.doc.remove_party_stack(self.cur_party, sidx)
            self._select_party(self.cur_party)
            self._set_status("已删除堆叠 #%d" % sidx)
        except Exception as e:
            messagebox.showerror("删除失败", str(e))

    # ---------------- 保存 ----------------
    def do_save(self):
        if not self.doc.path:
            return
        try:
            out = self.doc.save(backup=True)
            self._set_status("已保存并备份: %s" % out, ok=True)
        except Exception as e:
            messagebox.showerror("保存失败", str(e))

    def do_save_as(self):
        if not self.doc.path:
            return
        path = filedialog.asksaveasfilename(defaultextension=".sav",
                                            filetypes=[("Save", "*.sav")])
        if not path:
            return
        try:
            self.doc.save(path=path, backup=False)
            self._set_status("已另存为: %s" % path, ok=True)
        except Exception as e:
            messagebox.showerror("保存失败", str(e))

    def do_backup(self):
        if not self.doc.path:
            return
        try:
            import datetime
            ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            p = self.doc.path.with_suffix(".sav.bak_%s" % ts)
            p.write_bytes(self.doc.path.read_bytes())
            self._set_status("已备份到: %s" % p, ok=True)
        except Exception as e:
            messagebox.showerror("备份失败", str(e))

    def reset_current(self):
        if self.cur_idx is None:
            return
        messagebox.showinfo("提示", "复位需重新载入存档。请使用「打开存档」重新载入。")

    # ---------------- 状态 ----------------
    def _set_status(self, msg, ok=False, warn=False):
        self.status.config(text=msg)
        if ok:
            self.status.config(foreground="green")
        elif warn:
            self.status.config(foreground="red")
        else:
            self.status.config(foreground="black")

    def _on_tk_error(self, exc, val, tb):
        """Tk 回调异常兜底: 状态栏一行提示 + stderr 一行, 不打印整页 traceback。

        最常见的触发是 Spinbox 被清空瞬间 IntVar.get() 抛 TclError；
        真正的写入路径已用 safe_int() 吸收，这里只作为最后一道闸，防止控制台刷屏。
        """
        try:
            name = getattr(exc, "__name__", str(exc))
            msg = "%s: %s" % (name, val)
            self._set_status("⚠ 界面回调出错(已忽略): %s" % msg, warn=True)
            sys.stderr.write("[MB1011] Tk callback error: %s\n" % msg)
        except Exception:
            pass


def _parse_module_arg(argv):
    """支持 --module <目录> / --module=<目录> 显式指定模块(便于分发与批处理)。"""
    for i, a in enumerate(argv):
        if a == "--module" and i + 1 < len(argv):
            return argv[i + 1]
        if a.startswith("--module="):
            return a.split("=", 1)[1]
    return None


def main(argv=None):
    """供冻结版启动器直接调用 (不再依赖 __main__)。"""
    enable_dpi_awareness()   # 高 DPI 下文本漂移/点击错位的系统级修复
    app = App(module_arg=_parse_module_arg(argv if argv is not None else sys.argv[1:]))
    app.mainloop()


if __name__ == "__main__":
    main()
