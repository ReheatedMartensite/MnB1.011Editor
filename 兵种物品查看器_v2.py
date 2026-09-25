# -*- coding: utf-8 -*-
"""
骑砍 1.011 兵种/物品查看器 v2
================================
在原「骑砍1.011兵种物品查看.py」基础上增强，核心新增：

1) 技能位置对照
   troops.txt 的技能行只有 6 个十进制数字 —— 它们其实是 6 个 u32 的
   4-bit 打包位域(每项技能 4 bit, 共 48 个位置)。本工具把它解码成
   按 skills.txt 顺序排列的技能表: 位置 / 英文ID / 中文名 / 等级。
   已交叉验证: 弓箭手 -> [33] skl_power_draw(强弓)=4;

2) 阵营 (factions.txt 索引 + 中文名)

3) 兵种标志位 tf_* 解码 (含 tf_hero: 英雄不会被杀死/独立加点/队伍技能生效/显示血量百分比)

4) 存档对照: 可选载入 .sav，并排比较 troops.txt 模板值与存档实例值,
   差异高亮。用于验证「哪些字段在实例化时发生了变化」。
   已知规律:
     - 属性: 存档 = 模板 + 随机分配, 使 总和 = 等级 + 20
     - 技能: 存档 = 模板 + 若干随机技能点
     - 物品: 可能有装备随机变体

依赖: mb_model.py (同目录)
运行: E:\\BigBrother\\Anaconda\\python.exe 兵种物品查看器_v2.py
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
import mb_model as M

# 模组中文翻译(cns/*.csv)为逐字加空格格式(如 "福 门 特 势 力"),
# 仅合并「两个汉字之间的空格」, 保留 Latin/数字周围的正常空格(如 "culture 1")。
_CJK_SPACE = re.compile(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])")
def _clean_cjk(s):
    return _CJK_SPACE.sub("", str(s)) if s is not None else ""

def fmt(x):
    """格式化数值: None -> '?'，否则原样转字符串。"""
    return "?" if x is None else str(x)

# 默认模块: 仅作"首选偏好"(分发给他人时可按需改这一行, 也可不改)。
# 实际路径由 M.resolve_default_module() 解析, 优先级:
#   环境变量 MB_MODULE_DIR  >  本常量  >  上次记忆(mb_module_path.json)  >  自动探测  >  弹框选择
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
# 部队模板(party_templates.txt) 解析 / 序列化
# ----------------------------------------------------------------------------
# 格式(实测 WD - Minuet v0.12, 1.011, 全 64 模板一致):
#   第1行: partytemplatesfile version 1
#   第2行: 模板数量
#   其后每行一个模板:  id  name  flags  0  h4  h5  [兵种栈...]  -1 ...
#   头部共 6 个 token:
#     t[0]=id  t[1]=name  t[2]=flags  t[3]=0(固定)  t[4]=menu类(H4)  t[5]=ai_behavior类(H5)
#   ⚠ 旧版误把头部当成 4 个 token、兵种栈从 t[4:] 起读 —— 实际应从 t[6:] 起,
#     导致整体错位漂移 2 个 token, 兵种id与数量被读反/错乱。2026-09-21 修正。
#   兵种栈: 每个栈 4 个整数 = (troop_id, 数量A/min, 数量B/max, 标志);
#   以"栈首 troop==-1"哨兵终止, 之后是若干 -1 填充(尾部 -1 数量可变)。
#   写回须原样保留全部 6 个头部 token(含 h4/h5), 否则会丢失 2 字段、破坏存档。
# 解析采用「格式保持」策略: 仅把栈区解析成结构化列表, 尾部原文整体保留(trailing),
# 写回时按相同 token 布局拼接 —— 即使对字段语义(数量A/B 谁是 min/max)判断有偏差,
# 也绝不会改变文件 token 数量与 -1 布局, 不会写坏存档/模块。
# ============================================================================

def parse_party_templates(text):
    """把 party_templates.txt 文本解析为模板字典列表(格式保持)。

    头部共 6 个 token(id name flags 0 h4 h5), 兵种栈从 t[6:] 起读 —— 旧版误用 t[4:]
    会造成整体 2-token 漂移(兵种id/数量读反)。见模块头注释。"""
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
        body = t[6:]     # 兵种栈起点(1.011/WD: 头部共 6 个 token)
        stacks = []
        i = 0
        while i + 4 <= len(body):
            quad = [int(x) for x in body[i:i + 4]]
            if quad[0] == -1:                    # 哨兵: 仅兵种(troop)==-1 才终止
                break
            stacks.append(quad)                   # 每个栈存为 [troop, a, b, flag]
            i += 4
        trailing = [int(x) for x in body[i:]]     # 空槽的 -1 (每个占 1 token)
        trailing_ws = s[len(s.rstrip()):]         # 行末空白(尾部空格; CRLF 已在 line_sep 还原)
        # ★ 栈位容量 = 已用槽 + 空槽。真实格式是「固定 6 个栈槽」:
        #   有兵种的槽写 4 token(troop a b flag), 空槽只写 1 个 -1。
        #   => 铁律 栈数 + 尾部 -1 数 = 容量(恒为 6)。增删栈必须按容量重算尾部,
        #      否则整行 token 数偏移, 游戏按 token 流读取时会把下一行读串
        #      (表现为 unexpected end-of-file / 驻军出现主角、Temp Troop 等错乱兵种)。
        nslots = len(stacks) + len(trailing)
        out.append({"id": tid, "name": name, "flags": flags,
                    "fixed0": fixed0, "h4": h4, "h5": h5,
                    "stacks": stacks, "trailing": trailing,
                    "nslots": nslots, "trailing_ws": trailing_ws})
    return out


def serialize_party_templates(templates, header="partytemplatesfile version 1",
                              line_sep="\n"):
    """把模板列表序列化为可写回的文本: 6 个头部 token + 栈区 + 补足到容量的空槽 -1 + 行末空白。

    ⚠ 关键(2026-09-24 修复): 尾部 -1 不是"填充", 而是**未使用的栈槽**, 每个占 1 token。
    真实格式是「固定 6 个栈槽」: 有兵种的槽写 4 token, 空槽写 1 个 -1。
    因此增删栈后必须按容量**重算**尾部 (空槽数 = 容量 - 栈数), 不能原样保留 ——
    否则整行 token 数偏移, 游戏按 token 流读取时把下一行字段读串
    (实测: 加一个栈 → 槽数 6→7; 后果是 unexpected end-of-file / 驻军出现错乱兵种)。
    line_sep 用于还原原文件换行(CRLF/LF), 使未改动的行做到字节级一致。
    ⚠ 须原样写回 h4/h5(第5/6 token), 否则丢失 2 头部字段、破坏存档。"""
    lines = [header, str(len(templates))]
    for t in templates:
        parts = [t["id"], t["name"], str(int(t["flags"])), str(int(t["fixed0"])),
                 str(int(t.get("h4", 0))), str(int(t.get("h5", 0)))]
        stacks = t["stacks"]
        for s in stacks:
            parts += [str(int(x)) for x in s]
        # 容量: 优先用解析时记录的 nslots; 缺省按原始 trailing 推算; 再兜底 6
        nslots = int(t.get("nslots") or (len(stacks) + len(t.get("trailing", []))) or 6)
        if len(stacks) > nslots:                  # 超出容量: 截断并交由上层提示
            stacks = stacks[:nslots]
            parts = parts[:6 + 4 * nslots]
        trailing = [-1] * (nslots - len(stacks))   # ★ 按容量重算, 而非原样保留
        if not stacks and not trailing:            # 兜底: 至少保留一个空槽
            trailing = [-1]
        parts += [str(int(x)) for x in trailing]
        lines.append(" ".join(parts) + t.get("trailing_ws", ""))
    return line_sep.join(lines) + line_sep


def decode_module_text(raw):
    """安全解码模组文本文件, 返回 (text, encoding, bom)。

    ⚠ 绝不使用 decode("utf-8", errors="replace"): 它把无法按 UTF-8 解析的字节
      静默替换成 U+FFFD, 一旦写回就把原字符永久损坏(GBK/GB18030 编码的中文模组尤甚)。
    判定顺序: UTF-8(剥离 BOM 后再解) -> gb18030 -> latin-1。
      latin-1 恒成功且字节可逆, 保证任何文件都能"原样读回 / 原样写回"。"""
    bom = b"\xef\xbb\xbf" if raw[:3] == b"\xef\xbb\xbf" else b""
    body = raw[len(bom):]
    for enc in ("utf-8", "gb18030", "latin-1"):
        try:
            return body.decode(enc), enc, bom
        except UnicodeDecodeError:
            continue
    return body.decode("latin-1"), "latin-1", bom


def encode_module_text(text, encoding, bom=b""):
    """decode_module_text 的逆操作, 返回可直接 write_bytes 的原始字节。

    ⚠ 必须走 write_bytes, 不能走 write_text —— 后者在 Windows 上属文本模式,
      会把每个 '\\n' 翻译成 '\\r\\n'; 原本已是 CRLF 的文本会被二次翻倍成
      '\\r\\r\\n'(每处换行多 1 个 CR), 引擎解析时报 unexpected end-of-file。
      2026-09-24 修复: party_templates.txt 曾被此问题写坏。"""
    return (bom or b"") + text.encode(encoding)


# ---------------- 物品完整解析(自包含, 恢复 v1 的"物品属性查看/修改"功能) ----------------
# 旧版查看器(骑砍1.011兵种物品查看.py) 自带完整 item_kinds1.txt 解析; v2 早期改成依赖
# mb_model, 而 mb_model.items 只存了物品 ID 字符串, 导致物品页『连属性都看不到』。
# 此处把旧版的自包含解析原样移植回来, 作为 v2 的物品子系统, 与 mb_model 并存。
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
    """解析 item_kinds1.txt, 返回完整物品字典列表 (idx 与文件顺序一致)。"""
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
        return "0（砍 / 未使用）"
    if v >= 512:
        return f"钝 {v - 512}"
    if v >= 256:
        return f"刺 {v - 256}"
    return f"砍 {v}"

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


# ---- 伤害值编码: 原始值 <256=砍, +256=刺, +512=钝 (与 decode_damage 互逆) ----
DMG_KINDS = ("砍", "刺", "钝")
_DMG_OFF = {"砍": 0, "刺": 256, "钝": 512}


def split_damage(raw):
    """伤害原始值 -> (数值, 类型), 与 decode_damage 互逆。"""
    if raw is None:
        return 0, "砍"
    if raw >= 512:
        return raw - 512, "钝"
    if raw >= 256:
        return raw - 256, "刺"
    return raw, "砍"


def encode_damage(val, kind):
    """(数值, 类型) -> 伤害原始值。"""
    try:
        v = int(val)
    except Exception:
        v = 0
    if v < 0:
        v = 0
    return v + _DMG_OFF.get(kind, 0)


def item_attr_fields(it):
    """按物品类型, 为 it['stats'] 的每个位置给出可读标签 —— 属性编辑器的唯一真源。

    返回 [(绝对索引, 标签, 是否伤害字段)]。标签与 format_item 显示的分组保持一致,
    填充(fill)与应用(apply)共用本函数, 避免两边索引/标签漂移。
    """
    stats = it["stats"]
    n = len(stats)
    is_goods, is_horse, is_shield, is_ammo, is_ranged, is_throwing = item_categories(it)
    spec = []

    def add(idx, label, dmg=False):
        if 0 <= idx < n:
            spec.append((idx, label, dmg))

    # 前 4 项对所有物品同构, 只是语义随类型变化
    if n >= 4:
        add(0, "货物数量" if is_goods else "头防")
        add(1, "马匹防御" if is_horse else ("盾牌抗性" if is_shield else "身防"))
        add(2, "精度" if (is_ranged or is_ammo or is_throwing) else "腿防")
        add(3, "需求 / 难度")

    if is_horse and n >= 10:
        add(4, "生命")
        add(5, "马匹速度")
        add(6, "操纵")
        add(7, "字段7")
        add(9, "冲锋")

    if is_shield and n >= 8:
        add(4, "耐久")
        add(5, "盾牌速度")
        add(7, "尺寸")

    # 弹药/武器段走负索引, 这里换算成绝对索引
    if is_ammo and n >= 11:
        add(n - 4, "长度/模型缩放")
        add(n - 3, "数量")
        add(n - 2, "伤害", True)

    weapon_like = (
        not is_goods
        and not is_horse
        and not is_shield
        and not is_ammo
        and n >= 11
        and ((stats[5] not in (None, 0)) or (stats[-2] not in (None, 0)) or (stats[-1] not in (None, 0)))
    )
    if weapon_like:
        add(n - 6, "武器速度")
        if is_ranged or is_throwing:
            add(n - 5, "弹道飞行速度")
        else:
            add(n - 4, "范围 / 长度")
        if is_throwing:
            add(n - 3, "数量")
        add(n - 2, "伤害1", True)
        add(n - 1, "伤害2", True)

    # 同一索引只保留首个标签, 避免生成重复行
    out, seen = [], set()
    for idx, label, dmg in spec:
        if idx in seen:
            continue
        seen.add(idx)
        out.append((idx, label, dmg))
    return out


class ScrolledFrame(ttk.Frame):
    """内部放一个可垂直滚动的 Frame (用于 74 行物品槽网格)。"""
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
    此时 IntVar.get() 会抛 _tkinter.TclError: expected integer but got ""。
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


class ViewerV2:
    def __init__(self, root):
        self.root = root
        # 兜底: 未捕获的 Tk 回调异常只报一行, 不再往控制台刷整页 traceback
        self.root.report_callback_exception = self._on_tk_error
        self.root.title("骑马与砍杀 1.011 兵种/物品查看器 v2 (含技能位置对照)")
        self.root.geometry("1360x960")

        self.mod = M.ModuleData()
        self.doc = None            # 已载入的存档 (可选)
        self.troops = []
        self.filtered = []
        self.cur = None
        self.item_options = ["空 (-1)"]   # 物品下拉选项 (载入模块后填充)
        self.faction_options = []         # 阵营下拉选项 (载入模块后填充)
        self.inv_rows = []          # 背包 64 行控件
        self.equip_rows = []        # 装备 10 行控件

        # ---- 物品完整属性 (item_kinds1.txt 全解析) ----
        self.items_full = []        # 完整物品字典列表(含 flag/价格/重量/数值), 与 mod.items 按序对齐
        self.item_dirty = False     # 物品(item_kinds1.txt) 是否有未保存修改
        self.cur_item = None        # 当前选中物品索引 (指向 items_full / mod.items)
        self._items_meta = ""       # item_kinds1.txt 首行(meta)
        self._items_count = "0"     # item_kinds1.txt 次行(物品数量)

        # ---- 部队模板(party_templates.txt)编辑 ----
        self.pt_file = None         # party_templates.txt 路径
        self.pt_header = "partytemplatesfile version 1"  # 第1行(原样保留)
        self.pt_line_sep = "\n"      # 原文件换行符(CRLF/LF), 载入时校正
        self.pt_encoding = "utf-8"   # 原文件编码(载入时判定, 写回必须用同一编码)
        self.pt_bom = b""            # 原文件 UTF-8 BOM(写回时原样补回)
        self.pt_raw = b""            # 载入时的原始字节(用于往返自检)
        self.pt_roundtrip_ok = True  # 解析->序列化能否字节级还原(不能则禁止保存)
        self.party_templates = []   # 解析后的模板列表(含编辑)
        self.pt_original = []       # 载入时的原始副本(用于差异/修改记录)
        self.pt_cur = None          # 当前选中的模板索引
        self.troop_options = ["空 (-1)"]   # 兵种下拉(载入模块后填充)
        self.pt_rows = []           # 栈编辑行控件
        self.pt_changelog = []      # 本次会话的修改记录(用于显示)

        self.build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.after_build()

    # ---------------- UI ----------------
    def build_ui(self):
        menubar = tk.Menu(self.root, tearoff=0)
        fm = tk.Menu(menubar, tearoff=0)
        fm.add_command(label="打开其它目录…", command=self.open_module)
        fm.add_command(label="载入存档用于对照(可选)", command=self.open_save)
        fm.add_command(label="保存模块(troops.txt 抽象兵种)", command=self.save_module_file)
        fm.add_command(label="保存存档(写入实例物品修改)", command=self.save_save)
        fm.add_command(label="保存部队模板(party_templates.txt)", command=self.save_party_templates)
        fm.add_command(label="保存物品(item_kinds1.txt)", command=self.save_items)
        fm.add_separator()
        fm.add_command(label="退出", command=self.on_close)
        menubar.add_cascade(label="文件", menu=fm)
        self.root.config(menu=menubar)

        top = ttk.Frame(self.root)
        top.pack(fill="x", padx=4, pady=3)
        # 模块快速切换: 自动列出游戏 Modules 目录下所有可用模组
        self._module_map = self._detect_modules()
        self.module_combo = ttk.Combobox(top, state="readonly", width=34,
                                        values=list(self._module_map.keys()))
        self.module_combo.bind("<<ComboboxSelected>>", self._on_module_pick)
        ttk.Label(top, text="模块:").pack(side="left", padx=(0, 2))
        self.module_combo.pack(side="left", padx=(0, 8))
        self.mod_label = ttk.Label(top, text="(未载入)", anchor="w")
        self.mod_label.pack(side="left")
        self.sav_label = ttk.Label(top, text="存档: 未载入", anchor="w", foreground="#888")
        self.sav_label.pack(side="left", padx=16)

        self.nb = ttk.Notebook(self.root)
        self.tab_troop = ttk.Frame(self.nb)
        self.tab_item = ttk.Frame(self.nb)
        self.tab_cmp = ttk.Frame(self.nb)
        self.tab_pt = ttk.Frame(self.nb)
        self.nb.add(self.tab_troop, text="兵种")
        self.nb.add(self.tab_item, text="物品")
        self.nb.add(self.tab_cmp, text="存档对照")
        self.nb.add(self.tab_pt, text="部队模板")
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
        ttk.Label(top, text="搜索:").pack(side="left")
        self.tsearch = tk.StringVar()
        e = ttk.Entry(top, textvariable=self.tsearch)
        e.pack(side="left", fill="x", expand=True, padx=4)
        e.bind("<Return>", lambda ev: self.refresh_troops())
        ttk.Button(top, text="过滤", command=self.refresh_troops).pack(side="left")
        ttk.Button(top, text="全部", command=lambda: (self.tsearch.set(""), self.refresh_troops())).pack(side="left", padx=2)

        hp = tk.PanedWindow(self.tab_troop, orient="horizontal")
        hp.pack(fill="both", expand=True, padx=4, pady=4)

        lf = ttk.LabelFrame(hp, text="兵种列表")
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

        # 右侧: 多面板 (各独立, 互不挤压)
        vp = tk.PanedWindow(rf, orient="vertical")
        vp.pack(fill="both", expand=True)

        # 1) 概要 / 标志位(tf_*)勾选编辑 —— 修改 troops.txt 模板(影响新开局)
        gf = ttk.LabelFrame(vp, text="概要 / 标志位 tf_*")
        vp.add(gf, stretch="always", height=320)
        # 阵营(跳槽)编辑栏: 修改存档中该兵种的当前所属阵营
        facbar = ttk.Frame(gf)
        facbar.pack(side="top", fill="x", padx=4, pady=2)
        ttk.Label(facbar, text="存档阵营:").pack(side="left")
        self.fac_combo = ttk.Combobox(facbar, state="readonly", width=26)
        self.fac_combo.pack(side="left", padx=3)
        self.fac_apply_btn = ttk.Button(facbar, text="仅改归属", command=self._apply_faction)
        self.fac_apply_btn.pack(side="left", padx=2)
        self.fac_defect_btn = ttk.Button(facbar, text="执行跳槽", command=self._do_defect)
        self.fac_defect_btn.pack(side="left", padx=2)
        self.fac_hint = ttk.Label(facbar, text="载入存档后可改", foreground="#888")
        self.fac_hint.pack(side="left", padx=6)
        # 升级路径编辑: 修改 troops.txt 头行 upgrade1/upgrade2 (影响新开局)
        ulf = ttk.LabelFrame(gf, text="升级路径 (可编辑, 点『保存模块』写盘生效)")
        ulf.pack(side="top", fill="x", padx=4, pady=2)
        ulrow = ttk.Frame(ulf)
        ulrow.pack(side="top", fill="x", padx=6, pady=3)
        ttk.Label(ulrow, text="升级1:").pack(side="left")
        self.up1_combo = ttk.Combobox(ulrow, width=28, state="readonly")
        self.up1_combo.pack(side="left", padx=3)
        ttk.Label(ulrow, text="升级2:").pack(side="left")
        self.up2_combo = ttk.Combobox(ulrow, width=28, state="readonly")
        self.up2_combo.pack(side="left", padx=3)
        ttk.Button(ulrow, text="应用升级", command=self.apply_troop_upgrades).pack(side="left", padx=8)
        self.up_hint = ttk.Label(ulf, text="模板·未保存", foreground="#888")
        self.up_hint.pack(side="top", fill="x", padx=6, pady=(0, 3))
        # 标志位(tf_*)勾选编辑面板: 修改 troops.txt 抽象兵种头行的 flags (影响新开局)
        # 程序在「勾选项 ↔ 数值」之间自动转换, 用户无需理解每一位代表什么。
        flf = ttk.LabelFrame(gf, text="标志位 (勾选即写入模板, 点『保存模块』写盘)")
        flf.pack(side="top", fill="both", expand=True, padx=4, pady=2)
        # ⚠ 先把「按钮行」按 bottom 侧 pack(在其它内容之前), 这样即使 pane 被压缩,
        #   packer 也会先为按钮行保留高度 —— 否则按钮会塌成 1px 不可见/不可点(历史 bug)。
        fbbar = ttk.Frame(flf)
        fbbar.pack(side="bottom", fill="x", padx=6, pady=(2, 4))
        ttk.Button(fbbar, text="应用标志位", command=self.on_troop_flag).pack(side="left", padx=2)
        self.flag_hint = ttk.Label(fbbar, text="模板·未保存", foreground="#888")
        self.flag_hint.pack(side="left", padx=6)
        self.flag_calc = ttk.Label(flf, text="当前值: 0x00000000 (十进制 0)", foreground="#555")
        self.flag_calc.pack(side="top", fill="x", padx=6, pady=(3, 0))
        fgrid = ttk.Frame(flf)
        fgrid.pack(side="top", fill="both", expand=True, padx=6, pady=2)
        self.tf_vars = []          # [(mask, BooleanVar), ...], 顺序同 M.TF_NAMES
        for k, (mask, name) in enumerate(M.TF_NAMES):
            var = tk.BooleanVar()
            # 勾选即写入模板内存(真正"勾选即生效"), 见 _on_tf_toggle
            cb = ttk.Checkbutton(fgrid, text=name, variable=var,
                                 command=self._on_tf_toggle)
            cb.grid(row=k // 5, column=k % 5, sticky="w", padx=4, pady=1)
            self.tf_vars.append((mask, var))

        # 2) 兵种详情 (解码文本, 独立可滚动面板) —— 属性/熟练/技能/升级/阵营/标志位 全部可见
        df = ttk.LabelFrame(vp, text="兵种详情 (已解码)")
        vp.add(df, stretch="always", height=150)
        self.tdetail = self._mk_text(df, height=12)

        # 3) 属性 / 熟练 (可编辑) —— 还原旧版查看器逐项编辑能力
        af = ttk.LabelFrame(vp, text="属性 / 熟练 (可编辑, 保存模块生效)")
        vp.add(af, stretch="always", height=130)
        arow = ttk.Frame(af)
        arow.pack(side="top", fill="x", padx=4, pady=2)
        self.edit_attr_vars = []
        for lbl in ("力量", "敏捷", "智力", "魅力", "等级"):
            ttk.Label(arow, text=lbl).pack(side="left", padx=(4, 0))
            v = tk.StringVar()
            self.edit_attr_vars.append(v)
            ttk.Entry(arow, textvariable=v, width=5).pack(side="left", padx=(0, 5))
        ttk.Button(arow, text="应用属性", command=self.apply_troop_attrs).pack(side="left", padx=6)
        prow = ttk.Frame(af)
        prow.pack(side="top", fill="x", padx=4, pady=2)
        ttk.Label(prow, text="熟练:").pack(side="left")
        self.edit_prof_vars = []
        for lbl in ("单手", "双手", "长杆", "弓", "弩", "投掷", "火器"):
            ttk.Label(prow, text=lbl).pack(side="left", padx=(4, 0))
            v = tk.StringVar()
            self.edit_prof_vars.append(v)
            ttk.Entry(prow, textvariable=v, width=4).pack(side="left", padx=(0, 4))
        ttk.Button(prow, text="应用熟练", command=self.apply_troop_profs).pack(side="left", padx=6)

        # 4) 技能 (显示 + 可编辑) —— 还原旧版查看器的技能行编辑, 并保留 tpl/sav 对照表
        sf = ttk.LabelFrame(vp, text="技能 (按 skills.txt 位置对照, 位域 48 位, 可编辑)")
        vp.add(sf, stretch="always", height=250)
        # ⚠ 先按 bottom 侧 pack「技能编辑行」, 保证 pane 被压缩时输入框与按钮仍可见
        #   (历史 bug: 该行曾塌成 1px, 导致"技能无法编辑")。
        skedit = ttk.Frame(sf)
        skedit.pack(side="bottom", fill="x", padx=4, pady=2)
        ttk.Label(skedit, text="选中技能等级:").pack(side="left")
        self.edit_skill_lv_var = tk.StringVar(value="0")
        ttk.Entry(skedit, textvariable=self.edit_skill_lv_var, width=5).pack(side="left", padx=(0, 4))
        ttk.Button(skedit, text="应用选中技能", command=self.apply_troop_skill_selected).pack(side="left", padx=4)
        ttk.Label(skedit, text="整行(6词,可16进制):").pack(side="left", padx=(12, 0))
        self.edit_skill_raw_var = tk.StringVar()
        ttk.Entry(skedit, textvariable=self.edit_skill_raw_var, width=46).pack(side="left", padx=(0, 4))
        ttk.Button(skedit, text="应用技能行", command=self.apply_troop_skills_raw).pack(side="left", padx=4)
        cols = ("pos", "id", "name", "tpl", "sav")
        self.skill_tv = ttk.Treeview(sf, columns=cols, show="headings", height=8)
        for c, w, t in (("pos", 50, "位置"), ("id", 210, "技能ID"),
                        ("name", 110, "中文"), ("tpl", 60, "模板"), ("sav", 60, "存档")):
            self.skill_tv.heading(c, text=t)
            self.skill_tv.column(c, width=w, anchor="w" if c in ("id", "name") else "center")
        ssb = ttk.Scrollbar(sf, orient="vertical", command=self.skill_tv.yview)
        self.skill_tv.configure(yscrollcommand=ssb.set)
        ssb.pack(side="right", fill="y")
        self.skill_tv.pack(fill="both", expand=True)
        self.skill_tv.tag_configure("nz", background="#d9f2d9")
        self.skill_tv.tag_configure("diff", background="#ffe0e0")
        self.skill_tv.bind("<<TreeviewSelect>>", self._on_skill_select)

        # 5) 物品栏 / 装备 (可编辑) —— 保留(唯一始终可用的保存路径)
        ef = ttk.LabelFrame(vp, text="物品栏 / 装备 (可编辑)")
        vp.add(ef, stretch="always", height=220)
        hdr = ttk.Frame(ef)
        hdr.pack(side="top", fill="x", padx=4, pady=2)
        self.inv_summary = ttk.Label(hdr, text="背包=模板·装备=实例", anchor="w")
        self.inv_summary.pack(side="left", fill="x", expand=True, anchor="w")
        ttk.Button(hdr, text="保存模块", command=self.save_module_file).pack(side="right", padx=2)
        ttk.Button(hdr, text="保存存档", command=self.save_save).pack(side="right", padx=2)
        self.inv_scroll = ScrolledFrame(ef)
        self.inv_scroll.pack(fill="both", expand=True)

    def build_item_tab(self):
        top = ttk.Frame(self.tab_item)
        top.pack(fill="x", padx=4, pady=3)
        ttk.Label(top, text="搜索:").pack(side="left")
        self.isearch = tk.StringVar()
        e = ttk.Entry(top, textvariable=self.isearch)
        e.pack(side="left", fill="x", expand=True, padx=4)
        e.bind("<Return>", lambda ev: self.refresh_items())
        ttk.Button(top, text="过滤", command=self.refresh_items).pack(side="left")
        ttk.Button(top, text="全部", command=lambda: (self.isearch.set(""), self.refresh_items())).pack(side="left", padx=2)

        hp = tk.PanedWindow(self.tab_item, orient="horizontal")
        hp.pack(fill="both", expand=True, padx=4, pady=4)
        lf = ttk.LabelFrame(hp, text="物品列表")
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
        det_f = ttk.LabelFrame(rvp, text="物品详情 (属性已解码)")
        rvp.add(det_f, stretch="always", height=300)
        self.idetail = self._mk_text(det_f)
        ed_f = ttk.LabelFrame(rvp, text="属性 (可编辑, 保存物品生效)")
        # 高度需容纳: 基础行 + 整串数值行 + 逐项属性(最多约 9 行) + 两个按钮行
        rvp.add(ed_f, stretch="always", height=350)
        ef = ttk.Frame(ed_f); ef.pack(fill="x", padx=4, pady=2)
        ttk.Label(ef, text="价格:").pack(side="left")
        self.edit_price_var = tk.StringVar()
        ttk.Entry(ef, textvariable=self.edit_price_var, width=8).pack(side="left", padx=(0, 6))
        ttk.Label(ef, text="重量:").pack(side="left")
        self.edit_weight_var = tk.StringVar()
        ttk.Entry(ef, textvariable=self.edit_weight_var, width=10).pack(side="left", padx=(0, 6))
        ttk.Label(ef, text="丰裕度:").pack(side="left")
        self.edit_abundance_var = tk.StringVar()
        ttk.Entry(ef, textvariable=self.edit_abundance_var, width=6).pack(side="left", padx=(0, 6))
        ttk.Button(ef, text="应用基础", command=self.apply_item_basic).pack(side="left", padx=4)
        sf = ttk.Frame(ed_f); sf.pack(fill="x", padx=4, pady=2)
        ttk.Label(sf, text="后续数值(空格分隔):").pack(side="left")
        self.edit_stats_var = tk.StringVar()
        ttk.Entry(sf, textvariable=self.edit_stats_var).pack(side="left", fill="x", expand=True, padx=(0, 4))
        ttk.Button(sf, text="应用数值", command=self.apply_item_stats).pack(side="left", padx=4)

        # 逐项属性编辑器: 标签随物品类型变化(与上方详情区一致), 直接写回对应 stats 位置
        af = ttk.LabelFrame(ed_f, text="逐项属性 (按物品类型自动识别)")
        af.pack(fill="x", padx=4, pady=2)
        self.attr_rows = ttk.Frame(af)
        self.attr_rows.pack(fill="x")
        ab = ttk.Frame(af)
        ab.pack(fill="x", padx=4, pady=2)
        ttk.Button(ab, text="应用属性", command=self.apply_item_attrs).pack(side="left")
        ttk.Label(ab, text="(写回后续数值的对应位置; 未列出的位置保持原样)",
                  foreground="#666").pack(side="left", padx=6)
        self.attr_widgets = []

    def build_compare_tab(self):
        top = ttk.Frame(self.tab_cmp)
        top.pack(fill="x", padx=4, pady=3)
        ttk.Label(top, text="说明: 载入存档后, 逐兵种对照 troops.txt 模板值与存档实例值。").pack(side="left")
        ttk.Button(top, text="立即对照", command=self.run_compare).pack(side="left", padx=6)
        self.cmp_detail = self._mk_text(self.tab_cmp, expand=True)

    def _mk_text(self, parent, expand=True, height=None):
        f = ttk.Frame(parent)
        f.pack(fill="both", expand=True)
        sb = ttk.Scrollbar(f)
        sb.pack(side="right", fill="y")
        kw = {"height": height} if height else {}
        t = tk.Text(f, wrap="word", font=("Microsoft YaHei UI", 9), yscrollcommand=sb.set, **kw)
        t.pack(fill="both", expand=True)
        sb.config(command=t.yview)
        t.tag_configure("diff", foreground="#c0392b")
        t.tag_configure("ok", foreground="#27863b")
        t.tag_configure("h", foreground="#1f6fb2")
        return t

    def after_build(self):
        # 可移植解析: 环境变量 > DEFAULT_MODULE > 上次记忆 > 自动探测; 都没有才弹框
        mod = M.resolve_default_module(prefer=DEFAULT_MODULE)
        if mod:
            self.load_module(mod)
        else:
            self.open_module()

    # ---------------- 数据 ----------------
    def _detect_modules(self):
        """探测所有候选 Modules 根目录下的可用模组(含 troops.txt 的子目录)。"""
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
            messagebox.showerror("错误", f"载入模块失败:\n{ex}")
            return
        # 切换模组时清空上一次的残留选择/数据
        self.cur = None
        try:
            self.tlist.selection_clear(0, "end")
        except Exception:
            pass
        self._set(self.tdetail, "— 未选择兵种 —")
        for r in self.skill_tv.get_children():
            self.skill_tv.delete(r)
        if getattr(self, "_inv_built", False):
            for w in self.inv_rows + self.equip_rows:
                try:
                    w["cmb"].current(0); w["type_var"].set(self._imod_text(0)); w["amt_var"].set(0)
                    w["cmb"].config(state="disabled"); w["sp_t"].config(state="disabled")
                    w["sp_a"].config(state="disabled"); w["btn"].config(state="disabled")
                except Exception:
                    pass
        self.mod_label.config(text=f"模块: {folder}   兵种{n} 物品{len(self.mod.items)} 技能{len(self.mod.skills)} 阵营{len(self.mod.factions)}")
        # 物品下拉选项: 0 = 空(-1), 其后 0..nitems
        self.item_options = ["空 (-1)"] + [f"{i}  {self.item_name(i)}" for i in range(len(self.mod.items))]
        # 阵营下拉选项(用于改领主投靠阵营)
        self.faction_options = [
            f"{i}  {_clean_cjk(self.mod.faction_names.get(f, f))}"
            for i, f in enumerate(self.mod.factions)
        ]
        # 兵种下拉选项(用于部队模板的兵种栈编辑)
        self.troop_options = ["空 (-1)"] + [
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
        M.remember_module(folder)      # 记住本次模块, 下次启动直接进入
        self.set_status(f"已载入模块: {folder}")

    # ---------------- 物品完整属性载入 ----------------
    def _load_items_full(self, folder):
        """完整解析 item_kinds1.txt (含 flag1/flag2/价格/重量/丰裕度/数值), 用于物品页属性
        查看与编辑。与 self.mod.items (仅 ID 串) 同源、按文件顺序一一对齐: items_full[i]
        对应 mod.items[i]。翻译名沿用 mb_model 已载入的 self.mod.item_names。"""
        path = Path(folder) / "item_kinds1.txt"
        try:
            self.items_full, self._items_meta, self._items_count = parse_items(str(path))
        except Exception as ex:
            self.items_full = []
            messagebox.showerror("物品解析失败", f"无法完整解析 item_kinds1.txt:\n{ex}")
            return
        # 防御: 若两者数量不一致, 仅以较小者可用, 避免越界
        self.item_dirty = False
        self.cur_item = None
        n = min(len(self.items_full), len(self.mod.items))
        self.set_status(f"物品完整属性已载入: {n} 个 (mod.items={len(self.mod.items)})")

    def open_module(self):
        init = M.initial_dir_for_module_dialog()
        d = filedialog.askdirectory(title="选择模块目录(含 troops.txt)", initialdir=init)
        if d:
            self.load_module(d)

    def open_save(self):
        if not self.mod.loaded:
            messagebox.showwarning("提示", "请先载入模块。")
            return
        init = M.get_last_save_dir() or M.initial_dir_for_module_dialog()
        p = filedialog.askopenfilename(title="选择存档 .sav", initialdir=init,
                                       filetypes=[("存档", "*.sav"), ("所有", "*.*")])
        if not p:
            return
        M.remember_save_dir(p)
        try:
            d = M.SaveDoc()
            d.mod = self.mod
            d.load_save(p)
            self.doc = d
            self.sav_label.config(text=f"存档: {os.path.basename(p)} ({len(d.starts)} 兵种)", foreground="#27863b")
            self.set_status("存档已载入, 可查看差异。")
            self.refresh_troops()
        except Exception as ex:
            messagebox.showerror("错误", f"载入存档失败:\n{ex}")

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
        return f"(保留位{j})", ""

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
            tag = "英雄" if (t.get("flags", 0) & M.TF_HERO) else ""
            self.tlist.insert("end", f"#{i:3d}  {self.tr_name(i)}   [{self.fac_name(t.get('faction', -1))}] {tag}")
        self.set_status(f"兵种 {len(self.filtered)} 条")

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

    def _on_tk_error(self, exc, val, tb):
        """Tk 回调异常兜底: 状态栏一行 + stderr 一行, 不打印整页 traceback。

        最常见触发是 Spinbox 被清空瞬间 IntVar.get() 抛 TclError；
        写入路径已用 safe_int() 吸收, 这里只作最后一道闸防止控制台刷屏。
        """
        try:
            name = getattr(exc, "__name__", str(exc))
            msg = "%s: %s" % (name, val)
            self.set_status("⚠ 界面回调出错(已忽略): %s" % msg, err=True)
            sys.stderr.write("[MB1011] Tk callback error: %s\n" % msg)
        except Exception:
            pass

    # ---------------- 选中 ----------------
    def on_troop_select(self, ev=None):
        s = self.tlist.curselection()
        if not s:
            return
        i = self.filtered[s[0]]
        self.cur = i
        self.show_troop(i)

    def _update_troop_flag_calc(self):
        """根据勾选状态实时刷新『当前值』显示 (不改写文件)。
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
                text="当前值: 0x%08X (十进制 %d)  解析: %s"
                % (v & 0xFFFFFFFF, v & 0xFFFFFFFF, ", ".join(M.decode_tf(v)) or "—"))
        except Exception:
            pass

    def _commit_troop_flags(self, report=True):
        """把当前勾选写入模板 flags(troops.txt 头行)。
        数值 = (原值中『不在勾选表里的未知位』) | (已勾选的 tf_* 位)，未知位原样保留。
        返回写入的整数; 失败/无选中返回 None。"""
        if self.cur is None:
            if report:
                self.set_status("请先选择兵种。", err=True)
            return None
        if not getattr(self, "tf_vars", None):
            if report:
                self.set_status("标志位面板尚未初始化。", err=True)
            return None
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
            if report:
                self.set_status(f"写入失败: {ex}", err=True)
            return None
        try:
            self.flag_hint.config(text="模板·未保存")
        except Exception:
            pass
        if report:
            self.set_status(
                f"标志位已改为 0x{v & 0xFFFFFFFF:08X} (模板改动, 点『保存模块』写盘生效)", ok=True)
        return v

    def _on_tf_toggle(self):
        """勾选框回调: 勾选即写入模板内存并刷新『当前值』(真正『勾选即生效』)。
        这样即便用户没找到『应用标志位』按钮, 勾选也已进入待保存状态。"""
        self._update_troop_flag_calc()
        if self.cur is not None:
            self._commit_troop_flags()

    def on_troop_flag(self):
        """『应用标志位』按钮: 走与勾选即时写入相同的提交路径(冗余入口, 不削弱)。"""
        v = self._commit_troop_flags()
        if v is not None:
            self.show_troop(self.cur)

    # ---- 升级路径(头行 upgrade1/upgrade2) 编辑 ----
    def _sync_upgrade_combos(self, i):
        """把升级下拉框同步到该兵种模板当前的 upgrade1/upgrade2。"""
        if getattr(self, "up1_combo", None) is None:
            return
        t = self.mod.troops[i]
        opts = ["无升级(0)"] + [f"{k}: {self.tr_name(k)}" for k in range(len(self.mod.troops))]
        self.up1_combo["values"] = opts
        self.up2_combo["values"] = opts
        def _opt(u):
            if not u or u < 0:
                return opts[0]
            if 0 <= u < len(self.mod.troops):
                return f"{u}: {self.tr_name(u)}"
            return opts[0]
        self.up1_combo.set(_opt(t.get("upgrade1", -1)))
        self.up2_combo.set(_opt(t.get("upgrade2", -1)))

    def apply_troop_upgrades(self):
        """改写模板头行 upgrade1/upgrade2 (troops.txt, 影响新开局)。"""
        if self.cur is None:
            self.set_status("请先选择兵种。", err=True)
            return
        def _idx(combo):
            s = (combo.get() or "").strip()
            if not s or s.startswith("无升级"):
                return 0
            return int(s.split(":", 1)[0])
        try:
            u1 = _idx(self.up1_combo)
            u2 = _idx(self.up2_combo)
            self.mod.set_troop_upgrades(self.cur, u1, u2)
        except Exception as ex:
            self.set_status(f"写入失败: {ex}", err=True)
            return
        self.up_hint.config(text="模板·未保存")
        self.set_status("升级路径已写入模板 (点『保存模块』写盘生效)", ok=True)
        self.show_troop(self.cur)

    # ---- 属性 / 熟练 / 技能 编辑 (还原旧版查看器的逐项编辑能力) ----
    def apply_troop_attrs(self):
        """改写模板属性行: 力量/敏捷/智力/魅力/等级 (troops.txt, 影响新开局)。"""
        if self.cur is None:
            self.set_status("请先选择兵种。", err=True)
            return
        vals = []
        for v in self.edit_attr_vars:
            s = (v.get() or "").strip()
            try:
                vals.append(int(s))
            except Exception:
                self.set_status("属性必须为整数 (力量/敏捷/智力/魅力/等级)。", err=True)
                return
        if len(vals) != 5:
            self.set_status("需要 5 个整数。", err=True)
            return
        try:
            self.mod.set_troop_attrs(self.cur, vals)
        except Exception as ex:
            self.set_status(f"写入失败: {ex}", err=True)
            return
        self.show_troop(self.cur)
        self.set_status("属性已修改 (模板改动, 点『保存模块』生效)", ok=True)

    def apply_troop_profs(self):
        """改写模板武器熟练行: 单手/双手/长杆/弓/弩/投掷/火器。"""
        if self.cur is None:
            self.set_status("请先选择兵种。", err=True)
            return
        vals = []
        for v in self.edit_prof_vars:
            s = (v.get() or "").strip()
            try:
                vals.append(int(s))
            except Exception:
                self.set_status("熟练度必须为整数。", err=True)
                return
        if len(vals) != 7:
            self.set_status("需要 7 个整数 (单手/双手/长杆/弓/弩/投掷/火器)。", err=True)
            return
        try:
            self.mod.set_troop_profs(self.cur, vals)
        except Exception as ex:
            self.set_status(f"写入失败: {ex}", err=True)
            return
        self.show_troop(self.cur)
        self.set_status("熟练已修改 (模板改动, 点『保存模块』生效)", ok=True)

    @staticmethod
    def _parse_hex_or_dec(s):
        s = (s or "").strip()
        if not s:
            return 0
        if s.lower().startswith("0x"):
            return int(s, 16)
        return int(s)

    def _on_skill_select(self, ev=None):
        sel = self.skill_tv.selection()
        if not sel:
            return
        vals = self.skill_tv.item(sel[0], "values")
        if not vals:
            return
        try:
            self.edit_skill_lv_var.set(str(int(vals[3])))
        except Exception:
            self.edit_skill_lv_var.set("0")

    def apply_troop_skill_selected(self):
        """改写单个技能等级 (位域 48 位: 每技能 4bit, 位置 0..47)。"""
        if self.cur is None:
            self.set_status("请先选择兵种。", err=True)
            return
        sel = self.skill_tv.selection()
        if not sel:
            self.set_status("请先在技能表选中一行。", err=True)
            return
        pos = int(self.skill_tv.item(sel[0], "values")[0])
        try:
            lv = int((self.edit_skill_lv_var.get() or "0").strip())
        except Exception:
            self.set_status("等级必须为整数 (0..15)。", err=True)
            return
        if not (0 <= lv <= 15):
            self.set_status("技能等级需在 0..15。", err=True)
            return
        try:
            self.mod.set_troop_skill_level(self.cur, pos, lv)
        except Exception as ex:
            self.set_status(f"写入失败: {ex}", err=True)
            return
        self.show_troop(self.cur)
        self.set_status(f"技能#{pos} 已设为 {lv} (模板改动, 点『保存模块』生效)", ok=True)

    def apply_troop_skills_raw(self):
        """改写整个技能行 (6 个 u32 打包字, 支持 16 进制)。等价于旧版『应用技能行』。"""
        if self.cur is None:
            self.set_status("请先选择兵种。", err=True)
            return
        parts = (self.edit_skill_raw_var.get() or "").split()
        try:
            words = [self._parse_hex_or_dec(p) & 0xFFFFFFFF for p in parts]
        except Exception:
            self.set_status("技能行含无效整数/16进制。", err=True)
            return
        if len(words) != 6:
            self.set_status("技能行需要 6 个整数 (每词一个 u32)。", err=True)
            return
        try:
            self.mod.set_troop_skills(self.cur, words)
        except Exception as ex:
            self.set_status(f"写入失败: {ex}", err=True)
            return
        self.show_troop(self.cur)
        self.set_status("技能行已修改 (模板改动, 点『保存模块』生效)", ok=True)

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
        L.append(f"索引 #{i}     ID: {t['tid']}")
        L.append(f"名称: {self.tr_name(i)}")
        L.append(f"阵营: [{t.get('faction', -1)}] {self.fac_name(t.get('faction', -1))} (troops.txt 模板)")
        if self.doc is not None:
            sf = self.doc.get_faction(i)
            mark = "" if sf == t.get("faction", -1) else "   ← 与模板不同(已跳槽)"
            L.append(f"       存档当前阵营: [{sf}] {self.doc.get_faction_name(i)}{mark}")
        L.append(f"标志位: {flags} (0x{flags & 0xFFFFFFFF:08X})")
        L.append(f"  解析: {', '.join(M.decode_tf(flags))}")
        L.append(f"  英雄(tf_hero): {'是' if flags & M.TF_HERO else '否'}"
                 f"  —— 英雄不会被杀死/可独立加点/队伍技能生效/显示血量百分比")
        # 升级成谁(兵种树): troops.txt 头行 upgrade1/upgrade2 (0 或 -1 = 无升级)
        u1, u2 = t.get("upgrade1", -1), t.get("upgrade2", -1)
        def _upname(u):
            if not u:                       # 0 / -1 均表示无升级目标
                return "—"
            if 0 <= u < len(self.mod.troops):
                return self.tr_name(u)
            return f"?{u}"
        L.append(f"升级成谁: {_upname(u1)} / {_upname(u2)}")
        a = t.get("attrs", [])
        if len(a) >= 5:
            L.append("")
            L.append(f"属性: 力量{a[0]} 敏捷{a[1]} 智力{a[2]} 魅力{a[3]}    等级{a[4]}")
        p = t.get("profs", [])
        if p:
            lbl = ["单手", "双手", "长杆", "弓", "弩", "投掷", "火器"]
            L.append("熟练: " + "  ".join(f"{lbl[k] if k < len(lbl) else k}={p[k]}" for k in range(len(p))))
        sw = t.get("skills", [])
        L.append("")
        L.append(f"技能行(6个打包字): {' '.join(str(x) for x in sw)}")
        L.append(f"技能行(16进制):    {' '.join(f'0x{x & 0xFFFFFFFF:08X}' for x in sw)}")

        # 存档对照(概要)
        if self.doc is not None:
            L.append("")
            sa = self.doc.get_attrs(i)
            sl = self.doc.get_level(i)
            L.append(f"[存档实例] 属性: 力量{sa[0]} 敏捷{sa[1]} 智力{sa[2]} 魅力{sa[3]}    等级{sl}")
            L.append(f"            属性和={sum(sa)} (等级+20={sl + 20})")
            # 尾部『槽』(探索中: 英雄/领主这里有稀疏非零值, 推测含性格/声望/争议)
            nz = self.doc.get_tail_nonzero(i)
            if nz:
                L.append(f"            尾部槽(未解码) {len(nz)} 个非零: {nz[:12]}")
            else:
                L.append("            尾部槽: (全空 —— 普通兵种通常如此)")

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

        # 填充属性/熟练编辑框 (还原旧版查看器的逐项编辑)
        a = t.get("attrs", [])
        for k, v in enumerate(self.edit_attr_vars):
            v.set(fmt(a[k]) if k < len(a) else "")
        p = t.get("profs", [])
        for k, v in enumerate(self.edit_prof_vars):
            v.set(fmt(p[k]) if k < len(p) else "")
        # 技能整行(默认16进制)
        sw = t.get("skills", [])
        self.edit_skill_raw_var.set(" ".join("0x%x" % (int(x) & 0xFFFFFFFF) for x in sw))
        self.edit_skill_lv_var.set("0")

        # 阵营下拉: 载入存档且兵种可靠时可改(即领主跳槽)
        self._sync_faction_combo(i)

        # 升级路径下拉: 同步到模板头行的 upgrade1/upgrade2
        self._sync_upgrade_combos(i)

        # 物品: 可编辑网格 (无存档时显示模板值, 载入存档且兵种可靠时可编辑)
        self._fill_inv_editor(i)

    def _sync_faction_combo(self, i):
        """同步阵营下拉框到该兵种存档中的当前阵营。"""
        if getattr(self, "fac_combo", None) is None:
            return
        if self.doc is None:
            self.fac_combo.set("")
            self.fac_combo.config(state="disabled")
            self.fac_apply_btn.config(state="disabled")
            self.fac_defect_btn.config(state="disabled")
            self.fac_hint.config(text="载入存档后可改")
            return
        if not self.doc.is_reliable(i):
            self.fac_combo.set("")
            self.fac_combo.config(state="disabled")
            self.fac_apply_btn.config(state="disabled")
            self.fac_defect_btn.config(state="disabled")
            self.fac_hint.config(text="该兵种存档中无完整记录, 不可改")
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
                    pf = "  部队#%d=%s" % (pys[0], self.doc.get_party_faction(pys[0]))
                except Exception:
                    pf = ""
            self.fac_hint.config(text=("已跳槽(与模板不同)" if v != tpl else "与模板一致")
                                      + pf + ("" if pys else "  (未找到其部队, 不能跳槽)"))

    def _apply_faction(self):
        """把下拉框选中的阵营写入存档(令该领主投靠/跳槽到该阵营)。"""
        if self.doc is None or self.cur is None:
            return
        sel = self.fac_combo.current()
        if sel is None or sel < 0:
            self.set_status("请先选择一个阵营。", err=True)
            return
        try:
            self.doc.set_faction(self.cur, sel)
            self.set_status(
                f"已把 #{self.cur} {self.tr_name(self.cur)} 的阵营改为 "
                f"[{sel}] {self.doc.get_faction_name(self.cur)} (保存存档后生效)", ok=True)
            self._sync_faction_combo(self.cur)
            self.show_troop(self.cur)
        except Exception as ex:
            self.set_status(f"阵营修改失败: {ex}", err=True)

    def _do_defect(self):
        """执行跳槽脚本链: 领主归属 + 其部队归属一并改为选中的阵营。"""
        if self.doc is None or self.cur is None:
            return
        sel = self.fac_combo.current()
        if sel is None or sel < 0:
            self.set_status("请先选择目标阵营。", err=True)
            return
        try:
            changes = self.doc.defect_lord(self.cur, sel)
        except Exception as ex:
            self.set_status(f"跳槽失败: {ex}", err=True)
            return
        if not changes:
            self.set_status(f"#{self.cur} {self.tr_name(self.cur)} 已是该阵营, 无需改动。")
            return
        detail = "; ".join(f"{d} {o}→{nv}" for d, o, nv in changes)
        self.set_status(f"已执行跳槽: #{self.cur} {self.tr_name(self.cur)} —— {detail} "
                        f"(保存存档后生效)", ok=True)
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
                      f"索引: {i}\nID: {it}\n名称: {self.mod.item_names.get(it, it)}\n\n(完整属性未解析, 仅 ID 可用)")

    # ---------------- 物品属性查看/编辑 ----------------
    def _item_full_name(self, it):
        return self.mod.item_names.get(it["id"], _clean_cjk(it["raw_name"]))

    def format_item(self, it):
        """把完整物品字典解码为可读文本 (移植自旧版查看器, 已验证可用)。"""
        lines = []
        name = self._item_full_name(it)
        stats = it["stats"]
        is_goods, is_horse, is_shield, is_ammo, is_ranged, is_throwing = item_categories(it)

        lines.append(f"名称：{name}")
        lines.append(f"物品ID：{it['id']}")
        lines.append(f"内部索引：{it['idx']}")
        lines.append("")
        lines.append("基础：")
        lines.append(f"  价格：{fmt(it['price'])}")
        lines.append(f"  重量：{fmt(it['weight'])}")
        lines.append(f"  丰裕度：{fmt(it['abundance'])}")
        lines.append(f"  标志1：{fmt(it['flag1'])}")
        lines.append(f"  标志2：{it['flag2']}")
        lines.append(f"  属性标志：{it['prop']}")
        lines.append("")
        if stats:
            lines.append("后续数值：")
            lines.append("  " + " ".join(fmt(x) for x in stats))
            lines.append("")

        if len(stats) >= 4:
            lines.append("防御 / 需求字段：")
            if is_goods:
                lines.append(f"  货物数量：{fmt(stats[0])}")
            else:
                lines.append(f"  头防：{fmt(stats[0])}")
            if is_horse:
                lines.append(f"  马匹防御：{fmt(stats[1])}")
            elif is_shield:
                lines.append(f"  盾牌抗性：{fmt(stats[1])}")
            else:
                lines.append(f"  身防：{fmt(stats[1])}")
            if is_ranged or is_ammo or is_throwing:
                acc = stats[2]
                if acc == 0:
                    lines.append("  精度：99（原始值为 0）")
                else:
                    lines.append(f"  精度：{fmt(acc)}")
            else:
                lines.append(f"  腿防：{fmt(stats[2])}")
            lines.append(f"  需求 / 难度：{fmt(stats[3])}")

        if is_horse and len(stats) >= 10:
            lines.append("")
            lines.append("马匹字段：")
            lines.append(f"  生命：{fmt(stats[4])}")
            lines.append(f"  速度：{fmt(stats[5])}")
            lines.append(f"  操纵：{fmt(stats[6])}")
            lines.append(f"  字段7：{fmt(stats[7])}")
            lines.append(f"  冲锋：{fmt(stats[9])}")

        if is_shield and len(stats) >= 8:
            lines.append("")
            lines.append("盾牌字段：")
            lines.append(f"  抗性：{fmt(stats[1])}")
            lines.append(f"  耐久：{fmt(stats[4])}")
            lines.append(f"  速度：{fmt(stats[5])}")
            if len(stats) > 7:
                lines.append(f"  尺寸：{fmt(stats[7])}")

        if is_ammo and len(stats) >= 11:
            lines.append("")
            lines.append("弹药 / 投射字段：")
            lines.append(f"  长度/模型缩放（倒数第4）：{fmt(stats[-4])}")
            lines.append(f"  数量（倒数第3）：{fmt(stats[-3])}")
            lines.append(f"  伤害（倒数第2）：{decode_damage(stats[-2])}")

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
            lines.append("武器字段：")
            lines.append(f"  速度（倒数第6）：{fmt(stats[-6])}")
            if is_ranged:
                lines.append(f"  弹道飞行速度（倒数第5）：{fmt(stats[-5])}")
            elif is_throwing:
                lines.append(f"  弹道飞行速度（倒数第5）：{fmt(stats[-5])}")
                lines.append(f"  数量（倒数第3）：{fmt(stats[-3])}")
            else:
                lines.append(f"  范围（倒数第4）：{fmt(stats[-4])}")
            lines.append(f"  伤害1（倒数第2）：{decode_damage(stats[-2])}")
            lines.append(f"  伤害2（最后）：{decode_damage(stats[-1])}")

        extra = [e for e in it.get("extra", []) if e.strip() and e.strip() != "0"]
        if extra:
            lines.append("")
            lines.append("附加行：")
            for e in extra[:5]:
                lines.append("  " + e)
        lines.append("")
        lines.append("原始首行：")
        lines.append(it["raw_first_line"])
        return "\n".join(lines)

    def _fill_item_edit(self, it):
        self.edit_price_var.set(fmt(it["price"]))
        self.edit_weight_var.set(fmt(it["weight"]))
        self.edit_abundance_var.set(fmt(it["abundance"]))
        self.edit_stats_var.set(" ".join(fmt(x) for x in it["stats"]))
        self._build_attr_rows(it)

    def _build_attr_rows(self, it):
        """按物品类型重建逐项属性输入行 (标签来自 item_attr_fields)。"""
        for w in self.attr_rows.winfo_children():
            w.destroy()
        self.attr_widgets = []
        stats = it["stats"]
        for idx, label, dmg in item_attr_fields(it):
            row = ttk.Frame(self.attr_rows)
            row.pack(fill="x", padx=4, pady=1)
            ttk.Label(row, text=label, width=16, anchor="w").pack(side="left")
            raw = stats[idx]
            if dmg:
                val, kind = split_damage(raw)
                var = tk.StringVar(value=str(val))
                ttk.Entry(row, textvariable=var, width=8).pack(side="left")
                kv = tk.StringVar(value=kind)
                ttk.Combobox(row, values=list(DMG_KINDS), width=3, state="readonly",
                             textvariable=kv).pack(side="left", padx=2)
                self.attr_widgets.append((idx, label, var, kv))
            else:
                var = tk.StringVar(value=fmt(raw))
                ttk.Entry(row, textvariable=var, width=10).pack(side="left")
                self.attr_widgets.append((idx, label, var, None))

    def apply_item_attrs(self):
        """把逐项属性的改动写回 stats (只改界面里列出的索引, 其余保持原样)。"""
        if self.cur_item is None or not (0 <= self.cur_item < len(self.items_full)):
            return
        it = self.items_full[self.cur_item]
        stats = list(it["stats"])
        bad, updates = [], {}
        for idx, label, var, kv in self.attr_widgets:
            raw = var.get().strip()
            try:
                v = int(raw)
            except ValueError:
                bad.append(label)
                continue
            if kv is not None:          # 伤害字段: 数值 + 类型 -> 原始编码
                if v < 0:
                    bad.append(label)
                    continue
                v = encode_damage(v, kv.get())
            updates[idx] = v
        if bad:
            messagebox.showwarning("输入错误", "以下属性需为整数: " + ", ".join(sorted(set(bad))))
            return
        if not updates:
            return
        for idx, v in updates.items():
            if idx < len(stats):
                stats[idx] = v
        it["stats"] = stats
        self.item_dirty = True
        self._fill_item_edit(it)
        self._set(self.idetail, self.format_item(it))
        self.set_status("物品属性已修改（未保存）")

    def apply_item_basic(self):
        if self.cur_item is None or not (0 <= self.cur_item < len(self.items_full)):
            return
        it = self.items_full[self.cur_item]
        try:
            price = int(self.edit_price_var.get())
            weight = float(self.edit_weight_var.get())
            abundance = int(self.edit_abundance_var.get())
        except ValueError:
            messagebox.showwarning("输入错误", "价格/丰裕度需为整数, 重量需为数字。")
            return
        it["price"] = price
        it["weight"] = weight
        it["abundance"] = abundance
        self.item_dirty = True
        self._set(self.idetail, self.format_item(it))
        self.set_status("物品基础属性已修改（未保存）")

    def apply_item_stats(self):
        if self.cur_item is None or not (0 <= self.cur_item < len(self.items_full)):
            return
        it = self.items_full[self.cur_item]
        toks = self.edit_stats_var.get().split()
        try:
            vals = [int(t) for t in toks]
        except ValueError:
            messagebox.showwarning("输入错误", "后续数值需为整数 (空格分隔)。")
            return
        it["stats"] = vals
        self.item_dirty = True
        self._set(self.idetail, self.format_item(it))
        self.set_status("物品数值已修改（未保存）")

    def save_items(self, backup=True):
        """把 items_full 写回 item_kinds1.txt。仅替换各物品首行后部的 flag1/flag2/价格/属性
        标志/重量/丰裕度/数值 字段, 前部 (id/name/plural/mesh tokens) 与附加行原样保留。"""
        if not self.items_full:
            messagebox.showwarning("提示", "尚未载入模块。")
            return
        folder = self.mod.folder
        p = Path(folder) / "item_kinds1.txt"
        if not p.exists():
            messagebox.showerror("错误", f"未找到 {p}")
            return
        if backup:
            import shutil, datetime
            stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            bak = p.with_name(p.stem + f".{stamp}.bak")
            shutil.copy2(p, bak)
        # 探测原文件换行符, 尽量保留
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
        self.set_status(f"已保存物品并备份: {p.name}", ok=True)

    # ---------------- 物品编辑 ----------------
    def _ensure_inv_editor(self):
        """74 行槽控件 (背包 64 + 装备 10) 只创建一次, 之后仅更新值 —— 避免每次
        选中兵种都重建几百个控件造成卡顿。"""
        if getattr(self, "_inv_built", False):
            return
        parent = self.inv_scroll.inner
        tk.Label(parent, text="背包物品栏 (64 槽)", anchor="w").pack(anchor="w", padx=4, pady=(2, 0))
        self.inv_rows = [self._make_inv_row(parent, k, False) for k in range(M.INV_SLOTS)]
        tk.Label(parent, text="装备槽 (10 槽)", anchor="w").pack(anchor="w", padx=4, pady=(6, 0))
        self.equip_rows = [self._make_inv_row(parent, k, True) for k in range(M.EQUIP_SLOTS)]
        self._inv_built = True

    # ---- 修饰符 (imod) 下拉辅助 ----
    # 语义(已用全存档分布 + cns/item_modifiers.csv 验证):
    #   存档槽位第二列 u32 = (修饰符 << 24) | 耐久/数量;
    #   troops.txt 模板第二列 = 裸修饰符枚举(0=普通, 18=重型, 42=一大袋...)。
    def _imod_options(self):
        return ["%d %s" % (i, n) for i, n in enumerate(M.IMOD_NAMES)]

    def _imod_text(self, v):
        try:
            v = int(v)
        except Exception:
            return "0"
        if 0 <= v < len(M.IMOD_NAMES):
            return "%d %s" % (v, M.IMOD_NAMES[v])
        return str(v)

    def _parse_imod(self, s):
        try:
            return max(0, min(255, int(str(s).strip().split()[0])))
        except Exception:
            return 0

    def _make_inv_row(self, parent, k, is_equip):
        r = ttk.Frame(parent)
        r.pack(fill="x", padx=6, pady=1)
        label = ("装备#" if is_equip else "背包#") + str(k)
        ttk.Label(r, text=label, width=8).pack(side="left")
        cmb = ttk.Combobox(r, values=self.item_options, width=42, state="readonly")
        cmb.pack(side="left", padx=2)
        ttk.Label(r, text="修饰").pack(side="left")
        type_var = tk.StringVar(value=self._imod_text(0))
        sp_t = ttk.Combobox(r, values=self._imod_options(), width=11,
                            textvariable=type_var)   # 可编辑: 也可直接输入原始数值
        sp_t.pack(side="left", padx=1)
        amt_var = tk.IntVar(value=0)
        sp_a = None
        if is_equip:
            # 仅存档行有「耐久/数量」: 马匹=生命, 盾牌=耐久, 食物=数量; 模板第二列无此段
            ttk.Label(r, text="耐久/数量").pack(side="left")
            sp_a = ttk.Spinbox(r, from_=0, to=16777215, width=9, textvariable=amt_var)
            sp_a.pack(side="left", padx=1)
        btn = ttk.Button(r, text="清空", command=lambda k=k, eq=is_equip: self._clear_slot(k, eq))
        btn.pack(side="left", padx=2)

        def on_item(ev=None):
            self._apply_slot(k, is_equip, item_sel=cmb.current())
        def on_type(*a):
            self._apply_slot(k, is_equip)
        def on_amt(*a):
            self._apply_slot(k, is_equip)
        cmb.bind("<<ComboboxSelected>>", on_item)
        sp_t.bind("<<ComboboxSelected>>", on_type)
        sp_t.bind("<FocusOut>", on_type); sp_t.bind("<Return>", on_type)
        if sp_a is not None:
            sp_a.bind("<FocusOut>", on_amt); sp_a.bind("<Return>", on_amt)
        return dict(cmb=cmb, type_var=type_var, amt_var=amt_var, sp_t=sp_t, sp_a=sp_a, btn=btn)

    def _set_row(self, w, pair, is_equip):
        iid, mod = pair
        if iid == -1:
            w["cmb"].current(0)
        else:
            idx = (iid + 1) if 0 <= iid < len(self.item_options) - 1 else 0
            w["cmb"].current(idx)
        if is_equip:
            t, a = M.SaveDoc.decode_mod(mod)   # 存档: (修饰符, 耐久/数量)
        else:
            t, a = mod, 0                      # 模板: 第二列 = 裸修饰符
        w["type_var"].set(self._imod_text(t))
        w["amt_var"].set(a)

    def _tpl_inv(self, i, n):
        """troops.txt 模板物品行 (仅背包; 装备在模板中无独立段)。"""
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
        # 背包(64) = troops.txt 模板 (抽象兵种), 始终可编辑, 无需存档
        inv = self._tpl_inv(i, M.INV_SLOTS)
        # 装备(10) = 存档实例, 仅载档且兵种可靠时可编辑
        if self.doc is not None and self.doc.is_reliable(i):
            eq = self.doc.get_equipment(i); eq_editable = True
        else:
            eq = [(-1, 0)] * M.EQUIP_SLOTS; eq_editable = False
        for w in self.inv_rows + self.equip_rows:
            w["cmb"].config(state="normal")
        for k, w in enumerate(self.inv_rows):
            self._set_row(w, inv[k], False)
        for k, w in enumerate(self.equip_rows):
            self._set_row(w, eq[k], True)
        # 背包始终可编辑; 装备按条件
        for w in self.inv_rows:
            w["cmb"].config(state="readonly")
            w["sp_t"].config(state="normal")
            if w["sp_a"] is not None:
                w["sp_a"].config(state="normal")
            w["btn"].config(state="normal")
        cstate = "readonly" if eq_editable else "disabled"
        sstate = "normal" if eq_editable else "disabled"
        for w in self.equip_rows:
            w["cmb"].config(state=cstate); w["sp_t"].config(state=sstate)
            if w["sp_a"] is not None:
                w["sp_a"].config(state=sstate)
            w["btn"].config(state=sstate)
        self._update_inv_summary(i)

    def _update_inv_summary(self, i):
        if i is None:
            self.inv_summary.config(text="")
            return
        tpl = "背包(64)=troops.txt模板·抽象兵种·开新档生效"
        if getattr(self.mod, "_tpl_dirty", False):
            tpl += " [未保存]"
        if self.doc is not None and self.doc.is_reliable(i):
            eq = "装备(10)=存档实例·改后『保存存档』"
        else:
            eq = "装备(10)=需载档且兵种可靠"
        self.inv_summary.config(text=f"{tpl}   |   {eq}")

    def _collect_inv_flat(self):
        """从 64 个背包行收集扁平 int 列表 (物品id/修饰符 成对)。
        模板第二列 = 裸修饰符枚举(不带耐久/数量) —— 旧版在这里写入 (数量<<8|类型) 会破坏 troops.txt。"""
        flat=[]
        for w in self.inv_rows:
            sel=w["cmb"].current()
            iid=-1 if (sel is None or sel<=0) else sel-1
            iid=max(-1, min(self.mod.nitems, iid))
            flat.append(iid)
            flat.append(self._parse_imod(w["type_var"].get()))
        return flat

    def _apply_slot(self, k, is_equip, item_sel=None):
        i = self.cur
        if i is None:
            return
        w = (self.equip_rows if is_equip else self.inv_rows)[k]
        sel = w["cmb"].current() if item_sel is None else item_sel
        # 合法性校验: 物品 id 必须在 [-1, nitems]
        item_id = -1 if (sel is None or sel <= 0) else sel - 1
        item_id = max(-1, min(self.mod.nitems, item_id))
        t = self._parse_imod(w["type_var"].get())
        try:
            a = max(0, min(16777215, int(w["amt_var"].get())))
        except Exception:
            a = 0
        mod = M.SaveDoc.encode_mod(a, t)   # 存档: (修饰符<<24) | 耐久/数量
        if is_equip:
            if not (self.doc is not None and self.doc.is_reliable(i)):
                return
            try:
                self.doc.set_equipment_slot(i, k, item_id, mod)
                self._update_inv_summary(i)
                self.set_status(f"已写入 #{i} 装备槽{k}: 物品={item_id} 修饰符={self._imod_text(t)} 耐久/数量={a}  (存档实例)")
            except Exception as ex:
                self.set_status(f"写入失败: {ex}", err=True)
        else:
            # 抽象兵种模板 (troops.txt) —— 无需存档即可修改
            try:
                flat = self._collect_inv_flat()
                self.mod.set_troop_inventory(i, flat)
                self._update_inv_summary(i)
                self.set_status(f"已写入 #{i} 背包槽{k}: 物品={item_id} 修饰符={self._imod_text(t)}  (troops.txt 模板·未保存)")
            except Exception as ex:
                self.set_status(f"写入失败: {ex}", err=True)

    def _clear_slot(self, k, is_equip):
        i = self.cur
        if i is None:
            self.set_status("请先选中一个兵种。", err=True)
            return
        w = (self.equip_rows if is_equip else self.inv_rows)[k]
        w["cmb"].current(0); w["type_var"].set(self._imod_text(0)); w["amt_var"].set(0)
        if is_equip:
            if not (self.doc is not None and self.doc.is_reliable(i)):
                self.set_status("装备槽需载档且兵种可靠才可清空。", err=True)
                return
            self._apply_slot(k, True, item_sel=0)
        else:
            self._apply_slot(k, False, item_sel=0)

    def save_module_file(self):
        try:
            out = self.mod.save_module(backup=True)
            self.set_status(f"已保存模块并备份: {os.path.basename(str(out))}", ok=True)
            try:
                self.flag_hint.config(text="模板·已保存")
                self.up_hint.config(text="模板·已保存")
            except Exception:
                pass
            self._update_inv_summary(self.cur)
        except Exception as ex:
            messagebox.showerror("保存失败", str(ex))

    def save_save(self):
        if self.doc is None:
            messagebox.showinfo("提示", "请先在『文件』菜单载入存档。")
            return
        try:
            out = self.doc.save(backup=True)
            self.set_status(f"已保存并备份: {os.path.basename(str(out))}", ok=True)
        except Exception as ex:
            messagebox.showerror("保存失败", str(ex))

    def on_close(self):
        if getattr(self.mod, "_tpl_dirty", False):
            if not messagebox.askyesno("未保存的模组修改", "你对 troops.txt 模板的修改尚未保存, 确定退出?"):
                return
        if getattr(self, "item_dirty", False):
            if not messagebox.askyesno("未保存的物品修改", "你对 item_kinds1.txt 物品的修改尚未保存, 确定退出?"):
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
            # 物品多重集 (背包 + 装备)
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
            messagebox.showinfo("提示", "请先在『文件』菜单载入存档。")
            return
        total = len(self.doc.starts)
        s_all, n_all, _ = self._cmp_stat(0, total - 1)
        s_std, n_std, samples = self._cmp_stat(6, 140)
        out = []
        out.append(f"存档兵种数: {total}\n\n")

        def block(title, stat, n):
            out.append(title + "\n")
            for k, lbl in (("level", "等级"), ("prof", "熟练度"), ("skill", "技能"),
                           ("inv", "物品"), ("attr", "四维属性")):
                v = stat[k]
                out.append(f"   {lbl:6s}: {v}/{n}  ({v * 100 // max(n, 1)}%)\n")
            out.append("\n")

        block("【A】普通士兵 #6~#140  (黄金标准: 不是同伴, 实例化应与模板一致)\n"
              "     这是判断『解析有没有读错』的可靠依据。", s_std, n_std)
        block("【B】全部兵种 #0~#%d  (含未实例化的领主/同伴蓝图, 一致率天然偏低)" % (total - 1),
              s_all, n_all)

        out.append("判读说明:\n")
        out.append("  - 【A】的 等级/熟练度 应接近 100%; 若明显偏低, 说明字段偏移读错了。\n")
        out.append("  - 【B】偏低是正常的: 领主/同伴(如 #203 爱德华国王)在存档里多为\n")
        out.append("    『未实例化蓝图』—— 属性为 0、flags 为 0, 与 troops.txt 不同并非解析错误。\n")
        out.append("  - 属性: 存档 = 模板 + 随机分配, 使 属性和 = 等级 + 20, 故与模板不同属正常。\n")
        out.append("  - 技能: 存档 = 模板 + 少量随机技能点。\n")
        out.append("  - 物品: 可能有装备随机变体(同类物品替换), 故通常达不到 100%。\n")
        out.append("\n样例(#6起): 名称 / 模板属性→存档属性 / 模板熟练→存档熟练 / 技能差异位\n")
        for s in samples:
            out.append(f"  #{s[0]} {s[1]}\n     属性 {s[2]} -> {s[3]}\n"
                       f"     熟练 {s[4]} -> {s[5]}\n     技能差异位 {s[6]}\n")
        self._set(self.cmp_detail, "".join(out))

    def _set(self, w, txt):
        w.configure(state="normal")
        w.delete("1.0", "end")
        w.insert("1.0", txt)
        w.configure(state="disabled")


    # ---------------- 部队模板(party_templates.txt)编辑 ----------------
    def load_party_templates(self, folder):
        p = Path(folder) / "party_templates.txt"
        self.pt_file = p
        if not p.exists():
            self.party_templates = []
            self.pt_original = []
            self.set_status(f"未找到 party_templates.txt: {p} (该模块可能没有部队模板)", err=True)
            return
        raw = p.read_bytes()
        self.pt_raw = raw
        self.pt_line_sep = "\r\n" if b"\r\n" in raw else "\n"   # 还原原文件换行
        # ⚠ 不能用 decode("utf-8", errors="replace"): 无法解析的字节会被换成 U+FFFD,
        #    再写回就把原字符永久损坏。改为严格判定 + 记住编码, 写回时用同一编码。
        text, self.pt_encoding, self.pt_bom = decode_module_text(raw)
        self.pt_header = text.split("\n", 1)[0].rstrip("\r") or "partytemplatesfile version 1"
        self.party_templates = parse_party_templates(text)
        self.pt_original = copy.deepcopy(self.party_templates)
        self.pt_roundtrip_ok = self._pt_roundtrip_ok()
        if getattr(self, "ptlist", None) is not None:
            self.refresh_pt()
        n = len(self.party_templates)
        if self.pt_roundtrip_ok:
            self.set_status(f"已载入部队模板: {p.name}  ({n} 个模板, 自检通过)", ok=True)
        else:
            self.set_status(f"已载入部队模板: {p.name}  ({n} 个模板) "
                            f"⚠ 自检失败: 无法字节级还原该文件, 已禁止保存以免写坏模组", err=True)

    def _pt_roundtrip_ok(self):
        """自检: 把载入的原始模板序列化后能否还原为原始文件字节。

        这是落盘前的保险丝 —— 一旦本工具的解析器跟不上某个模组的实际格式,
        就拒绝写盘, 而不是把人家的模组写成坏档。"""
        try:
            out = serialize_party_templates(self.pt_original, self.pt_header,
                                            self.pt_line_sep)
            return encode_module_text(out, self.pt_encoding, self.pt_bom) == self.pt_raw
        except Exception:
            return False

    def build_pt_tab(self):
        top = ttk.Frame(self.tab_pt)
        top.pack(fill="x", padx=4, pady=3)
        ttk.Label(top, text="搜索:").pack(side="left")
        self.pt_search = tk.StringVar()
        e = ttk.Entry(top, textvariable=self.pt_search)
        e.pack(side="left", fill="x", expand=True, padx=4)
        e.bind("<Return>", lambda ev: self.refresh_pt())
        ttk.Button(top, text="过滤", command=self.refresh_pt).pack(side="left")
        ttk.Button(top, text="全部", command=lambda: (self.pt_search.set(""), self.refresh_pt())).pack(side="left", padx=2)

        hp = tk.PanedWindow(self.tab_pt, orient="horizontal")
        hp.pack(fill="both", expand=True, padx=4, pady=4)
        lf = ttk.LabelFrame(hp, text="部队模板列表")
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
        hf = ttk.LabelFrame(vp, text="模板信息 / 兵种栈编辑")
        vp.add(hf, stretch="always", height=360)
        idbar = ttk.Frame(hf)
        idbar.pack(side="top", fill="x", padx=4, pady=2)
        self.pt_id_label = ttk.Label(idbar, text="(未选择)", anchor="w")
        self.pt_id_label.pack(side="left", fill="x", expand=True)
        ttk.Label(idbar, text="flags:").pack(side="left")
        self.pt_flags_var = tk.IntVar(value=0)
        self.pt_flags_entry = ttk.Spinbox(idbar, from_=-2147483648, to=2147483647,
                                         width=12, textvariable=self.pt_flags_var)
        self.pt_flags_entry.pack(side="left", padx=2)
        self.pt_flags_entry.bind("<FocusOut>", lambda ev: self._apply_pt_flag())
        self.pt_flags_entry.bind("<Return>", lambda ev: self._apply_pt_flag())
        self.pt_stack_count = ttk.Label(idbar, text="兵种栈: 0", foreground="#888")
        self.pt_stack_count.pack(side="left", padx=6)

        stackbar = ttk.Frame(hf)
        stackbar.pack(side="top", fill="x", padx=4, pady=2)
        ttk.Button(stackbar, text="+ 添加兵种栈", command=self._add_pt_stack).pack(side="left", padx=2)
        ttk.Button(stackbar, text="保存部队模板(party_templates.txt)",
                   command=self.save_party_templates).pack(side="left", padx=2)

        self.pt_stack_scroll = ScrolledFrame(hf)
        self.pt_stack_scroll.pack(fill="both", expand=True)
        self.pt_stack_inner = self.pt_stack_scroll.inner

        df = ttk.LabelFrame(vp, text="模板文本(只读预览)")
        vp.add(df, stretch="always", height=110)
        self.pt_detail = self._mk_text(df)

        lf2 = ttk.LabelFrame(vp, text="修改记录(本会话显示 + 写入 party_template_edit_log.txt)")
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
            self.ptlist.insert("end", f"#{i:2d}  {t['id']}   [{t['name']}]  ({len(t['stacks'])}栈)")

    def on_pt_select(self, ev=None):
        s = self.ptlist.curselection()
        if not s:
            return
        self.show_pt(self.pt_filtered[s[0]])

    def show_pt(self, i):
        self.pt_cur = i
        t = self.party_templates[i]
        self.pt_id_label.config(text=f"ID: {t['id']}    名称: {t['name']}")
        self.pt_flags_var.set(int(t["flags"]))
        self._fill_pt_stacks(t)
        self.pt_stack_count.config(text=f"兵种栈: {len(t['stacks'])}")
        self.pt_detail.configure(state="normal")
        self.pt_detail.delete("1.0", "end")
        self.pt_detail.insert("1.0", self._pt_raw(t))
        self.pt_detail.configure(state="disabled")

    def _pt_raw(self, t):
        L = [f"{t['id']}  ({t['name']})", f"flags={t['flags']}  fixed0={t['fixed0']}", ""]
        for k, s in enumerate(t["stacks"]):
            L.append(f"  栈#{k}: 兵种={s[0]} 数量A={s[1]} 数量B={s[2]} 标志={s[3]}")
        L.append("")
        L.append("（数量A/B 为模板的 min/max 计数参数, 原文件书写顺序可能相反, 按原值改即可;")
        L.append("  标志为栈标志位; 兵种下标 -1 = 空）")
        return "\n".join(L)

    def _make_pt_row(self, parent, k):
        r = ttk.Frame(parent)
        r.pack(fill="x", padx=6, pady=1)
        ttk.Label(r, text=f"栈#{k}", width=6).pack(side="left")
        cmb = ttk.Combobox(r, values=self.troop_options, width=40, state="readonly")
        cmb.pack(side="left", padx=2)
        ttk.Label(r, text="数量A").pack(side="left")
        a_var = tk.IntVar(value=0)
        sp_a = ttk.Spinbox(r, from_=0, to=100000, width=8, textvariable=a_var)
        sp_a.pack(side="left", padx=1)
        ttk.Label(r, text="数量B").pack(side="left")
        b_var = tk.IntVar(value=0)
        sp_b = ttk.Spinbox(r, from_=0, to=100000, width=8, textvariable=b_var)
        sp_b.pack(side="left", padx=1)
        ttk.Label(r, text="标志").pack(side="left")
        f_var = tk.IntVar(value=0)
        sp_f = ttk.Spinbox(r, from_=-2147483648, to=2147483647, width=10, textvariable=f_var)
        sp_f.pack(side="left", padx=1)
        btn = ttk.Button(r, text="删除", command=lambda k=k: self._del_pt_stack(k))
        btn.pack(side="left", padx=2)

        def on_troop(ev=None):
            self._apply_pt_stack(k, 0, cmb.current())
        def on_a(*a):
            v = safe_int(a_var)
            if v is None: return      # 输入框被清空(正在重填), 忽略本次
            self._apply_pt_stack(k, 1, v)
        def on_b(*a):
            v = safe_int(b_var)
            if v is None: return
            self._apply_pt_stack(k, 2, v)
        def on_f(*a):
            v = safe_int(f_var)
            if v is None: return
            self._apply_pt_stack(k, 3, v)
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
            self.set_status("请先在左侧选择一个部队模板。", err=True)
            return
        t = self.party_templates[i]
        nslots = int(t.get("nslots") or 6)      # 该模板的栈位容量(真实格式恒为 6)
        if len(t["stacks"]) >= nslots:
            self.set_status(f"无法新增: 该模板只有 {nslots} 个栈位且已用完 "
                            f"(party_templates 每行的栈槽是固定 {nslots} 个, "
                            f"超出会让整行 token 数偏移、游戏读串后续模板)。"
                            f"请先删除或改写现有栈。", err=True)
            return
        # -1 是"空槽/结束"哨兵: 一行里空槽只占 1 个 token, 而写成一条栈要占 4 个 token,
        # 所以占位只能临时用 -1, 保存前必须选好兵种(见 save_party_templates 守卫1)。
        t["stacks"].append([-1, 0, 0, 0])
        self._fill_pt_stacks(t)
        self.pt_stack_count.config(text=f"兵种栈: {len(t['stacks'])} / {nslots}")
        self.set_status("已新增一个空兵种栈: 请先在它的下拉框里选好兵种再保存 "
                        "(-1 是栈区结束哨兵, 未指定兵种会被拒绝保存)。", ok=True)

    def _del_pt_stack(self, k):
        i = self.pt_cur
        if i is None:
            return
        st = self.party_templates[i]["stacks"]
        if 0 <= k < len(st):
            st.pop(k)
            self._fill_pt_stacks(self.party_templates[i])
            self.pt_stack_count.config(text=f"兵种栈: {len(st)}")

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
            messagebox.showinfo("提示", "请先载入含 party_templates.txt 的模块。")
            return
        # 守卫1: 不允许写入"兵种未指定(-1)"的栈。 -1 是栈区终止哨兵, 写进去会被引擎
        #        当成"栈到此为止", 该栈及其后的内容都不会生效(=静默失效的修改)。
        for _t in self.party_templates:
            for _k, _s in enumerate(_t["stacks"]):
                if int(_s[0]) < 0:
                    messagebox.showerror(
                        "无法保存",
                        f"模板 {_t.get('id', '?')} 的第 {_k + 1} 个兵种栈没有指定兵种(-1)。\n\n"
                        f"-1 是兵种栈区的结束哨兵, 写进文本游戏会把它当作“栈到此为止”。\n"
                        f"请为每个新增的栈先在下拉框里选好兵种, 然后再保存。")
                    return
        # 守卫2: 往返自检。若本工具无法字节级还原原文件, 宁可不写也不能写坏模组。
        if not getattr(self, "pt_roundtrip_ok", True) and not self._pt_roundtrip_ok():
            messagebox.showerror(
                "无法保存",
                "自检失败: 本工具无法字节级还原该 party_templates.txt。\n\n"
                "为避免写坏模组已阻止写入。请把该文件反馈给作者 —— "
                "它的编码或版式可能超出了当前解析器的支持范围。")
            return
        records = []
        for cur, orig in zip(self.party_templates, self.pt_original):
            tid = cur["id"]
            if int(cur["flags"]) != int(orig["flags"]):
                records.append((tid, "flags", orig["flags"], cur["flags"]))
            cs, os_ = cur["stacks"], orig["stacks"]
            if len(cs) != len(os_):
                records.append((tid, "栈数量", len(os_), len(cs)))
            else:
                for k in range(len(cs)):
                    for f, fn in enumerate(("兵种", "数量A", "数量B", "标志")):
                        if int(cs[k][f]) != int(os_[k][f]):
                            records.append((tid, f"栈#{k + 1}.{fn}", os_[k][f], cs[k][f]))
        if not records:
            self.set_status("部队模板没有改动, 无需保存。", ok=True)
            return
        try:
            if self.pt_file.exists():
                shutil.copy2(str(self.pt_file), str(self.pt_file) + ".bak")
            out = serialize_party_templates(self.party_templates, self.pt_header,
                                           self.pt_line_sep)
            # ⚠ 必须 write_bytes: write_text 是文本模式, 在 Windows 上会把每个 '\n'
            #    翻译成 '\r\n'; 原文件是 CRLF 时就被二次翻倍成 '\r\r\n'(每条换行多 1 个 CR),
            #    引擎解析会因行尾异常而报 unexpected end-of-file。2026-09-24 修复。
            data = encode_module_text(out, self.pt_encoding, self.pt_bom)
            self.pt_file.write_bytes(data)
            self.pt_raw = data          # 重新基线: 后续自检以本次写出的新文件为准
            self.pt_roundtrip_ok = True
            self.pt_original = copy.deepcopy(self.party_templates)
            self._log_pt_change(records)
            self.set_status(f"已保存部队模板并备份: {self.pt_file.name}  ({len(records)} 处修改)", ok=True)
        except Exception as ex:
            messagebox.showerror("保存失败", str(ex))

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
        self.pt_log_text.insert("1.0", "\n".join(self.pt_changelog) if self.pt_changelog else "(暂无修改记录)")
        self.pt_log_text.configure(state="disabled")


def main():
    root = tk.Tk()
    ViewerV2(root)
    root.mainloop()


if __name__ == "__main__":
    main()
