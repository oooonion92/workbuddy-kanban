# -*- coding: utf-8 -*-
"""
涨停质量分（情绪定位 · 连板梯队）
==================================
对当日**每只涨停股**逐票打分（0-100），用于情绪定位与连板梯队排序。

数据源（**全部本地，零网络**）：
  · 涨停池：`D:\\OneDrive\\Stock\\短线数据采集\\{YYYYMMDD}\\zt_pool.csv`
    字段：代码/名称/涨跌幅/成交额/流通市值/换手率/**封板资金**/**首次封板时间**/
          **最后封板时间**/**炸板次数**/涨停统计/**连板数**/所属行业
  · 大单：`D:\\OneDrive\\Stock\\details\\全部Ａ股{YYYYMMDD}.xlsx`（主力净额、成交额、细分行业）
  · 板块：`daily/{YYYYMMDD}/板块全量.json`（L1 方向景气分）
  产出：`daily/{YYYYMMDD}/涨停质量.json`

────────────────────────────────────────────────────────────────────
打分口径（0-100，**绝对尺度**，五维）

| 维度 | 权重 | 指标 | 满量程 / 判据 |
|---|---|---|---|
| **封板时间** | 25 | `首次封板时间` | 09:25 竞价一字 = 25；09:30 = 23；10:00 = 19；11:30 = 12；13:30 = 8；14:30 = 3；15:00 = 0 |
| **封板牢固度** | 25 | ①`炸板次数`(15) ②封单强度 = `封板资金`/`流通市值`(10) | 炸板 0/1/2/≥3 次 → 15/7/3/0；封单强度 3% 满量程 |
| **大单流入** | 20 | `主力净额`/`成交额` | ±5% 满量程（与板块打分同尺度） |
| **板块共振** | 20 | ①所属 L1 方向当日涨停家数(12) ②该方向景气分(8) | 家数 1→0、8→12；景气分 30→0、70→8 |
| **量能结构** | 10 | `换手率`（钟形） | 5%~20% = 满分；<2% 没换手易开板；>35% 分歧/出货 |

⛔ **为什么用绝对尺度而非截面分位**：封板时间、炸板次数、封单强度本身就有**绝对语义**
（09:25 一字板永远是高质量板），不像板块涨跌幅需要中性化。绝对尺度天然跨日可比，
且当日离散度如实呈现。仍另给「当日基准」（全市场涨停股的均分/中位分）供横向参照。

**另附标签（不计分，但决定参与策略）**：
  · `level` 连板身位（首板/2板/3板/高位板）—— **质量 ≠ 风险**，单列
  · `一字板`（**集合竞价 09:25 即封**，首封 ≤ 09:25:59 且全天未开板）、
    `开盘秒板`（09:30:00~09:34:59 首封且未开板 —— **不是一字板**，2026-09-21 用户实证修正）、
    `尾盘板`（14:30 后首封）、`高换手`（>30%）
"""
import argparse
import collections
import csv
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from sector_taxonomy import direction_of  # noqa: E402

ROOT = os.path.dirname(HERE)
DAILY = os.path.join(ROOT, "daily")
POOL_DIR = os.environ.get("SHORT_TERM_DATA_DIR", r"D:\OneDrive\Stock\短线数据采集")
XLSX_DIR = os.environ.get("DETAILS_DATA_DIR", r"D:\OneDrive\Stock\details")

W_TIME, W_SEAL, W_FUND, W_RESO, W_TURN = 25, 25, 20, 20, 10

# 首次封板时间 → 分（trading minutes since 09:30 → 分）
TIME_BREAKPOINTS = [(-5, 25), (0, 23), (30, 19), (120, 12), (150, 8), (210, 3), (240, 0)]
SEAL_STRENGTH_FULL = 0.03          # 封单/流通市值 3% 满量程
FUND_FULL = 0.05                   # 主力净额/成交额 ±5% 满量程
RESO_N_ZT_FULL = 8                 # 方向涨停家数 8 家满量程
RESO_SCORE_LO, RESO_SCORE_HI = 30, 70


def clamp01(x):
    return 0.0 if x < 0 else (1.0 if x > 1 else x)


def _f(v, default=None):
    try:
        return float(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return default


def trade_minutes(hhmmss):
    """'092500' → 自 09:30 起的交易分钟数（09:25 竞价 = -5；11:30/13:00 = 120）"""
    s = (hhmmss or "").strip()
    if len(s) != 6 or not s.isdigit():
        return None
    m = int(s[:2]) * 60 + int(s[2:4])
    o = 9 * 60 + 30
    if m <= o:
        return m - o
    if m <= 11 * 60 + 30:
        return m - o
    if m <= 13 * 60:
        return 120
    if m <= 15 * 60:
        return 120 + (m - 13 * 60)
    return 240


# ---------- 形态标签（不计分，但决定参与策略）----------
# ⛔ 2026-09-21 修正（用户实证）：**一字板判据必须精确到秒**。
#    原实现用 `trade_minutes(first_time) <= 0`，而该函数**丢弃秒**（只取 hh*60+mm）→
#    09:30:00 ~ 09:30:59 首次封板的「**开盘秒板**」全被误标为「一字板」。
#    实证被误标：华软科技 002453（09:30:00）、马矿股份 601123（09:30:03）、
#                新华制药 000756（09:30:51）、奥佳华 002614（09:30:00）、美盈森 002303（09:30:15）。
#    正确语义：**一字板 ＝ 集合竞价（09:25）即封涨停**（＝开盘价即涨停价），
#    故判据为 `首封 ≤ 09:25:59` 且全天未开板；09:30 之后封的属「开盘秒板」，两者量级完全不同。
YIZI_MAX = "092559"        # 首封 ≤ 09:25:59 → 竞价即封
MIAOBAN_RANGE = ("093000", "093459")   # 09:30:00 ~ 09:34:59 首封 → 开盘秒板


def is_yizi(first_time, opened):
    """一字板：集合竞价即封（首封 ≤ 09:25:59）且全天未开板。"""
    s = (first_time or "").strip()
    return len(s) == 6 and s.isdigit() and s <= YIZI_MAX and (opened or 0) == 0


def is_miaoban(first_time, opened):
    """开盘秒板：09:30:00~09:34:59 首次封板且未开板（开盘后 5 分钟内封死，非一字板）。"""
    s = (first_time or "").strip()
    return (len(s) == 6 and s.isdigit()
            and MIAOBAN_RANGE[0] <= s <= MIAOBAN_RANGE[1] and (opened or 0) == 0)


def interp(x, br):
    """分段线性插值（br 按 x 升序）"""
    if x is None:
        return 0.0
    if x <= br[0][0]:
        return float(br[0][1])
    if x >= br[-1][0]:
        return float(br[-1][1])
    for (x0, y0), (x1, y1) in zip(br, br[1:]):
        if x0 <= x <= x1:
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return 0.0


def s_time(hhmmss):
    return round(interp(trade_minutes(hhmmss), TIME_BREAKPOINTS), 2)


def s_seal(opened, strength):
    a = {0: 15.0, 1: 7.0, 2: 3.0}.get(int(opened or 0), 0.0)
    b = 10.0 * clamp01((strength or 0) / SEAL_STRENGTH_FULL)
    return round(a + b, 2)


def s_fund(main_ratio):
    if main_ratio is None:
        return 10.0
    return round(W_FUND * clamp01((main_ratio + FUND_FULL) / (2 * FUND_FULL)), 2)


def s_reso(n_zt, dir_score):
    a = 12.0 * clamp01(((n_zt or 1) - 1) / (RESO_N_ZT_FULL - 1))
    b = 0.0 if dir_score is None else 8.0 * clamp01((dir_score - RESO_SCORE_LO) / (RESO_SCORE_HI - RESO_SCORE_LO))
    return round(a + b, 2)


def s_turn(t):
    """换手率钟形：5%~20% 满分"""
    if t is None:
        return 5.0
    if t < 2:
        return 2.0
    if t < 5:
        return round(2 + 8 * (t - 2) / 3, 2)
    if t <= 20:
        return 10.0
    if t <= 35:
        return round(10 - 8 * (t - 20) / 15, 2)
    return 1.0


def level_of(lbc):
    lbc = int(lbc or 0)
    if lbc <= 1:
        return "首板"
    if lbc == 2:
        return "2板"
    if lbc == 3:
        return "3板"
    return "高位板"


def load_zt(day):
    p = os.path.join(POOL_DIR, day, "zt_pool.csv")
    if not os.path.isfile(p):
        return None, p
    with open(p, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f)), p


# ══════════════════════════════════════════════════════════════════════
# 首板体系接入（与 skill `first-board-auction-score` **同源同口径**）
# ──────────────────────────────────────────────────────────────────────
# 首板有**专属冻结模型**（六因子逻辑回归 → 首板晋级分），与 2 板及以上用的
# 「绝对尺度五维质量分」**不是同一套口径**：
#   · 质量分 = 封板质量（封板时间/牢固度/大单/板块共振/量能），对所有身位通用、跨日可比
#   · 首板晋级分 = 100×六因子逻辑回归概率，**只对首板**，用来排次日一进二候选
# 为「两边体系一致」，看板首板分区直接读 skill 的 eod 产物，**不在本脚本重算**。
#
# 目录约定：`daily/{交易日 D}/first_board/{D}_eod/`（D 收盘后产出）
# 参考：`C:\Users\xc92\.workbuddy\skills\first-board-auction-score\`
FB_DIRNAME = "first_board"


def _bool(v):
    return str(v).strip().lower() in ("true", "1", "1.0")


def load_first_board(day):
    """读 1J2 skill 的收盘评分产物；缺失返回 None（看板自动回退到只显示质量分）。"""
    d = os.path.join(DAILY, day, FB_DIRNAME, f"{day}_eod")
    state_p = os.path.join(d, "eod_state.csv")
    if not os.path.isfile(state_p):
        return None
    meta = {}
    mp = os.path.join(d, "manifest.json")
    if os.path.isfile(mp):
        try:
            meta = json.load(open(mp, encoding="utf-8")) or {}
        except Exception:                                   # noqa: BLE001
            meta = {}
    rows = []
    with open(state_p, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            _sc, _eo = _f(r.get("首板晋级分")), _f(r.get("预期开盘%"))
            _lo, _hi = _f(r.get("常态下界%")), _f(r.get("常态上界%"))
            rows.append({
                "code": (r.get("代码") or "").strip(),
                "name": (r.get("名称") or "").strip(),
                # 展示精度统一到 2 位（与 skill 的 CSV 同值，仅去尾数）
                "score": (round(_sc, 2) if _sc is not None else None),
                "rank": int(_f(r.get("首板排名"), 0) or 0),
                "top20": _bool(r.get("每日前20%")),
                "expect_open": (round(_eo, 2) if _eo is not None else None),
                "band_lo": (round(_lo, 2) if _lo is not None else None),
                "band_hi": (round(_hi, 2) if _hi is not None else None),
                "capped": _bool(r.get("价格上限约束")),
                "lbc": int(_f(r.get("连板数"), 0) or 0),
                "industry": (r.get("所属行业") or "").strip() or None,
            })
    excl = []
    ep = os.path.join(d, "排除明细.csv")
    if os.path.isfile(ep):
        with open(ep, encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                excl.append({"code": (r.get("代码") or "").strip(),
                             "name": (r.get("名称") or "").strip(),
                             "reason": (r.get("排除原因") or "").strip()})
    rows.sort(key=lambda x: x["rank"])
    sc = [x["score"] for x in rows if x["score"] is not None]
    eo = sorted(x["expect_open"] for x in rows if x["expect_open"] is not None)
    top = [x for x in rows if x["top20"]]
    return {
        "source_dir": os.path.relpath(d, ROOT).replace("\\", "/"),
        "model": meta.get("version"), "model_sha256": meta.get("model_sha256"),
        "signal_date": meta.get("signal_date"), "evaluation_date": meta.get("evaluation_date"),
        "n": len(rows), "n_top20": len(top), "n_excluded": len(excl),
        "score_max": (round(max(sc), 2) if sc else None),
        "score_median": (round(sc[len(sc) // 2], 2) if sc else None),
        "expect_open_median": (round(eo[len(eo) // 2], 2) if eo else None),
        "score_rule": ("首板晋级分 = 100 × 六因子逻辑回归概率"
                       "（一字状态 / 早封 / 末封 / 封单占成交 / 封单占流通 / 炸板次数）"),
        "excluded_detail": excl,
        "rows": rows,
    }


def verify_first_board_consistency(zt_level_codes, fb):
    """一致性自检：涨停池「首板」集合 vs 1J2「合格首板」集合。"""
    zt = set(zt_level_codes)
    fb_codes = {x["code"] for x in fb["rows"]}
    only_zt = sorted(zt - fb_codes)        # 在涨停池首板里但未被 1J2 评分 → 应为非主板/特殊
    only_fb = sorted(fb_codes - zt)        # 被 1J2 评分但不在涨停池首板 → 必须为空
    return only_zt, only_fb


def load_main_net(day):
    """从本地全量包取 主力净额/成交额(占比) 与 细分行业，按代码索引"""
    p = os.path.join(XLSX_DIR, f"全部Ａ股{day}.xlsx")
    if not os.path.isfile(p):
        return {}
    try:
        import openpyxl
        wb = openpyxl.load_workbook(p, read_only=True, data_only=True)
    except Exception:                                # noqa: BLE001
        return {}
    ws = wb[wb.sheetnames[0]]
    it = ws.iter_rows(values_only=True)
    next(it)
    out = {}
    for r in it:
        if not r or not r[0]:
            continue
        amt, net = _f(r[6]), _f(r[8])
        out[str(r[0]).strip()] = {
            "ind": (str(r[2]).strip() if r[2] else None),
            "main_ratio": (net / amt if amt else None),
        }
    wb.close()
    return out


def build_day(day, top_n=0):
    iso = f"{day[:4]}-{day[4:6]}-{day[6:8]}"
    rows, src = load_zt(day)
    if rows is None:
        print(f"  ✗ 缺文件 {src}")
        return None
    print(f"  读入涨停池 {len(rows)} 只（{os.path.basename(src)}）")
    main = load_main_net(day)
    print(f"  本地全量包索引 {len(main)} 只" + ("" if main else "（⚠️ 缺，大单维度记 0）"))

    # 板块景气分（按 L1 方向）
    dir_score = {}
    pj = os.path.join(DAILY, day, "板块全量.json")
    # ⛔ 静默降级禁令（2026-09-23 事故固化）：缺此文件会让「方向景气分(8 分)」子项**全池归零**，
    #    质量分被整体压低约 6 分，且旧实现**不打任何日志** → 极易与真实评分混淆。
    #    故此处改为**显式失败**，并打印正确的执行顺序。
    if not os.path.isfile(pj):
        print(f"  ✗ 缺文件 {pj}")
        print(f"    ⛔ 该文件是「板块共振 20 分」中『方向景气分 8 分』的唯一来源；缺失会使全池质量分静默偏低。")
        print(f"    → 必须**先**跑：python scripts/build_sector_full.py --dates {day}")
        print(f"    → **再**跑本脚本（两条链路必须串行，严禁并行）。")
        return None
    j = json.load(open(pj, encoding="utf-8"))
    dir_score = {b["name"]: b.get("score_smooth", b.get("score")) for b in j.get("boards") or []}
    print(f"  板块景气分索引 {len(dir_score)} 个 L1 方向")
    if not dir_score:
        print(f"    ⚠️ 板块全量.json 的 boards 为空 → 方向景气分将全池归零")

    stocks = []
    for r in rows:
        code = (r.get("代码") or "").strip()
        x = main.get(code) or {}
        ind = x.get("ind") or (r.get("所属行业") or None)
        d = direction_of(ind) or (ind or "未分类")
        seal_amt = _f(r.get("封板资金"), 0) or 0
        fcap = _f(r.get("流通市值"), 0) or 0
        strength = (seal_amt / fcap) if fcap else None
        stocks.append({
            "code": code, "name": (r.get("名称") or "").strip(),
            "chg": _f(r.get("涨跌幅")), "close": _f(r.get("最新价")),
            "lbc": int(_f(r.get("连板数"), 0) or 0),
            "level": level_of(r.get("连板数")),
            "first_time": r.get("首次封板时间"), "last_time": r.get("最后封板时间"),
            "opened": int(_f(r.get("炸板次数"), 0) or 0),
            "seal_yi": round(seal_amt / 1e8, 2),
            "seal_strength_pct": (round(strength * 100, 3) if strength is not None else None),
            "turnover": (round(_f(r.get("换手率")), 2) if _f(r.get("换手率")) is not None else None),
            "ind": ind, "direction": d,
            "main_ratio_pct": (round((x.get("main_ratio") or 0) * 100, 2) if x else None),
        })

    # 方向共振：当日各 L1 方向的涨停家数
    n_zt_by_dir = collections.Counter(s["direction"] for s in stocks)

    for s in stocks:
        d_time = s_time(s["first_time"])
        d_seal = s_seal(s["opened"], (s["seal_strength_pct"] or 0) / 100 if s["seal_strength_pct"] is not None else None)
        d_fund = s_fund((s["main_ratio_pct"] / 100) if s["main_ratio_pct"] is not None else None)
        d_reso = s_reso(n_zt_by_dir[s["direction"]], dir_score.get(s["direction"]))
        d_turn = s_turn(s["turnover"])
        s["dims"] = {"封板时间": d_time, "封板牢固度": d_seal, "大单流入": d_fund,
                     "板块共振": d_reso, "量能结构": d_turn}
        s["score"] = round(d_time + d_seal + d_fund + d_reso + d_turn, 1)
        s["dir_zt_n"] = n_zt_by_dir[s["direction"]]
        s["dir_score"] = dir_score.get(s["direction"])
        _tm = trade_minutes(s["first_time"])
        s["flags"] = (["一字板"] if is_yizi(s["first_time"], s["opened"]) else []) \
            + (["开盘秒板"] if is_miaoban(s["first_time"], s["opened"]) else []) \
            + (["尾盘板"] if (_tm is not None and _tm >= 210) else []) \
            + (["高换手"] if (s["turnover"] or 0) > 30 else [])

    stocks.sort(key=lambda x: -x["score"])
    for i, s in enumerate(stocks, 1):
        s["rank"] = i

    # ⭐ 方向景气分覆盖率自检（2026-09-23 新增，防静默降级）
    #    正常情况：绝大多数涨停股所属 L1 方向都能在 板块全量.json 里命中 → dir_score 非 None。
    #    若覆盖率 = 0，说明「板块共振」的 8 分子项全池丢失、质量分被系统性压低 → 必须显式告警。
    _cov = sum(1 for s in stocks if s.get("dir_score") is not None)
    if stocks and _cov == 0:
        print(f"    ⚠️ 提取告警：方向景气分覆盖 0/{len(stocks)} —— 『板块共振』8 分子项全池归零，质量分被系统性压低！")
        print(f"       → 检查 daily/{day}/板块全量.json 的方向名与 sector_taxonomy 是否一致")
    elif stocks and _cov < len(stocks) * 0.8:
        print(f"    ⚠️ 方向景气分覆盖偏低：{_cov}/{len(stocks)}（<80%）→ 部分涨停股所属方向未归口")
    else:
        print(f"  方向景气分覆盖 {_cov}/{len(stocks)}")

    sc = [s["score"] for s in stocks]
    med = sorted(sc)[len(sc) // 2] if sc else 0
    # 分连板身位聚合
    by_level = {}
    for lv in ("高位板", "3板", "2板", "首板"):
        g = [s for s in stocks if s["level"] == lv]
        if not g:
            continue
        gs = sorted([s["score"] for s in g])
        by_level[lv] = {
            "家数": len(g), "均分": round(sum(gs) / len(gs), 1), "中位": gs[len(gs) // 2],
            "最高": {"name": g[0]["name"], "score": g[0]["score"]},
            "最低": {"name": g[-1]["name"], "score": g[-1]["score"]},
        }

    # ── 首板体系接入：把 1J2 冻结模型的「首板晋级分」贴到首板个股上 ──
    fb = load_first_board(day)
    if fb:
        fbm = {x["code"]: x for x in fb["rows"]}
        for s in stocks:
            f = fbm.get(s["code"])
            if f:
                s["fb_score"] = f["score"]
                s["fb_rank"] = f["rank"]
                s["fb_top20"] = f["top20"]
                s["fb_expect_open"] = f["expect_open"]
                s["fb_band"] = [f["band_lo"], f["band_hi"]]
        zt_first = [s["code"] for s in stocks if s["level"] == "首板"]
        only_zt, only_fb = verify_first_board_consistency(zt_first, fb)
        fb["only_in_zt"] = only_zt
        fb["only_in_fb"] = only_fb

    out = {
        "date": iso, "schema": "涨停质量分 v2（绝对尺度五维 ＋ 首板六因子冻结模型）",
        "source": "本地 OneDrive：短线数据采集/zt_pool.csv + details/全部Ａ股{day}.xlsx + daily/{day}/板块全量.json"
                  + (" + daily/{day}/first_board/{day}_eod（1J2 skill 冻结模型）" if fb else ""),
        "score_rule": ("涨停质量分＝封板时间25 + 封板牢固度25(炸板次数15/封单强度10) + 大单流入20(主力净额/成交额±5%)"
                       " + 板块共振20(方向涨停家数12/方向景气分8) + 量能结构10(换手率钟形)；"
                       "首板另有专属口径「首板晋级分」＝100×六因子逻辑回归概率，"
                       "与 2 板及以上的质量分**不是同一套体系**，两套并列输出、禁止混用"),
        "market": {"涨停家数": len(stocks), "均分": round(sum(sc) / len(sc), 1) if sc else 0,
                   "中位分": med, "最高": sc[0] if sc else None, "最低": sc[-1] if sc else None,
                   "一字板家数": sum(1 for s in stocks if "一字板" in s["flags"]),
                   "开盘秒板家数": sum(1 for s in stocks if "开盘秒板" in s["flags"]),
                   "尾盘板家数": sum(1 for s in stocks if "尾盘板" in s["flags"]),
                   "平均封单强度_pct": (round(sum(s["seal_strength_pct"] or 0 for s in stocks) / len(stocks), 3) if stocks else None)},
        "by_level": by_level,
        "stocks": stocks,
    }
    if fb:
        out["first_board"] = fb
    dst_dir = os.path.join(DAILY, day)
    os.makedirs(dst_dir, exist_ok=True)
    dst = os.path.join(dst_dir, "涨停质量.json")
    json.dump(out, open(dst, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    print(f"  → 市场: 涨停 {len(stocks)} 家 · 均分 {out['market']['均分']} · 中位 {med}"
          f" · 一字板 {out['market']['一字板家数']} · 开盘秒板 {out['market']['开盘秒板家数']}"
          f" · 尾盘板 {out['market']['尾盘板家数']}")
    print(f"  → 分位阶: " + " | ".join(f"{k} {v['家数']}家 均{v['均分']}" for k, v in by_level.items()))
    if fb:
        print(f"  → 首板体系（1J2 冻结模型 {fb['model']}）：合格首板 {fb['n']} 只 · 前20% {fb['n_top20']} 只"
              f" · 排除 {fb['n_excluded']} 只 · 首板分 中位 {fb['score_median']} / 最高 {fb['score_max']}"
              f" · 预期开盘中位 {fb['expect_open_median']}%")
        print(f"    一致性自检：涨停池首板 {len(zt_first)} 只 − 1J2 合格 {fb['n']} 只 = 差 {len(fb['only_in_zt'])} 只"
              + (f"（{'/'.join(fb['only_in_zt'])}）" if fb['only_in_zt'] else ""))
        if fb["only_in_fb"]:
            print(f"    ⚠️ 1J2 独有代码（不在涨停池首板）：{'/'.join(fb['only_in_fb'])} —— 需人工查")
    else:
        print(f"  ⚠️ 未找到 1J2 首板评分（daily/{day}/first_board/{day}_eod/）"
              f" → 看板首板分区将只显示质量分，与 skill 体系不一致")
    print(f"  → 落盘 {dst}")
    if top_n:
        print(f"  ── 质量分 TOP{top_n} ──")
        for s in stocks[:top_n]:
            print(f"   {s['rank']:>3}. {s['score']:>5} {s['name']:<7}{s['code']} {s['level']:<4}"
                  f" 首封{s['first_time']} 炸{s['opened']}次 封单{s['seal_yi']}亿({s['seal_strength_pct']}%)"
                  f" 主力{s['main_ratio_pct']}% 换手{s['turnover']}% [{s['direction']}×{s['dir_zt_n']}]")
        print("  ── 质量分 末 5 位 ──")
        for s in stocks[-5:]:
            print(f"   {s['rank']:>3}. {s['score']:>5} {s['name']:<7}{s['code']} {s['level']:<4}"
                  f" 首封{s['first_time']} 炸{s['opened']}次 封单{s['seal_yi']}亿 主力{s['main_ratio_pct']}%")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dates", help="逗号分隔，如 2026-09-17,2026-09-18")
    ap.add_argument("--range", help="区间 YYYYMMDD-YYYYMMDD（扫描本地涨停池）")
    ap.add_argument("--top", type=int, default=12)
    args = ap.parse_args()

    days = []
    if args.range:
        a, b = args.range.split("-")
        for d in sorted(os.listdir(POOL_DIR)):
            if len(d) == 8 and d.isdigit() and a <= d <= b and os.path.isfile(os.path.join(POOL_DIR, d, "zt_pool.csv")):
                days.append(d)
        if not days:
            print(f"区间 {args.range} 内无本地涨停池")
            return 1
    elif args.dates:
        days = [x.strip().replace("-", "") for x in args.dates.split(",") if x.strip()]
    else:
        ap.error("需给 --dates 或 --range")

    for d8 in days:
        print("=" * 96)
        print(f"【{d8}】")
        build_day(d8, top_n=args.top)
    return 0


if __name__ == "__main__":
    sys.exit(main())
