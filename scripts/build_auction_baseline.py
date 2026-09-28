# -*- coding: utf-8 -*-
"""
竞价预期基准生成器 build_auction_baseline.py
====================================================================
**解决什么问题**：节点②（09:25 竞价）此前只有「涨幅/量比/竞价额」的即时读数，
却**没有「该涨多少才算正常」的基准** → 只能凭体感判强弱。
本脚本把**当日涨停质量分**翻译成**次日竞价的量化预期**，并给出
「符合预期 / 超预期 / 不及预期」三档判据。

**为什么可信：走实证校准，不拍阈值**
OneDrive 全量A股明细里带**竞价专属列**（`开盘%` = 集合竞价开盘涨幅、`竞价昨比`、`开盘金额`），
110 个交易日（20260407~今）→ 可把「D 日的涨停质量分」与「D+1 日的竞价开盘%」逐票对齐，
**按质量分档统计出真实分布**，再用该分布的分位作为三档判据。

**输入**
  · `daily/{D}/涨停质量.json`（D 日涨停股的五维质量分 + 标签 + 身位）—— 历史校准 & 当日基准
  · `D:/OneDrive/Stock/details/全部Ａ股{D+1}.xlsx` 的 `开盘%`（次日竞价开盘涨幅）—— 校准标签
    ⚠️ 只用于**历史校准**；生成「明日基准」时不需要 D+1 数据

**输出**
  · `daily/{D}/竞价预期基准.json` —— 供次日节点②消费
  · stdout 打印：① 校准表（质量分档 → 次日竞价开盘% 分位）② 明日逐票预期表 ③ 市场层阈值

**用法**
  python scripts/build_auction_baseline.py                     # 用最新交易日，自动校准全历史
  python scripts/build_auction_baseline.py --date 2026-09-21
  python scripts/build_auction_baseline.py --days 40           # 只用最近 N 日校准

口径铁律（详见 .workbuddy/memory/口径-竞价与情绪.md）
  · 质量分是**绝对尺度**（跨日可比）→ 分档阈值固定，不随日变
  · 竞价预期只锚**开盘%**（集合竞价结果），不锚盘中；盘中强弱另由 10:30 复核判
  · 一字板 / 开盘秒板 不等于「必高开」——用其**自己的档**统计，不并入大盘
"""
import argparse
import glob
import json
import os
import statistics as st
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DAILY = os.path.join(ROOT, "daily")
DETAILS = r"D:/OneDrive/Stock/details"

# 质量分分档（绝对尺度、固定阈值，跨日可比）
BUCKETS = [(85, 1000, "S ≥85"), (75, 85, "A 75-84"), (65, 75, "B 65-74"),
           (55, 65, "C 55-64"), (0, 55, "D <55")]
LEVEL_ORDER = ["高位板", "3板", "2板", "首板"]


# ---------------------------------------------------------------- 读次日竞价
def _code(v):
    return str(v or "").replace('="', "").replace('"', "").strip().zfill(6)


def load_detail(day8):
    """读 `全部Ａ股{day8}.xlsx` → {code: {kpan, chg, jjzb, jje}}（kpan=开盘% 即竞价开盘涨幅）"""
    import openpyxl
    p = os.path.join(DETAILS, f"全部Ａ股{day8}.xlsx")
    if not os.path.isfile(p):
        return None
    wb = openpyxl.load_workbook(p, read_only=True, data_only=True)
    ws = wb[wb.sheetnames[0]]
    it = ws.iter_rows(values_only=True)
    hdr = list(next(it))
    idx = {h: i for i, h in enumerate(hdr)}
    out = {}
    for row in it:
        if not row or row[idx["代码"]] is None:
            continue
        c = _code(row[idx["代码"]])
        g = lambda k: (row[idx[k]] if k in idx and isinstance(row[idx[k]], (int, float)) else None)
        out[c] = {"name": row[idx["名称"]], "kpan": g("开盘%"), "chg": g("涨幅%"),
                  "jjzb": g("竞价昨比"), "jje": g("开盘金额"), "hy": row[idx["细分行业"]]}
    wb.close()
    return out


def quality_days():
    ds = []
    for p in glob.glob(os.path.join(DAILY, "20*", "涨停质量.json")):
        d = os.path.basename(os.path.dirname(p))
        if len(d) == 8 and d.isdigit():
            ds.append(d)
    return sorted(ds)


# ---------------------------------------------------------------- 校准
def pct(sorted_vals, q):
    if not sorted_vals:
        return None
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    k = (len(sorted_vals) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo)


def ew(s):
    """东亚字符宽度（中文/全角＝2，其余＝1）—— 用于等宽对齐中英混排表格"""
    import unicodedata
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in str(s))


def pad(s, w, right=False):
    s = str(s)
    k = max(0, w - ew(s))
    return (" " * k + s) if right else (s + " " * k)


def stats(vals):
    v = sorted(x for x in vals if x is not None)
    if not v:
        return None
    return {"n": len(v), "med": round(pct(v, .5), 2), "q1": round(pct(v, .25), 2),
            "q3": round(pct(v, .75), 2), "p10": round(pct(v, .10), 2), "p90": round(pct(v, .90), 2),
            "up_rate": round(sum(1 for x in v if x > 0) / len(v) * 100, 1),
            "ge3_rate": round(sum(1 for x in v if x >= 3) / len(v) * 100, 1),
            "le_2_rate": round(sum(1 for x in v if x <= -2) / len(v) * 100, 1)}


def calibrate(days, samples):
    """按质量分档 / 标签 / 身位，统计次日竞价开盘%分布"""
    by_bucket, by_flag, by_level, allv = {}, {}, {}, []
    for s in samples:
        k = s.get("kpan")
        if k is None:
            continue
        allv.append(k)
        sc = s.get("score")
        for lo, hi, lab in BUCKETS:
            if lo <= (sc or -1) < hi:
                by_bucket.setdefault(lab, []).append(k)
                break
        for f in (s.get("flags") or ["无标签"]):
            by_flag.setdefault(f, []).append(k)
        by_level.setdefault(s.get("level") or "?", []).append(k)
    return {
        "sample_days": len(days),
        "n_samples": len(samples),
        "market": stats(allv),
        "by_score": {lab: stats(v) for lab, v in by_bucket.items()},
        "by_flag": {lab: stats(v) for lab, v in by_flag.items()},
        "by_level": {lab: stats(v) for lab, v in by_level.items()},
    }


def collect_samples(days):
    """逐日：D 日涨停质量 → D+1 竞价开盘%（末日后无 D+1，自动跳过）"""
    samples, used = [], []
    for i, d in enumerate(days):
        if i + 1 >= len(days):
            continue
        nxt = days[i + 1]
        det = load_detail(nxt)
        if not det:
            continue
        q = json.load(open(os.path.join(DAILY, d, "涨停质量.json"), encoding="utf-8"))
        used.append((d, nxt))
        for s in q["stocks"]:
            n = det.get(s["code"])
            if not n or n.get("kpan") is None:
                continue
            samples.append({"day": d, "next": nxt, "code": s["code"], "name": s["name"],
                            "score": s["score"], "level": s.get("level"), "flags": s.get("flags"),
                            "first_time": s.get("first_time"), "opened": s.get("opened"),
                            "seal_strength_pct": s.get("seal_strength_pct"),
                            "lbc": s.get("lbc"), "direction": s.get("direction"),
                            "kpan": n["kpan"], "next_chg": n.get("chg"), "next_jjzb": n.get("jjzb")})
    return samples, used


# ---------------------------------------------------------------- 明日基准
def tier_of(score):
    for lo, hi, lab in BUCKETS:
        if lo <= (score or -1) < hi:
            return lab
    return "D <55"


def build(day8, cal):
    qp = os.path.join(DAILY, day8, "涨停质量.json")
    q = json.load(open(qp, encoding="utf-8"))
    stocks = q["stocks"]
    by_score = cal["by_score"]

    rows = []
    for s in stocks:
        t = tier_of(s["score"])
        b = by_score.get(t) or {}
        flags = s.get("flags") or []
        # 标签优先：一字板 / 开盘秒板 用自身分布（不并入大盘）
        eff = t
        for f in ("一字板", "开盘秒板"):
            if f in flags and (cal["by_flag"].get(f) or {}).get("n", 0) >= 8:
                eff = f
                break
        src = cal["by_flag"].get(eff) if eff in ("一字板", "开盘秒板") else by_score.get(eff)
        src = src or b
        rows.append({
            "code": s["code"], "name": s["name"], "score": s["score"], "level": s.get("level"),
            "lbc": s.get("lbc"), "flags": flags, "first_time": s.get("first_time"),
            "opened": s.get("opened"), "seal_strength_pct": s.get("seal_strength_pct"),
            "direction": s.get("direction"), "tier": t, "基准档": eff,
            "预期_中位": src.get("med"), "符合区间": [src.get("q1"), src.get("q3")],
            "不及_阈值": src.get("q1"), "超_阈值": src.get("q3"),
            "历史高开率_pct": src.get("up_rate"), "历史样本n": src.get("n"),
        })
    rows.sort(key=lambda r: -(r["score"] or 0))

    # 市场层阈值
    mk = cal["market"]
    lv = {k: v for k, v in cal["by_level"].items() if v and v.get("n", 0) >= 5}
    anchor = next((r for r in rows if (r["lbc"] or 0) == max(x.get("lbc") or 0 for x in rows)), None)
    out = {
        "date_of_source": day8,
        "for_auction_of": None,          # 由调用方/次日填入（下个交易日）
        "schema": "竞价预期基准 v1（实证校准）",
        "calibration": {"样本交易日": cal["sample_days"], "样本票数": cal["n_samples"],
                        "标签列": "OneDrive 全部Ａ股 明细「开盘%」＝集合竞价开盘涨幅"},
        "market": mk,
        "by_score": by_score, "by_flag": cal["by_flag"], "by_level": lv,
        "height_anchor": anchor,
        "stocks": rows,
    }
    dst = os.path.join(DAILY, day8, "竞价预期基准.json")
    json.dump(out, open(dst, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return out, dst


# ---------------------------------------------------------------- 打印
def show_cal(cal):
    print(f"── 校准：{cal['sample_days']} 个交易日 / {cal['n_samples']} 个「涨停次日」样本 "
          f"（标签＝次日集合竞价开盘涨幅%）")
    print(f"{'质量分档':<10}{'n':>5}{'中位':>8}{'Q1':>8}{'Q3':>8}{'P10':>8}{'P90':>8}"
          f"{'高开率':>8}{'≥+3%':>8}{'≤-2%':>8}")
    for lo, hi, lab in BUCKETS:
        v = cal["by_score"].get(lab)
        if not v:
            continue
        print(f"{lab:<10}{v['n']:>5}{v['med']:>8}{v['q1']:>8}{v['q3']:>8}{v['p10']:>8}"
              f"{v['p90']:>8}{v['up_rate']:>7}%{v['ge3_rate']:>7}%{v['le_2_rate']:>7}%")
    m = cal["market"]
    print(f"{'全市场':<10}{m['n']:>5}{m['med']:>8}{m['q1']:>8}{m['q3']:>8}{m['p10']:>8}"
          f"{m['p90']:>8}{m['up_rate']:>7}%{m['ge3_rate']:>7}%{m['le_2_rate']:>7}%")
    print("\n── 按标签")
    for lab, v in sorted(cal["by_flag"].items(), key=lambda kv: -kv[1]["med"]):
        if v["n"] >= 5:
            print(f"  {lab:<8} n={v['n']:<4} 中位 {v['med']:>6}%  Q1 {v['q1']:>6}  Q3 {v['q3']:>6}"
                  f"  高开率 {v['up_rate']}%  ≥+3% {v['ge3_rate']}%")
    print("\n── 按身位")
    for lab in LEVEL_ORDER:
        v = cal["by_level"].get(lab)
        if v and v["n"] >= 5:
            print(f"  {lab:<8} n={v['n']:<4} 中位 {v['med']:>6}%  Q1 {v['q1']:>6}  Q3 {v['q3']:>6}"
                  f"  高开率 {v['up_rate']}%  ≥+3% {v['ge3_rate']}%")


def show_baseline(o, dst):
    m = o["market"]
    print(f"\n══ 明日竞价预期基准（源：{o['date_of_source']} 涨停质量分）══")
    print(f"  市场层：涨停股整体竞价开盘涨幅 中位 {m['med']}% · 符合区间 [{m['q1']}, {m['q3']}]"
          f" · 历史高开率 {m['up_rate']}% · ≥+3% 概率 {m['ge3_rate']}%")
    print(f"  → 明日竞价「整体强度」判据：全池中位开盘% > {m['q3']}% ＝ 超预期；"
          f"< {m['q1']}% ＝ 不及预期")
    a = o.get("height_anchor")
    if a:
        print(f"  高度锚：{a['name']} {a['code']}（{a['lbc']} 板 · 质量分 {a['score']} · {a['基准档']}）"
              f" → 预期中位 {a['预期_中位']}%，符合区间 {a['符合区间']}")
    hdr = [("标的", 18), ("身位", 7), ("质量分", 7), ("基准档", 10),
           ("预期中位", 9), ("符合区间", 17), ("超预期", 8), ("不及", 8), ("高开率", 8)]
    print("\n" + "".join(pad(h, w) for h, w in hdr))
    for r in o["stocks"]:
        if r["level"] == "首板" and (r["score"] or 0) < 78:
            continue     # 首板只显示高质量者，避免刷屏；完整数据在 json
        rng = f"[{r['符合区间'][0]}, {r['符合区间'][1]}]"
        cells = [(f"{r['name']} {r['code']}", 18), (r["level"] or "", 7),
                 (r["score"], 7), (r["基准档"], 10), (r["预期_中位"], 9), (rng, 17),
                 (r["超_阈值"], 8), (r["不及_阈值"], 8), (f"{r['历史高开率_pct']}%", 8)]
        print("".join(pad(c, w) for c, w in cells))
    print(f"\n  → 落盘 {os.path.relpath(dst, ROOT)}（含全部 {len(o['stocks'])} 只逐票预期）")


def write_md(o, dst_json):
    """写紧凑摘要 `竞价预期基准.md` —— 供次日 09:25（5 分钟 SLA）直接取用，不必解析 JSON"""
    m = o["market"]
    L = []
    L.append(f"# 竞价预期基准｜供 {o.get('for_auction_of') or '下一交易日'} 09:25 竞价使用")
    L.append("")
    L.append(f"> 源：{o['date_of_source']} 涨停质量分（绝对尺度五维） ｜ 校准："
             f"{o['calibration']['样本交易日']} 个交易日 / {o['calibration']['样本票数']} 个「涨停次日」样本")
    L.append(f"> 标签列＝OneDrive 全量A股明细「开盘%」（**集合竞价开盘涨幅**）｜ 数据源：通达信 + 本地 OneDrive")
    L.append("")
    L.append("## 一、市场层阈值（明日竞价整体强弱）")
    L.append("")
    L.append(f"| 指标 | 值 |")
    L.append(f"|---|---|")
    L.append(f"| 涨停股整体竞价开盘% 中位 | **{m['med']}%** |")
    L.append(f"| 符合预期区间 | **[{m['q1']}%, {m['q3']}%]** |")
    L.append(f"| 历史高开率 | {m['up_rate']}% ｜ ≥+3% 概率 {m['ge3_rate']}% ｜ ≤−2% 概率 {m['le_2_rate']}% |")
    L.append("")
    L.append(f"- **超预期**：全池竞价开盘% 中位 > **{m['q3']}%**")
    L.append(f"- **符合预期**：中位落在 **{m['q1']}% ~ {m['q3']}%**")
    L.append(f"- **不及预期**：中位 < **{m['q1']}%**")
    L.append("")
    L.append("## 二、分档基准（历史实证：质量分 → 次日竞价开盘%）")
    L.append("")
    L.append("| 质量分档 | n | 中位 | Q1 | Q3 | 高开率 | ≥+3% | ≤−2% |")
    L.append("|---|---|---|---|---|---|---|---|")
    for lo, hi, lab in BUCKETS:
        v = (o.get("by_score") or {}).get(lab)
        if v:
            L.append(f"| {lab} | {v['n']} | **{v['med']}%** | {v['q1']} | {v['q3']} | "
                     f"{v['up_rate']}% | {v['ge3_rate']}% | {v['le_2_rate']}% |")
    L.append("")
    L.append("| 标签 | n | 中位 | Q1 | Q3 | 高开率 | ≥+3% |")
    L.append("|---|---|---|---|---|---|---|")
    for lab, v in sorted((o.get("by_flag") or {}).items(), key=lambda kv: -kv[1]["med"]):
        if v["n"] >= 5:
            L.append(f"| {lab} | {v['n']} | **{v['med']}%** | {v['q1']} | {v['q3']} | "
                     f"{v['up_rate']}% | {v['ge3_rate']}% |")
    L.append("")
    L.append("| 身位 | n | 中位 | Q1 | Q3 | 高开率 | ≥+3% |")
    L.append("|---|---|---|---|---|---|---|")
    for lab in LEVEL_ORDER:
        v = (o.get("by_level") or {}).get(lab)
        if v:
            L.append(f"| {lab} | {v['n']} | **{v['med']}%** | {v['q1']} | {v['q3']} | "
                     f"{v['up_rate']}% | {v['ge3_rate']}% |")
    L.append("")
    L.append("## 三、逐票预期（三档判据）")
    L.append("")
    L.append("| 标的 | 身位 | 质量分 | 基准档 | 预期中位 | 符合区间 | 超预期 | 不及预期 | 历史高开率 |")
    L.append("|---|---|---|---|---|---|---|---|---|")
    for r in o["stocks"]:
        rng = f"{r['符合区间'][0]} ~ {r['符合区间'][1]}%"
        L.append(f"| {r['name']} {r['code']} | {r['level']} | {r['score']} | {r['基准档']} | "
                 f"**{r['预期_中位']}%** | {rng} | >{r['超_阈值']}% | <{r['不及_阈值']}% | "
                 f"{r['历史高开率_pct']}% |")
    L.append("")
    L.append("## 四、口径")
    L.append("")
    L.append("- **质量分是绝对尺度**（封板时间/炸板次数/封单强度本身有绝对语义）→ 分档阈值固定、跨日可比")
    L.append("- 预期只锚**集合竞价开盘%**；盘中强弱另由 **10:30 复核三项**判（涨停回升 60+ / 主力净额 / 炸板率 >25%）")
    L.append("- `一字板`＝集合竞价 09:25 即封（首封 ≤ 09:25:59 且未开板）；`开盘秒板`＝09:30:00–09:34:59 首封 —— 两者分档独立统计")
    L.append("- ⛔ 基准是**概率分布**，不是指令：S 档仍有 18% 概率低开 ≤−2%")
    L.append("- ⚠️ **一字板存在「结构性上限」**：主板一字板最大涨幅 **+10.02%**，恰为其符合区间上沿"
             "（Q3 = 10.02）→ **一字板档只能判「符合 / 不及预期」，几乎不可能判「超预期」**"
             "（20cm 品种除外）。判读时请对一字板档单独加注。")
    dst = os.path.join(os.path.dirname(dst_json), "竞价预期基准.md")
    open(dst, "w", encoding="utf-8").write("\n".join(L) + "\n")
    return dst



def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="源日期 YYYY-MM-DD（默认最新有涨停质量分的交易日）")
    ap.add_argument("--days", type=int, default=0, help="校准只用最近 N 个交易日（0=全部）")
    args = ap.parse_args()

    days = quality_days()
    if not days:
        print("✗ 未找到任何 涨停质量.json")
        return 1
    src = (args.date or days[-1]).replace("-", "")
    if src not in days:
        print(f"✗ {src} 无 涨停质量.json（可用：{days[0]} ~ {days[-1]}）")
        return 1

    cal_days = days[:days.index(src) + 1]
    if args.days:
        cal_days = cal_days[-args.days - 1:]
    samples, used = collect_samples(cal_days)
    if not samples:
        print("✗ 无可用校准样本（需 D 与 D+1 的 涨停质量.json + 全量A股明细）")
        return 2
    cal = calibrate([d for d, _ in used], samples)
    show_cal(cal)

    out, dst = build(src, cal)
    # 下一交易日（用于标注 for_auction_of）
    nxt = [d for d in days if d > src]
    out["for_auction_of"] = nxt[0] if nxt else None
    if out["for_auction_of"] is None:
        # 当日即最新交易日 → 下一交易日未知，用自然日顺推标注（仅供人读）
        import datetime
        dd = datetime.date(int(src[:4]), int(src[4:6]), int(src[6:])) + datetime.timedelta(days=1)
        while dd.weekday() >= 5:
            dd += datetime.timedelta(days=1)
        out["for_auction_of"] = dd.strftime("%Y%m%d") + "（自然日顺推，以实际交易日为准）"
    json.dump(out, open(dst, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    show_baseline(out, dst)
    md = write_md(out, dst)
    print(f"  → 摘要 {os.path.relpath(md, ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
