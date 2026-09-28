# -*- coding: utf-8 -*-
"""
行情快照自动采集 fetch_snapshot.py（v1.5 模块化第一步：客观数据脚本直填，AI 零接触）
收盘后运行，产出 daily/YYYYMMDD/行情快照.json —— 固定 schema，喂给看板客观区。

数据源优先级（纯客观，禁推算；拿不到标 null 并记入 meta.warnings）：
  涨停/跌停/炸板/晋级率/封板质量/昨日反馈
                     ① OneDrive 本地短线包 D:\OneDrive\Stock\短线数据采集\{date}\（约16:30同步，首选）
                     ② 同花顺 dataapi limit_up_pool / lower_limit_pool（联网兜底）
  指数/持仓/现货      腾讯 qt.gtimg.cn
  涨跌家数(宽度)      东财 clist 全市场扫描（f2 vs f18，按板块口径汇总）
  炸板/触板(兜底时)   东财 clist 全市场扫描：最高价触涨停价未封 = 炸板（proxy，meta 标注）
  板块涨跌/主力资金   东财 push2 clist
  股指期货(当月/隔季) 新浪 CFF hq.sinajs.cn（nf_ 前缀；3=最新 13=昨收 14=昨结算 15=持仓）
跨日字段（昨贴水/仓差/昨日两市额）从最近一份历史行情快照.json 读取，无则 null。

用法：python fetch_snapshot.py [--date YYYYMMDD] [--force]
"""
import os
import re
import sys
import json
import time
import argparse
import datetime

import requests

ROOT = os.environ.get("KANBAN_ROOT", r"D:\Work buddy project\每日盯盘")
DAILY = os.path.join(ROOT, "daily")
WATCHLIST = os.path.join(ROOT, ".workbuddy", "tmp", "watchlist.json")

session = requests.Session()
session.trust_env = False  # 绕过失效代理
session.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})

WARNINGS = []


def warn(msg):
    WARNINGS.append(msg)
    print(f"  [WARN] {msg}")


def get_with_retry(url, retries=3, backoff=3, **kw):
    for i in range(retries):
        try:
            return session.get(url, timeout=20, **kw)
        except requests.RequestException as e:
            if i == retries - 1:
                raise
            time.sleep(backoff * (i + 1))
    raise RuntimeError("unreachable")


# ---------------- 腾讯行情 ----------------
def fetch_qt(codes):
    """腾讯行情批量，返回 {sh/sz代码: {...}}"""
    out = {}
    for i in range(0, len(codes), 30):
        batch = codes[i:i + 30]
        url = "https://qt.gtimg.cn/q=" + ",".join(batch)
        r = get_with_retry(url)
        r.encoding = "gbk"
        for line in r.text.strip().split(";"):
            line = line.strip()
            m = re.match(r'v_(\w+)="(.+)"', line)
            if not m:
                continue
            parts = m.group(2).split("~")
            if len(parts) < 40 or not parts[3]:
                continue

            def f(idx, default=0.0):
                try:
                    v = parts[idx]
                    return float(v) if v else default
                except (ValueError, IndexError):
                    return default

            out[m.group(1)] = {
                "name": parts[1],
                "close": f(3),
                "prev_close": f(4),
                "open": f(5),
                "high": f(33),
                "low": f(34),
                "chg": f(32),
                "amount_yi": f(37) / 10000.0,   # 万 → 亿
                "turnover": f(38),
                "vol_ratio": f(49),
            }
    return out


def qt_prefix(code):
    return ("sh" if code[0] in "569" or code.startswith("68") else "sz") + code


# ---------------- 东财 clist 全市场统一扫描（宽度 + 触板/封板） ----------------
EM_UT = "bd1d9ddb040334b8a88925d7c85c2f89"
SCAN_FS = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23"  # 沪深A股（不含北交所）


def limit_ratio(code, name):
    if code.startswith(("30", "68")):
        return 0.20
    if code.startswith(("8", "4", "92")):
        return 0.30
    if "ST" in (name or "").upper():
        return 0.05
    return 0.10


def scan_market():
    """一次全市场扫描，返回 (breadth, touched, sealed)：
    breadth = {'000001':{up,down,flat}, ...}（沪A/深A/创业板/科创板口径）
    touched = 触涨停未封（炸板 proxy）；sealed = 收盘涨停"""
    breadth = {"000001": [0, 0, 0], "399001": [0, 0, 0],
               "399006": [0, 0, 0], "000688": [0, 0, 0]}
    touched, sealed = [], []
    pn = 1
    while pn <= 12:
        url = ("http://push2.eastmoney.com/api/qt/clist/get?pn=%d&pz=1000&po=1&np=1&fltt=2"
               "&invt=2&fid=f12&fs=%s&fields=f2,f12,f14,f15,f18&ut=%s" % (pn, SCAN_FS, EM_UT))
        r = get_with_retry(url)
        data = (r.json().get("data") or {})
        diff = data.get("diff") or []
        if isinstance(diff, dict):
            diff = list(diff.values())
        if not diff:
            break
        for d in diff:
            try:
                code = str(d["f12"])
                name = d.get("f14") or ""
                close = float(d["f2"])
                high = float(d["f15"])
                prev = float(d["f18"])
            except (KeyError, TypeError, ValueError):
                continue
            if prev <= 0 or close <= 0:
                continue
            # 宽度：按板块口径
            if code.startswith("30"):
                b = breadth["399006"]
            elif code.startswith("68"):
                b = breadth["000688"]
            elif code.startswith("6"):
                b = breadth["000001"]
            else:
                b = breadth["399001"]
            if close > prev:
                b[0] += 1
            elif close < prev:
                b[1] += 1
            else:
                b[2] += 1
            # 触板/封板
            limit_price = round(prev * (1 + limit_ratio(code, name)), 2)
            if high >= limit_price - 1e-6:
                item = {"code": code, "name": name}
                if close >= limit_price - 1e-6:
                    sealed.append(item)
                else:
                    touched.append(item)
        total = int(data.get("total") or 0)
        if pn * 1000 >= total:
            break
        pn += 1
        time.sleep(0.3)
    return ({"k": {"up": v[0], "down": v[1], "flat": v[2]} for k, v in breadth.items()},
            touched, sealed)


# ---------------- 本地 OneDrive 短线数据包（16:30 同步，池类数据首选源） ----------------
SHORT_TERM_DIR = os.environ.get("SHORT_TERM_DATA_DIR", r"D:\OneDrive\Stock\短线数据采集")


def _read_csv(path):
    import csv
    rows = []
    try:
        with open(path, encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
    except OSError:
        pass
    return rows


def _fmt_t(s):
    """'092500' → '09:25:00'；空/异常返回 None"""
    s = (s or "").strip()
    return f"{s[:2]}:{s[2:4]}:{s[4:6]}" if len(s) == 6 and s.isdigit() else None


def _f(v, default=None):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def load_shortterm_local(date):
    """读取 D:\\OneDrive\\Stock\\短线数据采集\\{date}\\，返回标准化 dict 或 None。
    成品指标（炸板率/封板质量/分层晋级率/昨日反馈）直接来自 snapshot.json，零推算。"""
    d = os.path.join(SHORT_TERM_DIR, date)
    snap_path = os.path.join(d, "snapshot.json")
    if not os.path.isfile(snap_path):
        return None
    try:
        with open(snap_path, encoding="utf-8") as f:
            ss = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        warn(f"本地短线包 snapshot.json 解析失败：{e}")
        return None
    ov = ss.get("市场概览") or {}
    if not ov.get("涨停家数") and not ov.get("涨停家数") == 0:
        return None

    zt = []
    for r in _read_csv(os.path.join(d, "zt_pool.csv")):
        stat = (r.get("涨停统计") or "")          # '2/2' = 2天2板
        high_days = int(stat.split("/")[1]) if "/" in stat else None
        zt.append({
            "code": r.get("代码", ""), "name": (r.get("名称") or "").strip(),
            "close": _f(r.get("最新价")), "chg": _f(r.get("涨跌幅")),
            "lbc": int(_f(r.get("连板数"), 0) or 0), "high_days": high_days,
            "first_time": _fmt_t(r.get("首次封板时间")), "last_time": _fmt_t(r.get("最后封板时间")),
            "opened": int(_f(r.get("炸板次数"), 0) or 0),
            "seal_amount_yi": round(_f(r.get("封板资金"), 0) / 1e8, 2) if r.get("封板资金") else None,
            "industry": r.get("所属行业") or None,
        })
    dtp = []
    for r in _read_csv(os.path.join(d, "dt_pool.csv")):
        dtp.append({
            "code": r.get("代码", ""), "name": (r.get("名称") or "").strip(),
            "close": _f(r.get("最新价")), "chg": _f(r.get("涨跌幅")),
            "seal_amount_yi": round(_f(r.get("封单资金"), 0) / 1e8, 2) if r.get("封单资金") else None,
            "last_time": _fmt_t(r.get("最后封板时间")),
            "consecutive_dt": int(_f(r.get("连续跌停"), 0) or 0),
            "industry": r.get("所属行业") or None,
        })
    zb = [{"code": r.get("代码", ""), "name": (r.get("名称") or "").strip(),
           "close": _f(r.get("最新价")), "chg": _f(r.get("涨跌幅")),
           "opened": int(_f(r.get("炸板次数"), 0) or 0),
           "first_time": _fmt_t(r.get("首次封板时间"))}
          for r in _read_csv(os.path.join(d, "zb_pool.csv"))]

    ladder = {}
    for s in zt:
        if s["lbc"] >= 2:
            ladder.setdefault(str(s["lbc"]), []).append(s["name"])

    return {
        "date": ss.get("交易日期"), "collected_at": ss.get("采集时间"),
        "overview": ov, "seal_quality": ss.get("封板质量") or {},
        "promotion_levels": ss.get("晋级率") or [],
        "prev_zt_feedback": ss.get("昨日涨停反馈") or {},
        "zt_list": zt, "dt_list": dtp, "zb_list": zb,
        "zb_count": len(zb),
        "zb_rate": ov.get("炸板率_pct"),
        "ladder": {k: v for k, v in sorted(ladder.items(), key=lambda x: -int(x[0]))},
        "max_board": ov.get("最高连板"),
    }


# ---------------- 同花顺涨停/跌停池（联网兜底） ----------------
def parse_high_days(s):
    """'首板'→1；'3连板'→3；'7天6板'→6（高度取板数）；None→None"""
    if not s:
        return None
    s = str(s)
    if "首板" in s:
        return 1
    m = re.search(r"(\d+)\s*连板", s)
    if m:
        return int(m.group(1))
    m = re.search(r"天\s*(\d+)\s*板", s)
    if m:
        return int(m.group(1))
    m = re.search(r"(\d+)\s*板", s)
    return int(m.group(1)) if m else None


THS_FIELD = "199112,10,9001,330323,330324,330325,9002,330329,133971,133970,1968584,3475914,9003,9004"


def fetch_ths_pool(kind, date):
    """同花顺涨停池 kind=limit_up_pool / lower_limit_pool；翻页取全"""
    out, page, total = [], 1, None
    while page <= 10:
        url = ("https://data.10jqka.com.cn/dataapi/limit_up/%s?page=%d&limit=200"
               "&order_field=330324&order_type=0&filter=HS,GEM2STAR&date=%s&_=%d&field=%s"
               % (kind, page, date, int(time.time() * 1000), THS_FIELD))
        try:
            r = get_with_retry(url, retries=2, backoff=2)
            data = (r.json().get("data") or {})
        except (ValueError, requests.RequestException) as e:
            warn(f"同花顺 {kind} 第{page}页失败：{type(e).__name__}")
            break
        info = data.get("info") or []
        for x in info:
            out.append({
                "code": str(x.get("code", "")),
                "name": x.get("name", ""),
                "high_days": x.get("high_days"),
                "lbc": parse_high_days(x.get("high_days")),
                "first_time": x.get("first_limit_up_time"),
                "last_time": x.get("last_limit_up_time"),
                "change_tag": x.get("change_tag"),
            })
        pg = data.get("page") or {}
        total = pg.get("total")
        if total is None or len(out) >= int(total) or not info:
            break
        page += 1
    return out, total


# ---------------- 新浪 CFF 期指 ----------------
def third_friday(y, m):
    d = datetime.date(y, m, 1)
    fri = d + datetime.timedelta(days=(4 - d.weekday()) % 7)  # 第一个周五
    return fri + datetime.timedelta(days=14)                  # 第三个周五


def next_month(y, m):
    return (y + 1, 1) if m == 12 else (y, m + 1)


def guess_contracts(today):
    """当月=未交割最近月；隔季=当月季度+2个季度后的季月（项目口径：2609→2703）"""
    y, m = today.year, today.month
    if today > third_friday(y, m):
        y, m = next_month(y, m)
    tq = (m - 1) // 3 + 2
    qy = y + tq // 4
    qm = [3, 6, 9, 12][tq % 4]
    return f"{y % 100:02d}{m:02d}", f"{qy % 100:02d}{qm:02d}"


def fetch_futures(today):
    cur_mm, nq_mm = guess_contracts(today)
    codes, keys = [], []
    for kind, spot in [("IF", "sh000300"), ("IH", "sh000016"),
                       ("IC", "sh000905"), ("IM", "sh000852")]:
        codes += [f"nf_{kind}{cur_mm}", f"nf_{kind}{nq_mm}"]
        keys += [(kind, spot, "cur"), (kind, spot, "next_q")]
    url = "http://hq.sinajs.cn/list=" + ",".join(codes)
    r = get_with_retry(url, headers={"Referer": "https://finance.sina.com.cn"})
    r.encoding = "gbk"
    raw = {}
    for line in r.text.strip().split("\n"):
        m = re.match(r'var hq_str_nf_(\w+)="(.+)"', line.strip())
        if m and m.group(2):
            raw[m.group(1)] = m.group(2).split(",")
    spots = fetch_qt(["sh000300", "sh000016", "sh000905", "sh000852"])
    out, order = {}, []
    for code, (kind, spot, role) in zip(codes, keys):
        p = raw.get(code[3:])  # raw 键无 nf_ 前缀
        if not p or len(p) < 16 or not p[3]:
            warn(f"期指 {code} 新浪返回不可用")
            continue
        try:
            last = float(p[3])
            prev_settle = float(p[14]) if p[14] else 0.0   # 昨结算
            oi = float(p[15]) if p[15] else None           # 持仓量
        except (ValueError, IndexError):
            warn(f"期指 {code} 字段解析失败")
            continue
        sp = spots.get(spot)
        basis = None
        if sp and sp["close"]:
            basis = round((last - sp["close"]) / sp["close"] * 100, 2)
        rec = out.setdefault(kind, {"code": kind, "spot": None, "spot_chg": None})
        rec[f"{role}_code"] = code[3:]
        rec[f"{role}"] = last
        rec[f"{role}_chg"] = round((last - prev_settle) / prev_settle * 100, 2) if prev_settle else None
        rec[f"{role}_oi"] = oi
        if role == "cur":
            rec["cur_basis"] = basis
            rec["spot"] = sp["close"] if sp else None
            rec["spot_chg"] = sp["chg"] if sp else None
        else:
            rec["next_q_basis"] = basis
        if kind not in order:
            order.append(kind)
    return [out[k] for k in order if k in out], cur_mm, nq_mm


# ---------------- 东财板块/资金 ----------------
def fetch_sectors():
    def clist(pz, po, fid, fields):
        url = ("http://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=%d&po=%d&np=1&fltt=2"
               "&invt=2&fid=%s&fs=m:90+t:2&fields=%s&ut=%s" % (pz, po, fid, fields, EM_UT))
        r = get_with_retry(url, retries=2)
        diff = (r.json().get("data") or {}).get("diff") or []
        if isinstance(diff, dict):
            diff = list(diff.values())
        return diff

    up, down, flow = [], [], []
    for d in clist(15, 1, "f3", "f3,f14"):
        try:
            up.append({"name": d["f14"], "chg": float(d["f3"])})
        except (KeyError, TypeError, ValueError):
            pass
    time.sleep(0.3)
    for d in clist(10, 0, "f3", "f3,f14"):
        try:
            down.append({"name": d["f14"], "chg": float(d["f3"])})
        except (KeyError, TypeError, ValueError):
            pass
    time.sleep(0.3)
    for d in clist(10, 1, "f62", "f14,f62,f184"):
        try:
            flow.append({"name": d["f14"], "main_net_yi": round(float(d["f62"]) / 1e8, 2),
                         "ratio_pct": float(d.get("f184") or 0)})
        except (KeyError, TypeError, ValueError):
            pass
    return up, down, flow


# ---------------- 历史快照（跨日字段） ----------------
def load_prev_snapshot(today_str):
    """取「最近的、日期小于今日的」行情快照。

    ⚠️ 09-16 修复：原先只返回快照内容，调用方无从知道它是哪一天的 —— 当中间交易日缺快照时
    （如 09-12/09-14/09-15 缺失）会**静默回退到更早的日期**（实测退到 09-11），
    导致 `prev_total_amount_yi` 把「3 个交易日前的成交额」当成「昨日」，量能同比结论直接反向
    （09-16 被误判为缩量 -6.7%，实际对 09-15 是放量 +14.0%）。
    修复：返回 (日期, 内容) 二元组，并在与今日间隔 >4 个自然日时告警。
    """
    best = None
    if not os.path.isdir(DAILY):
        return None, None
    for d in os.listdir(DAILY):
        p = os.path.join(DAILY, d, "行情快照.json")
        if len(d) == 8 and d.isdigit() and d < today_str and os.path.isfile(p):
            if best is None or d > best[0]:
                best = (d, p)
    if not best:
        return None, None
    try:
        with open(best[1], encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        warn(f"历史快照 {best[1]} 读取失败：{e}")
        return None, None
    # 间隔校验：>4 个自然日（含周末）说明中间交易日缺快照，量能同比不可信
    try:
        gap = (datetime.datetime.strptime(today_str, "%Y%m%d").date()
               - datetime.datetime.strptime(best[0], "%Y%m%d").date()).days
        if gap > 4:
            warn(f"上一份快照为 {best[0]}（距今日 {gap} 个自然日），中间交易日缺快照 → "
                 f"prev_total_amount_yi / 同比量能结论**不可信**，请以日线数据另行核对")
    except ValueError:
        pass
    return best[0], data


# ---------------- 主流程 ----------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=datetime.date.today().strftime("%Y%m%d"))
    ap.add_argument("--force", action="store_true", help="覆盖已存在的快照")
    args = ap.parse_args()
    date = args.date
    today = datetime.datetime.strptime(date, "%Y%m%d").date()
    out_dir = os.path.join(DAILY, date)
    out_path = os.path.join(out_dir, "行情快照.json")
    if os.path.isfile(out_path) and not args.force:
        print(f"已存在 {out_path}，跳过（--force 覆盖）")
        return

    print(f"=== 行情快照采集 {date} ===")
    prev_date, prev = load_prev_snapshot(date)

    # 1. 指数行情（腾讯）
    idx_qt = fetch_qt(["sh000001", "sz399001", "sz399006", "sh000688"])
    name_map = {"sh000001": "上证指数", "sz399001": "深证成指",
                "sz399006": "创业板指", "sh000688": "科创50"}
    indices = {}
    for qt_code, name in name_map.items():
        q = idx_qt.get(qt_code)
        if not q:
            warn(f"指数 {name} 腾讯行情缺失")
            continue
        indices[name] = {
            "close": q["close"], "chg": q["chg"], "open": q["open"],
            "high": q["high"], "low": q["low"], "amount_yi": round(q["amount_yi"], 1),
            "up": None, "down": None, "flat": None, "main_flow_yi": None,
        }
    total_amount = round(indices.get("上证指数", {}).get("amount_yi", 0) +
                         indices.get("深证成指", {}).get("amount_yi", 0), 1)

    # 2. 全市场扫描：宽度 + 触板/封板（东财 clist）
    breadth = None
    touched, sealed_scan = [], []
    try:
        breadth, touched, sealed_scan = scan_market()
    except requests.RequestException as e:
        warn(f"全市场扫描失败：{type(e).__name__}（宽度/炸板缺省）")
    for qt_code, name in name_map.items():
        if name in indices and breadth:
            b = breadth.get(qt_code[2:], {})
            indices[name]["up"] = b.get("up")
            indices[name]["down"] = b.get("down")
            indices[name]["flat"] = b.get("flat")

    # 3. 涨停/跌停/炸板池：本地 OneDrive 短线包优先（约 16:30 同步），联网接口兜底
    local = load_shortterm_local(date)
    promotion_levels = prev_feedback = seal_quality = None
    if local:
        zt, dtp = local["zt_list"], local["dt_list"]
        zb_count, zb_rate = local["zb_count"], local["zb_rate"]
        ladder, max_board = local["ladder"], local["max_board"]
        promotion_levels = local["promotion_levels"] or None
        prev_feedback = local["prev_zt_feedback"] or None
        seal_quality = local["seal_quality"] or None
        # 总晋级率 = 分层加权（昨日各层样本合计 → 今日晋级合计）
        try:
            _p = sum(x.get("晋级数", 0) for x in local["promotion_levels"])
            _s = sum(x.get("昨日样本数", 0) for x in local["promotion_levels"])
            promo_rate = round(_p / _s * 100, 1) if _s else None
            promo_detail = {"prev_date": None, "prev_zt": _s, "promoted": _p,
                            "levels": promotion_levels}
        except (KeyError, TypeError):
            promo_rate, promo_detail = None, None
        pool_src = "OneDrive本地短线包(东财口径,16:30采集)"
        print(f"  池类数据：本地包 {local['collected_at']}（涨停 {len(zt)} 炸板 {zb_count} 跌停 {len(dtp)}）")
        touched = []  # 本地包已有权威炸板池，触板 proxy 不再需要
    else:
        promo_rate = promo_detail = None
        zt, zt_total = fetch_ths_pool("limit_up_pool", date)
        dtp, dt_total = fetch_ths_pool("lower_limit_pool", date)
        if not zt:
            warn("涨停池为空（本地包未同步且联网接口异常），情绪统计将缺省")
        if zt and sealed_scan and abs(len(zt) - len(sealed_scan)) > max(10, len(zt) * 0.3):
            warn(f"涨停口径交叉校验偏差大：同花顺 {len(zt)} vs 东财扫描封住 {len(sealed_scan)}")

        ladder = {}
        for s in zt:
            if s["lbc"] and s["lbc"] >= 2:
                ladder.setdefault(str(s["lbc"]), []).append(s["name"])
        max_board = max([s["lbc"] for s in zt if s["lbc"]], default=None)
        zb_count = len(touched)
        zb_rate = round(zb_count / (len(zt) + zb_count) * 100, 1) if (zt or touched) else None
        pool_src = "同花顺dataapi(联网兜底)"

        # 晋级率：回溯最近一个有涨停池的交易日
        prev_pool, prev_date_used = None, None
        for back in range(1, 11):
            d = (today - datetime.timedelta(days=back)).strftime("%Y%m%d")
            pool, _tot = fetch_ths_pool("limit_up_pool", d)
            if pool:
                prev_pool, prev_date_used = pool, d
                break
        if prev_pool:
            prev_codes = {s["code"] for s in prev_pool}
            promoted = [s for s in zt if s["code"] in prev_codes]
            promo_rate = round(len(promoted) / len(prev_codes) * 100, 1) if prev_codes else None
            promo_detail = {"prev_date": prev_date_used, "prev_zt": len(prev_codes),
                            "promoted": len(promoted)}

    market = {
        "total_amount_yi": total_amount,
        "prev_date": prev_date,
        "prev_total_amount_yi": (prev.get("market", {}).get("total_amount_yi") if prev else None),
        "limit_up": len(zt), "limit_down": len(dtp), "broken_board": zb_count,
        "broken_rate_pct": zb_rate, "max_board": max_board,
        "ladder": {k: v for k, v in sorted(ladder.items(), key=lambda x: -int(x[0]))},
        "promotion_rate_pct": promo_rate, "promotion_detail": promo_detail,
        "promotion_levels": promotion_levels,
        "seal_quality": seal_quality, "prev_zt_feedback": prev_feedback,
        "zt_list": zt, "dt_list": dtp, "touched_list": touched[:80],
        "pool_sources": {"zt_dt_zb": pool_src},
    }

    # 4. 期指（新浪 CFF）
    futures, cur_mm, nq_mm = fetch_futures(today)
    for f in futures:
        pf = next((x for x in (prev or {}).get("futures", []) if x.get("code") == f["code"]), None)
        if pf:
            if f.get("cur_basis") is not None and pf.get("cur_basis") is not None:
                f["prev_cur_basis"] = pf["cur_basis"]
            if f.get("next_q_basis") is not None and pf.get("next_q_basis") is not None:
                f["prev_next_q_basis"] = pf["next_q_basis"]
            if f.get("cur_oi") is not None and pf.get("cur_oi") is not None:
                f["cur_oi_chg"] = int(round(f["cur_oi"] - pf["cur_oi"]))
            if f.get("next_q_oi") is not None and pf.get("next_q_oi") is not None:
                f["nextq_oi_chg"] = int(round(f["next_q_oi"] - pf["next_q_oi"]))
    if not prev:
        warn("无历史快照，期指昨贴水/仓差字段为 null（次日起自动回填）")

    # 5. 持仓 + 自选池行情（纯行情，纪律判断仍归 AI）
    holds_codes, watch_codes = [], []
    try:
        with open(WATCHLIST, encoding="utf-8") as f:
            wl = json.load(f)
        holds_codes = [h["code"] for h in wl.get("holds", [])]
        watch_codes = [w["code"] for w in wl.get("watchlist", [])]
    except (OSError, json.JSONDecodeError, KeyError) as e:
        warn(f"watchlist.json 读取失败：{e}")

    def quotes_for(codes):
        qt = fetch_qt([qt_prefix(c) for c in codes])
        rows = []
        for c in codes:
            q = qt.get(qt_prefix(c))
            if not q:
                warn(f"{c} 腾讯行情缺失")
                continue
            rows.append({"code": c, "name": q["name"], "close": q["close"], "chg": q["chg"],
                         "open": q["open"], "high": q["high"], "low": q["low"],
                         "amount_yi": round(q["amount_yi"], 2),
                         "turnover": q["turnover"], "vol_ratio": q["vol_ratio"]})
        return rows

    holdings = quotes_for(holds_codes)
    watch_quotes = quotes_for(watch_codes)

    # 6. 板块
    sectors_up, sectors_down, fund_flow = [], [], []
    try:
        sectors_up, sectors_down, fund_flow = fetch_sectors()
    except requests.RequestException as e:
        warn(f"板块数据失败：{type(e).__name__}")

    snap = {
        "meta": {
            "date": date, "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "schema": "行情快照 v1.2", "breadth_source": "em_clist_scan",
            "prev_snapshot_date": prev_date,
            "futures_contracts": {"cur": cur_mm, "next_q": nq_mm},
            "sources": {"indices_holdings": "腾讯qt.gtimg.cn", "breadth_broken": "东财clist全市场扫描",
                        "zt_dt_zb": pool_src, "futures": "新浪CFF(nf_)", "sectors": "东财push2"},
            "warnings": WARNINGS,
        },
        "indices": indices, "market": market, "futures": futures,
        "holdings": holdings, "watch_quotes": watch_quotes,
        "sectors_up": sectors_up, "sectors_down": sectors_down, "fund_flow_top": fund_flow,
    }

    os.makedirs(out_dir, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(snap, f, ensure_ascii=False, indent=2)
    print(f"\n已写入 {out_path}")
    print(f"摘要：两市 {total_amount} 亿 | 涨停 {len(zt)} 跌停 {len(dtp)} 炸板 {zb_count} | "
          f"最高板 {max_board} | 晋级率 {promo_rate}% | 期指 {cur_mm}/{nq_mm} | 池源 {pool_src}")
    if WARNINGS:
        print(f"警告 {len(WARNINGS)} 条：见 meta.warnings")


if __name__ == "__main__":
    main()
