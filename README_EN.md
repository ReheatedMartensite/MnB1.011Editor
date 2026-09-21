# Mount & Blade 1.011 Toolkit · README

Reverse-engineered editors built for the **WD - Minuet (v0.12 Full)** module, but they do not
hard-code any module content — the module folder is auto-detected or picked manually, so they
work with other Native-based 1.011 modules too.

> ⚠️ Experimental: edits can corrupt a save or a module. **Back up your `.sav` files and your
> module folder before writing anything.**

---

## 1. Two separate tools — do not mix them up

| Tool | What it edits | When it takes effect | Files |
|---|---|---|---|
| **Troop / Item Viewer** | Abstract templates: `troops.txt`, `item_kinds1.txt`, `party_templates.txt` | Item / party-template edits apply **globally and immediately** (affect every save); troop edits apply to **new games** | `troop_item_viewer_v2_EN.py` (English)<br>`兵种物品查看器_v2.py` (Chinese) |
| **Save Editor** | Binary save instance `.sav` | Only affects that save; reload it in-game after saving | `mb_editor_gui_EN.py` (English)<br>`mb_editor_gui.py` (Chinese) |

Different files, different data, different rules. Neither imports the other.

---

## 2. Requirements

- Python 3.8+ **with tkinter** (the official Windows installer and Anaconda both ship it).
  `ModuleNotFoundError: No module named 'tkinter'` means your Python lacks tkinter — use another one.
- No third-party packages needed.

## 3. How to run

1. **Double-click `run_all.py`** → pick a tool from the launcher window (easiest);
2. or run directly: `python troop_item_viewer_v2_EN.py`, `python mb_editor_gui_EN.py`, …
3. or `python run_all.py 2` (1 = Chinese viewer, 2 = English viewer,
   3 = Chinese save editor, 4 = English save editor).

## 4. How the module folder is located (portable)

No machine-specific hard-coded path is required. Resolution order:

1. Environment variable `MB_MODULE_DIR` (pin the module folder)
2. `--module <dir>` command-line argument (save editors)
3. Last successfully loaded module (remembered in `mb_module_path.json` next to the scripts)
4. Auto-detect: env var `MB_MODULES_DIR` → Steam library registry lookup →
   common install paths (`E:/D:/C:\Program Files\Game\Mount&Blade\Modules`, …)
5. Nothing found → a folder-picker dialog appears once, then the choice is remembered

**To share the toolkit:** copy the whole folder. On first run the other person picks their own
module folder once; after that it is automatic. (Optionally delete `mb_module_path.json` before
shipping — it only holds your local paths.)

---

## 5. Features

### Troop / Item Viewer (templates)
- Troops: attributes, proficiencies, skills, 64 inventory slots, 10 equipment slots,
  **checkbox-style `tf_*` flag editing**, **upgrade-chain display**, faction assignment,
  per-troop diff against a loaded save
- Items: full attribute decoding (price / weight / abundance / armour / weapon / horse /
  shield / ammo / ranged fields), and **editable** price, weight, abundance and stat values
- Party templates (`party_templates.txt`): view and edit troop stacks
- Every write creates a `name.<timestamp>.bak` backup

### Save Editor (.sav)
- Troops: attributes / proficiencies / skills / level / XP / inventory / equipment / bio
- **Checkbox-style `tf_*` flag editing** (kept in sync with the raw hex field)
- Factions: view, reassign, and **full defection** (troop faction + its party's faction)
- **Promote a hero to lord** (optionally granting a centre)
- Parties: view stacks, change owning faction (towns / castles / villages change hands)
- Saving creates a `.sav.bak_<timestamp>` backup

---

## 6. Known limitations — please read

1. **Write permission**: if the module lives under `Program Files`, saving `troops.txt` /
   `item_kinds1.txt` needs **administrator rights** (otherwise it silently fails). Move the
   module to a normal folder, or run as administrator.
2. **Template edits are global**: `troops.txt` / `item_kinds1.txt` changes affect **every save**
   (items immediately, troops on a new game).
3. **Not fully verified in-game**: parsing and write-back are covered by round-trip tests
   (889 items byte-identical, 794 troops round-trip identical), but whether the running game
   fully honours every written value still needs in-game testing — especially:
   - "Promote to lord" does not write troop slot 10 (led_party) or centre slot 7 (town_lord);
     the link relies on party `stack[0]`;
   - the ~4632-byte slot area at the end of party records is not decoded yet, so "which lord
     owns this fief" cannot be rewritten precisely.
4. **Hero matching uses the Chinese bio text** stored in the save. The English build only
   translates the display layer, so matching is unaffected.
5. Placeholder (never instantiated) heroes and UI-menu dummy troops are marked "not generated"
   and refuse editing — a deliberate safeguard.

---

## 7. Files to share

**Required (6)**
- `mb_model.py` — core model (parsing, offsets, path resolution), shared by both tools
- `mb_model_EN.py` — English display-name layer (subclasses `mb_model`; must be copied too)
- `troop_item_viewer_v2_EN.py` / `兵种物品查看器_v2.py`
- `mb_editor_gui_EN.py` / `mb_editor_gui.py`

**Recommended (3)**
- `run_all.py` — launcher
- `README_EN.md` and `README_CN.md`

**Optional**
- `mb_module_path.json` — remembered module folder (auto-created; delete before shipping)
- `骑砍1.011兵种物品查看_旧版参考.py` — the old self-contained viewer, kept for reference only

**Do not copy**: `backups/`, `__pycache__/`, all `*_out.txt`, the research scripts
(`a1…e3`, `b*`, `c*`, `d*`, `step*`), and the test `*.sav` files.
