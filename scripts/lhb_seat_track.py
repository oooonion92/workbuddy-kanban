# -*- coding: utf-8 -*-
"""
龙虎榜席位追踪（长期跟踪设施）
=================================
用法:
  python lhb_seat_track.py                          # 最近一个已披露交易日
  python lhb_seat_track.py 2026-08-28               # 单日
  python lhb_seat_track.py 2026-08-24,2026-08-28    # 多日区间(逗号分隔)
  python lhb_seat_track.py --backfill 10            # 回溯最近10个交易日并落盘
  python lhb_seat_track.py --history 消闲派         # 某选手历史操作轨迹(学习操作思路)
  python lhb_seat_track.py --history 消闲派 20      # 限制最近20条

输出: 控制台归因表 + 落盘 daily/lhb_seats/{YYYYMMDD}.json
配置: 席位跟踪池.csv（唯一源，Excel 可手工维护；组含 famous知名游资/other其他/quant量化/deprecated弃用）
  注：exclude/daily/node/watch 四组已废除（2026-09-16）。代码保留 exclude 解析路径仅作兼容，
  CSV 中已无 exclude 行；原 exclude 的机构专用/沪股通专用/深股通专用已迁入 other 组的「机构北向」id。
归纳逻辑（2026-09-07 用户拍板 v1）: 量化全部合并为一个「量化」；知名游资单独统计；
  不知名/未晋升小席位统一归「其他」（id=其他 合并 + 未匹配营业部兜底进其他）；
  晋升条件 = 连续活跃 + 归属证据确凿，晋升 = 从「其他」拆出独立 id
分组匹配优先级: deprecated > quant > famous > other（other 兜底）
⚠️ GROUP_ORDER 必须与 build_lhb_dashboard.py 的同名常量保持一致（白名单不同步会导致看板漏组）
"""
import csv, json, os, re, sys, urllib.request, urllib.parse
from collections import defaultdict

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
POOL_FILE = os.path.join(ROOT, "席位跟踪池.csv")
STORE_DIR = os.path.join(ROOT, "daily", "lhb_seats")
# 2026-09-18：主存储改为 SQLite 单库；STORE_DIR 仅作兜底
#   ⚠️ DB 位置不能靠 ROOT 硬推：云端脚本目录是 root 拥有、other 只读，
#      而 www-data 的 cron 也要跑 → 库固定在共享目录 /srv/lhb。
#      统一由 lhb_store.find_db() 决定（本机 data/lhb.db → 云端 /srv/lhb/lhb.db）。
if os.path.join(ROOT, "scripts") not in sys.path:
    sys.path.insert(0, os.path.join(ROOT, "scripts"))
try:
    from lhb_store import find_db
    DB_FILE = find_db()
except Exception:
    DB_FILE = os.path.join(ROOT, "data", "lhb.db")

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "Referer": "https://data.eastmoney.com/"}
BASE = "https://datacenter-web.eastmoney.com/api/data/v1/get"

# 展示顺序（看板分组自上而下）
GROUP_ORDER = ("quant", "famous", "other", "retail", "deprecated")
# 匹配顺序（仅用于「同一营业部被多处登记」时的择优；2026-09-16 起为【精确匹配】，
# 绝大部分情况下最多只有一个条目命中）
MATCH_ORDER = ("deprecated", "quant", "famous", "retail", "other")
# 「其他」虚拟席位 id：未匹配营业部统一兜底归入
OTHER_ID = "其他"

# ⚠️ 匹配规则（2026-09-16 改为精确匹配，根治「短关键词过匹配」）
#   历史事故：`中信证券上海分公司` 归一化后只剩 `中信证券上海`，在子串匹配下吞掉 9 个
#   中信证券上海分支（含孙哥的溧阳路）；`华泰证券南京` 吞掉 9 个华泰南京分支。
#   现行规则：CSV 的 depts 写「完整营业部名」（含地址/分支标识，可省 股份有限公司/营业部 装饰）
#   → 归一化后【全等】才算命中。
#   唯一例外：以 `*` 结尾的值做「品牌前缀」匹配（仅用于外资投行，如 `高盛*`）——
#   品牌名无碰撞风险，且可覆盖新开分支。
WILDCARD_SUFFIX = "*"


# ---------- 基础工具 ----------
def norm(s):
    """席位名归一化：去掉公司后缀/营业部后缀，便于跨券商改名与新旧名匹配"""
    if not s:
        return ""
    s = s.replace("股份有限公司", "").replace("有限责任公司", "").replace("有限公司", "")
    s = re.sub(r"(证券营业部|营业部|证券分公司|分公司)$", "", s)
    return s.strip()


def fetch(report, filter_str, page_size=300, sort_cols="", sort_type=-1, page_number=1):
    params = {"reportName": report, "columns": "ALL", "filter": filter_str,
              "pageNumber": str(page_number), "pageSize": str(page_size), "source": "WEB", "client": "WEB"}
    if sort_cols:
        params["sortColumns"] = sort_cols
        params["sortTypes"] = str(sort_type)
    url = BASE + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except Exception as e:
        return {"err": str(e)}


def fetch_all(report, filter_str, page_size=500, max_pages=40):
    """分页取全（2026-09-15 修复：此前 fetch 固定 pageNumber=1，单日记录超一页时
    排在后面的标的被截断——如 09-15 有 10 只票（603421/605069/605188/688004/688216/920xxx）
    的席位明细明明存在却被漏掉）。用 result.pages 作为翻页依据，避免服务端单页上限差异。"""
    out, first = [], fetch(report, filter_str, page_size=page_size, page_number=1)
    if "err" in first or not first.get("result"):
        return out
    res = first["result"]
    out.extend(res.get("data") or [])
    total_pages = res.get("pages") or 1
    for pn in range(2, min(int(total_pages), max_pages) + 1):
        r = fetch(report, filter_str, page_size=page_size, page_number=pn)
        if "err" in r or not r.get("result"):
            break
        out.extend(r["result"].get("data") or [])
    return out


def load_pool():
    """读席位池 CSV（唯一源，可 Excel 手工编辑）。
    CSV 列: group,id,depts,style,focus,shared
    depts 每行一个营业部；同一 id 的多个营业部拆成多行（group/id 重复），
    style/focus/shared 只在首行填写，此处按 (group,id) 聚合 + 向前填充。
    返回同构 dict：{group: [ {id,depts,style,focus,shared,_kw}, ... ]}
    """
    p = {"_order": GROUP_ORDER}
    _seen = {}  # (group,id) -> item
    with open(POOL_FILE, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            g = (row.get("group") or "").strip()
            if g not in GROUP_ORDER and g != "exclude":
                continue
            id_ = (row.get("id") or "").strip()
            if not id_:
                continue
            # 兼容两种写法：拆行后的单值，或 Excel 里偶尔写回的 | 合并
            depts = [d.strip() for d in (row.get("depts") or "").split("|") if d.strip()]
            if g == "exclude":
                # exclude 行：id 即剔除关键词，每行独立
                p.setdefault("_ex", []).append(norm(id_))
                continue
            key = (g, id_)
            item = _seen.get(key)
            if item is None:
                item = {
                    "id": id_,
                    "depts": [],
                    "style": row.get("style") or "",
                    "focus": row.get("focus") or "",
                    "shared": row.get("shared") or "",
                    "_kw": [],
                }
                _seen[key] = item
                p.setdefault(g, []).append(item)
            for d in depts:
                item["depts"].append(d)
                # 保留尾部 `*`（品牌前缀匹配标记），其余部分归一化
                if d.endswith(WILDCARD_SUFFIX):
                    item["_kw"].append(norm(d[:-1]) + WILDCARD_SUFFIX)
                else:
                    item["_kw"].append(norm(d))
            # 向前填充 style/focus/shared（首行填值，后续行可能留空）
            if not item["style"]:
                item["style"] = row.get("style") or ""
            if not item["focus"]:
                item["focus"] = row.get("focus") or ""
            if not item["shared"]:
                item["shared"] = row.get("shared") or ""
    return p


def match(dept_raw, pool):
    """返回 (分组, 条目dict)；分组 ∈ deprecated/quant/famous/retail/other/None

    匹配规则（2026-09-16 起为【精确匹配】）：
      · 归一化后【全等】才算命中 —— 根治子串匹配下「短关键词过匹配」的问题。
        历史事故：`中信证券上海`（源自"中信证券上海分公司"）吞掉 9 个中信上海分支，
        含孙哥的溧阳路；`华泰证券南京` 吞掉 9 个华泰南京分支。
      · 例外：关键词以 `*` 结尾 → 品牌前缀匹配（仅用于外资投行，如 `高盛*`），
        品牌名无碰撞风险且可覆盖新开分支。
      · 同一营业部被多处登记时，按 MATCH_ORDER 组优先级择优（正常情况下唯一命中）。
    """
    nd = norm(dept_raw)
    # exclude 为兼容保留的最强剔除层（CSV 现行已无 exclude 行）
    for k in pool.get("_ex", []):
        if k and k in nd:
            return "exclude", None
    for group in MATCH_ORDER:
        for item in pool.get(group, []):
            for k in item["_kw"]:
                if not k:
                    continue
                if k.endswith(WILDCARD_SUFFIX):
                    if nd.startswith(k[:-1]):
                        return group, item
                elif k == nd:
                    return group, item
    return None, None


# ---------- 数据采集 ----------
# ⛔ 榜别区分（2026-09-16 重大修复）
# 东财同一只票同日可能同时登上「单日榜」与「累计型榜」，两者的金额口径完全不同：
#   · 单日榜（日涨幅偏离7% / 日换手20% / 日振幅15% 等）→ 金额＝**当日**发生额 ✅
#   · 累计型榜（连续三个交易日…累计达20% / 严重异常期间…）→ 金额＝**区间累计** ❌
# 旧逻辑把两者按 (dept, code) 混在一起取 max → 累计值被当成当日值。
# 实测事故：2026-09-16 超声电子同时上「连续三个交易日涨幅偏离累计20%」与「日换手率20%」两榜，
#   消闲派（国泰海通武汉紫阳东路）只出现在三日榜（买 1.49亿 / 净 +1.4668亿 = 3 日累计），
#   看板却显示其当日净买 1.47亿 —— 实际当日并无该笔。
# 现规则：主统计只用【单日榜】，累计型榜单独落盘供参考，绝不混入日度净额。
MULTI_DAY_TOKENS = (
    "连续三个交易日", "连续3个交易日", "连续两个交易日", "连续2个交易日",
    "严重异常期间",
)


def board_of(reason):
    """判榜别：'day' = 单日榜（金额为当日口径）；'multi' = 累计型榜（金额为区间累计）。"""
    r = reason or ""
    return "multi" if any(t in r for t in MULTI_DAY_TOKENS) else "day"


def collect(date):
    """拉某日龙虎榜买入/卖出席位明细，返回 (单日榜记录, 累计型榜记录, meta)"""
    # 1) 个股榜 code->name + 涨跌 + 上榜原因（分页取全）
    meta = {}
    for d in fetch_all("RPT_DAILYBILLBOARD_DETAILSNEW", f"(TRADE_DATE<='{date}')(TRADE_DATE>='{date}')", page_size=500):
        meta[d.get("SECURITY_CODE")] = {
            "name": d.get("SECURITY_NAME_ABBR", ""),
            "chg": d.get("CHANGE_RATE"),
            "reason": (d.get("EXPLANATION") or "")[:24],
        }
    rows = []
    for report, side in (("RPT_BILLBOARD_DAILYDETAILSBUY", "买"), ("RPT_BILLBOARD_DAILYDETAILSSELL", "卖")):
        for d in fetch_all(report, f"(TRADE_DATE<='{date}')(TRADE_DATE>='{date}')", page_size=500):
            dept = str(d.get("OPERATEDEPT_NAME") or "").strip()
            if not dept:
                continue
            code = d.get("SECURITY_CODE", "")
            m = meta.get(code, {})
            expl = (d.get("EXPLANATION") or "").strip()
            rows.append({
                "dept": dept,
                "code": code,
                "name": m.get("name", code),
                "chg": m.get("chg"),
                # ⚠️ reason 必须取【本明细行】的 EXPLANATION，不能用 meta（meta 按 code 覆盖写入，
                # 同一只票多榜别时可能取到累计榜那条 → 显示与 board 分类不一致，误导读者）
                "reason": expl[:24],
                "board": board_of(expl),          # day / multi
                "board_reason": expl,
                "side": side,
                "buy": (d.get("BUY") or 0) / 1e4,
                "sell": (d.get("SELL") or 0) / 1e4,
                "net": (d.get("NET") or 0) / 1e4,
            })
    # 合并去重（2026-09-09 修复净额虚高 bug）：东财买入榜记录只含单边（SELL=None、NET=BUY），
    # 卖出榜记录只含单边（BUY=None、NET=-SELL），同一(席位,股票)会在两榜各出一条；
    # 旧逻辑「保留 buy 最大的一条」把卖出条整条丢掉 → 卖出丢失、净额=买入额虚高。
    # 新逻辑：按 (dept, code, board) 分组，buy/sell 各取最大（同榜多榜单类别重复披露值相同，max 安全），
    # net = buy - sell 自算（不信任 API 的 NET 单边字段）。
    # ⚠️ (dept, code, board) 的 board 维度不可省 —— 否则单日榜与累计榜会互相污染。
    uniq = {}
    for r in rows:
        key = (r["dept"], r["code"], r["board"])
        u = uniq.get(key)
        if u is None:
            uniq[key] = r
            continue
        if r["buy"] > u["buy"]:
            u["buy"] = r["buy"]
        if r["sell"] > u["sell"]:
            u["sell"] = r["sell"]
        u["net"] = u["buy"] - u["sell"]
        u["side"] = "买" if u["net"] >= 0 else "卖"
    merged = list(uniq.values())
    day_rows = [r for r in merged if r["board"] == "day"]
    multi_rows = [r for r in merged if r["board"] == "multi"]
    return day_rows, multi_rows, meta


def _tx_sym(code):
    """6 位代码 → 腾讯行情 symbol。
    6xxxxx=沪 · 0/3xxxxx=深 · 90xxxx=沪B(用 sh) · 920xxx/43/83/87/88=北交所(用 bj)
    """
    c = code[:1]
    if c == "6":
        return "sh" + code
    if c == "9":
        return ("sh" if code[1:2] == "0" else "bj") + code
    if c in "48":
        return "bj" + code
    return "sz" + code


def _em_secid(code):
    """6 位代码 → 东财 secid（沪=1. / 其余=0.，北交所亦为 0.）"""
    return ("1." if code[:1] == "6" else "0.") + code


def fetch_stock_chg(codes, n_days=30):
    """拉每只上榜股的**逐日涨跌幅**（含非上榜日）。

    用途：视图②「当日涨跌幅」行要逐列显示该股当日表现，而非仅上榜日。
    主源＝腾讯日线 `web.ifzq.gtimg.cn/.../fqkline/get`（返回 [日期,开,收,高,低,量]），
    涨跌幅由相邻收盘价自算 —— 它**不受东财 push2his 限流影响**
    （2026-09-16 教训：并发打东财日线 ~1080 次触发 RemoteDisconnected 全量失败）。
    兜底＝东财日线，仅对腾讯取不到的票（北交所 920xxx 等）尝试；
    **熔断**：首次失败即置 `_em_ok=False`，本次运行不再尝试，避免反复空转。
    返回 {code: {date: pct}}
    """
    import urllib.request, json as _json
    from concurrent.futures import ThreadPoolExecutor

    codes = [c for c in codes if c]
    if not codes:
        return {}

    state = {"em_ok": True}          # 东财兜底熔断开关（跨线程共享，单写无锁可接受）

    def _tx(code):
        u = (f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
             f"?param={_tx_sym(code)},day,,,{n_days},qfq")
        raw = urllib.request.urlopen(
            urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"}),
            timeout=10).read()
        node = (_json.loads(raw).get("data") or {}).get(_tx_sym(code)) or {}
        return node.get("qfqday") or node.get("day") or []

    def _sina(code):
        """新浪日线兜底：腾讯不覆盖北交所 920xxx（返回 null），新浪用 bj 前缀可取。"""
        u = ("https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
             f"CN_MarketData.getKLineData?symbol={_tx_sym(code)}"
             f"&scale=240&ma=no&datalen={n_days}")
        raw = urllib.request.urlopen(
            urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"}),
            timeout=10).read().decode("utf-8", "ignore")
        arr = _json.loads(raw) if raw and raw.strip() not in ("null", "") else []
        return [[d.get("day"), d.get("open"), d.get("close")] for d in (arr or [])]

    def _em(code):
        if not state["em_ok"]:
            return []
        u = ("https://push2his.eastmoney.com/api/qt/stock/kline/get"
             f"?secid={_em_secid(code)}&fields1=f1,f2,f3&fields2=f51,f53,f59"
             f"&klt=101&fqt=1&lmt={n_days}&end=20500101")
        try:
            raw = urllib.request.urlopen(
                urllib.request.Request(u, headers={
                    "User-Agent": "Mozilla/5.0",
                    "Referer": "https://quote.eastmoney.com/"}), timeout=10).read()
            ks = ((_json.loads(raw).get("data") or {}).get("klines")) or []
            return [k.split(",") for k in ks]
        except Exception:
            state["em_ok"] = False       # 熔断：本次运行不再尝试东财
            return []

    def one(code):
        ks = []
        for src in (_tx, _sina, _em):
            try:
                ks = src(code)
            except Exception:
                ks = []
            if ks:
                break
        out, prev = {}, None
        for k in ks:
            if len(k) < 3:
                continue
            try:
                close = float(k[2])
            except (TypeError, ValueError):
                continue
            if prev:                      # 三家第 3 列均为「收盘价」
                out[k[0]] = round((close - prev) / prev * 100, 2)
            prev = close
        return code, out

    # 熔断探测：源不可用时立刻放弃，避免 N/并发数 × 超时 的长时间空转。
    # 取前 3 只探测（单只可能因退市/长期停牌而无数据，不代表数据源不可用）
    out = {}
    for code in codes[:3]:
        c, mp = one(code)
        if mp:
            out[c] = mp
            break
    if not out:
        return {}
    with ThreadPoolExecutor(max_workers=6) as ex:
        for code, mp in ex.map(one, codes):
            if mp:
                out[code] = mp
    return out


def last_trade_days(n):
    """最近 n 个自然日回溯（含周末，无数据自动跳过）"""
    import datetime
    out = []
    d = datetime.date.today()
    while len(out) < n:
        out.append(d.strftime("%Y-%m-%d"))
        d -= datetime.timedelta(days=1)
    return out


# ---------- 归因 + 输出 ----------
def analyze(date, pool, verbose=True):
    rows, multi_rows, meta = collect(date)          # rows = 仅单日榜；multi_rows = 累计型榜
    if not rows:
        if verbose:
            print(f"{date}: 无龙虎榜数据（未到披露时间或未披露）")
        return None

    buckets = {k: defaultdict(list) for k in ("quant", "famous", "other", "retail", "deprecated")}
    unmatched = []
    for r in rows:
        grp, item = match(r["dept"], pool)
        if grp in buckets:
            buckets[grp][item["id"]].append(r)
        elif grp is None:
            # 未知小席位兜底：统一归「其他」合并统计（unmatched 列表仍保留用于 ≥2000万 提醒）
            unmatched.append(r)
            buckets["other"][OTHER_ID].append(r)

    result = {"date": date, "quant": {}, "famous": {}, "other": {}, "retail": {},
              "deprecated": {}, "unmatched": []}

    if verbose:
        print("=" * 100)
        print(f"龙虎榜席位追踪  {date}   （扫描 {len(rows)} 条席位记录 / 上榜股 {len(meta)} 只）")
        print("=" * 100)

    for grp, title in (("quant", "【量化】外资/机构/打板 · 合并统计"),
                       ("famous", "【知名游资】单独统计"),
                       ("other", "【其他】未晋升席位 + 未知兜底 · 合并统计"),
                       ("retail", "【散户通道/非营业部】噪音层 · 独立统计")):
        if not buckets[grp]:
            if verbose:
                print(f"\n{title}：今日无席位上榜")
            continue
        if verbose:
            print(f"\n{title}")
            print("-" * 100)
        for pid in sorted(buckets[grp], key=lambda k: -sum(abs(x["net"]) for x in buckets[grp][k])):
            recs = buckets[grp][pid]
            net_sum = sum(x["net"] for x in recs)
            buy_sum = sum(x["buy"] for x in recs)
            sell_sum = sum(x["sell"] for x in recs)   # v2：汇总层补齐卖出，否则净卖出席位看不到卖出总额
            # 该选手的席位清单
            depts = sorted({x["dept"] for x in recs})
            if verbose:
                print(f"\n● {pid}   净额 {net_sum:+,.0f}万  买入 {buy_sum:,.0f}万  卖出 {sell_sum:,.0f}万  {len(recs)} 只票")
                print(f"  席位: {'; '.join(norm(d) for d in depts)}")
                # 按净额绝对值展示（↑净买入 ↓净卖出）
                for x in sorted(recs, key=lambda v: -abs(v["net"])):
                    chg = f"{x['chg']:+.1f}%" if isinstance(x["chg"], (int, float)) else "  -  "
                    arrow = "↑" if x["net"] >= 0 else "↓"
                    print(f"    {arrow} {x['name']}({x['code']}) {chg}  净{x['net']:+,.0f}万  买{x['buy']:,.0f}万  卖{x['sell']:,.0f}万  [{x['reason']}]")
            result[grp][pid] = {
                "net": net_sum, "buy": buy_sum, "sell": sell_sum, "n": len(recs),
                "depts": depts,
                "ops": [{"dept": x["dept"], "name": x["name"], "code": x["code"],
                         "chg": x["chg"], "side": x["side"],
                         "net": x["net"], "buy": x["buy"], "sell": x["sell"], "reason": x["reason"]} for x in recs],
            }

    if buckets["deprecated"] and verbose:
        print("\n【已弃用席位】仅提示，勿据此判断")
        for pid, recs in buckets["deprecated"].items():
            print(f"  ⚠ {pid}: {', '.join(sorted({x['name'] for x in recs}))}")

    # 未匹配的高价值席位（提示补充映射）
    un = [u for u in unmatched if abs(u["net"]) >= 2000]
    un.sort(key=lambda x: -abs(x["net"]))
    if un and verbose:
        print("\n【未匹配席位·净额≥2000万】建议核查是否需加入跟踪池")
        for u in un[:12]:
            chg = f"{u['chg']:+.1f}%" if isinstance(u["chg"], (int, float)) else "  -  "
            print(f"  {norm(u['dept'])}  {u['side']} {u['name']}({u['code']}) {chg} 净{u['net']:+,.0f}万")
    result["unmatched"] = [{"dept": u["dept"], "name": u["name"], "code": u["code"],
                            "net": u["net"], "side": u["side"]} for u in un[:30]]
    # 逐日涨跌幅（该股每个交易日的表现，供看板视图②「当日涨跌幅」行逐列显示）
    codes = sorted({u["code"] for u in rows if u.get("code")})
    result["stock_chg"] = fetch_stock_chg(codes)
    # 抓取时刻东财的上榜标的数（完整性基线，配 --check 复核当日数据是否已披露齐全）
    result["n_stocks"] = len(meta)
    # ⭐ 当日**全部上榜标的**的代码（含无任何跟踪席位的票）+ 名称/涨跌幅/上榜原因。
    # 用途：看板视图②需要区分两类「空」——
    #   ① 该票当日**没上龙虎榜** → 该列本质「无法跟踪」（不是席位未交易）
    #   ② 该票当日**上榜了**，但某席位没参与 → 才是「席位未交易」
    # meta 来自 RPT_DAILYBILLBOARD_DETAILSNEW（个股榜，覆盖当日全部上榜票，与席位明细无关），
    # 是唯一能给出「当日上没上榜」的源 —— 之前只落 n_stocks 计数，丢掉了这份名单。
    result["board_codes"] = {
        c: {"name": m.get("name", c), "chg": m.get("chg"), "reason": m.get("reason", "")}
        for c, m in meta.items() if c
    }
    # 累计型榜（连续三日/严重异常期间）单独落盘：金额是区间累计，**不参与日度净额统计**，
    # 仅作参考（看清游资 3 日区间的建仓/派发轨迹）。按净额绝对值降序，取前 60 条防 json 膨胀。
    result["multi_day"] = [
        {"dept": r["dept"], "name": r["name"], "code": r["code"], "side": r["side"],
         "buy": round(r["buy"], 1), "sell": round(r["sell"], 1), "net": round(r["net"], 1),
         "board_reason": r["board_reason"]}
        for r in sorted(multi_rows, key=lambda x: -abs(x["net"]))[:60]
    ]
    result["n_multi_day"] = len(multi_rows)
    # 全部「累计型榜」涉及的标代码（multi_day 只存 TOP60，故单独记录代码全集，供 --check 判完整性）
    result["multi_codes"] = sorted({r["code"] for r in multi_rows if r.get("code")})
    return result


# ---------- 读取已落盘数据（存储层无关，统一入口） ----------
def load_saved(d):
    """按日期读回已落盘的一天，返回旧 JSON 同构 dict；没有则 None。

    ⚠️ 2026-09-18 新增：落盘已改 SQLite，但 `--check` / `--multi` 仍在读
       `daily/lhb_seats/*.json` → 永远报「未落盘」，自检形同失效
       （实测云端 16:50 跑完自检三次全报缺漏，误判为抓数失败）。
       凡是「读已落盘数据」的地方一律走这里，避免存储层迁移漏改。
    """
    try:
        from lhb_store import LhbStore
        if os.path.exists(DB_FILE):
            st = LhbStore(DB_FILE).init()
            try:
                return st.load_day(d)
            finally:
                st.close()
    except Exception as e:
        print(f"⚠️ SQLite 读取失败，回退 JSON：{e}")
    path = os.path.join(STORE_DIR, f"{d.replace('-', '')}.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return None


def save(result):
    """落盘一个交易日。

    2026-09-18 第 30 轮：存储层由「每日一个 JSON」改为 **SQLite 单库**（data/lhb.db）。
    - 语义不变：**覆盖式**（同一天重跑会先清该日再插，天然幂等，不会像旧 JSON 那样
      残留上一次跑残余）
    - 仍返回路径字符串（兼容调用方），指向库文件
    - 若库不可用 → 回退写旧 JSON（保证链路不中断）
    """
    if not result:
        return
    try:
        from lhb_store import LhbStore
        st = LhbStore(DB_FILE).init()
        try:
            st.save_day(result)
        finally:
            st.close()
        return DB_FILE
    except Exception as e:
        print(f"⚠️ SQLite 落盘失败，回退 JSON：{e}")
        os.makedirs(STORE_DIR, exist_ok=True)
        path = os.path.join(STORE_DIR, f"{result['date'].replace('-', '')}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=1)
        return path


def show_history(pid, limit=15):
    """某选手历史操作轨迹（优先走 SQLite 的 seat_history，比扫 JSON 快 ~60×）"""
    if os.path.exists(DB_FILE):
        try:
            from lhb_store import LhbStore
            st = LhbStore(DB_FILE).init()
            try:
                rows = st.seat_history(pid, limit=limit * 20)
            finally:
                st.close()
            if not rows:
                print(f"未找到 {pid} 的记录（检查席位跟踪池.csv 中的 id 拼写）")
                return
            print(f"=== {pid} 历史操作轨迹 ===")
            cur = None
            cnt = 0
            seen = set()
            for r in rows:
                if r["date"] not in seen:
                    if cur is not None:
                        cnt += 1
                        if cnt >= limit:
                            break
                    seen.add(r["date"])
                    print(f"\n{r['date']}  净额 {r['net']:+,.0f}万")
                arrow = "↑" if r["net"] >= 0 else "↓"
                chg = f"{r['chg']:+.1f}%" if isinstance(r["chg"], (int, float)) else "  -  "
                print(f"   {arrow} {r['stk_name']}({r['stk_code']}) {chg} "
                      f"净{r['net']:+,.0f}万 [{r['dept']}]")
            return
        except Exception as e:
            print(f"⚠️ SQLite 读取失败，回退 JSON：{e}")
    # ---- 兜底：旧 JSON ----
    if not os.path.isdir(STORE_DIR):
        print("尚无历史数据，请先运行回溯")
        return
    files = sorted(os.listdir(STORE_DIR))
    print(f"=== {pid} 历史操作轨迹 ===")
    cnt = 0
    for fn in reversed(files):
        try:
            with open(os.path.join(STORE_DIR, fn), encoding="utf-8") as f:
                d = json.load(f)
        except Exception:
            continue
        for grp in ("quant", "famous", "other", "retail"):
            if pid in d.get(grp, {}):
                info = d[grp][pid]
                print(f"\n{d['date']}  净额 {info['net']:+,.0f}万  买 {info['buy']:,.0f}万  {info['n']} 只票")
                for op in sorted(info["ops"], key=lambda v: -abs(v["net"]))[:8]:
                    chg = f"{op['chg']:+.1f}%" if isinstance(op["chg"], (int, float)) else "  -  "
                    arrow = "↑" if op["net"] >= 0 else "↓"
                    print(f"   {arrow} {op['name']}({op['code']}) {chg} 净{op['net']:+,.0f}万 [{op['reason']}]")
                cnt += 1
                break
        if cnt >= limit:
            break
    if cnt == 0:
        print(f"未找到 {pid} 的记录（检查席位跟踪池.json 中的 id 拼写）")


# ---------- 主流程 ----------
def rebuild_dashboard():
    """落盘后自动重建看板（抓数+看板合一，日常一条命令完成全链路）"""
    import subprocess
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "build_lhb_dashboard.py")
    r = subprocess.run([sys.executable, script], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.stdout.strip():
        print(r.stdout.strip())
    if r.returncode != 0:
        print("⚠ 看板重建失败（数据已落盘，可单独重跑 build_lhb_dashboard.py）:")
        print((r.stderr or "")[-500:])


def main():
    args = sys.argv[1:]
    pool = load_pool()

    if args and args[0] == "--check":
        # 完整性自检：比对「落盘的标的数」vs「东财当前实时标的数」。
        # 用途：当日龙虎榜披露是渐进的（2026-09-16 实测 16:40 抓只得 36 只，
        # 17:10 东财已补齐到 63 只）→ 抓完当日数据后必须复核一次。
        dates = args[1].split(",") if len(args) > 1 else [last_trade_days(1)[0]]
        for d in dates:
            live = {}
            for pn in range(1, 12):
                r = fetch("RPT_DAILYBILLBOARD_DETAILSNEW",
                          f"(TRADE_DATE<='{d}')(TRADE_DATE>='{d}')",
                          page_size=500, page_number=pn)
                res = (r or {}).get("result") or {}
                for x in (res.get("data") or []):
                    live[x.get("SECURITY_CODE")] = x.get("SECURITY_NAME_ABBR")
                if pn >= (res.get("pages") or 1):
                    break
            path = os.path.join(STORE_DIR, f"{d.replace('-', '')}.json")
            if not live:
                print(f"{d}: 东财尚未披露（标的数 0）")
                continue
            saved = load_saved(d)
            if not saved:
                print(f"{d}: 未落盘 —— 东财现有 {len(live)} 只，需执行抓取")
                continue
            have = set()
            for g in GROUP_ORDER:
                for pid, info in (saved.get(g) or {}).items():
                    for op in info.get("ops") or []:
                        if op.get("code"):
                            have.add(op["code"])
            # 口径修正（2026-09-16）：仅上「累计型榜」的标的会正常落在 multi_day、不在日度桶里，
            # 这不算缺漏 —— 用 multi_codes（累计榜标的全集）补齐已覆盖集合，否则会误报。
            for c in (saved.get("multi_codes") or []):
                if c:
                    have.add(c)
            for r in (saved.get("multi_day") or []):      # 兼容旧 json（无 multi_codes）
                if r.get("code"):
                    have.add(r["code"])
            n_multi = saved.get("n_multi_day", 0)
            miss = set(live) - have
            status = "✅ 已齐" if not miss else f"❌ 缺 {len(miss)} 只"
            print(f"{d}: 落盘 {len(have)} 只 / 东财 {len(live)} 只 → {status}")
            if miss:
                names = [f"{live[c]}({c})" for c in sorted(miss)]
                print("     缺:", "、".join(names[:20]) + ("…" if len(names) > 20 else ""))
                print(f"     修复：python scripts/lhb_seat_track.py {d}")
        return

    if args and args[0] == "--multi":
        # 查看某日（默认最新）的累计型榜明细（三日榜/严重异常期间）。
        # 这些记录金额为区间累计，**不计入日度净额**，故单独提供查看入口。
        if len(args) > 1:
            d = args[1]
        else:
            # 默认取最新一日：优先 DB，DB 不可用再扫 JSON 文件名
            d = None
            try:
                from lhb_store import LhbStore
                if os.path.exists(DB_FILE):
                    st = LhbStore(DB_FILE).init()
                    try:
                        d = st.last_date()
                    finally:
                        st.close()
            except Exception:
                d = None
            if not d:
                js = sorted(__import__("glob").glob(os.path.join(STORE_DIR, "*.json")))
                d = os.path.basename(js[-1])[:8] if js else None
            if not d:
                print("尚无历史数据，请先运行回溯")
                return
        d = f"{d[:4]}-{d[4:6]}-{d[6:8]}" if len(d) == 8 else d
        saved = load_saved(d)
        if not saved:
            print(f"{d}: 未落盘")
            return
        rows = saved.get("multi_day") or []
        print("=" * 96)
        print(f"{d}  累计型榜（金额为区间累计，非当日）  共 {saved.get('n_multi_day', 0)} 条，"
              f"下列按 |净额| 降序 TOP{len(rows)}")
        print("=" * 96)
        for r in rows:
            print(f"  {'↑' if r['net'] >= 0 else '↓'} {r['dept'][:30]:<32} "
                  f"{r['name']}({r['code']})  净{r['net']/1e4:>+8.2f}亿  [{r['board_reason'][:30]}]")
        return

    if args and args[0] == "--history":
        pid = args[1] if len(args) > 1 else "消闲派"
        limit = int(args[2]) if len(args) > 2 else 15
        show_history(pid, limit)
        return

    if args and args[0] == "--backfill":
        days = int(args[1]) if len(args) > 1 else 10
        dates = last_trade_days(days)
        ok = 0
        for d in dates:
            r = analyze(d, pool, verbose=False)
            if r:
                save(r)
                print(f"{d}: 已落盘（量化 {len(r['quant'])} / 游资 {len(r['famous'])} / "
                      f"其他 {len(r['other'])} / 散户 {len(r.get('retail', {}))}）")
                ok += 1
        print(f"\n回溯完成，共 {ok} 个交易日。")
        rebuild_dashboard()
        return

    if args and not args[0].startswith("-"):
        dates = args[0].split(",")
    else:
        dates = last_trade_days(4)

    for d in dates:
        r = analyze(d, pool)
        if r:
            p = save(r)
            print(f"\n>> 已落盘: {p}")
        print()

    rebuild_dashboard()


if __name__ == "__main__":
    main()
