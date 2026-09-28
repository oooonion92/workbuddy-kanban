# -*- coding: utf-8 -*-
"""
⛔ 已作废（2026-09-21）—— 请改用 `scripts/build_sector_full.py`
================================================================
本文件走的是**东财 push2/push2his 网络取数**（板块枚举 + 逐板块日K），已被否决，原因：
  ① 东财已对本机限流（push2his 板块日K 直接 RemoteDisconnected）
  ② 与项目现有数据源重复 —— OneDrive 里**已有每日全量A股明细**（含细分行业/主力净额/成交额），
     板块全量打分可**纯本地零网络**完成，且可任意回溯历史日
  ③ 东财 clist 只给实时快照、不给历史；回溯须逐板块取日K ≈1000 次请求（慢且易被限流）

保留本文件仅为记录「东财板块枚举口径」（clist: m:90+t:1 地域31 / t:2 行业496 / t:3 概念504，
字段 f3 涨幅 / f6 成交额 / f62 主力净额 / f104,f105 涨跌家数 / pz 上限 100/页）。
现行口径见 `scripts/build_sector_full.py` 与 memory `口径-环境与数据源.md`。

================================================================================
板块全量打分（首版原型·可回溯）
================================
产出 `daily/{YYYYMMDD}/板块全量.json`：对该日**全市场板块**逐只打分排序。

数据源：东财 push2（板块枚举）/ push2his（板块指数日K）
  ⚠️ 与 `scripts/fetch_snapshot.py` 的板块取数**同源**（东财 push2），非新增数据源。
  ⚠️ 东财 clist 只能取**实时**快照，**历史日必须逐板块取日K**（每只 1 次请求）。

打分口径（首版，0-100）——只用日K可得的三类客观量：
  · 相对强度 R = 板块涨跌幅 − 当日全板块涨跌幅中位数   → 权重 40
  · 量能     V = 当日成交额 / 前 5 日均额（放量倍数）  → 权重 35
  · 绝对强度 A = 板块当日涨跌幅                        → 权重 25
  ⏳ 未纳入：资金（主力净额/占比）、宽度（涨跌家数）—— 需另取 fflow 历史接口（每只再一次请求）

用法:
  python scripts/fetch_sector_full.py --dates 2026-09-17,2026-09-18
  python scripts/fetch_sector_full.py --dates 2026-09-18 --limit 120   # 小样本试跑
"""
import argparse
import datetime
import json
import os
import sys
import time
import urllib.request

EM_UT = "bd1d9ddb04089700cf9c27f6f7426281"
UA = {"User-Agent": "Mozilla/5.0"}

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DAILY = os.path.join(ROOT, "daily")
CACHE_UNIVERSE = os.path.join(ROOT, ".workbuddy", "tmp", "_board_universe.json")

FS_KINDS = [("行业", "m:90+t:2"), ("概念", "m:90+t:3")]
CLIST_FIELDS = "f12,f14,f3,f6,f62,f104,f105,f184"
KLINE_FIELDS = "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"


def _get(url, timeout=20, retries=3, base_sleep=0.6):
    """带重试 + 退避的 GET（东财限流会直接掐断连接 → RemoteDisconnected）"""
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8", "ignore"))
        except Exception as e:                      # noqa: BLE001
            last = e
            time.sleep(base_sleep * (i + 1))
    raise last


# ---------------------------------------------------------------- 板块枚举
def fetch_universe(throttle=0.35, use_cache=True):
    """全市场板块清单 {code: {code,name,kind}}。翻页 + 节流（东财 pz 上限 100）。"""
    if use_cache and os.path.exists(CACHE_UNIVERSE):
        try:
            u = json.load(open(CACHE_UNIVERSE, encoding="utf-8"))
            if len(u) > 500:
                print(f"  [缓存] 板块 universe {len(u)} 个")
                return u
        except Exception:
            pass
    universe = {}
    for label, fs in FS_KINDS:
        pn = 1
        while pn <= 8:
            url = ("http://push2.eastmoney.com/api/qt/clist/get?pn=%d&pz=100&po=1&np=1&fltt=2"
                   "&invt=2&fid=f3&fs=%s&fields=%s&ut=%s" % (pn, fs, CLIST_FIELDS, EM_UT))
            try:
                j = _get(url, retries=4, base_sleep=1.0)
            except Exception as e:                  # noqa: BLE001
                print(f"  {label} pn={pn} 取数失败({type(e).__name__}) → 跳过该页")
                break
            d = j.get("data") or {}
            rows = d.get("diff") or []
            if isinstance(rows, dict):
                rows = list(rows.values())
            if not rows:
                break
            for r in rows:
                if r.get("f12"):
                    universe[r["f12"]] = {"code": r["f12"], "name": r.get("f14") or r["f12"],
                                          "kind": label}
            print(f"  {label} pn={pn} +{len(rows)} 累计 {len(universe)} / total={d.get('total')}")
            if len(rows) < 100:
                break
            pn += 1
            time.sleep(throttle)
    os.makedirs(os.path.dirname(CACHE_UNIVERSE), exist_ok=True)
    json.dump(universe, open(CACHE_UNIVERSE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"  → universe {len(universe)} 个（已缓存）")
    return universe


# ---------------------------------------------------------------- 逐板块日K
def fetch_board_hist(code, lmt=9):
    """板块指数日K → [(date, close, chg_pct, amount_yuan), ...]（旧→新）"""
    url = ("http://push2his.eastmoney.com/api/qt/stock/kline/get?secid=90.%s&klt=101&fqt=1"
           "&lmt=%d&end=20500101&fields1=f1,f2,f3&fields2=%s&ut=%s" % (code, lmt, KLINE_FIELDS, EM_UT))
    j = _get(url, retries=3, base_sleep=0.5)
    out = []
    for row in ((j.get("data") or {}).get("klines") or []):
        p = row.split(",")
        if len(p) < 9:
            continue
        try:
            out.append((p[0], float(p[2]), float(p[8]), float(p[6])))
        except (ValueError, IndexError):
            continue
    return out


def collect(universe, dates, throttle=0.22):
    """一次性取全 universe 的日K，抽出目标日期行情 {date: {code: {...}}}"""
    want = {d.replace("-", "")[:4] + "-" + d[5:7] + "-" + d[8:10] for d in dates}
    per_day = {d: {} for d in want}
    codes = list(universe.keys())
    n = len(codes)
    t0 = time.time()
    for i, code in enumerate(codes, 1):
        try:
            hist = fetch_board_hist(code)
        except Exception:                           # noqa: BLE001
            hist = []
        if hist:
            by_date = {h[0]: h for h in hist}
            for d in want:
                h = by_date.get(d)
                if not h:
                    continue
                # 前 5 个交易日（不含当日）成交额均值 → 放量倍数
                idx = [k for k, x in enumerate(hist) if x[0] == d]
                prev = hist[max(0, idx[0] - 5):idx[0]] if idx else []
                base = (sum(x[3] for x in prev) / len(prev)) if prev else None
                per_day[d][code] = {
                    "code": code, "name": universe[code]["name"], "kind": universe[code]["kind"],
                    "chg": h[2], "amt_yi": round(h[3] / 1e8, 2),
                    "vr": round(h[3] / base, 2) if base else None,
                }
        if i % 100 == 0 or i == n:
            el = time.time() - t0
            eta = el / i * (n - i)
            print(f"    [{i}/{n}] 已耗时 {el:.0f}s，预计还需 {eta:.0f}s", flush=True)
        time.sleep(throttle)
    return per_day


# ---------------------------------------------------------------- 打分
def clamp01(x):
    return 0.0 if x < 0 else (1.0 if x > 1 else x)


def median(xs):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return 0.0
    m = len(xs) // 2
    return xs[m] if len(xs) % 2 else (xs[m - 1] + xs[m]) / 2


def score_day(rows):
    """就地写 score / excess / rank。口径见文件头。"""
    chgs = [r["chg"] for r in rows if r.get("chg") is not None]
    med = median(chgs)
    for r in rows:
        r["excess"] = round((r["chg"] or 0) - med, 2)
    for r in rows:
        s_r = 40 * clamp01((r["excess"] or 0) / 3.0 * 0.5 + 0.5)       # 相对强度 ±3% 满量程
        s_v = 35 * clamp01(((r["vr"] or 1.0) - 0.6) / 1.4)             # 放量 0.6x→0 / 2.0x→满
        s_a = 25 * clamp01((r["chg"] or 0) / 3.0 * 0.5 + 0.5)          # 绝对涨幅 ±3% 满量程
        r["score"] = round(s_r + s_v + s_a, 1)
    rows.sort(key=lambda x: -x["score"])
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    return med


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dates", required=True, help="逗号分隔，如 2026-09-17,2026-09-18")
    ap.add_argument("--limit", type=int, default=0, help="只取前 N 个板块（小样本试跑）")
    ap.add_argument("--throttle", type=float, default=0.22)
    args = ap.parse_args()

    dates = [d.strip() for d in args.dates.split(",") if d.strip()]
    print(f"目标日期: {dates}")
    universe = fetch_universe()
    if args.limit:
        universe = dict(list(universe.items())[:args.limit])
        print(f"  [限样] 仅取 {len(universe)} 个板块")

    per_day = collect(universe, dates, throttle=args.throttle)

    for d in dates:
        rows = list(per_day[d].values())
        if not rows:
            print(f"  {d}: 无数据")
            continue
        med = score_day(rows)
        out = {
            "date": d,
            "source": "东财 push2(板块枚举)+push2his(板块指数日K)",
            "schema": "板块全量打分 v0",
            "universe": len(universe),
            "covered": len(rows),
            "benchmark_median_chg": round(med, 2),
            "note": ("首版口径＝相对强度40 + 量能35 + 绝对涨幅25；"
                     "未纳入主力资金与涨跌家数（需另取 fflow 历史）"),
            "boards": rows,
        }
        dst_dir = os.path.join(DAILY, d.replace("-", ""))
        os.makedirs(dst_dir, exist_ok=True)
        dst = os.path.join(dst_dir, "板块全量.json")
        json.dump(out, open(dst, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(f"\n=== {d} 覆盖 {len(rows)}/{len(universe)} 个板块，中位涨幅 {med:+.2f}% ===")
        print("  ── 打分 TOP10 ──")
        for r in rows[:10]:
            print(f"   {r['rank']:>3}. {r['score']:>5}  {r['name']:<12}({r['kind']}) "
                  f"涨{r['chg']:+.2f}% 相对{r['excess']:+.2f}% 量比{r['vr']} 额{r['amt_yi']}亿")
        print("  ── 打分 BOTTOM5 ──")
        for r in rows[-5:]:
            print(f"   {r['rank']:>3}. {r['score']:>5}  {r['name']:<12}({r['kind']}) "
                  f"涨{r['chg']:+.2f}% 相对{r['excess']:+.2f}% 量比{r['vr']} 额{r['amt_yi']}亿")
        print(f"  → 落盘 {dst}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
