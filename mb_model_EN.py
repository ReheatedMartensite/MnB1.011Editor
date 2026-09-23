# -*- coding: utf-8 -*-
"""
mb_model_EN.py — English display-name layer on top of mb_model.py
=================================================================
Why this file exists
--------------------
`mb_model.py` resolves every display name from `languages/cns/*.csv`
(the Chinese translation tables). That is why the English GUIs
(`mb_editor_gui_EN.py`, `troop_item_viewer_v2_EN.py`) still showed Chinese
troop / item / faction / skill names even though all their labels were
already translated.

This module keeps *all* of mb_model's parsing logic and only swaps the
display-name layer to the **original English names taken from the module
txt files**:

    troops.txt        trp_swadian_recruit  Swadian_Recruit  Swadian_Recruits ...
    item_kinds1.txt   itm_horse_meat       Horse_Meat       Horse_Meat ...
    factions.txt      fac_no_faction       No_Faction ...
    skills.txt        skl_prisoner_management  Prisoner_Management ...

i.e. the second field; underscores are turned into spaces exactly the way
the game renders them (`Practice_Sword` -> `Practice Sword`).

Implementation: subclass, do NOT copy the 1500-line parser — otherwise the
two copies drift apart. Only the name tables and a few fallback strings
are overridden.

!!! Critical constraint (do not remove) !!!
-------------------------------------------
The save file's hero `bio` text is Chinese, and `SaveDoc._scan_heroes()`
matches those bios against **Chinese** troop names to build the
idx -> record-offset map. If the names were English there, hero mapping
would silently break. Therefore English is applied to the *display* layer
only: `SaveDocEN._scan_heroes()` temporarily swaps the Chinese table back
in for the duration of the scan and restores English afterwards.

Usage (drop-in for the English GUIs)
------------------------------------
    import mb_model_EN as M
    M.ModuleData()   # -> ModuleDataEN
    M.SaveDoc()      # -> SaveDocEN
The aliases `ModuleData` / `SaveDoc` at the bottom of this file make the
swap a one-line import change in the GUI files.
"""
import re
from pathlib import Path

import mb_model as _cn


def en_name(s):
    """'Practice_Sword' -> 'Practice Sword' (underscores -> spaces, as the game shows)."""
    if s is None:
        return ""
    s = str(s).strip()
    return s.replace("_", " ").strip() or s


def _second_field_map(text, prefix):
    """Map '<prefix>xxx' -> the first field after it (the English name)."""
    out = {}
    for line in (text or "").splitlines():
        s = line.strip()
        if not s.startswith(prefix):
            continue
        p = s.split()
        if len(p) >= 2:
            out[p[0]] = p[1]
    return out


class ModuleDataEN(_cn.ModuleData):
    """Identical to mb_model.ModuleData except: display names are English.

    The Chinese tables are preserved as `*_names_cn` because the save-file
    hero scan needs them (see module docstring).
    """

    def load(self, folder):
        n = super().load(folder)
        folder = Path(folder)

        # keep the Chinese tables (save bio matching depends on them)
        self.troop_names_cn = dict(self.troop_names)
        self.item_names_cn = dict(self.item_names)
        self.faction_names_cn = dict(self.faction_names)
        self.skill_names_cn = dict(self.skill_names)

        # troops: troops.txt header field #2 is the English name
        self.troop_names = {t["tid"]: en_name(t["raw"]) for t in self.troops}

        # items: mb_model stores only itm_xxx ids -> re-read item_kinds1.txt
        imap = _second_field_map(self._read_text(folder / "item_kinds1.txt"), "itm_")
        self.item_names = {k: en_name(imap.get(k, k[4:])) for k in self.items}

        # factions: factions.txt lines are like "0 fac_commoners Commoners ..."
        fmap = {}
        for line in self._read_text(folder / "factions.txt").splitlines():
            m = re.search(r"\b(fac_\w+)\s+(\S+)", line)
            if m:
                fmap[m.group(1)] = m.group(2)
        self.faction_names = {k: en_name(fmap.get(k, k[4:])) for k in self.factions}

        # skills: skills.txt lines are like "skl_trade Trade 19 10 ..."
        smap = _second_field_map(self._read_text(folder / "skills.txt"), "skl_")
        self.skill_names = {k: en_name(smap.get(k, k[4:])) for k in self.skills}
        return n

    # ---- English fallbacks (the base class returns Chinese) ----
    def troop_name(self, idx):
        if 0 <= idx < len(self.troops):
            return self.troop_names.get(self.troops[idx]["tid"],
                                        en_name(self.troops[idx]["raw"]))
        return "Troop #%d" % idx

    def item_name(self, idx):
        if idx is None or idx < 0:
            return "— (empty)"
        if 0 <= idx < len(self.items):
            return self.item_names.get(self.items[idx], en_name(self.items[idx][4:]))
        return "Item #%d" % idx


class SaveDocEN(_cn.SaveDoc):
    """English save document.

    Display names come from ModuleDataEN; hero scanning still uses the
    Chinese table internally (see `_scan_heroes` below).
    """

    PERSONALITY_NAMES = {
        1: "Upstanding", 2: "Sadistic", 3: "Charismatic",
        4: "Cunning", 5: "Martial", 6: "Benign", 7: "Ambitious",
    }
    PERSONALITY_MONARCH = "Monarch"
    OCCUPATION_NAMES = {
        0:  "Inactive / not hired (slto_inactive)",
        2:  "Kingdom hero · active as a lord (slto_kingdom_hero)",
        3:  "Player companion · in the player's party (slto_player_companion)",
        4:  "Lady of the court (slto_kingdom_lady)",
        5:  "Kingdom seneschal (slto_kingdom_seneschal)",
        6:  "Robber knight (slto_robber_knight, old id)",
        8:  "Robber knight (slto_robber_knight, new id, unconfirmed)",
        9:  "Inactive pretender (slto_inactive_pretender)",
        11: "Retired (slto_retirement)",
    }

    def __init__(self):
        super().__init__()
        self.mod = ModuleDataEN()      # base __init__ hardcodes ModuleData()

    def _scan_heroes(self):
        """Scan with the Chinese name table (bios are Chinese), then restore English."""
        cn = getattr(self.mod, "troop_names_cn", None)
        if not cn:
            return super()._scan_heroes()
        en = self.mod.troop_names
        self.mod.troop_names = cn
        try:
            return super()._scan_heroes()
        finally:
            self.mod.troop_names = en

    def personality_name(self, idx):
        v = self.get_personality(idx)
        if v == 0:
            kind = self.tail_kind(idx)
            if kind == self.KIND_BLOB and self.is_active_lord(idx):
                return "(Active · personality lives in the party section, not decoded yet)"
            if kind == self.KIND_BLOB:
                return "(%s 0)" % self.PERSONALITY_MONARCH
            return "(Not set 0)"
        return self.PERSONALITY_NAMES.get(
            v, "(Unknown %d, meaning to be confirmed in-game)" % v)

    def occupation_name(self, idx):
        v = self.get_occupation(idx)
        return self.OCCUPATION_NAMES.get(v, "(Unknown %d)" % v)


# ---- drop-in aliases: `import mb_model_EN as M` then use M.ModuleData / M.SaveDoc ----
ModuleData = ModuleDataEN
SaveDoc = SaveDocEN

# ---- 透出 mb_model 的其余公开名称 (常量/工具函数) ----
# 英文 GUI 大量使用 M.NSKILL / M.u32 / M.decode_tf / M.INV_SLOTS / M.OFF_SKILL ...
# 若不透出, `import mb_model_EN as M` 后这些引用会 AttributeError。
# 已在本模块定义的名称(ModuleData/SaveDoc/en_name 等)不覆盖。
_g = globals()
for _n in dir(_cn):
    if _n.startswith("_") or _n in _g:
        continue
    try:
        _g[_n] = getattr(_cn, _n)
    except Exception:
        pass


# ---- English overrides: troop-flag bit table & decoder ----
# mb_model.TF_NAMES / decode_tf 的标签是中文(勾选框与 Decoded 行会显示中文),
# 英文 GUI 需要英文标签。位值必须与中文版完全一致(同一 header_troops.py 定义),
# 只是标签翻译; 因此必须放在上面的透出循环之后, 确保最终生效的是这份英文表。

# English item-modifier names (same enum 0..42 as mb_model.IMOD_NAMES;
# source: module-system header_item_modifiers.py). Labels only — values/length identical.
IMOD_NAMES = [
    "Plain", "Cracked", "Rusty", "Bent", "Chipped", "Battered", "Poor", "Crude", "Old", "Cheap",
    "Fine", "Well-made", "Sharp", "Balanced", "Tempered", "Deadly", "Exquisite", "Masterwork",
    "Heavy", "Strong", "Powerful", "Tattered", "Ragged", "Rough", "Sturdy", "Thick", "Hardened",
    "Reinforced", "Superb", "Lordly", "Lame", "Swaybacked", "Stubborn", "Timid", "Meek",
    "Spirited", "Champion", "Fresh", "Day-old", "Two days old", "Smelling", "Rotten", "Large bag",
]

TF_NAMES = [
    (_cn.TF_FEMALE, "Female (tf_female)"),
    (_cn.TF_UNDEAD, "Undead (tf_undead)"),
    (_cn.TF_HERO, "Hero (tf_hero)"),
    (_cn.TF_INACTIVE, "Inactive"),
    (_cn.TF_UNKILLABLE, "Unkillable"),
    (_cn.TF_ALLWAYS_FALL_DEAD, "Always falls dead"),
    (_cn.TF_NO_CAPTURE_ALIVE, "Never captured alive"),
    (_cn.TF_MOUNTED, "Mounted"),
    (_cn.TF_MERCHANT, "Merchant"),
    (_cn.TF_RANDOMIZE_FACE, "Randomize face"),
    (_cn.TF_GUR_BOOTS, "Guarantee boots"),
    (_cn.TF_GUR_ARMOR, "Guarantee armor"),
    (_cn.TF_GUR_HELMET, "Guarantee helmet"),
    (_cn.TF_GUR_GLOVES, "Guarantee gloves"),
    (_cn.TF_GUR_HORSE, "Guarantee horse"),
    (_cn.TF_GUR_SHIELD, "Guarantee shield"),
    (_cn.TF_GUR_RANGED, "Guarantee ranged"),
    (_cn.TF_GUR_POLEARM, "Guarantee polearm"),
    (_cn.TF_UNMOVEABLE_IN_PARTY_WINDOW, "Unmoveable in party window"),
]


def decode_tf(v):
    """English twin of mb_model.decode_tf (labels follow TF_NAMES above)."""
    if v is None:
        return []
    out = []
    t = v & 0xF
    if t == 0:
        out.append("Male")
    elif t not in (1, 2):
        out.append("Unknown type (%d)" % t)
    for mask, name in TF_NAMES:
        if v & mask:
            out.append(name)
    known = 0xF
    for m, _ in TF_NAMES:
        known |= m
    unknown = v & ~known
    if unknown:
        out.append("Other bits: 0x%08X" % unknown)
    return out
