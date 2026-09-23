#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
骑砍 1.011 存档兵种模型 (model only, no GUI)  --  可无界面测试
================================================================
关键事实:
  * 兵种(hero/troop)记录在存档中按 troops.txt 索引 0..793 顺序紧密存储.
  * 记录头为固定 0x3E4 字节(全部定长可编辑字段: 四维/熟练/技能/旗帜/阵营/物品栏/装备),
    之后是 长度前缀的 UTF-8 简介, 再之后是可变『槽』(slot) 区。
  * 【2026-09-19 修正】1.011 兵种**确有**槽数据结构(此前"1.011 无 slot"结论已被脚本交叉验证推翻)。
    但英雄尾部**并非只有一种结构** —— 按 tail_kind 分三类(详见 tail_kind 文档):
      (lord254) 活跃领主且内联含固定 254 槽密集数组(记录尾, 起点 nxt-1016): 性格=逻辑槽 102,
                职业=槽 2, led_party=槽 10。
      (dense)   非活跃英雄(同伴/王子/小姐)的标准密集槽数组(起点 t0, 起手 [-1,-1,0,...]):
                性格=逻辑槽 128。
      (blob)    尾部是『外观/标记』压缩块(起手 [-1,32,0,0,32,...] 的 [32,0,0] 重复结构),
                **不是**可读槽数组: 复国者(308..312 等)及少数『有部队但实时数据写在 party 系统』
                的活跃领主(204/205/206/307)属此列。复国者性格按游戏规则恒为 君主(0);
                后者的性格在 party 实体数据里, 内联不可读。
    注: 旧结论"活跃领主实时槽在另一区段、性格=槽128"已被证伪 —— 那是把 blob 块误当槽数组所致。
  * 校验链: 等级(1..63) -> 四维(0..300) -> 7个熟练浮点(第7项恒0) ->
    物品栏64槽(item_id:-1或0..NITEMS) -> 装备10槽 -> 简介(UTF-8可打印).
  * 记录最小长度 = 0x3E4(996) + 0(bio) + 96(tail) = 1092; 因此相邻记录至少
    间隔 1092 字节, 可用此跳过记录内部的伪命中(误报).
  * 记录因 UTF-8 简介长度不同而对 4 字节不对齐 -> 必须逐字节(byte-by-byte)扫描.
"""
import os
import sys
import json
import struct
import re
from pathlib import Path

# ===========================================================================
# 模块目录定位 (可移植): 显式指定 > 上次记忆(json) > 自动探测 > 手动选择
# ---------------------------------------------------------------------------
# 目的: 工具拷到别人机器上时, 不应依赖本机硬编码路径 (E:\Program Files\...)。
# 统一由这里解析, 四个 GUI (中/英 x 查看器/存档编辑器) 共用同一套规则。
# ===========================================================================
MB_ENV_MODULE  = "MB_MODULE_DIR"     # 环境变量: 直接指定模块目录
MB_ENV_MODULES = "MB_MODULES_DIR"    # 环境变量: 指定 Modules 根目录

# 常见安装位置 (仅当目录真实存在且含 troops.txt 时才会被采用)
STANDARD_MODULES_ROOTS = [
    r"E:\Program Files\Game\Mount&Blade\Modules",
    r"D:\Program Files\Game\Mount&Blade\Modules",
    r"C:\Program Files\Game\Mount&Blade\Modules",
    r"E:\Steam\steamapps\common\MountBlade\Modules",
    r"D:\Steam\steamapps\common\MountBlade\Modules",
    r"C:\Program Files (x86)\Steam\steamapps\common\MountBlade\Modules",
]

CFG_FILE_NAME    = "mb_module_path.json"          # 新配置(与脚本同目录)
LEGACY_CFG_NAMES = ("mb_editor_module.json",)     # 旧配置, 仅作首次迁移读取

_HERE    = Path(__file__).resolve().parent
CFG_PATH = _HERE / CFG_FILE_NAME


def _steam_library_dirs():
    """从注册表 + libraryfolders.vdf 推断 Steam 游戏库目录 (失败则返回空列表)。"""
    steam = None
    try:
        import winreg
    except Exception:
        return []
    for hive, sub in ((winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam"),
                      (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam"),
                      (winreg.HKEY_CURRENT_USER,  r"SOFTWARE\Valve\Steam")):
        try:
            with winreg.OpenKey(hive, sub) as k:
                steam, _ = winreg.QueryValueEx(k, "InstallPath")
        except Exception:
            continue
        if steam:
            break
    if not steam:
        return []
    libs = [Path(steam)]
    vdf = Path(steam) / "steamapps" / "libraryfolders.vdf"
    try:
        if vdf.exists():
            txt = vdf.read_text(encoding="utf-8", errors="replace")
            for m in re.finditer(r'"(?:path|Path)"\s+"([^"]+)"', txt):
                try:
                    libs.append(Path(m.group(1).replace("\\\\", "\\")))
                except Exception:
                    pass
    except Exception:
        pass
    return libs


def modules_roots():
    """候选 Modules 根目录 (去重, 仅保留真实存在的目录)。"""
    cands = []
    env = os.environ.get(MB_ENV_MODULES)
    if env:
        cands.append(env)
    for lib in _steam_library_dirs():
        common = lib / "steamapps" / "common"
        try:
            if common.is_dir():
                for d in sorted(common.iterdir()):
                    nm = d.name.lower().replace("&", "")
                    if d.is_dir() and ("mount" in nm and "blade" in nm):
                        cands.append(str(d / "Modules"))
        except Exception:
            pass
    cands += list(STANDARD_MODULES_ROOTS)
    out, seen = [], set()
    for c in cands:
        try:
            p = Path(c)
        except Exception:
            continue
        key = str(p).lower()
        if key in seen:
            continue
        seen.add(key)
        if p.is_dir():
            out.append(p)
    return out


def discover_modules():
    """扫描所有候选根, 返回 {模块目录名: 绝对路径} (仅含 troops.txt 的子目录)。"""
    found = {}
    for root in modules_roots():
        try:
            for d in sorted(root.iterdir()):
                if d.is_dir() and (d / "troops.txt").exists():
                    found.setdefault(d.name, str(d))
        except Exception:
            continue
    return found


def _read_cfg():
    for p in [CFG_PATH] + [_HERE / n for n in LEGACY_CFG_NAMES]:
        try:
            if p.exists():
                return json.loads(p.read_text(encoding="utf-8")) or {}
        except Exception:
            continue
    return {}


def _write_cfg(patch):
    """合并写入配置 (保留其它键)。写入失败静默忽略(只读环境也能用)。"""
    try:
        cur = {}
        if CFG_PATH.exists():
            try:
                cur = json.loads(CFG_PATH.read_text(encoding="utf-8")) or {}
            except Exception:
                cur = {}
        cur.update(patch or {})
        CFG_PATH.write_text(json.dumps(cur, ensure_ascii=False, indent=2),
                            encoding="utf-8")
        return True
    except Exception:
        return False


def _dir_ok(d):
    try:
        return bool(d) and (Path(d) / "troops.txt").exists()
    except Exception:
        return False


def get_last_module():
    """上次成功载入的模块目录 (json 记忆; 旧配置文件首次兼容读取)。"""
    d = _read_cfg().get("module_dir")
    return str(Path(d)) if _dir_ok(d) else None


def remember_module(folder):
    if _dir_ok(folder):
        _write_cfg({"module_dir": str(folder)})


def get_last_save_dir():
    d = _read_cfg().get("save_dir")
    try:
        return d if d and Path(d).is_dir() else None
    except Exception:
        return None


def remember_save_dir(p):
    try:
        if p:
            _write_cfg({"save_dir": str(Path(p).parent)})
    except Exception:
        pass


def resolve_default_module(prefer=None):
    """解析默认模块目录。

    优先级: 环境变量 MB_MODULE_DIR > 显式 prefer > 上次记忆(json) >
            自动探测(优先与 prefer 同名) > None(由调用方弹框让用户选)。
    """
    for c in (os.environ.get(MB_ENV_MODULE), prefer):
        if _dir_ok(c):
            return str(Path(c))
    last = get_last_module()
    if last:
        return last
    found = discover_modules()
    if not found:
        return None
    if prefer:
        bn = Path(prefer).name.lower()
        for name, path in sorted(found.items()):
            if name.lower() == bn:
                return path
    return found[sorted(found.keys())[0]]


def initial_dir_for_module_dialog():
    """『选择模块目录』对话框的起始位置。"""
    r = modules_roots()
    if r:
        return str(r[0])
    last = get_last_module()
    if last:
        return str(Path(last).parent)
    return str(Path.home())


# ---- 物品修饰符 (imod) 名称表 ----
# 枚举 0..42, 与模组 languages/cns/item_modifiers.csv 及模块系统
# header_item_modifiers.py 逐项一致 (2026-09-23 用全存档分布交叉验证)。
# 存档槽位第二列 u32 = (imod << 24) | 耐久/数量; troops.txt 模板第二列 = 裸 imod。
IMOD_NAMES = [
    "普通", "破裂", "生锈", "弯曲", "有缺口", "凹陷", "粗劣", "粗糙", "陈旧", "廉价",
    "优质", "精良", "锋利", "平衡", "回火", "致命", "精致", "极品", "重型", "坚硬",
    "强有力", "破烂", "蓬乱", "粗制", "结实", "加厚", "加硬", "加强", "华丽", "豪华",
    "瘸腿", "鞍背", "倔犟", "胆小", "温顺", "神骏", "一流", "新鲜", "隔夜", "隔两夜",
    "发臭", "腐烂", "一大袋",
]

# ---- 固定字段偏移 (相对每条记录起点) ----
OFF_ATTR   = 0x00          # 力量/敏捷/智力/魅力  4 x u32
OFF_PROF   = 0x10          # 武器熟练 x7         7 x f32 (第7项恒 0.0)
OFF_SKILL  = 0x2C          # 技能位域            6 x u32, 每项技能 4 bit, 共 48 项
OFF_XP     = 0x50          # 经验值             u32
OFF_FACTION= 0x5C          # 当前所属阵营索引     u32  (领主跳槽即改此值; 已结构化验证)
OFF_PT     = 0x68          # 未用点数           u32
OFF_LEVEL  = 0x6C          # 等级               u32
OFF_FLAGS  = 0x44          # 兵种标志位           u32 (tf_hero 等, 已对 troops.txt 验证)
OFF_INV    = 0x70          # 物品栏 64 槽        64 x (i32 item_id, u32 modifier)
#   注: OFF_INV 原误作 0x78。经与 troops.txt 交叉验证(索引6~140 普通士兵)确认真实起点为
#   +0x70 —— 0x78 会整体偏移一个槽, 导致每件兵种"丢失"背包槽0 的物品。

# ---- 兵种标志位 tf_* (与 troops.txt 头行第 4 字段一致, 已交叉验证) ----
TF_FEMALE     = 0x00000001   # 女性 (默认男性=0, 不置位)
TF_UNDEAD     = 0x00000002   # 不死族
TF_HERO       = 0x00000010   # 英雄/领主: 不会被杀死、独立加点、队伍技能生效、显示血量百分比
TF_INACTIVE   = 0x00000020
TF_UNKILLABLE = 0x00000040
TF_ALLWAYS_FALL_DEAD = 0x00000080
TF_NO_CAPTURE_ALIVE  = 0x00000100
TF_MOUNTED    = 0x00000400   # 骑马(地图速度按骑术)
TF_MERCHANT   = 0x00001000
TF_RANDOMIZE_FACE = 0x00008000
TF_GUR_BOOTS  = 0x00100000
TF_GUR_ARMOR  = 0x00200000
TF_GUR_HELMET = 0x00400000
TF_GUR_GLOVES = 0x00800000
TF_GUR_HORSE  = 0x01000000
TF_GUR_SHIELD = 0x02000000
TF_GUR_RANGED = 0x04000000
TF_GUR_POLEARM = 0x08000000
TF_UNMOVEABLE_IN_PARTY_WINDOW = 0x10000000

# 勾选 UI 与 decode_tf 共用的位表 (顺序即勾选框顺序)。女性/不死已纳入,
# 低4位类型不再单独输出, 避免与下方 decode_tf 重复。
TF_NAMES = [
    (TF_FEMALE, "女性(tf_female)"), (TF_UNDEAD, "不死族(tf_undead)"),
    (TF_HERO, "英雄(tf_hero)"), (TF_INACTIVE, "未激活"), (TF_UNKILLABLE, "不可杀"),
    (TF_ALLWAYS_FALL_DEAD, "必倒地"), (TF_NO_CAPTURE_ALIVE, "不可活捉"),
    (TF_MOUNTED, "骑马"), (TF_MERCHANT, "商人"), (TF_RANDOMIZE_FACE, "随机脸"),
    (TF_GUR_BOOTS, "保证靴"), (TF_GUR_ARMOR, "保证甲"), (TF_GUR_HELMET, "保证盔"),
    (TF_GUR_GLOVES, "保证手套"), (TF_GUR_HORSE, "保证马"), (TF_GUR_SHIELD, "保证盾"),
    (TF_GUR_RANGED, "保证远程"), (TF_GUR_POLEARM, "保证长杆"), (TF_UNMOVEABLE_IN_PARTY_WINDOW, "不可移动"),
]

def decode_tf(v):
    """把兵种标志位解码成中文说明列表。
    低4位: 男性=0(默认); 女性/不死已由 TF_NAMES 输出, 此处不再单独输出(避免重复);
    其余非标准类型值仍标为『未知类型』。"""
    if v is None: return []
    out=[]
    t=v & 0xF
    if t==0:
        out.append("男性")
    elif t not in (1, 2):
        out.append(f"未知类型({t})")
    for mask,name in TF_NAMES:
        if v & mask: out.append(name)
    known=0xF
    for m,_ in TF_NAMES: known|=m
    unknown=v & ~known
    if unknown: out.append(f"其他位:0x{unknown:08X}")
    return out
INV_SLOTS  = 64
OFF_EQUIP  = 0x370         # 装备槽 10 槽        10 x (i32 item_id, u32 modifier)
EQUIP_SLOTS= 10
OFF_BIO    = 0x3E0         # u32 简介长度 + 简介(UTF-8)
HDR        = 0x3E4
MIN_REC    = 1092          # 最小记录长度, 用于跳过记录内部误报
NITEMS_DEFAULT = 888       # 模块物品数 (索引 0..888); 加载模块后更新

# 技能位域: 6 x u32, 每项 4 bit, 共 48 技能
NSKILL = 48

def u32(b,o): return struct.unpack_from("<I",b,o)[0]
def i32(b,o): return struct.unpack_from("<i",b,o)[0]
def f32(b,o): return struct.unpack_from("<f",b,o)[0]
def w32(b,o,v): struct.pack_into("<I",b,o,v)
def wf32(b,o,v): struct.pack_into("<f",b,o,v)

def skills_decode(words):
    out=[]
    for i in range(NSKILL):
        out.append((words[i//8] >> ((i%8)*4)) & 0xF)
    return out

def skills_encode(vals):
    words=[0]*6
    for i,v in enumerate(vals[:NSKILL]):
        if v:
            words[i//8] |= (v & 0xF) << ((i%8)*4)
    return words

# ---------------- 模块数据 ----------------
class ModuleData:
    def __init__(self):
        self.troops=[]          # list of dict(tid, raw, flags, inv, attrs, profs, line5)
        self.troop_names={}     # tid -> 中文名
        self.items=[]           # list of itm_xxx
        self.item_names={}      # itm_xxx -> 中文名
        self.skills=[]          # list of skl_xxx
        self.skill_names={}     # skl_xxx -> 中文名
        self.factions=[]        # list of fac_xxx
        self.faction_names={}   # fac_xxx -> 中文名
        self.nitems=0
        self.loaded=False

    def _read_text(self, p):
        p=Path(p)
        if not p.exists(): return ""
        data=p.read_bytes()
        for enc in ("utf-8-sig","utf-8","gb18030","latin-1"):
            try: return data.decode(enc)
            except Exception: pass
        return data.decode("gb18030","replace")

    def _read_lines(self, p):
        """返回 (带行尾的各行列表, 编码). 保留原始行尾与 BOM, 以便安全写回。"""
        p=Path(p)
        data=p.read_bytes()
        if data[:3]==b"\xef\xbb\xbf":
            enc="utf-8-sig"
        else:
            enc="utf-8"
        try:
            text=data.decode(enc)
        except Exception:
            text=data.decode("gb18030","replace")
            enc="gb18030"
        return text.splitlines(keepends=True), enc

    def _parse_trans(self, p):
        d={}
        for line in self._read_text(p).splitlines():
            line=line.strip()
            if "|" not in line: continue
            k,v=line.split("|",1)
            k=k.strip()
            if not k.endswith("_pl"):
                d[k]=v.strip()
        return d

    def load(self, folder):
        folder=Path(folder)
        # skills
        skl=self._read_text(folder/"skills.txt").splitlines()
        self.skills=[l.split()[0] for l in skl if l.strip().startswith("skl_")]
        strans=self._parse_trans(folder/"languages"/"cns"/"skills.csv")
        self.skill_names={k:(strans.get(k) or k[4:]) for k in self.skills}
        # troops
        tlines, tenc = self._read_lines(folder/"troops.txt")
        self._enc = tenc
        self._raw_lines = tlines
        self.folder = folder
        groups=[]; cur=None
        line_map=[]; cur_lines=None
        for gi, raw in enumerate(tlines):
            if gi < 2: continue   # 前两行为非兵种头注释, 跳过
            s=raw.strip()
            if not s: continue
            if s.startswith("trp_"):
                if cur is not None:
                    groups.append(cur); line_map.append(cur_lines)
                cur=[raw]; cur_lines=[gi]
            elif cur is not None:
                cur.append(raw); cur_lines.append(gi)
        if cur is not None:
            groups.append(cur); line_map.append(cur_lines)
        def ints(line,n):
            try: return [int(x) for x in line.split()[:n]]
            except Exception: return []
        self.troops=[]
        self._inv_line_no=[]
        self._hdr_line_no=[]
        self._attr_line_no=[]
        self._prof_line_no=[]
        self._skill_line_no=[]
        for g, lm in zip(groups, line_map):
            toks=g[0].split()
            if len(toks)<2: continue
            def _ti(j, d=0):
                try: return int(toks[j]) if len(toks)>j else d
                except Exception: return d
            self.troops.append(dict(
                tid=toks[0], raw=toks[1],
                flags=_ti(3),
                raw_header=g[0],
                # troops.txt 头行: trp_id name plural flags scene reserved faction upgrade1 upgrade2
                faction=(_ti(6, -1) if (len(toks)>6 and toks[6].lstrip("-").isdigit()) else -1),
                upgrade1=_ti(7, -1),     # 升级成谁(兵种索引), 旧版查看器据此显示
                upgrade2=_ti(8, -1),
                inv=ints(g[1],128) if len(g)>1 else [],
                attrs=ints(g[2],5) if len(g)>2 else [],
                profs=ints(g[3],7) if len(g)>3 else [],
                skills=ints(g[4],6) if len(g)>4 else [],
                line5=ints(g[5],8) if len(g)>5 else [],
            ))
            # 每个兵种组: lm[0]=头行(含 flags, 用于 set_troop_flags), lm[1]=物品/装备行,
            #            lm[2]=属性行, lm[3]=熟练行, lm[4]=技能行
            self._hdr_line_no.append(lm[0] if lm else None)
            self._inv_line_no.append(lm[1] if len(lm)>1 else None)
            self._attr_line_no.append(lm[2] if len(lm)>2 else None)
            self._prof_line_no.append(lm[3] if len(lm)>3 else None)
            self._skill_line_no.append(lm[4] if len(lm)>4 else None)
        self._tpl_dirty=False
        ttrans=self._parse_trans(folder/"languages"/"cns"/"troops.csv")
        self.troop_names={t["tid"]:(ttrans.get(t["tid"]) or t["raw"]) for t in self.troops}
        # items
        itxt=self._read_text(folder/"item_kinds1.txt").splitlines()
        self.items=[l.split()[0] for l in itxt if l.strip().startswith("itm_")]
        itrans=self._parse_trans(folder/"languages"/"cns"/"item_kinds.csv")
        self.item_names={k:(itrans.get(k) or k[4:]) for k in self.items}
        # factions
        # 注意: factions.txt 除首行外, 每行都带前导数字 (如 "0 fac_commoners Commoners ..."),
        #       因此不能简单用 startswith("fac_"), 必须正则提取 fac_xxx 记号。
        ftxt=self._read_text(folder/"factions.txt").splitlines()
        self.factions=[]
        for l in ftxt:
            m=re.search(r"\b(fac_\w+)", l)
            if m: self.factions.append(m.group(1))
        ftrans=self._parse_trans(folder/"languages"/"cns"/"factions.csv")
        self.faction_names={k:(ftrans.get(k) or k[4:]) for k in self.factions}
        self.nitems=max(len(self.items)-1,0)
        self.loaded=True
        return len(self.troops)

    def troop_name(self, idx):
        if 0<=idx<len(self.troops):
            return self.troop_names.get(self.troops[idx]["tid"], self.troops[idx]["raw"])
        return f"兵种#{idx}"

    # ---- troops.txt 模板(抽象兵种)读写: 模组层修改, 影响新开局 ----
    def get_troop_inventory(self, idx):
        """该兵种 troops.txt 模板物品行 (扁平 int 列表, 物品id/修饰符 成对)。"""
        if 0<=idx<len(self.troops):
            return list(self.troops[idx].get("inv", []))
        return []

    def set_troop_inventory(self, idx, flat):
        """写回抽象兵种模板物品行。flat: 物品id/修饰符 成对的整数列表 (<=128)。
        仅替换目标兵种那一行, 其余行逐字节保留(含行尾/BOM)。"""
        if not (0<=idx<len(self.troops)): raise IndexError("troop idx")
        ln=self._inv_line_no[idx]
        if ln is None: raise ValueError("该兵种无物品行, 无法写回")
        flat=[int(x) for x in flat]
        if len(flat)>128: flat=flat[:128]
        if len(flat)%2: flat.append(0)
        while len(flat)<128: flat.append(-1); flat.append(0)   # 补齐到 64 槽, 空槽 -1 0
        ending=""
        m=re.search(r"(\r?\n)$", self._raw_lines[ln])
        if m: ending=m.group(1)
        self._raw_lines[ln]=" ".join(str(x) for x in flat)+ending
        self.troops[idx]["inv"]=list(flat)
        self._tpl_dirty=True

    def set_troop_flags(self, idx, v):
        """改写抽象兵种头行的 flags (第 4 个 token, 0x 十六进制或十进制)。
        仅替换目标兵种头行第 4 个 token, 其余字节保留(含行尾/BOM)。"""
        if not (0<=idx<len(self.troops)): raise IndexError("troop idx")
        ln=self._hdr_line_no[idx]
        if ln is None: raise ValueError("该兵种无头行, 无法写回")
        toks=self._raw_lines[ln].split()
        if len(toks)<4: raise ValueError("头行过短, 无法定位 flags")
        toks[3]=str(int(v) & 0xFFFFFFFF)
        ending=""
        m=re.search(r"(\r?\n)$", self._raw_lines[ln])
        if m: ending=m.group(1)
        self._raw_lines[ln]=" ".join(toks)+ending
        self.troops[idx]["flags"]=int(v) & 0xFFFFFFFF
        self._tpl_dirty=True

    def _write_troop_int_line(self, idx, ln, values):
        """把整数列表写回 troops.txt 中该兵种的某一行 (仅替换该行, 保留行尾/BOM)。"""
        if ln is None: raise ValueError("该兵种缺少对应行, 无法写回")
        ending=""
        m=re.search(r"(\r?\n)$", self._raw_lines[ln])
        if m: ending=m.group(1)
        self._raw_lines[ln]=" ".join(str(int(x)) for x in values)+ending
        self._tpl_dirty=True

    def set_troop_attrs(self, idx, vals):
        """写回属性行: 力/敏/智/魅/等级 共 5 个整数 (troops.txt 模板, 影响新开局)。"""
        if not (0<=idx<len(self.troops)): raise IndexError("troop idx")
        vals=[int(x) for x in vals]
        if len(vals)!=5: raise ValueError("需要 5 个整数: 力/敏/智/魅/等级")
        self._write_troop_int_line(idx, self._attr_line_no[idx], vals)
        self.troops[idx]["attrs"]=list(vals)

    def set_troop_profs(self, idx, vals):
        """写回武器熟练行: 单手/双手/长杆/弓/弩/投掷/火器 共 7 个整数。"""
        if not (0<=idx<len(self.troops)): raise IndexError("troop idx")
        vals=[int(x) for x in vals]
        if len(vals)!=7: raise ValueError("需要 7 个整数 (7 项武器熟练)")
        self._write_troop_int_line(idx, self._prof_line_no[idx], vals)
        self.troops[idx]["profs"]=list(vals)

    def set_troop_skill_level(self, idx, pos, level):
        """设置单个技能等级 (troops.txt 模板技能行, 位域 48 位: 每技能 4bit)。
        pos: 技能位置 0..47 (与 skills.txt 顺序一致); level: 0..15 (游戏内上限一般 10)。"""
        if not (0<=idx<len(self.troops)): raise IndexError("troop idx")
        if not (0<=int(pos)<48): raise ValueError("技能位置 0..47")
        if not (0<=int(level)<=15): raise ValueError("技能等级 0..15")
        words=[int(x) & 0xFFFFFFFF for x in (self.troops[idx].get("skills") or [])]
        while len(words)<6: words.append(0)
        words[int(pos)//8] = (words[int(pos)//8] & ~(0xF << ((int(pos)%8)*4)) & 0xFFFFFFFF) \
                             | ((int(level) & 0xF) << ((int(pos)%8)*4))
        self._write_troop_int_line(idx, self._skill_line_no[idx], words)
        self.troops[idx]["skills"]=words

    def set_troop_skills(self, idx, words):
        """写回整个技能行 (6 个 u32 打包位域)。words: 长度 6 的整数列表。
        等价于旧版查看器的『应用技能行』, 一次改写全部 48 个技能等级。"""
        if not (0<=idx<len(self.troops)): raise IndexError("troop idx")
        if len(words)!=6: raise ValueError("需要 6 个整数 (技能行 6 词)")
        w=[int(x) & 0xFFFFFFFF for x in words]
        self._write_troop_int_line(idx, self._skill_line_no[idx], w)
        self.troops[idx]["skills"]=w

    def save_module(self, backup=True):
        """把模板改动写回 troops.txt (抽象兵种/模组层修改, 影响新开局)。
        以字节写回, 禁用换行符翻译, 精确保留原始 CRLF/BOM。"""
        p=self.folder/"troops.txt"
        if backup:
            import shutil, datetime
            stamp=datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            bak=p.with_name(p.stem+f".{stamp}.bak")
            shutil.copy2(p, bak)
        p.write_bytes("".join(self._raw_lines).encode(self._enc))
        self._tpl_dirty=False
        return p

    def item_name(self, idx):
        if idx is None or idx<0: return "—(空)"
        if 0<=idx<len(self.items):
            return self.item_names.get(self.items[idx], self.items[idx][4:])
        return f"物品#{idx}"

# ---------------- 校验 ----------------
def _inv_ok(b,o,nitems):
    for k in range(INV_SLOTS):
        it=i32(b,o+OFF_INV+8*k)
        if it!=-1 and not (0<=it<=nitems): return False
    return True

def _equip_ok(b,o,nitems):
    for k in range(EQUIP_SLOTS):
        it=i32(b,o+OFF_EQUIP+8*k)
        if it!=-1 and not (0<=it<=nitems): return False
    return True

def _bio_ok(b,o):
    bl=u32(b,o+OFF_BIO)
    if bl>4096: return False
    if bl==0: return True
    if o+OFF_BIO+4+bl>len(b): return False
    try: s=b[o+OFF_BIO+4:o+OFF_BIO+4+bl].decode("utf-8")
    except Exception: return False
    nul=0
    for c in s:
        oc=ord(c)
        if 0<oc<0x20 and c not in "\n\t\r": return False
        if c=="\x00": nul+=1
    if nul>2: return False
    return True

def valid_header(b,o,nitems):
    if o+OFF_EQUIP+80>len(b): return False
    lv=u32(b,o+OFF_LEVEL)
    if not (1<=lv<=63): return False
    for k in range(4):
        v=u32(b,o+OFF_ATTR+4*k)
        if not (0<=v<=300): return False
    for k in range(7):
        f=f32(b,o+OFF_PROF+4*k)
        if not (0.0<=f<=700.0): return False
    if not _inv_ok(b,o,nitems): return False
    if not _equip_ok(b,o,nitems): return False
    if not _bio_ok(b,o): return False
    return True

def precheck(b,o):
    if o+OFF_LEVEL+4>len(b): return False
    if not (1<=u32(b,o+OFF_LEVEL)<=63): return False
    for k in range(4):
        if not (0<=u32(b,o+OFF_ATTR+4*k)<=300): return False
    return True

def _first_hero_after(b, lo, nitems, hi_limit=24000):
    """从 lo 开始向后找第一个通过 valid_header 的有效兵种记录起点."""
    end=min(lo+hi_limit, len(b)-OFF_EQUIP-80)
    p=lo
    while p<=end:
        if precheck(b,p) and valid_header(b,p,nitems):
            return p
        p+=1
    return None

def _walk_chain(b, start, nitems, expect):
    """从 start (已确认是第一条有效记录) 向后逐字节遍历 expect 条记录."""
    starts=[start]; o=start
    for _ in range(expect-1):
        hi=min(o+8000, len(b)-OFF_EQUIP-80)
        p=o+MIN_REC
        nxt=None
        while p<=hi:
            if precheck(b,p) and valid_header(b,p,nitems):
                nxt=p; break
            p+=1
        if nxt is None:
            return starts, False
        starts.append(nxt); o=nxt
    return starts, True

def find_array(b, nitems=NITEMS_DEFAULT, expect=794):
    """
    定位兵种(hero)数组起点并返回 expect 条记录的偏移列表.
    策略(鲁棒, 不依赖玩家具体属性):
      1) 计数锚点: 模块兵种数 = N, 在文件中找 u32==N 的计数标记,
         从其后面扫描第一个有效兵种(即玩家 / 数组首条), 验证其后能连续
         遍历出 N 条有效记录. 该标记前的任何伪记录都不会被选中.
      2) 退化: 若计数锚点失败, 退化为"最长连续有效记录链"扫描,
         但优先接受长度恰为 N 的链, 并要求起点前不存在可衔接的前驱记录.
    """
    # ---- 1) 计数锚点 ----
    pat=struct.pack("<I", expect)
    X=0
    while True:
        j=b.find(pat, X)
        if j<0: break
        start=_first_hero_after(b, j+4, nitems)
        if start is None:
            X=j+1; continue
        starts,ok=_walk_chain(b, start, nitems, expect)
        if ok and len(starts)==expect:
            return start, starts
        X=j+1
    # ---- 2) 退化: 扫描整个文件找最长连续链 ----
    best_start=None; best_len=0
    end=len(b)-OFF_EQUIP-80
    o=0
    while o<=end:
        if precheck(b,o) and valid_header(b,o,nitems):
            starts,ok=_walk_chain(b,o,nitems,expect)
            ln=len(starts) if ok else 0
            if ln>=expect:
                return starts[0], starts[:expect]
            if ln>best_len:
                best_len=ln; best_start=starts[0]
            o=starts[-1]+1 if starts else o+1
            continue
        o+=1
    if best_start is not None:
        return best_start, _walk_chain(b,best_start,nitems,expect)[0]
    return None, []

# ================= 部队 (parties) 解析 =================
# 源自已验证的 mb_troop_editor.py: 模块部队以 长度前缀的 'p_xxx' 字符串定位,
# 动态部队(领主/商队/强盗)以 {u32=1, u32 b, u32 c, u32 id_len, 'pt_xxx'...} 定位.
# 记录体布局 (相对 body=id_len 字段位置):
#   id_len(u32), id(id_len字节),
#   name_len(u32), name(name_len字节),
#   18 x u32 固定字段 (下标 17 = num_stacks),
#   num_stacks x (troop u32, num u32, f3 u32, f4 u32)  = 每条 16 字节
_PARTY_RE = re.compile(rb"p_[A-Za-z0-9_]{2,40}")

def scan_parties(data):
    N = len(data)
    records = []
    # --- 模块部队 ---
    for m in _PARTY_RE.finditer(data):
        s, e = m.start(), m.end()
        if s >= 4 and u32(data, s - 4) == e - s:
            records.append(dict(kind="module", start=s - 4, id_off=s))
    last_mod_id = max((r["id_off"] for r in records), default=0)
    # --- 动态部队 ---
    pos = last_mod_id
    while pos < N - 24:
        i = data.find(b"pt_", pos)
        if i < 0:
            break
        L = u32(data, i - 4)
        if 5 <= L <= 40 and i >= 16 and u32(data, i - 16) == 1:
            sid = data[i:i + L]
            if all((97 <= ch <= 122 or 65 <= ch <= 90 or ch == 95 or 48 <= ch <= 57)
                   for ch in sid):
                records.append(dict(kind="dynamic", start=i - 16, id_off=i,
                                    index_b=u32(data, i - 12), field_c=u32(data, i - 8)))
                pos = i + L
                continue
        pos = i + 1
    records.sort(key=lambda r: r["start"])
    for r in records:
        r["body"] = r["id_off"] - 4
    return records

def parse_party(data, rec):
    """解析单条部队记录, 返回 (info, stacks, end_off)."""
    off = rec["body"]
    L = u32(data, off)
    pid = data[off + 4: off + 4 + L].decode("utf-8", "replace")
    off += 4 + L
    L2 = u32(data, off)
    name = data[off + 4: off + 4 + L2].decode("utf-8", "replace")
    off += 4 + L2
    fields_off = off
    fields = [u32(data, off + 4 * k) for k in range(18)]
    num_stacks = fields[17]
    off += 4 * 18
    stacks = []
    for k in range(num_stacks):
        troop = i32(data, off)
        num = u32(data, off + 4)
        f3 = u32(data, off + 8)
        f4 = u32(data, off + 12)
        stacks.append(dict(offset=off, troop=troop, num=num, f3=f3, f4=f4))
        off += 16
    info = dict(id=pid, name=name, flags=fields[0], index_b=rec.get("index_b"),
                kind=rec["kind"], num_stacks=num_stacks, fields=fields,
                fields_off=fields_off, body=rec["body"], start=rec["start"])
    return info, stacks, off

# ---------------- 存档模型 ----------------
class SaveDoc:
    def __init__(self):
        self.data=bytearray()
        self.path=None
        self.mod=ModuleData()
        self.starts=[]        # 794 个记录起点
        self.nitems=NITEMS_DEFAULT
        self.dirty=False
        self._kind_cache={}   # tail_kind 记忆化(依赖 hero_offs/parties, 载入时清空)

    # 模块目录探测: 存档名通常就是模块名 (WD - Minuet ...)
    def guess_module_dir(self):
        if self.path:
            name=self.path.parent.name
            for base in (
                r"E:\Program Files\Game\Mount&Blade\Modules",
                r"D:\Program Files\Game\Mount&Blade\Modules",
                r"C:\Program Files\Game\Mount&Blade\Modules",
            ):
                cand=Path(base)/name
                if (cand/"troops.txt").exists():
                    return cand
        return None

    def load_save(self, path):
        self.path=Path(path)
        self.data=bytearray(self.path.read_bytes())
        self.starts=[]
        self.parties=[]
        self.dirty=False
        if self.mod.loaded:
            self._walk()
            self.load_parties()

    def load_module(self, folder):
        n=self.mod.load(folder)
        self.nitems=self.mod.nitems
        if self.data:
            self._walk()
            self.load_parties()

    def _walk(self):
        expect = len(self.mod.troops) if self.mod.troops else 794
        self.rec0,self.starts=find_array(self.data, self.nitems, expect=expect)
        if len(self.starts)!=expect:
            print(f"[warn] 兵种记录数={len(self.starts)} (期望 {expect})")
        self.hero_offs={}      # idx -> 英雄完整记录偏移 (同伴/领主)
        self._kind_cache={}    # 重置 tail_kind 记忆化
        self._scan_heroes()

    # ---- 英雄完整记录区 ----
    def _scan_heroes(self):
        """扫描『英雄完整记录』并建立 idx -> 偏移 映射。

        背景(经与 troops.txt 交叉验证确认): troops 数组里同伴/领主的条目常是
        残缺的(flags/attrs 为 0), 其完整数据(属性/熟练/技能/装备/简介)存在
        后面的『英雄完整记录』中 —— 已招募同伴的完整记录与残缺条目交替排列;
        领主的完整记录集中在标记 202(bio='kingdom hero', lvl=38) 之后, 且 bio
        以该领主中文名开头(如 '爱 德 华 国 王 是 <fac=1>...')。

        识别特征: flags 含 tf_hero 且 attrs 非零 且通过记录校验。
        映射手段: ① bio 名字与 troops.csv 中文名精确匹配(领主/标记);
                  ② 标记 202 之前的条目, 按文件序 + '属性>=模板(逐分量,加点只增)'
                     双指针匹配已招募同伴;
                  ③ 标记之后仍未匹配的条目, 按 '属性>=模板' 贪心匹配未确认领主。"""
        b=self.data
        n=len(self.mod.troops)
        if n==0 or not self.starts: return
        start = self.starts[182] if len(self.starts)>182 else self.starts[0]
        end = len(b)-OFF_EQUIP-80
        seq=[]
        o=start
        while o<end:
            if precheck(b,o) and valid_header(b,o,self.nitems):
                fl=u32(b,o+OFF_FLAGS)
                at=[u32(b,o+OFF_ATTR+4*k) for k in range(4)]
                # 伪命中过滤: 伪记录由真实记录内部的 -1 填充区/杂项字节构成,
                # 其 flags 常为 0xffffffff 且熟练度区全 0; 真实英雄至少一项熟练 >0。
                if (fl & TF_HERO) and fl!=0xFFFFFFFF and any(at) \
                   and any(f32(b,o+OFF_PROF+4*k)>0.5 for k in range(6)):
                    seq.append(o)
                    o+=MIN_REC        # 英雄记录均 > MIN_REC: 跳过本条并抑制其内部伪命中
                    continue
            o+=1

        # 名字 -> idx (领主 203+)
        name2idx={}
        for idx in range(203,n):
            nm=str(self.mod.troop_names.get(self.mod.troops[idx]["tid"],""))
            key="".join(nm.split())
            if key: name2idx.setdefault(key, idx)

        used=set(); mark_pos=None
        # ① bio 名字匹配
        for pos,o in enumerate(seq):
            txt=self._bio_text(o)
            if not txt: continue
            key="".join(txt.split("是")[0].split())
            if not key: continue
            if key.lower().startswith("kingdomhero"):
                if mark_pos is None:
                    self.hero_offs[202]=o; used.add(pos); mark_pos=pos
                continue
            idx=name2idx.get(key)
            if idx is not None and idx not in self.hero_offs:
                self.hero_offs[idx]=o; used.add(pos)
                continue
            # 容错: bio 名与 troops.csv 翻译名偶有 1~2 字差异(如 露/伦),
            # 用'最长公共前缀>=5'再匹配一次, 找回因字差被漏掉的真实领主。
            if idx is None and len(key)>=5:
                best=None; bestL=0
                for nm2,i2 in name2idx.items():
                    if i2 in self.hero_offs: continue
                    L=0
                    for a,c in zip(key,nm2):
                        if a==c: L+=1
                        else: break
                    if L>bestL:
                        bestL=L; best=i2
                if best is not None and bestL>=min(5,len(nm2)):
                    self.hero_offs[best]=o; used.add(pos)

        # 标记 202 兜底: 模板 level + flags
        if mark_pos is None and n>202:
            t=self.mod.troops[202].get("attrs",[])
            tl=t[4] if len(t)>4 else 38
            tf=self.mod.troops[202].get("flags",0)
            for pos,o in enumerate(seq):
                if pos in used: continue
                if u32(b,o+OFF_LEVEL)==tl and u32(b,o+OFF_FLAGS)==tf:
                    self.hero_offs[202]=o; used.add(pos); mark_pos=pos
                    break

        # ② 标记之前: 双指针顺序匹配同伴。
        #    npc 区在存档流中是 残缺条目+完整记录 交替(每个已招募同伴两条),
        #    完整记录按 idx 升序出现; 属性>=模板(逐分量,加点只增不减)验证。
        #    已验证: npc1 [18,18,20,8]->[18,18,21,8](int+1), npc4 [10,10,13,10]->[10,10,19,10](int+6)...
        if mark_pos is not None:
            j=182
            for pos in range(mark_pos):
                if pos in used: continue
                o=seq[pos]
                sa=[u32(b,o+OFF_ATTR+4*k) for k in range(4)]
                while j<202:
                    ta=self.mod.troops[j].get("attrs",[])[:4]
                    if len(ta)==4 and any(sa) and all(sa[k]>=ta[k] for k in range(4)):
                        self.hero_offs[j]=o; used.add(pos); j+=1
                        break
                    j+=1
                if j>=202: break

        # ③ 强特征匹配剩余英雄(小领主):
        #    熟练度(7项f32)与等级均与模板完全一致 -> 几乎不可能误配。
        #    匹配不上的(已升级/数据缺失)一律不映射, 由 is_reliable() 保护。
        pend=[idx for idx in range(182,n) if idx not in self.hero_offs and idx!=202]
        begin = mark_pos+1 if mark_pos is not None else 0
        for pos in range(begin, len(seq)):
            if not pend: break
            if pos in used: continue
            o=seq[pos]
            sl=u32(b,o+OFF_LEVEL)
            sp=[round(f32(b,o+OFF_PROF+4*k)) for k in range(7)]
            for idx in list(pend):
                t=self.mod.troops[idx]
                tp=t.get("profs",[])
                ta=t.get("attrs",[])
                tl=ta[4] if len(ta)>4 else None
                if not tp or tl is None: continue
                if sl==tl and sp==[int(x) for x in tp[:7]]:
                    self.hero_offs[idx]=o; used.add(pos); pend.remove(idx)
                    break

        # ④ 简介倒查兜底: 简介正文必位于记录起点 +0x3E4 (=OFF_BIO+4), 因此可由
        #    "姓名在文件中出现的字节位置"直接倒推记录起点, 再用结构校验 + 简介
        #    前缀 + tf_hero 位三者确认。
        #    此法已在已知链接的领主上验证 100% 一致(#203/#204/#209/#227/#229/#246
        #    均能回溯到既有 hero_offs), 并能找回前三道扫描遗漏的领主
        #    (#248/#253/#258/#260/#278/#331 —— 其阵营值与 troops.txt 完全吻合)。
        #    注意领主简介是"逐字空格"格式, 故检索串也要按单字插入空格。
        pend=[idx for idx in range(182,n) if idx not in self.hero_offs and idx!=202]
        for idx in pend:
            nm="".join(self.mod.troop_name(idx).split())
            if len(nm)<2: continue
            pat=" ".join(nm).encode("utf-8")
            pos=b.find(pat)
            while pos>=0:
                st=pos-(OFF_BIO+4)
                if st>0 and st+MIN_REC<=len(b) \
                   and valid_header(b,st,self.nitems) \
                   and (u32(b,st+OFF_FLAGS) & TF_HERO) \
                   and self._bio_head_key(st)==nm:
                    self.hero_offs[idx]=st
                    break
                pos=b.find(pat,pos+1)

        # ⑤ 姓名兜底(同名整组跳过): 剩余英雄按"简介开头姓名 == 兵种名"在已扫描
        #    候选中匹配; troops.txt 有 52 组重名(如 教练x5), 命中即整组移除以免瞎猜。
        pend=[idx for idx in range(182,n) if idx not in self.hero_offs and idx!=202]
        if pend:
            name2idx={}
            for idx in pend:
                nm="".join(self.mod.troop_name(idx).split())
                if len(nm)>=2:
                    name2idx.setdefault(nm,[]).append(idx)
            for pos in range(len(seq)):
                if not name2idx: break
                if pos in used: continue
                key=self._bio_head_key(seq[pos])
                if not key or key not in name2idx: continue
                self.hero_offs[name2idx.pop(key)[0]]=seq[pos]
                used.add(pos)

    def _is_complete(self, o, idx):
        """该偏移处是否是一条『完整真实』记录, 可作为该兵种的权威数据源。

        判定: 通过结构校验 + 简介(名字)非空; 若该兵种模板为英雄, 记录还须
        带 tf_hero 标志 —— 否则只是『简介尚在、但属性/标志被清零』的残本
        (英雄区扫描时主数组条目常被清零成这种状态, 如 #208 高贵公爵 attrs
        全0 而 level=19)。仅含简介但不带 tf_hero 的英雄残本不可作为权威记录。

        用途: _off 据此选择权威记录; is_reliable 据此区分真哨兵与真实领主。"""
        if o is None: return False
        if not valid_header(self.data, o, self.nitems): return False
        if u32(self.data, o+OFF_BIO) == 0: return False
        if self.mod.troops[idx].get("flags", 0) & TF_HERO:
            return bool(u32(self.data, o+OFF_FLAGS) & TF_HERO)
        return True

    def _is_sentinel_name(self, idx):
        """troops.txt 中的数组边界哨兵 (trp_*_end / *_begin / *_last / *_marker)。
        注意: 本模组 trp_princes_end 其实是真实领主(公爵莫纳, 主数组槽含真实简介),
        故仅凭名字不能禁编辑 —— 是否真哨兵还需结合主数组槽是否空简介判断(见 is_reliable)。"""
        if 0 <= idx < len(self.mod.troops):
            return bool(re.search(r'_(end|begin|last|marker|start)$', self.mod.troops[idx]["tid"], re.I))
        return False

    def _bio_text(self,o):
        bl=u32(self.data,o+OFF_BIO)
        if bl==0: return ""
        if not (0<bl<=4096): return None
        try: return self.data[o+OFF_BIO+4:o+OFF_BIO+4+bl].decode("utf-8")
        except Exception: return None

    def _bio_head_key(self,o):
        txt=self._bio_text(o)
        if not txt: return None
        key="".join(txt.split("是")[0].split())
        return key or None

    # ---- 取值 ----
    def _off(self, idx):
        so = self.starts[idx] if idx < len(self.starts) else None
        ho = self.hero_offs.get(idx)
        hero = (bool(self.mod.troops[idx].get("flags", 0) & TF_HERO)
                if idx < len(self.mod.troops) else False)
        if hero:
            # 【2026-09-19 重要修正】英雄一律以英雄区记录 hero_offs[i] 为准。
            #
            # 此前曾让"主数组槽若是完整真实记录(非空简介 + tf_hero 位)"优先 —— 这是错的:
            #   * starts[] 实际只有**前 182 条**是兵种实例数组(与 hero_offs 零重合);
            #     182 之后与英雄区记录大量重合(400-600 段 185/200 命中)且存在渐进错位:
            #     starts[222]→#202, starts[224]→#203, starts[248]→#219, starts[299]→#257 …
            #   * 实测主数组中"英雄槽且带简介"的 50 条里, **身份正确 0 条** —— 存的全是
            #     别人的记录; 据此曾把 44 名领主误判为"已跳槽"。
            #   * 英雄区记录已验证可靠: 简介自带 <fac=N> 标签, 与 0x05C 阵营字段
            #     93/93 = 100% 一致; 与 troops.txt 模板阵营 91/93 相符。
            # idx<182 的英雄(同伴/城镇 NPC)主数组前段确为实例数组, 无英雄区记录时用它。
            return ho if ho is not None else so
        return so

    def get_attrs(self, idx):
        o=self._off(idx)
        return [u32(self.data,o+OFF_ATTR+4*k) for k in range(4)]

    def get_profs(self, idx):
        o=self._off(idx)
        return [round(f32(self.data,o+OFF_PROF+4*k)) for k in range(7)]

    def get_skills(self, idx):
        o=self._off(idx)
        words=[u32(self.data,o+OFF_SKILL+4*k) for k in range(6)]
        return skills_decode(words)

    def get_level(self, idx): return u32(self.data,self._off(idx)+OFF_LEVEL)
    def get_flags(self, idx): return u32(self.data,self._off(idx)+OFF_FLAGS)
    def is_hero(self, idx):   return bool(self.get_flags(idx) & TF_HERO)

    # ---- 只读视图: 不可靠兵种(存档无完整记录)回退显示 troops.txt 模板值 ----
    def _tpl(self, idx):
        return self.mod.troops[idx] if idx < len(self.mod.troops) else {}

    def view_basic(self, idx):
        """(attrs4, profs7int, level, xp, pts, flags); 不可靠时为模板值。"""
        if self.is_reliable(idx):
            return (self.get_attrs(idx), self.get_profs(idx), self.get_level(idx),
                    self.get_xp(idx), self.get_pts(idx), self.get_flags(idx))
        t=self._tpl(idx); a=t.get("attrs",[])
        return ((list(a[:4])+[0]*4)[:4],
                [int(x) for x in (list(t.get("profs",[]))+[0]*7)[:7]],
                (a[4] if len(a)>4 else 0), 0, 0, t.get("flags",0))

    def view_skills(self, idx):
        if self.is_reliable(idx): return self.get_skills(idx)
        return skills_decode((self._tpl(idx).get("skills",[])+[0]*6)[:6])

    def view_inventory(self, idx):
        """64 槽; 不可靠时用模板物品行( troops.txt 的 64 对)。"""
        if self.is_reliable(idx): return self.get_inventory(idx)
        inv=self._tpl(idx).get("inv",[])
        out=[(-1,0)]*INV_SLOTS
        for k in range(min(INV_SLOTS, len(inv)//2)):
            iid=inv[2*k]
            if iid!=-1: out[k]=(iid, inv[2*k+1])
        return out

    def view_level(self, idx):
        if self.is_reliable(idx): return self.get_level(idx)
        a=self._tpl(idx).get("attrs",[])
        return a[4] if len(a)>4 else 0

    def is_reliable(self, idx):
        """该兵种的定位是否可靠(可安全读写)。

        判定层次:
          idx<182
              -> 可靠 (已与 troops.txt 交叉验证)
          模板非英雄 (>=182)
              -> 主数组即唯一且完整的实例化副本, 结构有效即可靠
          模板英雄 (tf_hero)
              -> 【2026-09-19 修正】仅在 hero_offs 中(已映射到英雄区记录)才可靠可编辑。
                 读取路径见 _off(): 英雄一律优先英雄区记录; 仅 idx<182 的英雄(同伴/
                 城镇 NPC)在无英雄区记录时才用主数组前段(该段确为兵种实例数组)。
                 主数组对 >=182 的英雄**不按兵种索引对齐**, 已禁止用于英雄。
              * 真哨兵特例: tid 形如 *_end/_begin/_last/_marker 且主数组槽
                又为空简介者(如 trp_heroes_end / trp_merchants_end /
                trp_kingdom_heroes_including_player_begin), 它们是 troops.txt
                的数组边界标记, 不是真实可改兵种, 强制不可编辑。
                注: 同模组 trp_princes_end 实为真实领主(公爵莫纳, 主数组槽
                含真实简介), 故仅当『名字像哨兵 且 主数组空简介』才视为真哨兵,
                避免误伤真实领主。"""
        if idx < 182:
            return True
        if self.mod.troops[idx].get("flags", 0) & TF_HERO:
            so = self.starts[idx] if idx < len(self.starts) else None
            # 真哨兵(名字像哨兵 且 主数组槽非完整真实记录)强制不可编辑
            if self._is_sentinel_name(idx) and not self._is_complete(so, idx):
                return False
            # 【2026-09-19 修正】英雄必须以英雄区记录为唯一可写依据。
            #
            # 不再接受"英雄区链接失败但主数组槽自身完整"这条路径: 主数组对 >=182 的
            # 英雄并不按兵种索引对齐(实测有简介的英雄槽身份正确 0 条, 存的是别人的
            # 记录), 据此开放的编辑会写到错误槽位, 并曾据以误判出 44 名"跳槽"领主。
            # 英雄区链接失败者(如 #248/#253/#258/#260/#278/#331/#337)说明存档中确实
            # 没有该领主的实例数据(未激活), 属占位, 不可编辑。
            return idx in self.hero_offs
        # 非英雄 >=182: 主数组即唯一且完整的实例化副本
        if idx < len(self.starts):
            return valid_header(self.data, self.starts[idx], self.nitems)
        return False

    def _rec_end(self, idx):
        """返回 idx 对应记录的结束偏移(不含)。

        兵种记录按"所属数组"取下一个同数组记录作为边界:
          * 英雄(idx 在 hero_offs 中): 取 hero_offs 中 > 起点的最小值;
          * 普通兵种(主数组): 取 starts 中 > 起点的最小值。
        旧实现跨数组取 min(starts, hero_offs), 但两数组在文件里交错(153 处英雄记录
        之间夹着普通兵种记录), 会把普通兵种起点误当英雄尾部边界(或反之),
        导致英雄槽区被截断 / 普通兵种被误判有内联槽。"""
        o = self._off(idx)
        if idx in self.hero_offs:
            hpos = sorted(self.hero_offs.values())
            nxt = next((h for h in hpos if h > o), None)
        else:
            nxt = next((s for s in self.starts if s > o), None)
        return nxt if nxt is not None else len(self.data)

    def get_tail_u32(self, idx, limit=1024):
        """读取记录尾部(简介之后)的 u32 列表 —— 这里就是兵种的『槽』(slots) 区。

        2026-09-19 修正: 1.011 兵种**确有**槽数据结构(此前"1.011 无 slot"的结论已被
        脚本交叉验证推翻)。非活跃领主(王子/小姐/复国者/模板领主)的槽以【密集 u32 数组】
        内联存储: 槽号 = 简介尾之后第 k 个 u32, 值 = 该 u32; 0 / 0xFFFFFFFF = 未设置。
        已验证: 无部队的王子 slot10(led_party)=-1; slot128(性格)=1..7 逐领主不同。
        但复国者等『blob 尾部』英雄的 slot128 落在压缩块里, 不可读作性格(见 tail_kind)。

        活跃领主分两种: 内联含 254 槽数组者(lord254) 性格在槽 102; 内联为 blob 块者(blob_active)
        其性格写在 party 实体数据, 内联不可读(见 tail_kind / get_personality)。"""
        o=self._off(idx)
        if o is None: return []
        nxt=self._rec_end(idx)
        bl=u32(self.data,o+OFF_BIO)
        t0=o+OFF_BIO+4+bl
        if t0>=nxt: return []
        return [u32(self.data,k) for k in range(t0, min(nxt, t0+limit), 4)]

    def get_tail_nonzero(self, idx):
        """尾部槽中非零且非 -1 的 (槽号, 值) 列表, 便于观察槽分布。"""
        return [(j,v) for j,v in enumerate(self.get_tail_u32(idx)) if v not in (0, 0xFFFFFFFF)]

    # ================= 兵种槽 (troop slots) =================
    # 1.011 兵种确有槽: 由模块脚本交叉验证(troop_set_slot/troop_get_slot 大量使用,
    # game_start 初始化 slots 0..151; 同伴性格槽 71-77/118-123; led_party = slot10;
    # create_kingdom_hero_party 写 troop_set_slot(troop,10,party))。
    #
    # 内联密集数组(非活跃领主): 槽号 = 简介尾之后第 k 个 u32, 值 = 该 u32;
    #   0 / 0xFFFFFFFF = 未设置。活跃领主的实时槽在存档另一区段(见 has_inline_slots / tail_kind)。
    SLOT_PERSONALITY = 128      # 非活跃英雄(dense)的性格逻辑槽号; 活跃领主(lord254)在槽 102。
    SLOT_COUNT_LORD = 254       # 活跃领主内联 254 槽数组固定长度(位于记录尾, 起点 nxt-1016)。
    # tail_kind 取值
    KIND_LORD254 = 'lord254'    # 活跃领主 + 内联 254 槽数组(性格=槽102)
    KIND_DENSE    = 'dense'     # 非活跃英雄标准密集槽数组(性格=槽128)
    KIND_BLOB     = 'blob'      # 外观/标记压缩块(不可读槽; 复国者=君主 / 活跃领主性格在部队)
    KIND_NONE     = 'none'      # 无法识别
    PERSONALITY_NAMES = {
        1: "刚正不阿", 2: "冷酷无情", 3: "魅力十足", 4: "狡黠善谋",
        5: "好勇斗狠", 6: "仁厚宽和", 7: "野心勃勃",
    }
    PERSONALITY_MONARCH = "君主"  # 复国者按游戏规则恒为君主; 活跃 blob 领主性格在部队数据(内联不可读)

    def _is_blob_region(self, s, e):
        """该区间是否像『外观压缩块』(一长串 2^28 量级巨值)而非槽数组。

        真实槽数组即使含个别大值, 最长连续巨值段仅 1..10; 外观压缩块为 20..40。
        取 15 作分界。"""
        big = []
        for k in range(s, min(e, s + 8192), 4):
            if u32(self.data, k) >= 0x10000000:
                big.append((k - s) // 4)
        if not big:
            return False
        run = mx = 1
        for a, b in zip(big, big[1:]):
            run = run + 1 if b == a + 1 else 1
            if run > mx:
                mx = run
        return mx >= 15

    def tail_kind(self, idx):
        """分类英雄记录尾部的『槽数据结构』类型 —— 正确解析性格/职业的前提。

        依赖: 是否率领部队(is_active_lord 的判据 = find_lord_party) 与尾部起始字节。

        'lord254' : 活跃领主且内联含固定 254 槽密集数组(起点 nxt-1016), 且起点处
                    slot2==2(occupation=王国英雄) 且 slot10 为合法部队号。
                    性格=逻辑槽 102, 职业=槽 2, led_party=槽 10。
        'dense'   : 非活跃英雄(同伴/王子/小姐)的标准密集槽数组(起点 t0, 起手 [-1,-1,0,-1,-1,0...])。
                    性格=逻辑槽 128。
        'blob'    : 尾部是『外观/标记』压缩块(起手 [-1,32,0,0,32,76,...] 的 [32,0,0] 重复结构),
                    **不是**可读槽数组。含:
                      - 复国者(308..312 等模板英雄): 按游戏规则性格恒为 君主;
                      - 少数『有部队但其实时数据写在 party 系统而非内联』的活跃领主(204/205/206/307):
                        性格在 party 实体数据, 内联不可读。
        'none'    : 无法识别(无完整记录 / 尾部过短)。

        === 实证(2026-09-20, 本存档 sg00_current.sav) ===
        lord254=98, dense=397, blob_active=4, blob_inactive=11, 无歧义。
        旧方案(按 active/non-active 二分)对 blob 类英雄失效: 误把压缩块当槽数组,
        导致复国者 #312 性格被读成 6(压缩块噪声)而非 君主(0)。"""
        if idx in self._kind_cache:
            return self._kind_cache[idx]
        o = self._off(idx)
        if o is None:
            self._kind_cache[idx] = self.KIND_NONE; return self.KIND_NONE
        nxt = self._rec_end(idx)
        bl = u32(self.data, o + OFF_BIO)
        t0 = o + OFF_BIO + 4 + bl
        if t0 >= nxt:
            self._kind_cache[idx] = self.KIND_NONE; return self.KIND_NONE
        slot1 = u32(self.data, t0 + 4)
        is_act = (idx in self.hero_offs) and bool(self.find_lord_party(idx))
        if is_act:
            A = nxt - self.SLOT_COUNT_LORD * 4
            if A >= t0:
                s2 = u32(self.data, A + 2*4)
                s10 = u32(self.data, A + 10*4)
                if s2 == 2 and (0 <= s10 < self.party_count()):
                    self._kind_cache[idx] = self.KIND_LORD254; return self.KIND_LORD254
            self._kind_cache[idx] = self.KIND_BLOB; return self.KIND_BLOB
        # 非活跃
        if slot1 == 0xFFFFFFFF:
            self._kind_cache[idx] = self.KIND_DENSE; return self.KIND_DENSE
        if slot1 == 32:
            self._kind_cache[idx] = self.KIND_BLOB; return self.KIND_BLOB
        self._kind_cache[idx] = self.KIND_NONE; return self.KIND_NONE

    def _slot_region(self, idx):
        """返回 (start, nxt) 或 None。start=可读密集槽数组起点, nxt=记录结束(不含)。

        按 tail_kind 选起点:
          - lord254 : 记录尾固定 254 槽数组, start = nxt - 254*4。
          - dense   : 简介尾 t0 直接是密集槽数组, start = t0。
          - blob/none : 内联尾部不是可读槽数组 → 返回 None(get_troop_slot 一律 0,
                        不腐蚀数据; 这些英雄的"实时"数据在 party 系统, 不在内联)。"""
        o = self._off(idx)
        if o is None: return None
        nxt = self._rec_end(idx)
        kind = self.tail_kind(idx)
        if kind == self.KIND_LORD254:
            start = nxt - self.SLOT_COUNT_LORD * 4
            if start < 0: return None
            return start, nxt
        if kind == self.KIND_DENSE:
            bl = u32(self.data, o + OFF_BIO)
            t0 = o + OFF_BIO + 4 + bl
            if t0 >= nxt: return None
            return t0, nxt
        return None

    def has_inline_slots(self, idx):
        """该兵种的内联尾部是否足够长以容纳密集槽数组(逻辑槽 128, 即性格)。

        仅英雄区记录(hero_offs)才可能含内联槽; 普通兵种尾部不是槽数组, 一律 False。
        起点由 _slot_region 按类别给出(非活跃=t0, 活跃=记录尾 254 槽), 故长度须用
        实际返回的起点计算, 不能用 (nxt-t0)(活跃领主会算错)。"""
        if idx not in self.hero_offs:
            return False
        r=self._slot_region(idx)
        if not r: return False
        start,nxt=r
        return (nxt-start)//4 >= (self.SLOT_PERSONALITY+1)

    def get_troop_slot(self, idx, slot):
        """读取兵种槽值(内联密集数组; slot = 简介尾之后的 u32 序号)。
        返回 int; 槽不存在/超出内联范围返回 0 (活跃领主实时槽在别处, 此处只能读到内联部分)。"""
        r=self._slot_region(idx)
        if not r: return 0
        t0,nxt=r
        off=t0+slot*4
        if off+4>nxt: return 0
        return u32(self.data, off)

    def set_troop_slot(self, idx, slot, value):
        """就地写入兵种槽值(内联密集数组, 长度不变, 安全)。
        活跃领主(实时槽在存档另一区段)会拒绝 —— 该区段尚未解码, 盲写有腐蚀风险。"""
        if not self.is_reliable(idx):
            raise ValueError("该兵种在存档中无完整记录, 不可改槽")
        if idx not in self.hero_offs:
            raise ValueError("该兵种非英雄区记录, 没有内联槽区, 不可改槽(普通兵种尾部非槽数组)")
        if not self.has_inline_slots(idx):
            raise ValueError("该英雄内联尾部是外观压缩数据而非槽数组(活跃领主), 写入会损坏外观, "
                             "其真实槽在存档另一未解码区段")
        # 防御: 活跃领主的 囚禁(槽8)/率部队(槽10) 槽位语义尚未验证(实测取值与脚本预期不符),
        # 盲写会损坏存档。occupation(槽2) 已用锚点交叉验证, 可写。
        if self.is_active_lord(idx) and slot in (self.SLOT_PRISONER, self.SLOT_LEADED_PARTY):
            raise ValueError("活跃领主的『囚禁(槽8)』/『率部队(槽10)』槽位语义尚未验证, "
                             "盲写会损坏存档(occupation 槽2 已验证可写)")
        # 防御: 活跃领主槽区仅在通过可信锚点(occupation==2)时才允许写入, 否则 X 偏移算错,
        # 写入会腐蚀相邻字段(约 4 个锚点不符的领主即此情况)。
        if self.is_active_lord(idx) and not self.slot_region_trusted(idx):
            raise ValueError("该活跃领主槽区定位未通过可信锚点(occupation!=2), "
                             "盲写会损坏存档; 需用第二锚点(如声望)重新定位后再写")
        r=self._slot_region(idx)
        if not r: raise ValueError("无槽区可读")
        t0,nxt=r
        off=t0+slot*4
        if off+4>nxt:
            raise ValueError("槽 %d 超出内联槽区范围(该兵种为活跃领主, 实时槽在存档另一区段, 暂未解码)" % slot)
        w32(self.data, off, int(value) & 0xFFFFFFFF)
        self.dirty=True
        return int(value)

    def _personality_slot(self, idx):
        """性格逻辑槽号随 tail_kind 不同(实证, 非假设):
          - lord254 (活跃领主 + 内联 254 槽数组): 逻辑槽 102
          - dense   (非活跃英雄的标准密集数组):    逻辑槽 128
          - blob / none: 内联尾部无可读性格槽 → 返回 None
              * blob_inactive(复国者等): 性格恒为 君主(0), 由游戏规则决定, 不在内联存储;
              * blob_active (活跃但实时数据在 party 区段): 内联无性格, 真实值在 party 系统, 本次未解码。
        旧版硬编码 128 → 活跃领主全读成 0(本次 bug 根因); 误读 blob 的 [32,0,0] 压缩块
        → 复国者 #312 曾被错读成 6(温厚)。"""
        kind = self.tail_kind(idx)
        if kind == self.KIND_LORD254:
            return 102
        if kind == self.KIND_DENSE:
            return 128
        return None

    def get_personality(self, idx):
        """性格 / reputation_type。返回 0 表示未设置 / 内联不可读。

        槽号随类别不同(见 _personality_slot): 活跃领主=102, 非活跃英雄=128。
        blob 类(复国者=君主 0 / 活跃领主数据在 party 区段)内联无性格 → 返回 0。"""
        s = self._personality_slot(idx)
        if s is None:
            return 0
        return self.get_troop_slot(idx, s)

    def set_personality(self, idx, val):
        """设置性格。val ∈ 0..15。槽号随类别路由到 102(lord254) 或 128(dense)。

        活跃领主(槽102)写入需通过 set_troop_slot 的可信锚点闸(occupation==2), 否则拒绝;
        非活跃英雄(槽128)按内联槽区正常写入。
        blob 类(复国者性格恒君主 / 活跃领主数据在 party 区段)内联无性格槽 → 拒绝写入。"""
        s = self._personality_slot(idx)
        if s is None:
            raise ValueError("该英雄内联尾部无可读性格槽(blob: 复国者性格恒为君主, "
                             "或活跃领主数据在 party 区段暂未解码), 不可写")
        return self.set_troop_slot(idx, s, val)

    def personality_name(self, idx):
        v=self.get_personality(idx)
        if v==0:
            kind=self.tail_kind(idx)
            if kind==self.KIND_BLOB and self.is_active_lord(idx):
                # 活跃领主但内联尾部是 [32,0,0] 压缩块: 性格不在内联存储,
                # 真实值在 party 区段(存档另一区段), 本次未解码 —— 切勿误标为"君主"。
                return "（活跃·性格在 party 区段，暂未解码）"
            if kind==self.KIND_BLOB:
                # 复国者/占位英雄: 性格 = 君主(游戏规则, 恒 0)。
                return "（君主 0）"
            return "（未设置 0）"
        return self.PERSONALITY_NAMES.get(v, "（未知 %d, 含义待开档确认）" % v)
    # ================= 英雄活跃状态 (occupation / 囚禁) =================
    # 槽号来自本模组脚本实证(simple_triggers 重生触发器 + game_start):
    #   slot 2  = slot_troop_occupation   (状态枚举, 见 OCCUPATION_NAMES)
    #   slot 8  = slot_troop_prisoner_of_party (-1=自由; >=0 = 关押他的部队号)
    #   slot 10 = slot_troop_leaded_party (所率部队, <1 表示无部队)
    # 枚举值由 scripts.txt 反查得出(本 Mod 用旧版编号), 与战团 slto_* 对应:
    #   0 不活跃 / 2 王国英雄(领主活跃) / 3 玩家同伴(在主角队伍) /
    #   4 宫廷贵妇 / 8 强盗骑士(待确认) / 11 已退休
    SLOT_OCCUPATION      = 2
    SLOT_PRISONER        = 8
    SLOT_LEADED_PARTY    = 10
    OCCUPATION_NAMES = {
        0:  "不活跃 / 未雇佣 (slto_inactive)",
        2:  "王国英雄 · 作为领主活跃 (slto_kingdom_hero)",
        3:  "玩家同伴 · 在主角队伍 (slto_player_companion)",
        4:  "宫廷贵妇 (slto_kingdom_lady)",
        5:  "王室总管 (slto_kingdom_seneschal)",
        6:  "强盗骑士 (slto_robber_knight, 旧编号)",
        8:  "强盗骑士 (slto_robber_knight, 新编号, 待确认)",
        9:  "未激活复国者 (slto_inactive_pretender)",
        11: "已退休 (slto_retirement)",
    }

    def get_occupation(self, idx):
        """英雄当前活跃状态(槽2)。0 表示未设置/不可读。

        4 个『活跃但内联数据在 party 区段』的领主(blob_active)其内联槽2不可读,
        但 find_lord_party 确认其率领部队 → 回退返回 2(王国英雄/活跃),
        使『活跃状态』被正确解析, 而非误报为不活跃。其余 blob(复国者等)返回 0。"""
        kind = self.tail_kind(idx)
        if kind == self.KIND_BLOB and self.is_active_lord(idx):
            return 2
        return self.get_troop_slot(idx, self.SLOT_OCCUPATION)
    def set_occupation(self, idx, val):
        """设置活跃状态(槽2)。活跃领主(槽在未解码区段)会抛错拒绝, 避免写坏外观数据。"""
        return self.set_troop_slot(idx, self.SLOT_OCCUPATION, int(val))
    def occupation_name(self, idx):
        v = self.get_occupation(idx)
        return self.OCCUPATION_NAMES.get(v, "（未知 %d）" % v)
    def get_prisoner_of_party(self, idx):
        """关押该英雄的部队号(槽8); -1 = 未被囚禁。"""
        v = self.get_troop_slot(idx, self.SLOT_PRISONER)
        return -1 if v in (0,) or v >= 0x80000000 else int(v)
    def set_prisoner_of_party(self, idx, val):
        """设置囚禁状态(槽8): -1=释放; >=0 = 关押方部队号。"""
        return self.set_troop_slot(idx, self.SLOT_PRISONER, int(val) & 0xFFFFFFFF)
    def get_led_party(self, idx):
        """所率部队(槽10); <1 表示当前无部队。"""
        return self.get_troop_slot(idx, self.SLOT_LEADED_PARTY)

    def is_active_lord(self, idx):
        """该英雄当前是否在地图上率领一支活跃部队(= 有 party 以它为 stack[0])。"""
        return bool(self.find_lord_party(idx))

    def slot_region_trusted(self, idx):
        """该英雄的槽区定位是否经过交叉验证、可安全**读写**(尤其可写)。

        判定:
          - 无内联槽(has_inline_slots=False) → 不可信(普通兵种/外观块等) → False。
          - 活跃领主(有部队): 必须在 _slot_region 给出的起点(记录尾 254 槽)
            读到 slot2(occupation)==2 才算锚定正确 —— 否则起点定位落在外观块/噪声,
            写入会腐蚀相邻字段。仅 98/102 活跃领主满足(其余 4 个锚点不符, 写禁)。
          - 非活跃英雄(无部队): 内联槽紧接名字, 历来可靠 → True。

        注意: 即使区域可信, 也**仅 slot2(occupation) 经锚点验证**;
        slot8(囚禁)/slot10(率部队) 在活跃领主身上的取值与脚本预期不符(见 _slot_region
        文档), 其写操作仍须另行把关(见 GUI on_prisoner)。"""
        if not self.has_inline_slots(idx):
            return False
        if self.is_active_lord(idx):
            return self.get_occupation(idx) == 2
        return True


    def get_xp(self, idx):    return u32(self.data,self._off(idx)+OFF_XP)
    def get_pts(self, idx):    return u32(self.data,self._off(idx)+OFF_PT)

    # ---- 当前所属阵营 (领主跳槽) ----
    def get_faction(self, idx):
        """存档中该兵种的『当前所属阵营』索引。
        与 troops.txt 模板阵营不同: 领主跳槽后此值会变为新阵营。
        已结构化验证: 可靠王国领主中 44 名此值 != 模板阵营且均为合法王国索引。"""
        o=self._off(idx)
        if o is None: return -1
        return u32(self.data, o+OFF_FACTION)

    def get_faction_name(self, idx):
        """当前阵营的可读名(中文)。"""
        v=self.get_faction(idx)
        if 0<=v<len(self.mod.factions):
            f=self.mod.factions[v]
            return self.mod.faction_names.get(f, f)
        return f"?{v}"

    def set_faction(self, idx, fac):
        """设置该兵种在存档中的当前阵营 —— 即令领主投靠/跳槽到指定阵营。
        校验: 记录必须可靠, 且阵营索引必须在 [0, 阵营数) 内。"""
        if not self.is_reliable(idx):
            raise ValueError("该兵种在存档中无完整记录(占位/哨兵), 不允许修改阵营")
        n=len(self.mod.factions)
        v=int(fac)
        if not (0<=v<n):
            raise ValueError(f"阵营索引越界: {v} (合法范围 0..{n-1})")
        o=self._off(idx)
        if o is None:
            raise ValueError("无法定位该兵种的存档记录")
        w32(self.data, o+OFF_FACTION, v)
        self.dirty=True
        return v

    def get_inventory(self, idx):
        o=self._off(idx)
        return [[i32(self.data,o+OFF_INV+8*k), u32(self.data,o+OFF_INV+8*k+4)]
                for k in range(INV_SLOTS)]

    def get_equipment(self, idx):
        o=self._off(idx)
        return [[i32(self.data,o+OFF_EQUIP+8*k), u32(self.data,o+OFF_EQUIP+8*k+4)]
                for k in range(EQUIP_SLOTS)]

    def get_bio(self, idx):
        o=self._off(idx)
        bl=u32(self.data,o+OFF_BIO)
        if bl==0 or o+OFF_BIO+4+bl>len(self.data): return ""
        try: return self.data[o+OFF_BIO+4:o+OFF_BIO+4+bl].decode("utf-8")
        except Exception: return ""

    def set_bio(self, idx, text):
        """改写简介. 安全策略: 只允许缩短或等长, 不允许变长(变长会破坏后续记录偏移).
        若新文本比原简介长则截断到原长度, 返回 True 表示发生过截断."""
        o=self._off(idx)
        old_len=u32(self.data,o+OFF_BIO)
        data=text.encode("utf-8")
        truncated=False
        if len(data)>old_len:
            data=data[:old_len]; truncated=True
        new_len=len(data)
        w32(self.data,o+OFF_BIO,new_len)
        self.data[o+OFF_BIO+4:o+OFF_BIO+4+new_len]=data
        if new_len<old_len:
            for j in range(new_len,old_len):
                self.data[o+OFF_BIO+4+j]=0
        self.dirty=True
        return truncated

    # ---- 设值 (就地写入, 长度不变) ----
    # 所有写入均做安全钳制(clamp), 确保改后记录仍满足 valid_header, 否则下次读取会错位.
    def set_attrs(self, idx, vals):
        o=self._off(idx)
        for k,v in enumerate(vals[:4]): w32(self.data,o+OFF_ATTR+4*k, max(0,min(300,int(v))))
        self.dirty=True

    def set_profs(self, idx, vals):
        o=self._off(idx)
        for k,v in enumerate(vals[:7]): wf32(self.data,o+OFF_PROF+4*k, float(max(0,min(700,int(v)))))
        self.dirty=True

    def set_skills(self, idx, vals48):
        o=self._off(idx)
        vals48=[max(0,min(15,int(v))) for v in vals48[:NSKILL]]
        words=skills_encode(vals48)
        for k,w in enumerate(words): w32(self.data,o+OFF_SKILL+4*k,w)
        self.dirty=True

    def set_flags(self, idx, v): w32(self.data,self._off(idx)+OFF_FLAGS, int(v) & 0xFFFFFFFF); self.dirty=True
    def set_level(self, idx, v): w32(self.data,self._off(idx)+OFF_LEVEL, max(1,min(63,int(v)))); self.dirty=True
    def set_xp(self, idx, v):    w32(self.data,self._off(idx)+OFF_XP, max(0,int(v))); self.dirty=True
    def set_pts(self, idx, v):    w32(self.data,self._off(idx)+OFF_PT, max(0,int(v))); self.dirty=True

    def set_inventory_slot(self, idx, slot, item_id, modifier):
        if not (0<=slot<INV_SLOTS): raise IndexError("slot")
        o=self._off(idx)
        # 空槽 item_id=-1; 读取端用 i32 解码, 故写入端按有符号 32 位写(0xFFFFFFFF == -1)
        iid = -1 if int(item_id) < 0 else max(0, min(self.nitems, int(item_id)))
        w32(self.data,o+OFF_INV+8*slot, iid & 0xFFFFFFFF)
        w32(self.data,o+OFF_INV+8*slot+4, int(modifier) & 0xFFFFFFFF)
        self.dirty=True

    def set_equipment_slot(self, idx, slot, item_id, modifier):
        if not (0<=slot<EQUIP_SLOTS): raise IndexError("slot")
        o=self._off(idx)
        iid = -1 if int(item_id) < 0 else max(0, min(self.nitems, int(item_id)))
        w32(self.data,o+OFF_EQUIP+8*slot, iid & 0xFFFFFFFF)
        w32(self.data,o+OFF_EQUIP+8*slot+4, int(modifier) & 0xFFFFFFFF)
        self.dirty=True

    def clear_inventory_slot(self, idx, slot):
        self.set_inventory_slot(idx, slot, -1, 0)

    def clear_equipment_slot(self, idx, slot):
        self.set_equipment_slot(idx, slot, -1, 0)

    def add_inventory_item(self, idx, item_id, modifier=0):
        inv=self.get_inventory(idx)
        for s,(it,mo) in enumerate(inv):
            if it==-1:
                self.set_inventory_slot(idx,s,item_id,modifier); return s
        raise RuntimeError("物品栏已满 (64 槽)")

    def add_equipment_item(self, idx, item_id, modifier=0):
        eq=self.get_equipment(idx)
        for s,(it,mo) in enumerate(eq):
            if it==-1:
                self.set_equipment_slot(idx,s,item_id,modifier); return s
        raise RuntimeError("装备槽已满 (10 槽)")

    # ---- 部队 (parties) ----
    def load_parties(self):
        self.parties = scan_parties(self.data)
        return len(self.parties)

    def party_count(self):
        return len(getattr(self, "parties", []))

    def get_party(self, i):
        info, stacks, _ = parse_party(self.data, self.parties[i])
        return info, stacks

    def set_party_stack_count(self, i, sidx, num):
        info, stacks = self.get_party(i)
        if 0 <= sidx < len(stacks):
            struct.pack_into("<I", self.data, stacks[sidx]["offset"] + 4, max(0, int(num)))
            self.dirty = True

    def set_party_stack_troop(self, i, sidx, troop):
        info, stacks = self.get_party(i)
        if 0 <= sidx < len(stacks):
            struct.pack_into("<i", self.data, stacks[sidx]["offset"], int(troop))
            self.dirty = True

    # ---- 部队归属阵营 (party fields[4]) ----
    # 实测: 领主部队(pt_kingdom_hero_party) stack[0] 即领主本人,
    #       其 fields[4] 与领主 0x05C 阵营 102/102 = 100% 一致;
    #       城池(p_town_/p_castle_/p_village_)的 fields[4] 亦为其归属王国(148 条全部落在 5 个王国)。
    PY_FAC_FIELD = 4

    def get_party_faction(self, i):
        info, _ = self.get_party(i)
        return info["fields"][self.PY_FAC_FIELD]

    def get_party_faction_name(self, i):
        v = self.get_party_faction(i)
        if 0 <= v < len(self.mod.factions):
            f = self.mod.factions[v]
            return self.mod.faction_names.get(f, f)
        return f"?{v}"

    def set_party_faction(self, i, fac):
        n = len(self.mod.factions)
        v = int(fac)
        if not (0 <= v < n):
            raise ValueError(f"阵营索引越界: {v} (合法范围 0..{n - 1})")
        info, _ = self.get_party(i)
        w32(self.data, info["fields_off"] + 4 * self.PY_FAC_FIELD, v)
        self.dirty = True
        return v

    def find_lord_party(self, troop_idx):
        """找该领主本人率领的部队 (stack[0].troop == troop_idx)。"""
        out = []
        for pi in range(self.party_count()):
            info, stacks = self.get_party(pi)
            if stacks and stacks[0]["troop"] == troop_idx:
                out.append(pi)
        return out

    def defect_lord(self, troop_idx, new_fac):
        """执行『领主跳槽』脚本链 —— 把归属相关的数据一并改掉。

        1.011 没有战团那种"预约跳槽"槽(slot_troop_change_to_faction): 已对领主
        记录的全部固定偏移与内联尾部槽区做过扫描, 只有 +0x05C 一个阵营字段。
        (注: 这特指"待跳槽"这一**特定**槽; 1.011 兵种**确有**槽区, 如性格=槽128、
        led_party=槽10, 详见模块头注释与 `存档格式解析_兵种篇.md`。) 故跳槽在
        1.011 里是脚本直接改写归属, 这里等价地一次性完成:

          (1) 领主记录 +0x05C 当前阵营
          (2) 该领主所率部队 party 的 fields[4] 归属阵营
              —— 只改 (1) 不改 (2) 会出现"人是新阵营、部队仍替旧阵营打仗"的不一致

        适用对象 = 『领主块(troops.txt 英雄块 idx 203..307 连续段)内的真实领主』,
        含被击败/被俘而暂无部队者: 其 0x05C 改后, 游戏 respawning 脚本
        (create_kingdom_hero_party / randomly_make_prisoner_heroes_escape_from_party)
        会在重建部队时读取 0x05C 决定新部队归属 —— 故改 0x05C 即令其『下次以新阵营身份刷新』。

        ⚠ 适用边界澄清(2026-09-20 修正): 王子(_son)/小姐(_lady)/复国者(_pretender) 也能成为
        正常领主 —— 游戏通过脚本 create_kingdom_hero_party / give_center_to_lord 直接为「任意英雄兵种」
        创建部队与封地: 复国者复国成功后即该国国王(有封地、被击败也正常刷新); 同伴可被玩家分封为领主。
        领主块 idx 203..307 只是『引擎自动刷新循环』管理的初始领主集合, 并非硬边界。故
        "仅改 0x05C 不够"仍成立(还需为其创建 map party 并设 fields[4]=阵营), 但
        "他们永远无法变成领主" 是错的 —— 用本工具 promote_to_lord(idx, faction[, center]) 即可
        复刻游戏行为将其提拔为领主(创建 pt_kingdom_hero_party, stack[0]=该兵种, fields[4]=阵营)。

        返回改动明细 list[(描述, 旧值, 新值)]。
        注意: 封地(城池)归属需另行处理 —— 领主<->封地的关联尚未定位。
        """
        if not self.is_reliable(troop_idx):
            raise ValueError("该兵种在存档中无完整记录, 不允许跳槽")
        n = len(self.mod.factions)
        v = int(new_fac)
        if not (0 <= v < n):
            raise ValueError(f"阵营索引越界: {v} (合法范围 0..{n - 1})")
        changes = []
        old = self.get_faction(troop_idx)
        if old != v:
            self.set_faction(troop_idx, v)
            changes.append(("领主记录 +0x05C 阵营", old, v))
        for pi in self.find_lord_party(troop_idx):
            pf_old = self.get_party_faction(pi)
            if pf_old != v:
                self.set_party_faction(pi, v)
                info, _ = self.get_party(pi)
                changes.append((f"部队 #{pi} {info['id']} 归属阵营", pf_old, v))
        return changes

    # ---- 封地授予 (center ↔ 领主 绑定) ----
    # 复刻脚本 give_center_to_lord (scripts.txt:227) 的『可持久化子集』。
    #   原生脚本在运行时还会写 center 的 slot7(town_lord) 与村庄 slot120(bound_center);
    #   但 1.011 存档并不把这两个槽以 troop 索引形式持久化(全存档扫描 slot7 命中率仅 4.1% 噪声级),
    #   由引擎运行时重建。故这里只写『可持久化』的部分: center 的 fields[4](管辖阵营) 划归领主所属国。
    def give_center_to_lord(self, center_party_idx, troop_idx):
        """把一座城池/村庄(center party)的管辖阵营拨给 troop_idx 所属国, 等价于游戏封地授予。

        参数:
          center_party_idx: 城池 party 的索引 (p_town_/p_castle_/p_village_)
          troop_idx       : 领主兵种索引(英雄, 已提拔或为既有领主)
        返回: 改动明细 list。
        注意: 仅改 fields[4](管辖阵营); 精确的"城池领主绑定" slot7 在 1.011 存档未持久化,
              需进游戏用『封地授予』菜单或脚本补完(见 promote_to_lord 注释)。
        """
        if not (0 <= center_party_idx < self.party_count()):
            raise ValueError(f"center 索引越界: {center_party_idx}")
        if not self.is_reliable(troop_idx):
            raise ValueError("该兵种在存档中无完整记录, 不能接收封地")
        fac = self.get_faction(troop_idx)
        cinfo, _ = self.get_party(center_party_idx)
        old = self.get_party_faction(center_party_idx)
        if old == fac:
            return [(f"封地 #{center_party_idx} {cinfo['id']} 管辖阵营", old, fac)]
        self.set_party_faction(center_party_idx, fac)
        return [(f"封地 #{center_party_idx} {cinfo['id']} 管辖阵营", old, fac)]

    def promote_to_lord(self, idx, faction, center=None, party_name=None):
        """把一个英雄兵种(王子/复国者/同伴/小姐等)提拔为正常领主。

        复刻游戏 create_kingdom_hero_party + give_center_to_lord 的运行时行为:
          (1) 设其当前阵营 0x05C = faction;
          (2) 新建一支 map party: id=pt_kingdom_hero_party, stack[0]={troop=idx, num=1},
              party fields[4] = faction(归属阵营);
          (3) 若给定 center(城池 party index 或 index 列表), 将其 fields[4]=faction(管辖阵营
              划归新领主所属国); 多座封地可一次拨给。内部复用 give_center_to_lord。

        与 defect_lord 的区别: defect_lord 只改"已有领主"的归属; promote_to_lord 是为
        「原本没有部队的英雄」从无到有创建部队 —— 这正是游戏把复国者/同伴变成领主所做的事。

        适用: 任何 is_reliable 且带 tf_hero 的英雄兵种(领主块 idx 203..307 之外亦可,
        例如王子 #313+、复国者 #308+、同伴 <182)。

        返回 (新 party index, 改动明细 list)。
        注意:
          * 兵种自身 slot10(led_party = 所率部队索引) **已在此写入** —— 2026-09-19 确认
            1.011 兵种确有内联槽区, 且原生 create_kingdom_hero_party 正是用
            `troop_set_slot(troop, 10, party)` 写它; 故本方法对"有内联槽区的英雄"(被提拔时
            必为非活跃领主, 内联区可写)一并写入 slot10, 与游戏行为 1:1 对齐。
            仅当该英雄恰好无内联槽区时跳过(罕见), 此时仍以 party stack[0]==该兵种 为权威链接。
          * center 的具体领主绑定(slot 7)位于 party 未解析尾部, 本方法仅改其 fields[4](管辖阵营);
            精确"封给某人"需解码 party 尾部 slot 区(见 change_troop_faction 脚本链)。
          * 涉及在存档 parties 区插入新记录, 已用快照+整体重定位+校验回滚保证安全,
            但游戏运行时是否真吃这套结构, 仍需用户开档验证。
        """
        if not self.is_reliable(idx):
            raise ValueError("该兵种在存档中无完整记录(占位/哨兵), 不能提拔为领主")
        if not (self.get_flags(idx) & TF_HERO):
            raise ValueError("仅英雄兵种(tf_hero)可被提拔为领主")
        existing = self.find_lord_party(idx)
        if existing:
            raise ValueError(f"该英雄已拥有部队(部队#{existing}), 不能重复提拔(本就已是领主/已上场?)")
        n = len(self.mod.factions)
        v = int(faction)
        if not (0 <= v < n):
            raise ValueError(f"阵营索引越界: {v} (合法范围 0..{n - 1})")
        o = self._off(idx)
        if o is None:
            raise ValueError("无法定位该兵种记录")

        tid = self.mod.troops[idx]["tid"] if idx < len(self.mod.troops) else f"trp_{idx}"
        disp = self.mod.troop_names.get(tid, tid)
        pname = (party_name or disp).encode("utf-8")[:60]
        pid = b"pt_kingdom_hero_party"

        # 动态 party 的 12 字节信封: [marker=1][index_b][field_c]
        # 真实存档里 index_b==field_c 且为单调递增全局计数器, 这里取 max+1 保证唯一/单调。
        max_ib = max((r.get("index_b") or 0) for r in self.parties) if self.parties else 0
        next_ib = max_ib + 1

        fields = [0] * 18
        fields[0] = self.get_flags(idx)   # party flags 沿用兵种 flags
        fields[4] = v                     # 归属阵营 = faction
        fields[17] = 1                    # num_stacks = 1 (车队首条=领主本人)

        body = (struct.pack("<I", 1) + struct.pack("<I", next_ib) + struct.pack("<I", next_ib)
                + struct.pack("<I", len(pid)) + pid
                + struct.pack("<I", len(pname)) + pname
                + b"".join(struct.pack("<I", f) for f in fields)
                + struct.pack("<i", idx) + struct.pack("<I", 1)   # stack[0]: troop=idx, num=1
                + struct.pack("<I", 0) + struct.pack("<I", 0))     # stack[0]: f3, f4

        snap = bytes(self.data)
        try:
            if not self.parties:
                self.load_parties()
            last = max(self.parties, key=lambda r: r["start"])
            _, _, end_off = parse_party(self.data, last)
            self.data[end_off:end_off] = body
            if not self._rescan_after_resize():
                raise RuntimeError("重定位校验失败")
            new_pi = None
            for pi in range(self.party_count()):
                info, stacks = self.get_party(pi)
                if stacks and stacks[0]["troop"] == idx:
                    new_pi = pi
                    break
            if new_pi is None:
                raise RuntimeError("新建 party 后未能定位")
            # (1) 阵营
            self.set_faction(idx, v)
            changes = [("兵种 0x05C 阵营", None, v),
                       (f"新建领主部队 #{new_pi} pt_kingdom_hero_party", None,
                        f"stack[0]={idx}({disp}), 归属={v}")]
            # (1b) 复刻原生 create_kingdom_hero_party: troop_set_slot(troop, 10, party)
            #      slot10 = led_party(所率部队索引)。被提拔的英雄此时必为非活跃领主,
            #      其内联槽区存在且可写, 故此处一并写入以与游戏运行时行为 1:1 对齐。
            try:
                if self.has_inline_slots(idx):
                    self.set_troop_slot(idx, 10, new_pi)
                    changes.append(("兵种 slot10(led_party)", None, new_pi))
            except Exception:
                # 极少数情况(无内联槽区)跳过, 仍以 stack[0]==兵种 为权威链接
                pass
            # (3) 封地(可多选): 每座城池的管辖阵营划归该领主所属国
            if center is not None:
                centers = center if isinstance(center, (list, tuple)) else [center]
                for c in centers:
                    for d, o, nv in self.give_center_to_lord(c, idx):
                        changes.append((d, o, nv))
            # 一致性校验: 新建 party 必须能被 find_lord_party 找到
            if not self.find_lord_party(idx):
                raise RuntimeError("新建领主部队后 find_lord_party 仍为空, 结构可能未被引擎识别")
            self.dirty = True
            return new_pi, changes
        except Exception:
            self.data = bytearray(snap)
            try:
                self._rescan_after_resize()
            except Exception:
                pass
            raise

    def _rescan_after_resize(self):
        """resize 后重新定位部队与兵种数组, 失败返回 False (用于回滚判断)."""
        new_parties = scan_parties(self.data)
        expect = len(self.mod.troops) if self.mod.troops else 794
        rec0, starts = find_array(self.data, self.nitems, expect=expect)
        if len(starts) != expect or len(new_parties) < 1:
            return False
        self.parties = new_parties
        self.starts = starts
        self.rec0 = rec0
        return True

    def add_party_stack(self, i, troop, num, f3=0, f4=0):
        """在部队 i 末尾追加一条兵种堆叠. 会变长, 故用快照+重定位+校验回滚保证安全."""
        snap = bytes(self.data)
        try:
            self.parties = scan_parties(self.data)
            info, stacks, end_off = parse_party(self.data, self.parties[i])
            idlen = len(info["id"].encode("utf-8"))
            namelen = len(info["name"].encode("utf-8"))
            ns_off = info["body"] + 4 + idlen + 4 + namelen + 4 * 17
            new_bytes = struct.pack("<IIII", int(troop), max(0, int(num)), int(f3), int(f4))
            self.data[end_off:end_off] = new_bytes
            struct.pack_into("<I", self.data, ns_off, info["num_stacks"] + 1)
            if not self._rescan_after_resize():
                raise RuntimeError("重定位校验失败")
            self.dirty = True
        except Exception:
            self.data = bytearray(snap)
            self._rescan_after_resize()
            raise

    def remove_party_stack(self, i, sidx):
        """删除部队 i 的第 sidx 条堆叠. 会变长, 同样带快照回滚."""
        snap = bytes(self.data)
        try:
            self.parties = scan_parties(self.data)
            info, stacks, _ = parse_party(self.data, self.parties[i])
            if not (0 <= sidx < len(stacks)):
                return
            idlen = len(info["id"].encode("utf-8"))
            namelen = len(info["name"].encode("utf-8"))
            ns_off = info["body"] + 4 + idlen + 4 + namelen + 4 * 17
            so = stacks[sidx]["offset"]
            del self.data[so:so + 16]
            struct.pack_into("<I", self.data, ns_off, info["num_stacks"] - 1)
            if not self._rescan_after_resize():
                raise RuntimeError("重定位校验失败")
            self.dirty = True
        except Exception:
            self.data = bytearray(snap)
            self._rescan_after_resize()
            raise

    # ---- 写回 ----
    def save(self, path=None, backup=True):
        out=Path(path) if path else self.path
        if backup and out.exists():
            ts=__import__("datetime").datetime.now().strftime("%Y%m%d_%H%M%S")
            out.with_suffix(f".sav.bak_{ts}").write_bytes(out.read_bytes())
        out.write_bytes(bytes(self.data))
        self.dirty=False
        return out

    # ---- 修饰符编解码 ----
    # 存档槽位第二列 u32 = (修饰符 imod << 24) | 耐久/数量。
    #   imod 0..42 = 物品修饰符枚举(见模块级 IMOD_NAMES);
    #   低 24 位: 马匹=当前生命, 盾牌=当前耐久, 食物等货物=当前数量, 其余物品=0。
    # troops.txt 模板第二列 = 裸的 imod 枚举(不带耐久), WD 模板全为 0。
    # 旧实现按 (低8位=类型, 高24位=数量) 解码是错误猜测, 2026-09-23 已用
    # 全存档 11124 个槽位的分布 + cns/item_modifiers.csv 证伪并修正。
    @staticmethod
    def decode_mod(mod):
        return (mod >> 24, mod & 0xFFFFFF)   # (修饰符 imod, 耐久/数量)

    @staticmethod
    def encode_mod(amount, imod):
        return ((int(imod) & 0xFF) << 24) | (int(amount) & 0xFFFFFF)

def _pause(msg=""):
    """打印信息后等待回车 —— 避免双击运行时"一闪而过"; 随后结束进程。"""
    if msg: print(msg)
    try: input("\n按回车键退出...")
    except Exception: pass
    raise SystemExit(0)   # SystemExit 继承自 BaseException, 不会被上面的 except 吞掉


if __name__=="__main__":
    import sys
    # 不带参数运行(例如双击本文件)时, 说明用法并停下, 不要直接退出
    if len(sys.argv) < 2:
        _pause(
            "【mb_model.py 是模型库, 不是要运行的程序】\n"
            "它只提供存档解析/修改能力, 本身没有界面。\n\n"
            "要修改存档, 请运行带界面的版本:\n"
            "    E:\\BigBrother\\Anaconda\\python.exe mb_editor_gui.py\n\n"
            "要查看兵种/物品(含技能位置对照):\n"
            "    E:\\BigBrother\\Anaconda\\python.exe 兵种物品查看器_v2.py\n\n"
            "注意: GUI 需要带 tkinter 的 Python(上面那个 Anaconda);\n"
            "      托管版 python 3.13 没装 tkinter, 运行 GUI 会报错。\n\n"
            "若确实想跑命令行自测, 请带参数:\n"
            "    python mb_model.py <存档.sav> [模块目录]"
        )
    doc=SaveDoc()
    mp=sys.argv[2] if len(sys.argv)>2 else r"E:\Program Files\Game\Mount&Blade\Modules\WD - Minuet (v.0.12 Full)"
    doc.load_module(mp)
    doc.load_save(sys.argv[1])
    print(f"records={len(doc.starts)} nitems={doc.nitems}")
    print("rec0 name=",doc.mod.troop_name(0),"attrs=",doc.get_attrs(0),"inv=",doc.get_inventory(0)[:3])
    print("rec203 name=",doc.mod.troop_name(203),"attrs=",doc.get_attrs(203))
    print("rec0 flags=0x%08X hero=%s" % (doc.get_flags(0), doc.is_hero(0)))
    _pause("自测完成。")
