# -*- coding: utf-8 -*-
"""
板块全量打分 v2（基于 OneDrive 每日全量A股明细，**纯本地、零网络**）
====================================================================
输入：`D:\\OneDrive\\Stock\\details\\全部Ａ股{YYYYMMDD}.xlsx`
产出：`daily/{YYYYMMDD}/板块全量.json`

────────────────────────────────────────────────────────────────────
v2 设计目标（2026-09-21 重设）：**分数必须横向、纵向都可比**

横向可比 = 同一天里，板块之间能直接比
纵向可比 = 同一板块（或不同日子）跨日能直接比

v1 的两个致命缺陷：
  ① **未中性化**：强度维用「板块绝对涨幅」→ 普涨日全场分高、普跌日全场分低。
     实测 09-17 中位涨幅 +0.21% 时 core 板块分数中位 **39.7**；09-18 中位 +1.26% 时中位 **69.8**
     —— 仅因大盘强度差 1.05pct，全场中位摆动 **30 分**，跨日完全不可比。
  ② **固定阈值 + 截断饱和**：`lin(x, a, b)` 超满量程不再加分 → 小样本板块轻易打到 100，
     同日内高分区被"顶格"抹平，横向区分度丢失。

────────────────────────────────────────────────────────────────────
v2 口径：**先截面相对化，再稳健标准化**

四个指标全部**只取当日截面上的相对位置**（绝对水位被自动吸收，即"中性化"）：

| 维度 | 权重 | 指标 | 说明 |
|---|---|---|---|
| 强度 | 30 | 流通市值加权涨幅 `chg_w` | Σ(个股涨幅×流通市值)/Σ流通市值 |
| 资金 | 30 | 主力净额占比 `net_ratio` | Σ主力净额/Σ成交额 |
| 宽度 | 20 | 上涨家数占比 `breadth` | 上涨家数/家数 |
| 量能 | 20 | 放量倍数 `vr` | Σ今额/Σ昨额 |

对每个指标在**当日 core 板块截面**上做**稳健标准化**：

    scale = 1.4826×MAD →（MAD=0 时）IQR/1.349 →（仍为 0 时）标准差 →（仍为 0 时）全体 50 分
    z     = (x − median) / scale
    P(x)  = 100 × Φ(z) = 100 × 0.5 × (1 + erf(z/√2))          ∈ (0,100)

  · **中位值恒为 50**、分布跨日稳定 → 纵向可比 ✓
  · **同日同一标尺、无截断**（用正态 CDF 而非线性截断）→ 横向可比 ✓
  · 用 **MAD/IQR**（而非标准差）→ 对少样本板块的极端值稳健，不再出现"顶格 100" ✓
  · 正态 CDF 保序且**保留幅度**（比纯排名百分位多保留了"离中位多远"的信息）

`score = 0.30·P(chg_w) + 0.30·P(net_ratio) + 0.20·P(breadth) + 0.20·P(vr)`
→ **中位数恒 ≈50**，任何一天都是同一把尺子。

⚠️ 代价（已在看板补偿）：中性化后分数**不再含"当日大盘整体强弱"**，
故顶层新增 `market` 块（当日中位涨幅/中位量能/上涨板块占比/档位），
热力图底部也加一行「当日中位涨幅」→ 整体 β 单独看，不混进板块分数。

阶段定性（结合"相对强"与"绝对方向"，避免把普跌日最强但仍在跌的板块叫"主升"）：
    excess = chg_w − 当日 core 中位涨幅
    excess>0 : ≥80 主升 / 65~80 加速 / 50~65 升温 / 35~50 分歧 / 20~35 修复 / <20 调整
    excess≤0 : ≥65 抗跌 / 35~65 分歧 / 20~35 修复 / <20 调整
────────────────────────────────────────────────────────────────────
用法：
  python scripts/build_sector_full.py --dates 2026-09-17,2026-09-18
  python scripts/build_sector_full.py --range 20260814-20260918 --top 20
  （需 venv：C:\\Users\\xc92\\.workbuddy\\binaries\\python\\envs\\default\\Scripts\\python.exe）
"""
import argparse
import collections
import json
import math
import os
import sys

import openpyxl

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from build_dashboard import normalize_sector_name, STANDARD_SECTOR_POOL  # noqa: E402
from sector_taxonomy import direction_of, DIRECTIONS, coverage_report  # noqa: E402

ROOT = os.path.dirname(HERE)
DAILY = os.path.join(ROOT, "daily")
SRC_DIR = os.environ.get("DETAILS_DATA_DIR", r"D:\OneDrive\Stock\details")

# 板块口径阈值
MIN_STOCKS = 4            # 板块最少家数（<4 家不构成板块，纯噪声）
MIN_CORE_STOCKS = 8       # 「核心板块」最少家数（同时作为标准化参考截面）
MIN_CORE_AMT_YI = 30.0    # 「核心板块」最少成交额（亿元）

# xlsx 列序（0-based）
C_CODE, C_NAME, C_IND = 0, 1, 2
C_CHG, C_AMT, C_PREV_AMT, C_NET, C_FLOATCAP, C_REGION = 4, 6, 7, 8, 9, 22

W_STR, W_FUND, W_BRD, W_VOL = 0.30, 0.30, 0.20, 0.20

# ⛔ 各维「固定尺度」＝ 26 个交易日实测的「每日 MAD 的中位数」（2026-09-21 测定）
#   为什么必须固定：每日自适应的 MAD 日间波动极大 —— 宽度维 5.2x、强度维 3.0x
#   （实测 breadth：紧致日 MAD 0.064 / 剧烈日 0.334）→ 紧致日把中段急剧拉伸，
#   同一指标值在不同日子落到完全不同的分位（同样 55% 涨占比：09-17 分位 62.9 / 09-18 分位 0.6）
#   → 这就是「评分波动过大 / 小动大变」的根源。
SIGMA_FIXED = {"chg_w": 1.1268, "net_ratio": 3.7843, "breadth": 0.1761, "vr": 0.1631}

# 平滑分权重（3 日）：抑制单日噪声
SMOOTH_W = (0.5, 0.3, 0.2)


def num(v):
    try:
        return float(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def load_stocks(day):
    """读当日全量A股 → [{code,name,ind,region,chg,amt,prev_amt,net,cap}]（金额单位：万元）"""
    p = os.path.join(SRC_DIR, f"全部Ａ股{day}.xlsx")
    if not os.path.exists(p):
        return None, p
    wb = openpyxl.load_workbook(p, read_only=True, data_only=True)
    ws = wb[wb.sheetnames[0]]
    it = ws.iter_rows(values_only=True)
    next(it)
    out = []
    for r in it:
        if not r or not r[C_CODE]:
            continue
        out.append({
            "code": str(r[C_CODE]).strip(), "name": r[C_NAME],
            "ind": (r[C_IND] or "未分类").strip(),
            "region": (r[C_REGION] or "未分类").strip(),
            "chg": num(r[C_CHG]), "amt": num(r[C_AMT]), "prev_amt": num(r[C_PREV_AMT]),
            "net": num(r[C_NET]), "cap": num(r[C_FLOATCAP]),
        })
    wb.close()
    return out, p


def aggregate(stocks, key):
    """按 key（细分行业 / 地区）聚合 → {板块名: 分项累加}"""
    g = collections.defaultdict(lambda: {
        "n": 0, "up": 0, "down": 0, "flat": 0, "amt": 0.0, "prev_amt": 0.0,
        "net": 0.0, "cap": 0.0, "wsum": 0.0, "eqsum": 0.0, "raw_names": []})
    for s in stocks:
        b = g[s[key]]
        b["n"] += 1
        c = s["chg"]
        if c is not None:
            if c > 0:
                b["up"] += 1
            elif c < 0:
                b["down"] += 1
            else:
                b["flat"] += 1
            b["eqsum"] += c
            if s["cap"]:
                b["wsum"] += c * s["cap"]
        b["amt"] += s["amt"] or 0.0
        b["prev_amt"] += s["prev_amt"] or 0.0
        b["net"] += s["net"] or 0.0
        b["cap"] += s["cap"] or 0.0
    return g


# ---------------------------------------------------------------- 稳健标准化
def _median(xs):
    xs = sorted(xs)
    if not xs:
        return 0.0
    m = len(xs) // 2
    return xs[m] if len(xs) % 2 else (xs[m - 1] + xs[m]) / 2.0


def _quantile(xs, q):
    xs = sorted(xs)
    if not xs:
        return 0.0
    k = (len(xs) - 1) * q
    lo, hi = int(math.floor(k)), int(math.ceil(k))
    return xs[lo] if lo == hi else xs[lo] * (hi - k) + xs[hi] * (k - lo)


def robust_scale(vals):
    """稳健尺度：1.4826×MAD → IQR/1.349 → 标准差 → 0（由调用方兜底）"""
    vals = [v for v in vals if v is not None]
    if not vals:
        return 0.0, 0.0
    med = _median(vals)
    mad = _median([abs(v - med) for v in vals])
    scale = 1.4826 * mad
    if scale <= 1e-9:
        scale = (_quantile(vals, 0.75) - _quantile(vals, 0.25)) / 1.349
    if scale <= 1e-9:
        n = len(vals)
        mu = sum(vals) / n
        var = sum((v - mu) ** 2 for v in vals) / n
        scale = math.sqrt(var)
    return med, (scale if scale > 1e-9 else 0.0)


def pct_score(x, med, scale):
    """把指标值映射成「当日截面分位（0~100，中位=50）」——稳健正态近似"""
    if x is None or scale <= 0:
        return 50.0
    z = (x - med) / scale
    z = max(-8.0, min(8.0, z))                       # 防极端值把 erf 打飞
    return 100.0 * 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def phase_of(score, excess, vr):
    """阶段定性：兼顾「相对强」与「绝对方向」"""
    if excess > 0:
        if score >= 80:
            return "主升"
        if score >= 65:
            return "加速" if (vr or 1.0) >= 1.0 else "升温"
        if score >= 50:
            return "升温"
        if score >= 35:
            return "分歧"
        if score >= 20:
            return "修复"
        return "调整"
    # 相对强但绝对仍在跌 → 不能说"主升"
    if score >= 65:
        return "抗跌"
    if score >= 35:
        return "分歧"
    if score >= 20:
        return "修复"
    return "调整"


def fund_dir_text(s):
    sign = "净流入" if s["net_yi"] >= 0 else "净流出"
    return (f'{sign} {abs(s["net_yi"]):.1f} 亿（{s["up"]} 涨 {s["down"]} 跌，'
            f'{s["chg_w"]:+.2f}%，超额{s["excess"]:+.2f}%）')


def market_tier(median_chg):
    if median_chg >= 1.5:
        return "普涨"
    if median_chg >= 0.5:
        return "偏强"
    if median_chg > -0.5:
        return "中性"
    if median_chg > -1.5:
        return "偏弱"
    return "普跌"


# ---------------------------------------------------------------- 单日构建
def build_day(day, top_n=0):
    iso = f"{day[:4]}-{day[4:6]}-{day[6:8]}"
    stocks, src = load_stocks(day)
    if stocks is None:
        print(f"  ✗ 缺文件 {src}")
        return None
    print(f"  读入 {len(stocks)} 只个股（{os.path.basename(src)}）")

    # ① 先按「细分行业」聚合，再按 **显式分类表** 归口到 L1 方向
    #    ⛔ 不再用 SECTOR_ALIAS 按名字硬并 —— 那会产生"名字与成分不符"（光通信/CPO 只含光纤光缆）
    #       与"乱并"（锂电池被并进金属/贵金属/稀土）。分类表见 scripts/sector_taxonomy.py
    by_ind = aggregate(stocks, "ind")
    merged = collections.defaultdict(lambda: {
        "n": 0, "up": 0, "down": 0, "flat": 0, "amt": 0.0, "prev_amt": 0.0,
        "net": 0.0, "cap": 0.0, "wsum": 0.0, "eqsum": 0.0, "raw_names": []})
    members = collections.defaultdict(list)
    unknown_ind = []
    for raw_name, b in by_ind.items():
        d = direction_of(raw_name)
        if d is None:
            unknown_ind.append(raw_name)
            d = ("未归类·" + raw_name) if raw_name else "未分类"
        m = merged[d]
        for f in ("n", "up", "down", "flat", "amt", "prev_amt", "net", "cap", "wsum", "eqsum"):
            m[f] += b[f]
        m["raw_names"].append(raw_name)
        # L2 明细：该方向内部各细分行业的读数（用于下钻「今天是谁在动」）
        _n = b["n"]
        members[d].append({
            "name": raw_name, "n": _n, "up": b["up"], "down": b["down"],
            "chg_w": round((b["wsum"] / b["cap"]) if b["cap"] else (b["eqsum"] / _n), 2),
            "net_yi": round(b["net"] / 1e4, 2), "amt_yi": round(b["amt"] / 1e4, 2),
        })

    # ② 原始指标
    rows = []
    for name, b in merged.items():
        if b["n"] < MIN_STOCKS:
            continue
        n = b["n"]
        chg_w = (b["wsum"] / b["cap"]) if b["cap"] else (b["eqsum"] / n)
        amt_yi, prev_yi, net_yi = b["amt"] / 1e4, b["prev_amt"] / 1e4, b["net"] / 1e4
        rows.append({
            "name": name, "n": n, "up": b["up"], "down": b["down"],
            "raw_names": sorted(b["raw_names"]),
            "chg_w": chg_w, "amt_yi": amt_yi, "prev_amt_yi": prev_yi, "net_yi": net_yi,
            "vr": (b["amt"] / b["prev_amt"]) if b["prev_amt"] else None,
            "breadth": b["up"] / n,
            "net_ratio": (b["net"] / b["amt"] * 100.0) if b["amt"] else 0.0,
        })

    # ③ 分层：core 作为标准化参考截面
    for r in rows:
        r["tier"] = ("core" if (r["n"] >= MIN_CORE_STOCKS and r["amt_yi"] >= MIN_CORE_AMT_YI)
                     else "small")
    core = [r for r in rows if r["tier"] == "core"] or rows
    # 中性化仍按「当日中位」；尺度取「固定尺度」与「当日尺度」的较大者：
    #   紧致日 → 用固定尺度，不放大（消除"小动大变"）
    #   剧烈日 → 用当日尺度，如实拉开（保留真实离散度）
    ref = {}
    for k in ("chg_w", "breadth", "net_ratio", "vr"):
        _med, _scale = robust_scale([r[k] for r in core])
        ref[k] = (_med, max(SIGMA_FIXED[k], _scale))

    # ④ 截面分位 → 加权打分（中位≈50，跨日稳定 ⇒ 横向 & 纵向可比）
    for r in rows:
        r["p_str"] = round(pct_score(r["chg_w"], *ref["chg_w"]), 1)
        r["p_fund"] = round(pct_score(r["net_ratio"], *ref["net_ratio"]), 1)
        r["p_brd"] = round(pct_score(r["breadth"], *ref["breadth"]), 1)
        r["p_vol"] = round(pct_score(r["vr"], *ref["vr"]), 1)
        r["score"] = round(W_STR * r["p_str"] + W_FUND * r["p_fund"]
                           + W_BRD * r["p_brd"] + W_VOL * r["p_vol"], 1)

    med_chg = _median([r["chg_w"] for r in core])
    for r in rows:
        r["excess"] = round(r["chg_w"] - med_chg, 2)
        r["chg_w"] = round(r["chg_w"], 2)
        r["amt_yi"] = round(r["amt_yi"], 2)
        r["prev_amt_yi"] = round(r["prev_amt_yi"], 2)
        r["net_yi"] = round(r["net_yi"], 2)
        r["vr"] = round(r["vr"], 2) if r["vr"] is not None else None
        r["breadth"] = round(r["breadth"], 3)
        r["net_ratio"] = round(r["net_ratio"], 2)

    # 附 L2 明细（按成交额降序）
    for r in rows:
        r["members"] = sorted(members.get(r["name"], []), key=lambda x: -x["amt_yi"])
    rows.sort(key=lambda x: -x["score"])
    for i, r in enumerate(rows, 1):
        r["rank"] = i
        r["phase"] = phase_of(r["score"], r["excess"], r["vr"])
        r["fund_dir"] = fund_dir_text(r)
    # ⚠️ core 是排序前构建的（用于算标准化参考截面，与顺序无关）
    #    → 打印 TOP 必须用排序后的顺序重建，否则打印出来的不是最高分
    core_sorted = [r for r in rows if r["tier"] == "core"]
    # ⚠️ core 是在排序前构建的 → 打印 TOP 必须用排序后的顺序重建（否则打印的不是最高分）
    core_sorted = [r for r in rows if r["tier"] == "core"]

    # ⑤ 地区维度（同口径）
    regions = []
    for raw_name, b in aggregate(stocks, "region").items():
        if b["n"] < MIN_STOCKS:
            continue
        n = b["n"]
        chg_w = (b["wsum"] / b["cap"]) if b["cap"] else (b["eqsum"] / n)
        regions.append({"name": raw_name, "n": n, "up": b["up"], "down": b["down"],
                        "chg_w": round(chg_w, 2), "amt_yi": round(b["amt"] / 1e4, 2),
                        "net_yi": round(b["net"] / 1e4, 2),
                        "breadth": round(b["up"] / n, 3),
                        "score": round(pct_score(chg_w, *ref["chg_w"]), 1)})
    regions.sort(key=lambda x: -x["score"])

    # ⑥ 市场 β 块：中性化后被剥离的「整体强弱」单独呈现
    med_vr = _median([r["vr"] for r in core if r["vr"] is not None] or [1.0])
    up_ratio = (sum(1 for r in core if r["chg_w"] > 0) / len(core)) if core else 0.0
    market = {
        "median_chg": round(med_chg, 2),
        "median_vr": round(med_vr, 2),
        "up_board_ratio": round(up_ratio, 3),
        "tier": market_tier(med_chg),
        "note": "中性化后被剥离的当日整体强弱，单独看，不混进板块分数",
    }

    out = {
        "date": iso, "schema": "板块全量打分 v2（截面相对化）",
        "source": "OneDrive 全部Ａ股每日明细（本地 xlsx，零网络）",
        "universe_stocks": len(stocks),
        "boards_industry": len(rows), "boards_core": len(core),
        "boards_region": len(regions),
        "market": market,
        "score_rule": ("四指标各取当日 core 截面分位"
                       "（稳健 z=1.4826×MAD，退化用 IQR/标准差 → 正态CDF，中位恒 50），"
                       "再按 强度30/资金30/宽度20/量能20 加权 → 横向与纵向均可比"),
        "ref_scale": {k: {"median": round(v[0], 4), "scale": round(v[1], 4)}
                      for k, v in ref.items()},
        "thresholds": {"min_stocks": MIN_STOCKS, "core_stocks": MIN_CORE_STOCKS,
                       "core_amt_yi": MIN_CORE_AMT_YI},
        "standard_pool_hit": sorted({r["name"] for r in rows if r["name"] in STANDARD_SECTOR_POOL}),
        "unknown_industries": sorted(set(unknown_ind)),
        "boards": rows, "regions": regions,
    }
    dst_dir = os.path.join(DAILY, day)
    os.makedirs(dst_dir, exist_ok=True)
    dst = os.path.join(dst_dir, "板块全量.json")
    json.dump(out, open(dst, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    med_score = _median([r["score"] for r in core])
    print(f"  → {len(rows)} 个行业板块（家数≥{MIN_STOCKS}，核心 {len(core)} 个）+ {len(regions)} 个地区板块")
    print(f"  → 市场β: 中位涨幅 {med_chg:+.2f}% [{market['tier']}] · 中位量能 {med_vr:.2f}x"
          f" · 上涨板块占比 {up_ratio:.0%}")
    print(f"  → 分数中位 {med_score:.1f}（v2 目标 ≈50，跨日稳定 ⇒ 可比）")
    _unk = sorted({x for x in unknown_ind if x and x != "未分类"})
    if _unk:
        print(f"  ⚠️ 未归类细分行业 {len(_unk)} 个（须补进 scripts/sector_taxonomy.py）：{_unk[:8]}")
    print(f"  → 落盘 {dst}")
    if top_n:
        print(f"  ── 核心板块 TOP{top_n} ──")
        for r in core_sorted[:top_n]:
            print(f"   {r['rank']:>3}. {r['score']:>5} {r['name']:<15}{r['n']:>4}家 "
                  f"涨{r['chg_w']:+.2f}%(超额{r['excess']:+.2f}%) 净{r['net_yi']:+.1f}亿 "
                  f"占比{r['net_ratio']:+.2f}% 量比{r['vr']} 涨占{r['breadth']:.0%} [{r['phase']}]")
        print("  ── 末 5 位 ──")
        for r in rows[-5:]:
            print(f"   {r['rank']:>3}. {r['score']:>5} {r['name']:<15}{r['n']:>4}家 "
                  f"涨{r['chg_w']:+.2f}%(超额{r['excess']:+.2f}%) 净{r['net_yi']:+.1f}亿 [{r['phase']}]")
    return out


def smooth_scores(days):
    """给指定交易日补 `score_smooth` = 0.5×当日 + 0.3×前1日 + 0.2×前2日。

    作用：抑制单日噪声（板块分数不再"一天暴涨一天暴跌"），纵向更可比。
    历史日从磁盘读（`daily/*/板块全量.json`）→ 单日跑也能算；缺日按可用权重归一。
    """
    all_days = sorted(d for d in os.listdir(DAILY)
                      if len(d) == 8 and d.isdigit()
                      and os.path.exists(os.path.join(DAILY, d, "板块全量.json")))
    cache = {}

    def load(d):
        if d not in cache:
            j = json.load(open(os.path.join(DAILY, d, "板块全量.json"), encoding="utf-8"))
            cache[d] = {b["name"]: b.get("score") for b in j.get("boards") or []}
        return cache[d]

    done = []
    for d in days:
        if d not in all_days:
            continue
        i = all_days.index(d)
        hist = [all_days[k] for k in range(i, max(-1, i - 3), -1)]      # 当日 + 前两日
        p = os.path.join(DAILY, d, "板块全量.json")
        j = json.load(open(p, encoding="utf-8"))
        for b in j["boards"]:
            num = den = 0.0
            for w, hd in zip(SMOOTH_W, hist):
                sc = load(hd).get(b["name"])
                if sc is not None:
                    num += w * sc
                    den += w
            b["score_smooth"] = round(num / den, 1) if den else None
        j["smooth_rule"] = ("score_smooth = 0.5×当日 + 0.3×前1日 + 0.2×前2日"
                            "（缺日按可用权重归一）；用于抑制单日噪声")
        json.dump(j, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        done.append(d)
    if done:
        print(f"  score_smooth 已写入 {len(done)} 个交易日（{done[0]} ~ {done[-1]}）")
    return done


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dates", help="逗号分隔，如 2026-09-17,2026-09-18")
    ap.add_argument("--range", help="日期区间 YYYYMMDD-YYYYMMDD（扫描本地已有 xlsx，逐日批跑）")
    ap.add_argument("--top", type=int, default=0)
    args = ap.parse_args()

    days = []
    if args.range:
        a, b = args.range.split("-")
        pref, suf = "全部Ａ股", ".xlsx"
        for f in os.listdir(SRC_DIR):
            if f.startswith(pref) and f.endswith(suf):
                d8 = f[len(pref):-len(suf)]
                if d8.isdigit() and a <= d8 <= b:
                    days.append(d8)
        days.sort()
        if not days:
            print(f"区间 {args.range} 内未找到本地文件")
            return 1
    elif args.dates:
        days = [d.strip().replace("-", "") for d in args.dates.split(",") if d.strip()]
    else:
        ap.error("需给 --dates 或 --range")

    print(f"共 {len(days)} 个交易日：{days[0]} → {days[-1]}")
    summary = []
    for d8 in days:
        print("=" * 78)
        print(f"【{d8}】")
        r = build_day(d8, top_n=args.top)
        if r:
            summary.append((d8, r["market"]["tier"], r["market"]["median_chg"],
                            _median([b["score"] for b in r["boards"] if b["tier"] == "core"])))
    print("=" * 78)
    print("【平滑】补 score_smooth（3 日加权，抑制单日噪声）")
    smooth_scores(days)
    if len(summary) > 1:
        print("=" * 78)
        print("【可比性自检】核心板块分数中位应稳定在 50 附近（v1 会随大盘在 40~70 漂移）")
        for d8, tier, mc, ms in summary:
            print(f"   {d8}  β={mc:+6.2f}% [{tier:<2}]  分数中位 {ms:5.1f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
