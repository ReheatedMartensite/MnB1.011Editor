# -*- coding: utf-8 -*-
"""
Mount & Blade 1.011 Troop/Item Viewer v2
================================
Enhanced from the original “M&B 1.011 Troop/Item Viewer” — new core features:

1) Skill position mapping
   The troops.txt skills row has only 6 decimal numbers — they are actually six u32
   4-bit packed bitfields (4 bits per skill, 48 slots total). This tool decodes them into
   a skill table ordered like skills.txt: position / English ID / Chinese name / level.
   Cross-verified: archer -> [33] skl_power_draw = 4;

2) Factions (factions.txt index + Chinese name)

3) Troop flag tf_* decoding (incl. tf_hero: heroes can't die / independent level-ups / party skills / show HP percentage)

4) Save comparison: optionally load a .sav and compare troops.txt template values against save instance values,
   highlighting differences — used to verify “which fields change when instantiated”.
   Known rules:
     - Attributes: save = template + random allocation, so sum = level + 20
     - Skills: save = template + a few random skill points
     - Items: may have random equipment variants

Depends on: mb_model.py (same directory)
Run: E:\\BigBrother\\Anaconda\\python.exe troop_item_viewer_v2_EN.py
"""
import os
import re
import sys
import copy
import shutil
import datetime
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mb_model_EN as M       # English display names (txt originals), not cns/*.csv

# 模组中文翻译(cns/*.csv)为逐字加空格格式(如 "福 门 特 势 力"),
# 仅合并「两个汉字之间的空格」, 保留 Latin/数字周围的正常空格(如 "culture 1")。
_CJK_SPACE = re.compile(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])")
def _clean_cjk(s):
    return _CJK_SPACE.sub("", str(s)) if s is not None else ""

def fmt(x):
    """格式化数值: None -> '?'，否则原样转字符串。"""
    return "?" if x is None else str(x)

# Default module: only a "preferred" path (edit this when redistributing, optional).
# Real path comes from M.resolve_default_module(), priority:
#   env MB_MODULE_DIR  >  this constant  >  last remembered (mb_module_path.json)
#   >  auto-detect  >  folder picker dialog
DEFAULT_MODULE = r"E:\Program Files\Game\Mount&Blade\Modules\WD - Minuet (v.0.12 Full)"

# 技能位域共 48 个位置 (6 x u32, 每项 4 bit); skills.txt 通常只定义前 42 个
SKILL_SLOTS = M.NSKILL


def decode_skills(words):
    """6 个 u32 打包位域 -> 48 个技能等级。
    位置 i 对应 skills.txt 的第 i 个技能 (在 word[i//8] 的第 i%8 个 nibble)。
    复用模型层实现, 已用 troops.txt + 存档交叉验证。"""
    words = list(words) + [0] * (6 - len(words))
    return M.skills_decode(words)


def encode_skills(vals):
    """48 个技能等级 -> 6 个 u32 打包位域。"""
    return M.skills_encode(vals)


# ============================================================================
# Party Templates(party_templates.txt) 解析 / 序列化
# ----------------------------------------------------------------------------
# 格式(实测 WD - Minuet v0.12, 1.011, 全 64 模板一致):
#   第1行: partytemplatesfile version 1
#   第2行: 模板Count
#   其后每行一个模板:  id  name  flags  0  h4  h5  [Troops栈...]  -1 ...
#   头部共 6 个 token:
#     t[0]=id  t[1]=name  t[2]=flags  t[3]=0(固定)  t[4]=menu类(H4)  t[5]=ai_behavior类(H5)
#   ⚠ 旧版误把头部当成 4 个 token、Troops栈从 t[4:] 起读 —— 实际应从 t[6:] 起,
#     导致整体错位漂移 2 个 token, Troopsid与Count被读反/错乱。2026-09-21 修正。
#   Troops栈: 每个栈 4 个整数 = (troop_id, Count A/min, Count B/max, Flag);
#   以"栈首 troop==-1"哨兵终止, 之后是若干 -1 填充(尾部 -1 Count可变)。
#   写回须原样保留All 6 个头部 token(含 h4/h5), 否则会丢失 2 字段、破坏存档。
# 解析采用「格式保持」策略: 仅把栈区解析成结构化列表, 尾部原文整体保留(trailing),
# 写回时按相同 token 布局拼接 —— 即使对字段语义(Count A/B 谁是 min/max)判断有偏差,
# 也绝不会改变File token Count与 -1 布局, 不会写坏存档/模块。
# ============================================================================

def parse_party_templates(text):
    """把 party_templates.txt 文本解析为模板字典列表(格式保持)。

    头部共 6 个 token(id name flags 0 h4 h5), Troops栈从 t[6:] 起读 —— 旧版误用 t[4:]
    会造成整体 2-token 漂移(Troopsid/Count读反)。见模块头注释。"""
    out = []
    for ln in text.split("\n"):
        s = ln.rstrip("\r")
        if not s.strip():
            continue
        t = s.split()
        if len(t) < 6:
            continue
        tid, name, flags, fixed0 = t[0], t[1], int(t[2]), int(t[3])
        h4 = int(t[4])   # 第5 token: menu / 模板类别(各模板固定, 写回须保留)
        h5 = int(t[5])   # 第6 token: ai_behavior / 经验等(各模板固定, 写回须保留)
        body = t[6:]     # Troops栈起点(1.011/WD: 头部共 6 个 token)
        stacks = []
        i = 0
        while i + 4 <= len(body):
            quad = [int(x) for x in body[i:i + 4]]
            if quad[0] == -1:                    # 哨兵: 仅Troops(troop)==-1 才终止
                break
            stacks.append(quad)                   # 每个栈存为 [troop, a, b, flag]
            i += 4
        trailing = [int(x) for x in body[i:]]     # 哨兵及之后的 -1 填充(原样保留)
        trailing_ws = s[len(s.rstrip()):]         # 行末空白(尾部空格; CRLF 已在 line_sep 还原)
        out.append({"id": tid, "name": name, "flags": flags,
                    "fixed0": fixed0, "h4": h4, "h5": h5,
                    "stacks": stacks, "trailing": trailing,
                    "trailing_ws": trailing_ws})
    return out


def serialize_party_templates(templates, header="partytemplatesfile version 1",
                              line_sep="\n"):
    """把模板列表序列化为可写回的文本。格式保持: 6 个头部 token + 栈区 + 原样 trailing + 行末空白。
    line_sep 用于还原原File换行(CRLF/LF), 使未改动的行做到字节级一致。
    ⚠ 须原样写回 h4/h5(第5/6 token), 否则丢失 2 头部字段、破坏存档。"""
    lines = [header, str(len(templates))]
    for t in templates:
        parts = [t["id"], t["name"], str(int(t["flags"])), str(int(t["fixed0"])),
                 str(int(t.get("h4", 0))), str(int(t.get("h5", 0)))]
        for s in t["stacks"]:
            parts += [str(int(x)) for x in s]
        trailing = list(t.get("trailing", []))
        if not t["stacks"] and not trailing:    # 空模板兜底: 至少保留哨兵 -1
            trailing = [-1]
        parts += [str(int(x)) for x in trailing]
        lines.append(" ".join(parts) + t.get("trailing_ws", ""))
    return line_sep.join(lines) + line_sep


# ---------------- Items完整解析(自包含, 恢复 v1 的"Items属性查看/修改"功能) ----------------
# 旧版查看器(骑砍1.011TroopsItems查看.py) 自带完整 item_kinds1.txt 解析; v2 早期改成依赖
# mb_model, 而 mb_model.items 只存了Items ID 字符串, 导致Items页『连属性都看不到』。
# 此处把旧版的自包含解析原样移植回来, 作为 v2 的Items子系统, 与 mb_model 并存。
GOOD_FLAGS = {11, 20, 65547, 33619979, 34078731, 34144267}
HORSE_FLAGS = {1, 19, 65537}
SHIELD_FLAGS = {327687, 262151, 65543}
AMMO_FLAGS = {65541, 65542, 65554, 16842758, 67174406}
RANGED_FLAGS = {6357000, 6357001, 274792457, 4259856}
THROWING_FLAGS = {71368714, 71303178, 4259850, 4194314}

def read_text(path):
    import pathlib
    p = pathlib.Path(path)
    if not p.exists():
        return ""
    data = p.read_bytes()
    for enc in ("utf-8-sig", "utf-8", "gb18030", "big5", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            pass
        except Exception:
            pass
    return data.decode("gb18030", errors="replace")

def parse_trans(path):
    d = {}
    text = read_text(path)
    if not text:
        return d
    for line in text.splitlines():
        line = line.strip()
        if not line or "|" not in line:
            continue
        k, v = line.split("|", 1)
        k = k.strip(); v = v.strip()
        if not k.endswith("_pl"):
            d[k] = _clean_cjk(v)
    return d

def parse_items(path):
    """解析 item_kinds1.txt, 返回完整Items字典列表 (idx 与File顺序一致)。"""
    text = read_text(path)
    lines_all = text.splitlines()
    groups = []
    cur = None
    for raw in lines_all[2:]:
        line = raw.strip()
        if not line:
            continue
        if line.startswith("itm_"):
            if cur is not None:
                groups.append(cur)
            cur = [line]
        else:
            if cur is not None:
                cur.append(line)
    if cur is not None:
        groups.append(cur)
    items = []
    for idx, g in enumerate(groups):
        if not g:
            continue
        parts = g[0].split()
        if len(parts) < 4:
            continue
        item_id = parts[0]
        raw_name = parts[1]
        raw_plural = parts[2] if len(parts) > 2 else ""
        mesh_count = int(parts[3]) if parts[3].lstrip("-").isdigit() else 0
        if mesh_count is None or mesh_count < 0:
            mesh_count = 0
        start = 4 + 2 * mesh_count
        rest = parts[start:]
        flag1 = int(rest[0]) if len(rest) > 0 and rest[0].lstrip("-").isdigit() else None
        flag2 = rest[1] if len(rest) > 1 else ""
        price = int(rest[2]) if len(rest) > 2 and rest[2].lstrip("-").isdigit() else None
        prop = rest[3] if len(rest) > 3 else ""
        weight = float(rest[4]) if len(rest) > 4 and _is_float(rest[4]) else None
        abundance = int(rest[5]) if len(rest) > 5 and rest[5].lstrip("-").isdigit() else None
        stats = [int(x) if x.lstrip("-").isdigit() else 0 for x in rest[6:]]
        items.append({
            "idx": idx, "id": item_id, "raw_name": raw_name, "raw_plural": raw_plural,
            "flag1": flag1, "flag2": flag2, "price": price, "prop": prop,
            "weight": weight, "abundance": abundance, "stats": stats,
            "raw_first_line": g[0], "extra": g[1:],
        })
    meta_line = lines_all[0] if lines_all else ""
    count_line = lines_all[1] if len(lines_all) > 1 else "0"
    return items, meta_line, count_line

def _is_float(s):
    try:
        float(s); return True
    except Exception:
        return False

def decode_damage(v):
    if v is None:
        return "?"
    if v == 0:
        return "0 (cut / unused)"
    if v >= 512:
        return f"Blunt {v - 512}"
    if v >= 256:
        return f"Pierce {v - 256}"
    return f"Cut {v}"

def item_categories(it):
    flag = it["flag1"] if it["flag1"] is not None else 0
    idlow = it["id"].lower()
    is_goods = flag in GOOD_FLAGS
    is_horse = flag in HORSE_FLAGS
    is_shield = flag in SHIELD_FLAGS or "shield" in idlow or "targe" in idlow
    is_ammo = flag in AMMO_FLAGS or any(k in idlow for k in ("arrow", "bolt", "cartridge"))
    is_ranged = flag in RANGED_FLAGS or any(k in idlow for k in ("bow", "crossbow", "pistol"))
    is_throwing = flag in THROWING_FLAGS or any(
        k in idlow for k in ("throwing", "javelin", "jarid", "stones", "chucking", "wrist_daggers"))
    return is_goods, is_horse, is_shield, is_ammo, is_ranged, is_throwing


class ScrolledFrame(ttk.Frame):
    """内部放一个可垂直滚动的 Frame (用于 74 行Items槽网格)。"""
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


class ViewerV2:
    def __init__(self, root):
        self.root = root
        self.root.title("Mount & Blade 1.011 Troop/Item Viewer v2 (with skill position mapping)")
        self.root.geometry("1360x860")

        self.mod = M.ModuleData()
        self.doc = None            # 已载入的存档 (可选)
        self.troops = []
        self.filtered = []
        self.cur = None
        self.item_options = ["Empty (-1)"]   # Items下拉选项 (载入模块后填充)
        self.faction_options = []         # 阵营下拉选项 (载入模块后填充)
        self.inv_rows = []          # 背包 64 行控件
        self.equip_rows = []        # 装备 10 行控件

        # ---- Items完整属性 (item_kinds1.txt 全解析) ----
        self.items_full = []        # 完整Items字典列表(含 flag/价格/重量/数值), 与 mod.items 按序对齐
        self.item_dirty = False     # Items(item_kinds1.txt) 是否有未保存修改
        self.cur_item = None        # 当前选中Items索引 (指向 items_full / mod.items)
        self._items_meta = ""       # item_kinds1.txt 首行(meta)
        self._items_count = "0"     # item_kinds1.txt 次行(ItemsCount)

        # ---- Party Templates(party_templates.txt)编辑 ----
        self.pt_file = None         # party_templates.txt 路径
        self.pt_header = "partytemplatesfile version 1"  # 第1行(原样保留)
        self.pt_line_sep = "\n"      # 原File换行符(CRLF/LF), 载入时校正
        self.party_templates = []   # 解析后的模板列表(含编辑)
        self.pt_original = []       # 载入时的原始副本(用于差异/修改记录)
        self.pt_cur = None          # 当前选中的模板索引
        self.troop_options = ["Empty (-1)"]   # Troops下拉(载入模块后填充)
        self.pt_rows = []           # 栈编辑行控件
        self.pt_changelog = []      # 本次会话的修改记录(用于显示)

        self.build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.after_build()

    # ---------------- UI ----------------
    def build_ui(self):
        menubar = tk.Menu(self.root, tearoff=0)
        fm = tk.Menu(menubar, tearoff=0)
        fm.add_command(label="Open Other Directory…", command=self.open_module)
        fm.add_command(label="Load Save for Comparison (Optional)", command=self.open_save)
        fm.add_command(label="Save Module (troops.txt Abstract Troops)", command=self.save_module_file)
        fm.add_command(label="Save Game (Write Instance Item Changes)", command=self.save_save)
        fm.add_command(label="Save Party Templates (party_templates.txt)", command=self.save_party_templates)
        fm.add_command(label="Save Items (item_kinds1.txt)", command=self.save_items)
        fm.add_separator()
        fm.add_command(label="Exit", command=self.on_close)
        menubar.add_cascade(label="File", menu=fm)
        self.root.config(menu=menubar)

        top = ttk.Frame(self.root)
        top.pack(fill="x", padx=4, pady=3)
        # 模块快速切换: 自动列出游戏 Modules 目录下所有可用模组
        self._module_map = self._detect_modules()
        self.module_combo = ttk.Combobox(top, state="readonly", width=34,
                                        values=list(self._module_map.keys()))
        self.module_combo.bind("<<ComboboxSelected>>", self._on_module_pick)
        ttk.Label(top, text="Module:").pack(side="left", padx=(0, 2))
        self.module_combo.pack(side="left", padx=(0, 8))
        self.mod_label = ttk.Label(top, text="(not loaded)", anchor="w")
        self.mod_label.pack(side="left")
        self.sav_label = ttk.Label(top, text="Save: not loaded", anchor="w", foreground="#888")
        self.sav_label.pack(side="left", padx=16)

        self.nb = ttk.Notebook(self.root)
        self.tab_troop = ttk.Frame(self.nb)
        self.tab_item = ttk.Frame(self.nb)
        self.tab_cmp = ttk.Frame(self.nb)
        self.tab_pt = ttk.Frame(self.nb)
        self.nb.add(self.tab_troop, text="Troops")
        self.nb.add(self.tab_item, text="Items")
        self.nb.add(self.tab_cmp, text="Compare")
        self.nb.add(self.tab_pt, text="Party Templates")
        self.nb.pack(fill="both", expand=True)

        self.build_troop_tab()
        self.build_item_tab()
        self.build_compare_tab()
        self.build_pt_tab()

        self.status = ttk.Label(self.root, text="", anchor="w")
        self.status.pack(side="bottom", fill="x")

    def build_troop_tab(self):
        top = ttk.Frame(self.tab_troop)
        top.pack(fill="x", padx=4, pady=3)
        ttk.Label(top, text="Search:").pack(side="left")
        self.tsearch = tk.StringVar()
        e = ttk.Entry(top, textvariable=self.tsearch)
        e.pack(side="left", fill="x", expand=True, padx=4)
        e.bind("<Return>", lambda ev: self.refresh_troops())
        ttk.Button(top, text="Filter", command=self.refresh_troops).pack(side="left")
        ttk.Button(top, text="All", command=lambda: (self.tsearch.set(""), self.refresh_troops())).pack(side="left", padx=2)

        hp = tk.PanedWindow(self.tab_troop, orient="horizontal")
        hp.pack(fill="both", expand=True, padx=4, pady=4)

        lf = ttk.LabelFrame(hp, text="Troop list")
        rf = ttk.Frame(hp)
        hp.add(lf, stretch="always", width=360)
        hp.add(rf, stretch="always")

        sb = ttk.Scrollbar(lf)
        sb.pack(side="right", fill="y")
        self.tlist = tk.Listbox(lf, yscrollcommand=sb.set, exportselection=False,
                                font=("Microsoft YaHei UI", 9))
        self.tlist.pack(fill="both", expand=True)
        sb.config(command=self.tlist.yview)
        self.tlist.bind("<<ListboxSelect>>", self.on_troop_select)

        # 右侧: 上=Summary, 下=技能表
        vp = tk.PanedWindow(rf, orient="vertical")
        vp.pack(fill="both", expand=True)

        gf = ttk.LabelFrame(vp, text="Summary")
        vp.add(gf, stretch="always", height=250)
        # 阵营(跳槽)编辑栏: 修改存档中该Troops的当前所属阵营
        facbar = ttk.Frame(gf)
        facbar.pack(side="top", fill="x", padx=4, pady=2)
        ttk.Label(facbar, text="Faction (save):").pack(side="left")
        self.fac_combo = ttk.Combobox(facbar, state="readonly", width=26)
        self.fac_combo.pack(side="left", padx=3)
        self.fac_apply_btn = ttk.Button(facbar, text="Change ownership only", command=self._apply_faction)
        self.fac_apply_btn.pack(side="left", padx=2)
        self.fac_defect_btn = ttk.Button(facbar, text="Defect", command=self._do_defect)
        self.fac_defect_btn.pack(side="left", padx=2)
        self.fac_hint = ttk.Label(facbar, text="Editable after loading a save", foreground="#888")
        self.fac_hint.pack(side="left", padx=6)
        # Flag位(tf_*)勾选编辑面板: 修改 troops.txt 抽象Troops头行的 flags (影响新开局)
        # 程序在「勾选项 ↔ 数值」之间自动转换, 用户无需理解每一位代表什么。
        flf = ttk.LabelFrame(gf, text="Flags tf_* (check to set; value computed automatically)")
        flf.pack(side="top", fill="x", padx=4, pady=2)
        self.flag_calc = ttk.Label(flf, text="Current value: 0x00000000 (decimal 0)", foreground="#555")
        self.flag_calc.pack(side="top", fill="x", padx=6, pady=(3, 0))
        fgrid = ttk.Frame(flf)
        fgrid.pack(side="top", fill="x", padx=6, pady=2)
        self.tf_vars = []          # [(mask, BooleanVar), ...], 顺序同 M.TF_NAMES
        for k, (mask, name) in enumerate(M.TF_NAMES):
            var = tk.BooleanVar()
            cb = ttk.Checkbutton(fgrid, text=name, variable=var,
                                 command=self._update_troop_flag_calc)
            cb.grid(row=k // 3, column=k % 3, sticky="w", padx=4, pady=1)
            self.tf_vars.append((mask, var))
        fbbar = ttk.Frame(flf)
        fbbar.pack(side="top", fill="x", padx=6, pady=(2, 4))
        ttk.Button(fbbar, text="Apply flags", command=self.on_troop_flag).pack(side="left", padx=2)
        self.flag_hint = ttk.Label(fbbar, text="Template · unsaved", foreground="#888")
        self.flag_hint.pack(side="left", padx=6)
        self.tdetail = self._mk_text(gf)

        sf = ttk.LabelFrame(vp, text="Skills (mapped by skills.txt position; 48-slot bitfield)")
        vp.add(sf, stretch="always", height=330)
        cols = ("pos", "id", "name", "tpl", "sav")
        self.skill_tv = ttk.Treeview(sf, columns=cols, show="headings", height=14)
        for c, w, t in (("pos", 50, "Position"), ("id", 210, "Skill ID"),
                        ("name", 110, "Chinese"), ("tpl", 60, "Template"), ("sav", 60, "Save")):
            self.skill_tv.heading(c, text=t)
            self.skill_tv.column(c, width=w, anchor="w" if c in ("id", "name") else "center")
        ssb = ttk.Scrollbar(sf, orient="vertical", command=self.skill_tv.yview)
        self.skill_tv.configure(yscrollcommand=ssb.set)
        ssb.pack(side="right", fill="y")
        self.skill_tv.pack(fill="both", expand=True)
        self.skill_tv.tag_configure("nz", background="#d9f2d9")
        self.skill_tv.tag_configure("diff", background="#ffe0e0")

        ef = ttk.LabelFrame(vp, text="Inventory / Equipment (editable)")
        vp.add(ef, stretch="always", height=240)
        hdr = ttk.Frame(ef)
        hdr.pack(side="top", fill="x", padx=4, pady=2)
        self.inv_summary = ttk.Label(hdr, text="Backpack = template · Equipment = instance", anchor="w")
        self.inv_summary.pack(side="left", fill="x", expand=True, anchor="w")
        ttk.Button(hdr, text="Save module", command=self.save_module_file).pack(side="right", padx=2)
        ttk.Button(hdr, text="Save game", command=self.save_save).pack(side="right", padx=2)
        self.inv_scroll = ScrolledFrame(ef)
        self.inv_scroll.pack(fill="both", expand=True)

    def build_item_tab(self):
        top = ttk.Frame(self.tab_item)
        top.pack(fill="x", padx=4, pady=3)
        ttk.Label(top, text="Search:").pack(side="left")
        self.isearch = tk.StringVar()
        e = ttk.Entry(top, textvariable=self.isearch)
        e.pack(side="left", fill="x", expand=True, padx=4)
        e.bind("<Return>", lambda ev: self.refresh_items())
        ttk.Button(top, text="Filter", command=self.refresh_items).pack(side="left")
        ttk.Button(top, text="All", command=lambda: (self.isearch.set(""), self.refresh_items())).pack(side="left", padx=2)

        hp = tk.PanedWindow(self.tab_item, orient="horizontal")
        hp.pack(fill="both", expand=True, padx=4, pady=4)
        lf = ttk.LabelFrame(hp, text="Item list")
        rf = ttk.Frame(hp)
        hp.add(lf, stretch="always", width=430)
        hp.add(rf, stretch="always")
        sb = ttk.Scrollbar(lf)
        sb.pack(side="right", fill="y")
        self.ilist = tk.Listbox(lf, yscrollcommand=sb.set, exportselection=False,
                                font=("Microsoft YaHei UI", 9))
        self.ilist.pack(fill="both", expand=True)
        sb.config(command=self.ilist.yview)
        self.ilist.bind("<<ListboxSelect>>", self.on_item_select)

        # 右: 上=详情(属性全解码), 下=属性编辑(价格/重量/丰裕度/后续数值)
        rvp = tk.PanedWindow(rf, orient="vertical")
        rvp.pack(fill="both", expand=True)
        det_f = ttk.LabelFrame(rvp, text="Item details (decoded)")
        rvp.add(det_f, stretch="always", height=360)
        self.idetail = self._mk_text(det_f)
        ed_f = ttk.LabelFrame(rvp, text="Attributes (editable; applied when saving items)")
        rvp.add(ed_f, stretch="always", height=170)
        ef = ttk.Frame(ed_f); ef.pack(fill="x", padx=4, pady=2)
        ttk.Label(ef, text="Price:").pack(side="left")
        self.edit_price_var = tk.StringVar()
        ttk.Entry(ef, textvariable=self.edit_price_var, width=8).pack(side="left", padx=(0, 6))
        ttk.Label(ef, text="Weight:").pack(side="left")
        self.edit_weight_var = tk.StringVar()
        ttk.Entry(ef, textvariable=self.edit_weight_var, width=10).pack(side="left", padx=(0, 6))
        ttk.Label(ef, text="Abundance:").pack(side="left")
        self.edit_abundance_var = tk.StringVar()
        ttk.Entry(ef, textvariable=self.edit_abundance_var, width=6).pack(side="left", padx=(0, 6))
        ttk.Button(ef, text="Apply basic", command=self.apply_item_basic).pack(side="left", padx=4)
        sf = ttk.Frame(ed_f); sf.pack(fill="x", padx=4, pady=2)
        ttk.Label(sf, text="Stats (space-separated):").pack(side="left")
        self.edit_stats_var = tk.StringVar()
        ttk.Entry(sf, textvariable=self.edit_stats_var).pack(side="left", fill="x", expand=True, padx=(0, 4))
        ttk.Button(sf, text="Apply stats", command=self.apply_item_stats).pack(side="left", padx=4)

    def build_compare_tab(self):
        top = ttk.Frame(self.tab_cmp)
        top.pack(fill="x", padx=4, pady=3)
        ttk.Label(top, text="After loading a save, compares troops.txt template values against the save instance values, troop by troop.").pack(side="left")
        ttk.Button(top, text="Run comparison", command=self.run_compare).pack(side="left", padx=6)
        self.cmp_detail = self._mk_text(self.tab_cmp, expand=True)

    def _mk_text(self, parent, expand=True):
        f = ttk.Frame(parent)
        f.pack(fill="both", expand=True)
        sb = ttk.Scrollbar(f)
        sb.pack(side="right", fill="y")
        t = tk.Text(f, wrap="word", font=("Microsoft YaHei UI", 9), yscrollcommand=sb.set)
        t.pack(fill="both", expand=True)
        sb.config(command=t.yview)
        t.tag_configure("diff", foreground="#c0392b")
        t.tag_configure("ok", foreground="#27863b")
        t.tag_configure("h", foreground="#1f6fb2")
        return t

    def after_build(self):
        # portable resolution: env > DEFAULT_MODULE > remembered > auto-detect
        mod = M.resolve_default_module(prefer=DEFAULT_MODULE)
        if mod:
            self.load_module(mod)
        else:
            self.open_module()

    # ---------------- 数据 ----------------
    def _detect_modules(self):
        """Scan every candidate Modules root for sub-dirs containing troops.txt."""
        found = {}
        for base in M.modules_roots():
            try:
                for d in sorted(base.iterdir()):
                    if d.is_dir() and (d / "troops.txt").exists():
                        found.setdefault(d.name, str(d))
            except Exception:
                continue
        dp = Path(DEFAULT_MODULE)
        if dp.is_dir() and (dp / "troops.txt").exists():
            found.setdefault(dp.name, str(dp))
        return found

    def _on_module_pick(self, ev=None):
        name = self.module_combo.get()
        path = self._module_map.get(name)
        if path:
            self.load_module(path)

    def load_module(self, folder):
        try:
            n = self.mod.load(folder)
        except Exception as ex:
            messagebox.showerror("Error", f"Failed to load module:\n{ex}")
            return
        # 切换模组时Clear上一次的残留选择/数据
        self.cur = None
        try:
            self.tlist.selection_clear(0, "end")
        except Exception:
            pass
        self._set(self.tdetail, "— no troop selected —")
        for r in self.skill_tv.get_children():
            self.skill_tv.delete(r)
        if getattr(self, "_inv_built", False):
            for w in self.inv_rows + self.equip_rows:
                try:
                    w["cmb"].current(0); w["type_var"].set(0); w["amt_var"].set(0)
                    w["cmb"].config(state="disabled"); w["sp_t"].config(state="disabled")
                    w["sp_a"].config(state="disabled"); w["btn"].config(state="disabled")
                except Exception:
                    pass
        self.mod_label.config(text=f"Module: {folder}   troops {n} items {len(self.mod.items)} skills {len(self.mod.skills)} factions {len(self.mod.factions)}")
        # Items下拉选项: 0 = 空(-1), 其后 0..nitems
        self.item_options = ["Empty (-1)"] + [f"{i}  {self.item_name(i)}" for i in range(len(self.mod.items))]
        # 阵营下拉选项(用于改领主投靠阵营)
        self.faction_options = [
            f"{i}  {_clean_cjk(self.mod.faction_names.get(f, f))}"
            for i, f in enumerate(self.mod.factions)
        ]
        # Troops下拉选项(用于Party Templates的Troops栈编辑)
        self.troop_options = ["Empty (-1)"] + [
            f"{i}  {self.tr_name(i)}" for i in range(len(self.mod.troops))
        ]
        if getattr(self, "fac_combo", None) is not None:
            self.fac_combo.config(values=self.faction_options)
        if getattr(self, "_inv_built", False):
            for w in self.inv_rows + self.equip_rows:
                w["cmb"]["values"] = self.item_options
        # 同步模块下拉框(手动选择的目录也动态纳入, 便于再次切换)
        try:
            nm = Path(folder).name
            if nm not in self._module_map:
                self._module_map[nm] = str(folder)
                self.module_combo.config(values=list(self._module_map.keys()))
            self.module_combo.set(nm)
        except Exception:
            pass
        self.refresh_troops()
        self.refresh_items()
        self.load_party_templates(Path(folder))
        self._load_items_full(folder)
        M.remember_module(folder)      # remember for next launch
        self.set_status(f"Loaded module: {folder}")

    # ---------------- Items完整属性载入 ----------------
    def _load_items_full(self, folder):
        """完整解析 item_kinds1.txt (含 flag1/flag2/价格/重量/丰裕度/数值), 用于Items页属性
        查看与编辑。与 self.mod.items (仅 ID 串) 同源、按File顺序一一对齐: items_full[i]
        对应 mod.items[i]。翻译名沿用 mb_model 已载入的 self.mod.item_names。"""
        path = Path(folder) / "item_kinds1.txt"
        try:
            self.items_full, self._items_meta, self._items_count = parse_items(str(path))
        except Exception as ex:
            self.items_full = []
            messagebox.showerror("Item parse failed", f"Could not fully parse item_kinds1.txt:\n{ex}")
            return
        # 防御: 若两者Count不一致, 仅以较小者可用, 避免越界
        self.item_dirty = False
        self.cur_item = None
        n = min(len(self.items_full), len(self.mod.items))
        self.set_status(f"Full item attributes loaded: {n} (mod.items={len(self.mod.items)})")

    def open_module(self):
        init = M.initial_dir_for_module_dialog()
        d = filedialog.askdirectory(title="Select module directory (contains troops.txt)", initialdir=init)
        if d:
            self.load_module(d)

    def open_save(self):
        if not self.mod.loaded:
            messagebox.showwarning("Info", "Please load a module first.")
            return
        init = M.get_last_save_dir() or M.initial_dir_for_module_dialog()
        p = filedialog.askopenfilename(title="Select save file (.sav)", initialdir=init,
                                       filetypes=[("Save files", "*.sav"), ("All files", "*.*")])
        if not p:
            return
        M.remember_save_dir(p)
        try:
            d = M.SaveDoc()
            d.mod = self.mod
            d.load_save(p)
            self.doc = d
            self.sav_label.config(text=f"Save: {os.path.basename(p)} ({len(d.starts)} troops)", foreground="#27863b")
            self.set_status("Save loaded; differences can be viewed.")
            self.refresh_troops()
        except Exception as ex:
            messagebox.showerror("Error", f"Failed to load save:\n{ex}")

    # 名称助手
    def tr_name(self, i):
        t = self.mod.troops[i]
        return _clean_cjk(self.mod.troop_names.get(t["tid"], t["raw"]))

    def fac_name(self, idx):
        if idx is None or idx < 0 or idx >= len(self.mod.factions):
            return "—"
        f = self.mod.factions[idx]
        return _clean_cjk(self.mod.faction_names.get(f, f))

    def item_name(self, iid):
        if iid is None or iid < 0 or iid >= len(self.mod.items):
            return f"?{iid}"
        it = self.mod.items[iid]
        return _clean_cjk(self.mod.item_names.get(it, it))

    def skill_label(self, j):
        if 0 <= j < len(self.mod.skills):
            t = self.mod.skills[j]
            return t, self.mod.skill_names.get(t, t[4:])
        return f"(reserved {j})", ""

    # ---------------- 列表 ----------------
    def refresh_troops(self):
        q = self.tsearch.get().strip().lower()
        self.filtered = []
        self.tlist.delete(0, "end")
        for i, t in enumerate(self.mod.troops):
            hay = f"{i} {t['tid']} {self.tr_name(i)}".lower()
            if q and q not in hay:
                continue
            self.filtered.append(i)
            tag = "hero" if (t.get("flags", 0) & M.TF_HERO) else ""
            self.tlist.insert("end", f"#{i:3d}  {self.tr_name(i)}   [{self.fac_name(t.get('faction', -1))}] {tag}")
        self.set_status(f"{len(self.filtered)} troops shown")

    def refresh_items(self):
        q = self.isearch.get().strip().lower()
        self.ifiltered = []
        self.ilist.delete(0, "end")
        for i, it in enumerate(self.mod.items):
            nm = self.mod.item_names.get(it, it)
            if q and q not in f"{i} {it} {nm}".lower():
                continue
            self.ifiltered.append(i)
            self.ilist.insert("end", f"[{i:3d}] {nm}   {it}")

    def set_status(self, s, ok=False, err=False):
        self.status.config(text=s)
        if ok:
            self.status.config(foreground="#27863b")
        elif err:
            self.status.config(foreground="#c0392b")
        else:
            self.status.config(foreground="#000000")

    # ---------------- 选中 ----------------
    def on_troop_select(self, ev=None):
        s = self.tlist.curselection()
        if not s:
            return
        i = self.filtered[s[0]]
        self.cur = i
        self.show_troop(i)

    def _update_troop_flag_calc(self):
        """根据勾选状态实时刷新『当前值』显示 (不改写File)。
        保留不在勾选表里的未知位, 避免丢失数据。"""
        if not getattr(self, "tf_vars", None):
            return
        known_all = 0
        for mask, _ in self.tf_vars:
            known_all |= mask
        base = 0
        if self.cur is not None:
            try:
                base = self.mod.troops[self.cur].get("flags", 0) & ~known_all & 0xFFFFFFFF
            except Exception:
                base = 0
        v = base
        for mask, var in self.tf_vars:
            if var.get():
                v |= mask
        try:
            self.flag_calc.config(
                text="Current value: 0x%08X (decimal %d)  Decoded: %s"
                % (v & 0xFFFFFFFF, v & 0xFFFFFFFF, ", ".join(M.decode_tf(v)) or "—"))
        except Exception:
            pass

    def on_troop_flag(self):
        """改写Troops头行 flags (troops.txt 模板, 影响新开局)。
        数值由勾选的 tf_* 项自动按位或计算; 不在勾选表里的未知位原样保留。"""
        if self.cur is None:
            self.set_status("Please select a troop first.", err=True)
            return
        if not getattr(self, "tf_vars", None):
            self.set_status("Flags panel not yet initialized.", err=True)
            return
        known_all = 0
        for mask, _ in self.tf_vars:
            known_all |= mask
        base = self.mod.troops[self.cur].get("flags", 0) & ~known_all & 0xFFFFFFFF
        v = base
        for mask, var in self.tf_vars:
            if var.get():
                v |= mask
        try:
            self.mod.set_troop_flags(self.cur, v)
        except Exception as ex:
            self.set_status(f"Write failed: {ex}", err=True)
            return
        self.show_troop(self.cur)
        self.set_status(f"Flags changed to 0x{v & 0xFFFFFFFF:08X} (template change; click 'Save module' to apply)", ok=True)

    def show_troop(self, i):
        t = self.mod.troops[i]
        flags = t.get("flags", 0)
        # 用勾选面板反映当前模板 flags (程序 ↔ 数值 自动同步)
        try:
            for mask, var in getattr(self, "tf_vars", []):
                var.set(bool(flags & mask))
            self._update_troop_flag_calc()
        except Exception:
            pass
        L = []
        L.append(f"Index #{i}     ID: {t['tid']}")
        L.append(f"Name: {self.tr_name(i)}")
        L.append(f"Faction: [{t.get('faction', -1)}] {self.fac_name(t.get('faction', -1))} (troops.txt template)")
        if self.doc is not None:
            sf = self.doc.get_faction(i)
            mark = "" if sf == t.get("faction", -1) else "   ← differs from template (defected)"
            L.append(f"       Save faction: [{sf}] {self.doc.get_faction_name(i)}{mark}")
        L.append(f"Flags: {flags} (0x{flags & 0xFFFFFFFF:08X})")
        L.append(f"  Decoded: {', '.join(M.decode_tf(flags))}")
        L.append(f"  Hero (tf_hero): {'Yes' if flags & M.TF_HERO else 'No'}"
                 f"  — heroes can't die / level independently / party skills apply / show HP percentage")
        # 升级成谁(Troops树): troops.txt 头行 upgrade1/upgrade2 (0 或 -1 = 无升级)
        u1, u2 = t.get("upgrade1", -1), t.get("upgrade2", -1)
        def _upname(u):
            if not u:                       # 0 / -1 均表示无升级目标
                return "—"
            if 0 <= u < len(self.mod.troops):
                return self.tr_name(u)
            return f"?{u}"
        L.append(f"Upgrades to: {_upname(u1)} / {_upname(u2)}")
        a = t.get("attrs", [])
        if len(a) >= 5:
            L.append("")
            L.append(f"Attributes: STR {a[0]} AGI {a[1]} INT {a[2]} CHA {a[3]}    Level {a[4]}")
        p = t.get("profs", [])
        if p:
            lbl = ["One-handed", "Two-handed", "Polearm", "Bow", "Crossbow", "Throwing", "Firearm"]
            L.append("Proficiency: " + "  ".join(f"{lbl[k] if k < len(lbl) else k}={p[k]}" for k in range(len(p))))
        sw = t.get("skills", [])
        L.append("")
        L.append(f"Skills row (6 packed words): {' '.join(str(x) for x in sw)}")
        L.append(f"Skills row (hex):    {' '.join(f'0x{x & 0xFFFFFFFF:08X}' for x in sw)}")

        # Compare(Summary)
        if self.doc is not None:
            L.append("")
            sa = self.doc.get_attrs(i)
            sl = self.doc.get_level(i)
            L.append(f"[Save instance] Attributes: STR {sa[0]} AGI {sa[1]} INT {sa[2]} CHA {sa[3]}    Level {sl}")
            L.append(f"            Attribute sum={sum(sa)} (level+20={sl + 20})")
            # 尾部『槽』(探索中: hero/领主这里有稀疏非零值, 推测含性格/声望/争议)
            nz = self.doc.get_tail_nonzero(i)
            if nz:
                L.append(f"            Tail slots (undecoded) {len(nz)} nonzero: {nz[:12]}")
            else:
                L.append("            Tail slots: (all empty — typical for regular troops)")

        self._set(self.tdetail, "\n".join(L))

        # 技能表
        for r in self.skill_tv.get_children():
            self.skill_tv.delete(r)
        tpl = decode_skills(sw)
        sav = None
        if self.doc is not None and i < len(self.doc.starts):
            o = self.doc.starts[i]
            sav = decode_skills([M.u32(self.doc.data, o + M.OFF_SKILL + 4 * k) for k in range(6)])
        for j in range(SKILL_SLOTS):
            sid, sname = self.skill_label(j)
            v = tpl[j]
            sv = sav[j] if sav else ""
            tags = ()
            if v:
                tags = ("nz",)
            if sav is not None and sav[j] != v:
                tags = ("diff",)
            self.skill_tv.insert("", "end", values=(j, sid, sname, v, sv), tags=tags)

        # 阵营下拉: 载入存档且Troops可靠时可改(即领主跳槽)
        self._sync_faction_combo(i)

        # Items: 可编辑网格 (无存档时显示模板值, 载入存档且Troops可靠时可编辑)
        self._fill_inv_editor(i)

    def _sync_faction_combo(self, i):
        """同步阵营下拉框到该Troops存档中的当前阵营。"""
        if getattr(self, "fac_combo", None) is None:
            return
        if self.doc is None:
            self.fac_combo.set("")
            self.fac_combo.config(state="disabled")
            self.fac_apply_btn.config(state="disabled")
            self.fac_defect_btn.config(state="disabled")
            self.fac_hint.config(text="Editable after loading a save")
            return
        if not self.doc.is_reliable(i):
            self.fac_combo.set("")
            self.fac_combo.config(state="disabled")
            self.fac_apply_btn.config(state="disabled")
            self.fac_defect_btn.config(state="disabled")
            self.fac_hint.config(text="No complete record in the save for this troop; read-only")
            return
        v = self.doc.get_faction(i)
        if 0 <= v < len(self.faction_options):
            self.fac_combo.config(state="readonly")
            self.fac_combo.set(self.faction_options[v])
            self.fac_apply_btn.config(state="normal")
            tpl = self.mod.troops[i].get("faction", -1)
            # 跳槽按钮: 需能找到该领主所率部队
            try:
                pys = self.doc.find_lord_party(i)
            except Exception:
                pys = []
            self.fac_defect_btn.config(state="normal" if pys else "disabled")
            pf = ""
            if pys:
                try:
                    pf = "  party #%d=%s" % (pys[0], self.doc.get_party_faction(pys[0]))
                except Exception:
                    pf = ""
            self.fac_hint.config(text=("defected (differs from template)" if v != tpl else "matches template")
                                      + pf + ("" if pys else "  (no party found — cannot defect)"))

    def _apply_faction(self):
        """把下拉框选中的阵营写入存档(令该领主投靠/跳槽到该阵营)。"""
        if self.doc is None or self.cur is None:
            return
        sel = self.fac_combo.current()
        if sel is None or sel < 0:
            self.set_status("Please select a faction first.", err=True)
            return
        try:
            self.doc.set_faction(self.cur, sel)
            self.set_status(
                f"Changed #{self.cur} {self.tr_name(self.cur)} faction to "
                f"[{sel}] {self.doc.get_faction_name(self.cur)} (takes effect when the save is saved)", ok=True)
            self._sync_faction_combo(self.cur)
            self.show_troop(self.cur)
        except Exception as ex:
            self.set_status(f"Failed to change faction: {ex}", err=True)

    def _do_defect(self):
        """Defect脚本链: 领主归属 + 其部队归属一并改为选中的阵营。"""
        if self.doc is None or self.cur is None:
            return
        sel = self.fac_combo.current()
        if sel is None or sel < 0:
            self.set_status("Please select a target faction first.", err=True)
            return
        try:
            changes = self.doc.defect_lord(self.cur, sel)
        except Exception as ex:
            self.set_status(f"Defection failed: {ex}", err=True)
            return
        if not changes:
            self.set_status(f"#{self.cur} {self.tr_name(self.cur)} is already in that faction; nothing to change.")
            return
        detail = "; ".join(f"{d} {o}→{nv}" for d, o, nv in changes)
        self.set_status(f"Defection executed: #{self.cur} {self.tr_name(self.cur)} — {detail} "
                        f"(takes effect when the save is saved)", ok=True)
        self._sync_faction_combo(self.cur)
        self.show_troop(self.cur)

    def on_item_select(self, ev=None):
        s = self.ilist.curselection()
        if not s:
            return
        i = self.ifiltered[s[0]]
        self.cur_item = i
        if 0 <= i < len(self.items_full):
            it = self.items_full[i]
            self._set(self.idetail, self.format_item(it))
            self._fill_item_edit(it)
        else:
            it = self.mod.items[i]
            self._set(self.idetail,
                      f"Index: {i}\nID: {it}\nName: {self.mod.item_names.get(it, it)}\n\n(full attributes not parsed; only ID available)")

    # ---------------- Items属性查看/编辑 ----------------
    def _item_full_name(self, it):
        return self.mod.item_names.get(it["id"], _clean_cjk(it["raw_name"]))

    def format_item(self, it):
        """把完整Items字典解码为可读文本 (移植自旧版查看器, 已验证可用)。"""
        lines = []
        name = self._item_full_name(it)
        stats = it["stats"]
        is_goods, is_horse, is_shield, is_ammo, is_ranged, is_throwing = self.item_categories(it)

        lines.append(f"Name: {name}")
        lines.append(f"Item ID: {it['id']}")
        lines.append(f"Internal index: {it['idx']}")
        lines.append("")
        lines.append("Basic:")
        lines.append(f"  Price: {fmt(it['price'])}")
        lines.append(f"  Weight: {fmt(it['weight'])}")
        lines.append(f"  Abundance: {fmt(it['abundance'])}")
        lines.append(f"  Flag 1: {fmt(it['flag1'])}")
        lines.append(f"  Flag 2: {it['flag2']}")
        lines.append(f"  Property flag: {it['prop']}")
        lines.append("")
        if stats:
            lines.append("Stats:")
            lines.append("  " + " ".join(fmt(x) for x in stats))
            lines.append("")

        if len(stats) >= 4:
            lines.append("Defense / requirements:")
            if is_goods:
                lines.append(f"  Goods quantity: {fmt(stats[0])}")
            else:
                lines.append(f"  Head armor: {fmt(stats[0])}")
            if is_horse:
                lines.append(f"  Horse defense: {fmt(stats[1])}")
            elif is_shield:
                lines.append(f"  Shield resistance: {fmt(stats[1])}")
            else:
                lines.append(f"  Body armor: {fmt(stats[1])}")
            if is_ranged or is_ammo or is_throwing:
                acc = stats[2]
                if acc == 0:
                    lines.append("  Accuracy: 99 (raw value 0)")
                else:
                    lines.append(f"  Accuracy: {fmt(acc)}")
            else:
                lines.append(f"  Leg armor: {fmt(stats[2])}")
            lines.append(f"  Requirement / difficulty: {fmt(stats[3])}")

        if is_horse and len(stats) >= 10:
            lines.append("")
            lines.append("Horse fields:")
            lines.append(f"  HP: {fmt(stats[4])}")
            lines.append(f"  Speed: {fmt(stats[5])}")
            lines.append(f"  Maneuver: {fmt(stats[6])}")
            lines.append(f"  Field 7: {fmt(stats[7])}")
            lines.append(f"  Charge: {fmt(stats[9])}")

        if is_shield and len(stats) >= 8:
            lines.append("")
            lines.append("Shield fields:")
            lines.append(f"  Resistance: {fmt(stats[1])}")
            lines.append(f"  Durability: {fmt(stats[4])}")
            lines.append(f"  Speed: {fmt(stats[5])}")
            if len(stats) > 7:
                lines.append(f"  Size: {fmt(stats[7])}")

        if is_ammo and len(stats) >= 11:
            lines.append("")
            lines.append("Ammo / projectile fields:")
            lines.append(f"  Length/model scale (4th from end): {fmt(stats[-4])}")
            lines.append(f"  Count (3rd from end): {fmt(stats[-3])}")
            lines.append(f"  Damage (2nd from end): {decode_damage(stats[-2])}")

        weapon_like = (
            not is_goods
            and not is_horse
            and not is_shield
            and not is_ammo
            and len(stats) >= 11
            and (
                (stats[5] not in (None, 0)) or
                (stats[-2] not in (None, 0)) or
                (stats[-1] not in (None, 0))
            )
        )
        if weapon_like:
            lines.append("")
            lines.append("Weapon fields:")
            lines.append(f"  Speed (6th from end): {fmt(stats[-6])}")
            if is_ranged:
                lines.append(f"  Missile speed (5th from end): {fmt(stats[-5])}")
            elif is_throwing:
                lines.append(f"  Missile speed (5th from end): {fmt(stats[-5])}")
                lines.append(f"  Count (3rd from end): {fmt(stats[-3])}")
            else:
                lines.append(f"  Range (4th from end): {fmt(stats[-4])}")
            lines.append(f"  Damage 1 (2nd from end): {decode_damage(stats[-2])}")
            lines.append(f"  Damage 2 (last): {decode_damage(stats[-1])}")

        extra = [e for e in it.get("extra", []) if e.strip() and e.strip() != "0"]
        if extra:
            lines.append("")
            lines.append("Extra lines:")
            for e in extra[:5]:
                lines.append("  " + e)
        lines.append("")
        lines.append("Raw first line:")
        lines.append(it["raw_first_line"])
        return "\n".join(lines)

    def _fill_item_edit(self, it):
        self.edit_price_var.set(fmt(it["price"]))
        self.edit_weight_var.set(fmt(it["weight"]))
        self.edit_abundance_var.set(fmt(it["abundance"]))
        self.edit_stats_var.set(" ".join(fmt(x) for x in it["stats"]))

    def apply_item_basic(self):
        if self.cur_item is None or not (0 <= self.cur_item < len(self.items_full)):
            return
        it = self.items_full[self.cur_item]
        try:
            price = int(self.edit_price_var.get())
            weight = float(self.edit_weight_var.get())
            abundance = int(self.edit_abundance_var.get())
        except ValueError:
            messagebox.showwarning("Input error", "Price/abundance must be integers; weight must be a number.")
            return
        it["price"] = price
        it["weight"] = weight
        it["abundance"] = abundance
        self.item_dirty = True
        self._set(self.idetail, self.format_item(it))
        self.set_status("Item basic attributes modified (unsaved)")

    def apply_item_stats(self):
        if self.cur_item is None or not (0 <= self.cur_item < len(self.items_full)):
            return
        it = self.items_full[self.cur_item]
        toks = self.edit_stats_var.get().split()
        try:
            vals = [int(t) for t in toks]
        except ValueError:
            messagebox.showwarning("Input error", "Stats must be integers (space-separated).")
            return
        it["stats"] = vals
        self.item_dirty = True
        self._set(self.idetail, self.format_item(it))
        self.set_status("Item stats modified (unsaved)")

    def save_items(self, backup=True):
        """把 items_full 写回 item_kinds1.txt。仅替换各Items首行后部的 flag1/flag2/价格/属性
        Flag/重量/丰裕度/数值 字段, 前部 (id/name/plural/mesh tokens) 与附加行原样保留。"""
        if not self.items_full:
            messagebox.showwarning("Info", "Module not loaded yet.")
            return
        folder = self.mod.folder
        p = Path(folder) / "item_kinds1.txt"
        if not p.exists():
            messagebox.showerror("Error", f"File not found: {p}")
            return
        if backup:
            import shutil, datetime
            stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            bak = p.with_name(p.stem + f".{stamp}.bak")
            shutil.copy2(p, bak)
        # 探测原File换行符, 尽量保留
        raw = p.read_bytes()
        raw_text = raw.decode("utf-8", errors="replace")
        nl = "\r\n" if "\r\n" in raw_text else "\n"
        lines = [self._items_meta, self._items_count]
        for it in self.items_full:
            parts = it["raw_first_line"].split()
            try:
                mesh_count = int(parts[3]) if len(parts) > 3 and parts[3].lstrip("-").isdigit() else 0
            except Exception:
                mesh_count = 0
            if mesh_count is None or mesh_count < 0:
                mesh_count = 0
            start = 4 + 2 * mesh_count
            new_rest = [
                str(it["flag1"]) if it["flag1"] is not None else "0",
                it["flag2"],
                str(it["price"]) if it["price"] is not None else "0",
                it["prop"],
                f"{it['weight']:.6f}" if it["weight"] is not None else "0.000000",
                str(it["abundance"]) if it["abundance"] is not None else "0",
            ]
            new_rest += [str(x) if x is not None else "0" for x in it["stats"]]
            new_parts = parts[:start] + new_rest
            lines.append(" ".join(new_parts))
            for e in it.get("extra", []):
                lines.append(e)
        out = nl.join(lines)
        p.write_bytes(out.encode("utf-8"))
        self.item_dirty = False
        self.set_status(f"Items saved with backup: {p.name}", ok=True)

    # ---------------- Items编辑 ----------------
    def _ensure_inv_editor(self):
        """74 行槽控件 (背包 64 + 装备 10) 只创建一次, 之后仅更新值 —— 避免每次
        选中Troops都重建几百个控件造成卡顿。"""
        if getattr(self, "_inv_built", False):
            return
        parent = self.inv_scroll.inner
        tk.Label(parent, text="Backpack inventory (64 slots)", anchor="w").pack(anchor="w", padx=4, pady=(2, 0))
        self.inv_rows = [self._make_inv_row(parent, k, False) for k in range(M.INV_SLOTS)]
        tk.Label(parent, text="Equipment slots (10)", anchor="w").pack(anchor="w", padx=4, pady=(6, 0))
        self.equip_rows = [self._make_inv_row(parent, k, True) for k in range(M.EQUIP_SLOTS)]
        self._inv_built = True

    def _make_inv_row(self, parent, k, is_equip):
        r = ttk.Frame(parent)
        r.pack(fill="x", padx=6, pady=1)
        label = ("Equip#" if is_equip else "Bag#") + str(k)
        ttk.Label(r, text=label, width=8).pack(side="left")
        cmb = ttk.Combobox(r, values=self.item_options, width=42, state="readonly")
        cmb.pack(side="left", padx=2)
        ttk.Label(r, text="Type").pack(side="left")
        type_var = tk.IntVar(value=0)
        sp_t = ttk.Spinbox(r, from_=0, to=255, width=5, textvariable=type_var)
        sp_t.pack(side="left", padx=1)
        ttk.Label(r, text="Count").pack(side="left")
        amt_var = tk.IntVar(value=0)
        sp_a = ttk.Spinbox(r, from_=0, to=16777215, width=9, textvariable=amt_var)
        sp_a.pack(side="left", padx=1)
        btn = ttk.Button(r, text="Clear", command=lambda k=k, eq=is_equip: self._clear_slot(k, eq))
        btn.pack(side="left", padx=2)

        def on_item(ev=None):
            self._apply_slot(k, is_equip, item_sel=cmb.current())
        def on_type(*a):
            self._apply_slot(k, is_equip)
        def on_amt(*a):
            self._apply_slot(k, is_equip)
        cmb.bind("<<ComboboxSelected>>", on_item)
        sp_t.bind("<FocusOut>", on_type); sp_t.bind("<Return>", on_type)
        sp_a.bind("<FocusOut>", on_amt); sp_a.bind("<Return>", on_amt)
        return dict(cmb=cmb, type_var=type_var, amt_var=amt_var, sp_t=sp_t, sp_a=sp_a, btn=btn)

    def _set_row(self, w, pair):
        iid, mod = pair
        if iid == -1:
            w["cmb"].current(0)
        else:
            idx = (iid + 1) if 0 <= iid < len(self.item_options) - 1 else 0
            w["cmb"].current(idx)
        t, a = M.SaveDoc.decode_mod(mod)
        w["type_var"].set(t)
        w["amt_var"].set(a)

    def _tpl_inv(self, i, n):
        """troops.txt 模板Items行 (仅背包; 装备在模板中无独立段)。"""
        t = self.mod.troops[i]
        inv = t.get("inv", [])
        out = [(-1, 0)] * n
        for k in range(min(n, len(inv) // 2)):
            iid = inv[2 * k]
            if iid != -1:
                out[k] = (iid, inv[2 * k + 1])
        return out

    def _fill_inv_editor(self, i):
        self._ensure_inv_editor()
        # 背包(64) = troops.txt 模板 (抽象Troops), 始终可编辑, 无需存档
        inv = self._tpl_inv(i, M.INV_SLOTS)
        # 装备(10) = 存档实例, 仅载档且Troops可靠时可编辑
        if self.doc is not None and self.doc.is_reliable(i):
            eq = self.doc.get_equipment(i); eq_editable = True
        else:
            eq = [(-1, 0)] * M.EQUIP_SLOTS; eq_editable = False
        for w in self.inv_rows + self.equip_rows:
            w["cmb"].config(state="normal")
        for k, w in enumerate(self.inv_rows):
            self._set_row(w, inv[k])
        for k, w in enumerate(self.equip_rows):
            self._set_row(w, eq[k])
        # 背包始终可编辑; 装备按条件
        for w in self.inv_rows:
            w["cmb"].config(state="readonly")
            w["sp_t"].config(state="normal"); w["sp_a"].config(state="normal"); w["btn"].config(state="normal")
        cstate = "readonly" if eq_editable else "disabled"
        sstate = "normal" if eq_editable else "disabled"
        for w in self.equip_rows:
            w["cmb"].config(state=cstate); w["sp_t"].config(state=sstate)
            w["sp_a"].config(state=sstate); w["btn"].config(state=sstate)
        self._update_inv_summary(i)

    def _update_inv_summary(self, i):
        if i is None:
            self.inv_summary.config(text="")
            return
        tpl = "Backpack (64) = troops.txt template · abstract troop · applies to new games"
        if getattr(self.mod, "_tpl_dirty", False):
            tpl += " [unsaved]"
        if self.doc is not None and self.doc.is_reliable(i):
            eq = "Equipment (10) = save instance · applies via 'Save game'"
        else:
            eq = "Equipment (10) = requires save loaded and reliable troop"
        self.inv_summary.config(text=f"{tpl}   |   {eq}")

    def _collect_inv_flat(self):
        """从 64 个背包行收集扁平 int 列表 (Itemsid/修饰符 成对)。"""
        flat=[]
        for w in self.inv_rows:
            sel=w["cmb"].current()
            iid=-1 if (sel is None or sel<=0) else sel-1
            iid=max(-1, min(self.mod.nitems, iid))
            try: t=max(0,min(255,int(w["type_var"].get() or 0)))
            except Exception: t=0
            try: a=max(0,min(16777215,int(w["amt_var"].get() or 0)))
            except Exception: a=0
            flat.append(iid); flat.append(M.SaveDoc.encode_mod(a, t))
        return flat

    def _apply_slot(self, k, is_equip, item_sel=None):
        i = self.cur
        if i is None:
            return
        w = (self.equip_rows if is_equip else self.inv_rows)[k]
        sel = w["cmb"].current() if item_sel is None else item_sel
        # 合法性校验: Items id 必须在 [-1, nitems]
        item_id = -1 if (sel is None or sel <= 0) else sel - 1
        item_id = max(-1, min(self.mod.nitems, item_id))
        try:
            t = max(0, min(255, int(w["type_var"].get())))
        except Exception:
            t = 0
        try:
            a = max(0, min(16777215, int(w["amt_var"].get())))
        except Exception:
            a = 0
        mod = M.SaveDoc.encode_mod(a, t)
        if is_equip:
            if not (self.doc is not None and self.doc.is_reliable(i)):
                return
            try:
                self.doc.set_equipment_slot(i, k, item_id, mod)
                self._update_inv_summary(i)
                self.set_status(f"Wrote #{i} equip slot {k}: item={item_id} type={t} count={a} (save instance)")
            except Exception as ex:
                self.set_status(f"Write failed: {ex}", err=True)
        else:
            # 抽象Troops模板 (troops.txt) —— 无需存档即可修改
            try:
                flat = self._collect_inv_flat()
                self.mod.set_troop_inventory(i, flat)
                self._update_inv_summary(i)
                self.set_status(f"Wrote #{i} backpack slot {k}: item={item_id} type={t} count={a} (troops.txt template · unsaved)")
            except Exception as ex:
                self.set_status(f"Write failed: {ex}", err=True)

    def _clear_slot(self, k, is_equip):
        i = self.cur
        if i is None:
            self.set_status("Please select a troop first.", err=True)
            return
        w = (self.equip_rows if is_equip else self.inv_rows)[k]
        w["cmb"].current(0); w["type_var"].set(0); w["amt_var"].set(0)
        if is_equip:
            if not (self.doc is not None and self.doc.is_reliable(i)):
                self.set_status("Equip slots can only be cleared when a save is loaded and the troop is reliable.", err=True)
                return
            self._apply_slot(k, True, item_sel=0)
        else:
            self._apply_slot(k, False, item_sel=0)

    def save_module_file(self):
        try:
            out = self.mod.save_module(backup=True)
            self.set_status(f"Module saved with backup: {os.path.basename(str(out))}", ok=True)
            self._update_inv_summary(self.cur)
        except Exception as ex:
            messagebox.showerror("Save failed", str(ex))

    def save_save(self):
        if self.doc is None:
            messagebox.showinfo("Info", "First load a save from the File menu.")
            return
        try:
            out = self.doc.save(backup=True)
            self.set_status(f"Saved with backup: {os.path.basename(str(out))}", ok=True)
        except Exception as ex:
            messagebox.showerror("Save failed", str(ex))

    def on_close(self):
        if getattr(self.mod, "_tpl_dirty", False):
            if not messagebox.askyesno("Unsaved module changes", "Your changes to the troops.txt template have not been saved. Exit anyway?"):
                return
        if getattr(self, "item_dirty", False):
            if not messagebox.askyesno("Unsaved item changes", "Your changes to item_kinds1.txt are not saved. Exit anyway?"):
                return
        self.root.destroy()

    # ---------------- 对照 ----------------
    def _cmp_stat(self, lo, hi):
        """统计区间 [lo,hi] 内『模板 vs 存档』的一致数, 返回 (stat, n, samples)。"""
        d = self.doc
        stat = {"attr": 0, "level": 0, "prof": 0, "skill": 0, "inv": 0}
        n = 0
        samples = []
        hi = min(hi, len(d.starts) - 1, len(self.mod.troops) - 1)
        for i in range(max(lo, 0), hi + 1):
            t = self.mod.troops[i]
            n += 1
            ta = t.get("attrs", [])
            tp = t.get("profs", [])
            ts = t.get("skills", [])
            sa = d.get_attrs(i)
            sl = d.get_level(i)
            sp = [int(round(x)) for x in d.get_profs(i)]
            ss = decode_skills([M.u32(d.data, d.starts[i] + M.OFF_SKILL + 4 * k) for k in range(6)])
            tpl_sk = decode_skills(ts)
            if len(ta) >= 5:
                if sa == ta[:4]:
                    stat["attr"] += 1
                if sl == ta[4]:
                    stat["level"] += 1
            if tp and sp[:len(tp)] == tp:
                stat["prof"] += 1
            if ss == tpl_sk:
                stat["skill"] += 1
            # Items多重集 (背包 + 装备)
            want = {}
            for k in range(0, len(t.get("inv", [])) - 1, 2):
                if t["inv"][k] != -1:
                    want[t["inv"][k]] = want.get(t["inv"][k], 0) + 1
            got = {}
            for it_, _ in d.get_inventory(i):
                if it_ not in (-1, 0):
                    got[it_] = got.get(it_, 0) + 1
            for it_, _ in d.get_equipment(i):
                if it_ not in (-1, 0):
                    got[it_] = got.get(it_, 0) + 1
            if got == want:
                stat["inv"] += 1
            if len(samples) < 10:
                samples.append((i, self.tr_name(i), ta[:4], sa, tp, sp[:len(tp)],
                                [j for j in range(SKILL_SLOTS) if ss[j] != tpl_sk[j]][:6]))
        return stat, n, samples

    def run_compare(self):
        if self.doc is None:
            messagebox.showinfo("Info", "First load a save from the File menu.")
            return
        total = len(self.doc.starts)
        s_all, n_all, _ = self._cmp_stat(0, total - 1)
        s_std, n_std, samples = self._cmp_stat(6, 140)
        out = []
        out.append(f"Troops in save: {total}\n\n")

        def block(title, stat, n):
            out.append(title + "\n")
            for k, lbl in (("level", "Level"), ("prof", "Proficiency"), ("skill", "Skills"),
                           ("inv", "Items"), ("attr", "Attributes")):
                v = stat[k]
                out.append(f"   {lbl:6s}: {v}/{n}  ({v * 100 // max(n, 1)}%)\n")
            out.append("\n")

        block("[A] Regular soldiers #6~#140 (gold standard: not companions; instanced values should match the template)\n"
              "     This is a reliable check of whether the parsing is correct.", s_std, n_std)
        block("[B] All troops #0~#%d (includes uninstanced lord/companion blueprints; match rate is naturally lower)" % (total - 1),
              s_all, n_all)

        out.append("How to read this:\n")
        out.append("  - In [A], Level / Proficiency should be near 100%; if clearly lower, a field offset is misread.\n")
        out.append("  - Low [B] values are normal: lords/companions (e.g. #203 King Edward) in the save are mostly\n")
        out.append("    uninstanced blueprints — attributes 0, flags 0; differing from troops.txt is not a parse error.\n")
        out.append("  - Attributes: save = template + random allocation, so sum = level + 20; differing from the template is expected.\n")
        out.append("  - Skills: save = template + a few random skill points.\n")
        out.append("  - Items: may have random equipment variants (same-category swaps), so 100% is usually not reached.\n")
        out.append("\nSamples (from #6): name / template attrs→save attrs / template prof→save prof / skill diff positions\n")
        for s in samples:
            out.append(f"  #{s[0]} {s[1]}\n     Attr {s[2]} -> {s[3]}\n"
                       f"     Prof {s[4]} -> {s[5]}\n     Skill diffs {s[6]}\n")
        self._set(self.cmp_detail, "".join(out))

    def _set(self, w, txt):
        w.configure(state="normal")
        w.delete("1.0", "end")
        w.insert("1.0", txt)
        w.configure(state="disabled")


    # ---------------- Party Templates(party_templates.txt)编辑 ----------------
    def load_party_templates(self, folder):
        p = Path(folder) / "party_templates.txt"
        self.pt_file = p
        if not p.exists():
            self.party_templates = []
            self.pt_original = []
            self.set_status(f"party_templates.txt not found: {p} (this module may have no party templates)", err=True)
            return
        raw = p.read_bytes()
        self.pt_line_sep = "\r\n" if b"\r\n" in raw else "\n"   # 还原原File换行
        text = raw.decode("utf-8", errors="replace")
        self.pt_header = text.split("\n", 1)[0].rstrip("\r") or "partytemplatesfile version 1"
        self.party_templates = parse_party_templates(text)
        self.pt_original = copy.deepcopy(self.party_templates)
        if getattr(self, "ptlist", None) is not None:
            self.refresh_pt()
        self.set_status(f"Loaded party templates: {p.name}  ({len(self.party_templates)} templates)")

    def build_pt_tab(self):
        top = ttk.Frame(self.tab_pt)
        top.pack(fill="x", padx=4, pady=3)
        ttk.Label(top, text="Search:").pack(side="left")
        self.pt_search = tk.StringVar()
        e = ttk.Entry(top, textvariable=self.pt_search)
        e.pack(side="left", fill="x", expand=True, padx=4)
        e.bind("<Return>", lambda ev: self.refresh_pt())
        ttk.Button(top, text="Filter", command=self.refresh_pt).pack(side="left")
        ttk.Button(top, text="All", command=lambda: (self.pt_search.set(""), self.refresh_pt())).pack(side="left", padx=2)

        hp = tk.PanedWindow(self.tab_pt, orient="horizontal")
        hp.pack(fill="both", expand=True, padx=4, pady=4)
        lf = ttk.LabelFrame(hp, text="Party template list")
        rf = ttk.Frame(hp)
        hp.add(lf, stretch="always", width=360)
        hp.add(rf, stretch="always")
        sb = ttk.Scrollbar(lf)
        sb.pack(side="right", fill="y")
        self.ptlist = tk.Listbox(lf, yscrollcommand=sb.set, exportselection=False,
                                 font=("Microsoft YaHei UI", 9))
        self.ptlist.pack(fill="both", expand=True)
        sb.config(command=self.ptlist.yview)
        self.ptlist.bind("<<ListboxSelect>>", self.on_pt_select)

        vp = tk.PanedWindow(rf, orient="vertical")
        vp.pack(fill="both", expand=True)
        hf = ttk.LabelFrame(vp, text="Template info / troop stack editing")
        vp.add(hf, stretch="always", height=360)
        idbar = ttk.Frame(hf)
        idbar.pack(side="top", fill="x", padx=4, pady=2)
        self.pt_id_label = ttk.Label(idbar, text="(not selected)", anchor="w")
        self.pt_id_label.pack(side="left", fill="x", expand=True)
        ttk.Label(idbar, text="flags:").pack(side="left")
        self.pt_flags_var = tk.IntVar(value=0)
        self.pt_flags_entry = ttk.Spinbox(idbar, from_=-2147483648, to=2147483647,
                                         width=12, textvariable=self.pt_flags_var)
        self.pt_flags_entry.pack(side="left", padx=2)
        self.pt_flags_entry.bind("<FocusOut>", lambda ev: self._apply_pt_flag())
        self.pt_flags_entry.bind("<Return>", lambda ev: self._apply_pt_flag())
        self.pt_stack_count = ttk.Label(idbar, text="Troop stacks: 0", foreground="#888")
        self.pt_stack_count.pack(side="left", padx=6)

        stackbar = ttk.Frame(hf)
        stackbar.pack(side="top", fill="x", padx=4, pady=2)
        ttk.Button(stackbar, text="+ Add troop stack", command=self._add_pt_stack).pack(side="left", padx=2)
        ttk.Button(stackbar, text="Save Party Templates (party_templates.txt)",
                   command=self.save_party_templates).pack(side="left", padx=2)

        self.pt_stack_scroll = ScrolledFrame(hf)
        self.pt_stack_scroll.pack(fill="both", expand=True)
        self.pt_stack_inner = self.pt_stack_scroll.inner

        df = ttk.LabelFrame(vp, text="Template text (read-only preview)")
        vp.add(df, stretch="always", height=110)
        self.pt_detail = self._mk_text(df)

        lf2 = ttk.LabelFrame(vp, text="Change log (shown this session; also written to party_template_edit_log.txt)")
        vp.add(lf2, stretch="always", height=110)
        self.pt_log_text = self._mk_text(lf2)
        self._render_pt_log()

    def refresh_pt(self):
        q = self.pt_search.get().strip().lower()
        self.pt_filtered = []
        self.ptlist.delete(0, "end")
        for i, t in enumerate(self.party_templates):
            hay = f"{i} {t['id']} {t['name']}".lower()
            if q and q not in hay:
                continue
            self.pt_filtered.append(i)
            self.ptlist.insert("end", f"#{i:2d}  {t['id']}   [{t['name']}]  ({len(t['stacks'])} stacks)")

    def on_pt_select(self, ev=None):
        s = self.ptlist.curselection()
        if not s:
            return
        self.show_pt(self.pt_filtered[s[0]])

    def show_pt(self, i):
        self.pt_cur = i
        t = self.party_templates[i]
        self.pt_id_label.config(text=f"ID: {t['id']}    Name: {t['name']}")
        self.pt_flags_var.set(int(t["flags"]))
        self._fill_pt_stacks(t)
        self.pt_stack_count.config(text=f"Troop stacks: {len(t['stacks'])}")
        self.pt_detail.configure(state="normal")
        self.pt_detail.delete("1.0", "end")
        self.pt_detail.insert("1.0", self._pt_raw(t))
        self.pt_detail.configure(state="disabled")

    def _pt_raw(self, t):
        L = [f"{t['id']}  ({t['name']})", f"flags={t['flags']}  fixed0={t['fixed0']}", ""]
        for k, s in enumerate(t["stacks"]):
            L.append(f"  Stack #{k}: troop={s[0]} countA={s[1]} countB={s[2]} flag={s[3]}")
        L.append("")
        L.append("(Count A/B are the template's min/max count parameters — their order in the original file may be reversed; edit the values as-is.")
        L.append("  Flag is the stack flag; troop index -1 = empty)")
        return "\n".join(L)

    def _make_pt_row(self, parent, k):
        r = ttk.Frame(parent)
        r.pack(fill="x", padx=6, pady=1)
        ttk.Label(r, text=f"Stack #{k}", width=6).pack(side="left")
        cmb = ttk.Combobox(r, values=self.troop_options, width=40, state="readonly")
        cmb.pack(side="left", padx=2)
        ttk.Label(r, text="Count A").pack(side="left")
        a_var = tk.IntVar(value=0)
        sp_a = ttk.Spinbox(r, from_=0, to=100000, width=8, textvariable=a_var)
        sp_a.pack(side="left", padx=1)
        ttk.Label(r, text="Count B").pack(side="left")
        b_var = tk.IntVar(value=0)
        sp_b = ttk.Spinbox(r, from_=0, to=100000, width=8, textvariable=b_var)
        sp_b.pack(side="left", padx=1)
        ttk.Label(r, text="Flag").pack(side="left")
        f_var = tk.IntVar(value=0)
        sp_f = ttk.Spinbox(r, from_=-2147483648, to=2147483647, width=10, textvariable=f_var)
        sp_f.pack(side="left", padx=1)
        btn = ttk.Button(r, text="Delete", command=lambda k=k: self._del_pt_stack(k))
        btn.pack(side="left", padx=2)

        def on_troop(ev=None):
            self._apply_pt_stack(k, 0, cmb.current())
        def on_a(*a):
            self._apply_pt_stack(k, 1, a_var.get())
        def on_b(*a):
            self._apply_pt_stack(k, 2, b_var.get())
        def on_f(*a):
            self._apply_pt_stack(k, 3, f_var.get())
        cmb.bind("<<ComboboxSelected>>", on_troop)
        sp_a.bind("<FocusOut>", on_a); sp_a.bind("<Return>", on_a)
        sp_b.bind("<FocusOut>", on_b); sp_b.bind("<Return>", on_b)
        sp_f.bind("<FocusOut>", on_f); sp_f.bind("<Return>", on_f)
        return dict(frame=r, cmb=cmb, a_var=a_var, b_var=b_var, f_var=f_var,
                    sp_a=sp_a, sp_b=sp_b, sp_f=sp_f, btn=btn)

    def _fill_pt_stacks(self, tmpl):
        for w in self.pt_rows:
            try:
                w["frame"].destroy()
            except Exception:
                pass
        self.pt_rows = []
        for k, s in enumerate(tmpl["stacks"]):
            w = self._make_pt_row(self.pt_stack_inner, k)
            tr = int(s[0])
            w["cmb"].current(0 if tr < 0 else tr + 1)
            w["a_var"].set(int(s[1])); w["b_var"].set(int(s[2])); w["f_var"].set(int(s[3]))
            self.pt_rows.append(w)

    def _add_pt_stack(self):
        i = self.pt_cur
        if i is None:
            self.set_status("Please select a party template on the left first.", err=True)
            return
        self.party_templates[i]["stacks"].append([-1, 0, 0, 0])
        self._fill_pt_stacks(self.party_templates[i])
        self.pt_stack_count.config(text=f"Troop stacks: {len(self.party_templates[i]['stacks'])}")

    def _del_pt_stack(self, k):
        i = self.pt_cur
        if i is None:
            return
        st = self.party_templates[i]["stacks"]
        if 0 <= k < len(st):
            st.pop(k)
            self._fill_pt_stacks(self.party_templates[i])
            self.pt_stack_count.config(text=f"Troop stacks: {len(st)}")

    def _apply_pt_flag(self):
        i = self.pt_cur
        if i is None:
            return
        try:
            v = int(self.pt_flags_var.get())
        except Exception:
            v = 0
        self.party_templates[i]["flags"] = v

    def _apply_pt_stack(self, k, field, raw):
        i = self.pt_cur
        if i is None:
            return
        st = self.party_templates[i]["stacks"]
        if k >= len(st):
            return
        if field == 0:
            idx = raw if isinstance(raw, int) else 0
            if idx is None or idx <= 0:
                st[k][0] = -1
            else:
                cap = max(0, len(self.mod.troops) - 1)
                st[k][0] = max(-1, min(cap, idx - 1))
        else:
            try:
                v = int(raw)
            except Exception:
                v = 0
            st[k][field] = v

    def save_party_templates(self):
        if self.pt_file is None or not self.party_templates:
            messagebox.showinfo("Info", "First load a module that contains party_templates.txt.")
            return
        records = []
        for cur, orig in zip(self.party_templates, self.pt_original):
            tid = cur["id"]
            if int(cur["flags"]) != int(orig["flags"]):
                records.append((tid, "flags", orig["flags"], cur["flags"]))
            cs, os_ = cur["stacks"], orig["stacks"]
            if len(cs) != len(os_):
                records.append((tid, "stack count", len(os_), len(cs)))
            else:
                for k in range(len(cs)):
                    for f, fn in enumerate(("troop", "count A", "count B", "flag")):
                        if int(cs[k][f]) != int(os_[k][f]):
                            records.append((tid, f"Stack #{k + 1}.{fn}", os_[k][f], cs[k][f]))
        if not records:
            self.set_status("Party templates have no changes; nothing to save.", ok=True)
            return
        try:
            if self.pt_file.exists():
                shutil.copy2(str(self.pt_file), str(self.pt_file) + ".bak")
            out = serialize_party_templates(self.party_templates, self.pt_header,
                                           self.pt_line_sep)
            self.pt_file.write_text(out, encoding="utf-8")
            self.pt_original = copy.deepcopy(self.party_templates)
            self._log_pt_change(records)
            self.set_status(f"Party templates saved with backup: {self.pt_file.name}  ({len(records)} changes)", ok=True)
        except Exception as ex:
            messagebox.showerror("Save failed", str(ex))

    def _log_pt_change(self, records):
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        lines = [f"[{ts}] {tid}: {field}  {old} -> {new}" for tid, field, old, new in records]
        self.pt_changelog.extend(lines)
        logp = Path(__file__).parent / "party_template_edit_log.txt"
        try:
            with open(logp, "a", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
        except Exception:
            pass
        self._render_pt_log()

    def _render_pt_log(self):
        self.pt_log_text.configure(state="normal")
        self.pt_log_text.delete("1.0", "end")
        self.pt_log_text.insert("1.0", "\n".join(self.pt_changelog) if self.pt_changelog else "(no changes yet)")
        self.pt_log_text.configure(state="disabled")


def main():
    root = tk.Tk()
    ViewerV2(root)
    root.mainloop()


if __name__ == "__main__":
    main()
