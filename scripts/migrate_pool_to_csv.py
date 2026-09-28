# -*- coding: utf-8 -*-
"""把 席位跟踪池.json 迁移为 席位跟踪池.csv（唯一源）。

用户决策（09-07）：
1. exclude 中「外资投行 11 家 + 券商总部/机构 7 条」= 量化，迁入新组 quant（量化）
2. 「量化打板-开源」从 daily 移入 quant
3. JSON 删除，CSV 唯一源（UTF-8 BOM，Excel 可直接编辑）

CSV schema（表头固定，Excel 编辑勿动第一行）：
  group, id, depts(多个用|分隔), style, focus, shared(可空)
group 取值：quant/daily/node/watch/deprecated/exclude
exclude 仅需 group,id,depts（id=depts=原剔除关键词）
"""
import json, csv, os, sys

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "席位跟踪池.json")
DST = os.path.join(ROOT, "席位跟踪池.csv")

QUANT_FOREIGN = ["高盛", "摩根大通", "瑞银", "瑞信", "野村", "摩根士丹利",
                 "法国巴黎", "汇丰", "花旗", "巴克莱", "德意志"]
QUANT_HQ = ["国泰海通证券总部", "华泰证券总部", "中信证券总部", "中信证券上海分公司",
            "中国国际金融上海分公司", "中金公司上海分公司", "国泰海通证券上海自贸试验区第二分公司"]
QUANT_KEYWORDS = QUANT_FOREIGN + QUANT_HQ

p = json.load(open(SRC, encoding="utf-8"))

rows = []
# --- quant 组：外资投行 + 券商总部，统一 style/focus 模板，可后续在 CSV 里改 ---
q_style = "外资/机构量化通道（QFII 对冲、指数增强等程序化资金）"
q_focus = "看量化集体行为：与游资席位是否同票、封板率、进出节奏"
for kw in QUANT_KEYWORDS:
    rows.append({"group": "quant", "id": kw.replace("证券总部", "总部").replace("分公司", "").strip() or kw,
                 "depts": kw, "style": q_style, "focus": q_focus, "shared": ""})

# daily 中的 量化打板-开源 移入 quant
moved = []
for it in p.get("daily", []):
    if it["id"] == "量化打板-开源":
        rows.append({"group": "quant", "id": "量化打板-开源",
                     "depts": "|".join(it.get("depts", [])),
                     "style": it.get("style", ""), "focus": it.get("focus", ""), "shared": ""})
        moved.append(it["id"])

# --- daily / node / watch / deprecated 原样迁入 ---
for g in ("daily", "node", "watch", "deprecated"):
    for it in p.get(g, []):
        if it["id"] == "量化打板-开源":
            continue  # 已移入 quant
        rows.append({"group": g, "id": it["id"],
                     "depts": "|".join(it.get("depts", [])),
                     "style": it.get("style") or it.get("note", ""),
                     "focus": it.get("focus", ""), "shared": it.get("shared", "")})

# --- exclude 剩余（剔除词保留：机构专用/散户通道/互联网等） ---
for kw in p.get("exclude", []):
    if kw in QUANT_KEYWORDS:
        continue  # 已迁入 quant
    rows.append({"group": "exclude", "id": kw, "depts": kw,
                 "style": "", "focus": "", "shared": ""})

# 写 CSV（UTF-8 BOM）
with open(DST, "w", encoding="utf-8-sig", newline="") as f:
    w = csv.DictWriter(f, fieldnames=["group", "id", "depts", "style", "focus", "shared"])
    w.writeheader()
    for r in rows:
        w.writerow(r)

# 统计
from collections import Counter
cnt = Counter(r["group"] for r in rows)
print("CSV 已生成:", DST)
for g, n in cnt.items():
    print(f"  {g}: {n}")
print("量化组明细:")
for r in rows:
    if r["group"] == "quant":
        print(f"   {r['id']}  <- {r['depts']}")
