# 骑砍 1.011 修改工具箱 · Mount & Blade 1.011 Toolkit

> 面向 **WD - Minuet (v0.12 Full)** 模组的逆向编辑工具；不写死模组内容，模块目录自动探测，因此也适用于其它基于 Native 的 1.011 模组。
> Reverse-engineered editors for **WD - Minuet (v0.12 Full)** — no module content is hard-coded, the module folder is auto-detected, so they also work with other Native-based 1.011 modules.

---

## 🌐 选择语言 · Choose your language

| | 语言 / Language | 说明文档 / Documentation |
| :---: | --- | --- |
| 🇨🇳 | **简体中文** | 👉 **[README_CN.md](./README_CN.md)** |
| 🇬🇧 | **English** | 👉 **[README_EN.md](./README_EN.md)** |

**完整的使用说明、功能清单、已知限制与分享文件清单，都在上面对应的语言文档里。**
**The full usage guide, feature list, known limitations and the file manifest live in the language documents above.**

---

## 这是什么 · What this is

本仓库包含**两套彼此独立的工具**，别混淆：

| 工具 · Tool | 编辑对象 · What it edits | 生效时机 · When it takes effect |
| --- | --- | --- |
| **兵种/物品查看器**<br>Troop / Item Viewer | 抽象模板 `troops.txt` / `item_kinds1.txt` / `party_templates.txt` <br>Abstract templates | 物品与部队模板改动**全局即时生效**；兵种改动**开新档生效**<br>Items & party templates: global & immediate · Troops: new game |
| **存档修改器**<br>Save Editor | 二进制存档实例 `.sav` <br>Binary save instance | 只影响该存档，保存后重新载入即生效<br>Only that save; reload in-game |

两套工具文件不同、数据不同、互不引用 —— 详见语言文档第一节。
The two tools are independent (different files, data and rules) — see section 1 of the language docs.

---

## 快速开始 · Quick start

1. 环境：需要 **Python 3.8+ 且必须带 tkinter**（Windows 官方安装包 / Anaconda 均自带）；**无需任何第三方库**。
   Requires **Python 3.8+ with tkinter** (official Windows installer and Anaconda both ship it); **no third-party packages**.
2. **推荐：双击 `run_all.py`** → 在弹出的窗口里点击选择工具。
   **Recommended: double-click `run_all.py`** and pick a tool from the launcher window.
   - `1` = 中文查看器 · Chinese viewer
   - `2` = 英文查看器 · English viewer
   - `3` = 中文存档修改器 · Chinese save editor
   - `4` = 英文存档修改器 · English save editor
3. 也可用命令行：`python run_all.py 1`，或直接 `python 兵种物品查看器_v2.py` / `python mb_editor_gui_EN.py`。
   Or from a terminal: `python run_all.py 1`, or run a script directly.

---

## ⚠️ 动手前必读 · Before you edit

- 这是**实验性工具**，所有修改都可能破坏存档或模组：**请先备份 `.sav` 存档和模组目录**（工具写回时自身也会生成 `.bak` 备份）。
  **Experimental** — edits can corrupt a save or a module: **back up your `.sav` files and module folder first** (the tools also write `.bak` backups on every save).
- 模组若装在 `Program Files` 下，保存模板需要**管理员权限**（否则会静默失败）。
  If the module lives under `Program Files`, saving templates needs **administrator rights** (otherwise it silently fails).
- 解析与写回已做往返测试，但**游戏运行时是否完整采纳**仍需实测。全部已知限制见语言文档第 6 节。
  Round-trip tests cover parsing and write-back, but **whether the running game fully honours** every value still needs in-game testing. See section 6 of the language docs.

---

## 分享文件清单 · Files to share

- **必需 / Required (6)**：`mb_model.py`、`mb_model_EN.py`、两个查看器、两个存档修改器。
- **建议附带 / Recommended**：`run_all.py` 启动器 + 两份语言 README + 本文件 `README.md`。
- **可选 / Optional**：`mb_module_path.json`（模块目录记忆，分发前建议删除）、旧版参考查看器。
- **不需要拷贝 / Do not copy**：`backups/`、`__pycache__/`、`*_out.txt`、探查脚本、测试用 `*.sav`。

完整清单见 **README_CN.md / README_EN.md 第 7 节**。
For the complete manifest, see **section 7** of either language README.
