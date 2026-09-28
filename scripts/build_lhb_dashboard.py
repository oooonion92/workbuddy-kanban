# -*- coding: utf-8 -*-
"""
龙虎榜专属复盘页 —— 生成 daily/lhb_dashboard.html（v6 折叠版）
====================================================================
两个入口（pivot 表格）：
  ① 席位 → 个股 汇总（学操作思路：跟踪某席位的个股分布与节奏）
  ② 个股 → 席位 汇总（学节点把握：跟踪某票的资金构成与共识）

v6 折叠（2026-09-02）：
  · 日期列：默认只显示最近 N_RECENT_DATES(5) 个交易日，更早的列折叠，
    顶部导航「近5日 / 全部」一键切换，选择存 localStorage
  · 视图①行折叠：每个席位块默认只显示「最近 N_RECENT_STOCK_DAYS(3) 个交易日
    内有买卖」的标的，其余折叠；席位名单元格右侧出现 +N 展开按钮
    - 若某席位所有标的近期均无操作，仍保留 1 行（块内首行），避免整块消失
    - 折叠/展开后 JS 动态重算 rowspan（rowspan 会随隐藏行失效，必须重算）
    - 视图②默认不折叠（collapse_stale=False），需要时打开开关即可
  · 打印时自动展开全部日期列与全部折叠行
v5 紧凑度优化（基于 v4 Carbon）：
  - 默认行高 36px（vs v4 默认 44px，缩 ~18%）
  - 单元格 padding 横向 12→8px；首列 min-width 112→96px；次列 min-width 176→144px
  - 字号、字距、行高、字重等Carbon核心规范不动；强度条、sticky行为全部保留
  - 2026-09-17：密度切换（舒适/紧凑）已移除——用户反馈无使用价值，行高统一 36px
设计语言：IBM Carbon + Stripe 金融色（借 colorspace-skill 设计规范）
  - 圆角 0（Carbon 身份）；深浅靠背景灰阶分层，卡片/表格零阴影，阴影只给浮动 nav
  - 单色强调 Blue 60 #0f62fe；字重封顶 600（Carbon 禁用 700）；间距全部落 8px 网格
  - 微字距：14px→+0.16px、12px→+0.32px（Carbon 小字可读性秘诀）
  - 块区分：行背景 #fff/#f4f4f4 交替 + 首列左侧 3px 组别竖条（不整行染色，保住红绿对比度）
  - 语义色：A股红涨绿跌，取 Carbon Red 60 #da1e28 / Green 50 #24a148
v4 增强：
  - 净额强度条：单元格背景横向条，宽度∝|净额|，零额外行高；颜色+箭头+长度三重编码（色盲友好）
数据源：daily/lhb_seats/{日期}.json + 席位跟踪池.csv
分组：famous知名游资 / other其他(含机构北向、国泰海通上海分) / quant量化 / deprecated已弃用
      （2026-09-16：daily/node/watch/exclude 四组已废除，exclude 的机构专用与沪深股通已迁入 other/机构北向）
用法：  python scripts/build_lhb_dashboard.py [--days N]
"""
import json, os, sys, re, glob
from collections import OrderedDict, Counter

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STORE_DIR = os.path.join(ROOT, "daily", "lhb_seats")
POOL_FILE = os.path.join(ROOT, "席位跟踪池.csv")
OUT = os.path.join(ROOT, "daily", "lhb_dashboard.html")
MAX_DAYS = int(sys.argv[sys.argv.index("--days") + 1]) if "--days" in sys.argv else 10

# 席位池分组（唯一真源，必须与 lhb_seat_track.py 的 GROUP_ORDER 完全一致）
# 顺序 = 展示顺序：知名游资 → 其他 → 量化 → 散户通道 → 已弃用
GROUP_ORDER = ("famous", "other", "quant", "retail", "deprecated")

# v7 折叠参数
N_DATES_PER_GROUP = 10      # 日期分组大小：连续 N 个交易日为一组（默认展开最新一组 = 10 日全展示）
N_RECENT_STOCK_DAYS = 3     # 视图①：标的在最近 N 个交易日有买卖才默认展开（纵向折叠保留，2026-09-07 用户定 3）

# 「未出榜」单元格文案：全站仅 2018 格渲染真实 <span>（其余同类格只显示 `—`，见 op_cell）。
# 2026-09-17 瘦身：span 的 class 由 `ob` 保留（已足够短），仅外侧 td 的类名做了压缩。
SHOW_OB_HTML = '<span class="ob">未出榜</span>'
# 「仅累计榜」文案（2026-09-18 第 29 轮新增）：
# 该票当日**确实上了龙虎榜**，但唯一上榜原因是**累计型榜**（连续三日/严重异常期间），
# 故它的席位记录只落在 ③ 累计型榜里，日度矩阵中**一格数据都没有**。
# 旧口径只判「有没有上过榜」（board_codes 含即 True）→ 这类票在日度矩阵里表现为
# 大片裸 `—`，与「真没出榜」视觉上完全一致却无任何标注（用户 09-18 以博汇科技 09-17 指出）。
# 全量实测：19 天共 **212 个 (标的, 日期)** 组合属此类，且 100% 的上榜原因都是累计型榜。
SHOW_MO_HTML = '<span class="ob mo">仅累计榜</span>'


# ============== 基础工具 ==============
def fmt_amt(v):
    """金额紧凑：≥1亿显示为亿（2位小数），否则万（整数）"""
    if v is None or v == 0:
        return "0"
    a = abs(v)
    if a >= 10000:
        return f"{v/10000:.2f}亿"
    return f"{v:.0f}万"


def cls(v):
    return "up" if (v is not None and v >= 0) else "down" if v is not None else "flat"


def chg_html(r):
    """标的旁的当日涨跌幅（红涨绿跌 A 股语义；取该行最近一次上榜日的读数）"""
    c = r.get("chg")
    if not isinstance(c, (int, float)):
        return ""
    return f'<span class="chg {cls(c)}">{c:+.2f}%</span>'


def stock_cell(r):
    """标的次列：第一行股票名，第二行代码 + 当日涨跌幅（分行显示，字号放大）。

    2026-09-17 新增：视图①（席位→个股）里股票名可点击 → 跳到视图② 该股详情块。
    用 `<a href="#s-<code>">` 而非 button：原生锚点语义（可右键新开、可被浏览器
    原生 focus 环提示），目标元素由渲染端写死 id（见 render_pivot 的块首）。
    ⚠️ 同一只票可能在多个席位块下重复出现 → id 只能有一个，靠全局 JS
    按 data-stock 找「当前视图里第一个可见块」来决定落点。
    """
    return (f'<td class="c2 stock-cell">'
            f'<a class="stock-link" href="#s-{r["code"]}" data-jump="{r["code"]}"'
            f' title="跳到「② 个股 → 席位」查看 {r["stock"]} 详情">{r["stock"]}</a>'
            f'<span class="sub-line"><span class="code">{r["code"]}</span>'
            f'{chg_html(r)}</span></td>')


def c1_head(r, key1, rowspan, gcls, depts_html):
    """块首列（rowspan 合并）：
    - key1 == "stock"（视图② 个股→席位）：标的块首列 → 股票名 + 代码
      （逐日涨跌幅改由块内首行的「当日涨跌幅」行承载，此处不再重复）
    - key1 == "player"（视图① 席位→个股）：席位名 + 名下营业部浅色小字
    """
    if key1 == "stock":
        return (f'<th scope="row" class="c1 stock-head{gcls}" rowspan="{rowspan}">'
                f'<span class="stock-name">{r["stock"]}</span>'
                f'<span class="sub-line"><span class="code">{r["code"]}</span></span></th>')
    return (f'<th scope="row" class="c1{gcls}" rowspan="{rowspan}">'
            f'{r[key1]}{depts_html}</th>')


def on_board_val(board_by, code, d):
    """该标的在日期 d 的**日度榜**状态（三态，2026-09-18 第 29 轮扩为三态）。

    board_by: {code: {date: state}}，state ∈ {"day", "multi"}（见 build 里的构造注释）
      · "day"   → 当日上了**日度榜**（有席位明细行）→ 该席位没出现就是干净的 `—`
      · "multi" → 当日**只上了累计型榜** → 日度矩阵里必然无一格数据 → 标「仅累计榜」

    返回：
      "day"   = 上了日度榜（席位没参与时显示 `—`）
      "multi" = 仅上累计型榜（日度矩阵无数据可看）
      False   = 当日**根本没出榜** → 标「未出榜」
      None    = 无该股任何记录（数据缺失，不做标注，向后兼容旧 json）
    """
    if not board_by or not code:
        return None
    s = board_by.get(code)
    if s is None:
        return None
    if d not in s:
        return False
    return s[d] if s[d] in ("day", "multi") else "day"


def chg_row(r, bidx, blk, gcls, rowspan, dates_desc, date_to_grp, chg_by, board_by=None,
            stock_anchor_code=None):
    """视图② 每个个股块的首行：「当日涨跌幅」行。

    该股在每个交易日的涨跌幅是**股票的属性**（与席位无关），故独立成行、
    落在各自的日期列里，与下方各席位的净额逐列对齐（2026-09-16 用户要求：
    「显示每一天这个个股的涨跌幅，新起一行写在日期下面」）。
    本行同时承载 block 的 c1（标的），故 c1 的 rowspan = 本行 + 该段全部行数。
    标记 data-sec="fresh" → 参与 JS 的 rowSpan 重算与折叠（始终可见）。

    该行只承载**涨跌幅**（股票的行情属性）。空格是「没行情数据」→ 标「无数据」。
    「未出榜」/「仅累计榜」（该票当日没上日度榜）改由**块内首个席位行**统一标注一次，
    见 render_pivot。

    stock_anchor_code：视图② 传该股 code → 本行带 `id="s-<code>"` 与 `data-stock="<code>"`，
      作为视图① 「点击标的跳转」的**原生锚点落点**（并在跳转后由 JS 加高亮类）。
      视图① 不传（None）→ 不输出该 id，保证 id 全站唯一。
    """
    # ⚠️ 日期组名走行级 data-grps（按列序，逗号分隔；全部同组时只写一次值）。
    #    2026-09-18 第 29 轮后日期列**常显**，data-grps 不再驱动显隐，
    #    仅作为分组信息的载体保留（供未来分组高亮/统计使用）。
    grp_seq = _grp_seq_for(dates_desc, date_to_grp)
    anchor = (f' id="s-{stock_anchor_code}" data-stock="{stock_anchor_code}"'
              if stock_anchor_code else "")
    tr = [f'<tr class="chgrow {blk} blk-start" data-blk="{bidx}" data-sec="fresh" '
          f'data-c2="1" data-grps="{grp_seq}"{anchor}>']
    tr.append(f'<th scope="row" class="c1 stock-head{gcls}" rowspan="{rowspan}">'
              f'<span class="stock-name">{r["stock"]}</span>'
              f'<span class="sub-line"><span class="code">{r["code"]}</span></span></th>')
    tr.append('<td class="c2 chg-label">当日涨跌幅</td>')
    for d in dates_desc:
        c = chg_by.get(d)
        if isinstance(c, (int, float)):
            tr.append(f'<td class="c x">'
                      f'<span class="{cls(c)}">{c:+.2f}%</span></td>')
        else:
            tr.append('<td class="e x"><span class="na">无数据</span></td>')
    tr.append("</tr>")
    return "".join(tr)


def short_dept(name):
    """短化营业部名展示"""
    s = name
    s = s.replace("股份有限公司", "").replace("有限责任公司", "").replace("有限公司", "")
    s = re.sub(r"(证券营业部|营业部|证券分公司|分公司)$", "", s)
    return s.strip()


# ============== 数据加载 ==============
# ⚠️ DB 位置由 lhb_store.find_db() 统一决定（本机 data/lhb.db → 云端 /srv/lhb/lhb.db），
#    不能靠 ROOT 硬推：云端脚本目录 root 拥有、other 只读，www-data 的 cron 也要读。
if os.path.join(ROOT, "scripts") not in sys.path:
    sys.path.insert(0, os.path.join(ROOT, "scripts"))
try:
    from lhb_store import find_db
    DB_FILE = find_db()
except Exception:
    DB_FILE = os.path.join(ROOT, "data", "lhb.db")


def load_days():
    """读全部交易日（旧 JSON 同构的 list[dict]）。

    2026-09-18 第 30 轮：数据源由「每日一个 JSON」改为 **SQLite 单库**。
    返回结构**完全不变**（`_load_bulk` 负责还原旧 dict 形状），故本函数以下
    所有消费逻辑（build_v1/build_v2/render_*）**无需任何改动**。
    遗留 JSON（daily/lhb_seats/*.json）仍作兜底：库不存在时回退旧逻辑。
    """
    # 优先 SQLite
    if os.path.exists(DB_FILE):
        try:
            from lhb_store import LhbStore
            st = LhbStore(DB_FILE).init()
            try:
                return st.load_days(limit=MAX_DAYS)
            finally:
                st.close()
        except Exception as e:
            print(f"⚠️ SQLite 读取失败，回退 JSON：{e}")
    # 兜底：旧 JSON
    if not os.path.isdir(STORE_DIR):
        return []
    files = sorted(glob.glob(os.path.join(STORE_DIR, "*.json")))[-MAX_DAYS:]
    out = []
    for f in files:
        try:
            with open(f, encoding="utf-8") as fp:
                out.append(json.load(fp))
        except Exception:
            continue
    return out


def load_pool():
    """读席位池 CSV（唯一源，可 Excel 手工编辑）。
    CSV 列: group,id,depts,style,focus,shared
    depts 每行一个营业部；同一 id 的多个营业部拆成多行（group/id 重复），
    style/focus/shared 只在首行填写，此处按 (group,id) 聚合 + 向前填充。
    group 取值: famous/other/quant/deprecated（与 lhb_seat_track.py 的 GROUP_ORDER 保持一致）
    ⚠️ 2026-09-16 修复：旧白名单 ("quant","daily","node","watch","deprecated","exclude") 漏掉
       famous 与 other → 看板「席位池配置」里知名游资/其他两张卡整段消失、只剩量化+弃用，
       且残留一张已废除的「排除组」卡（显示「共 0 类」）。白名单必须与 GROUP_ORDER 同步。
    """
    import csv
    pool = {}
    _seen = {}  # (group,id) -> item
    with open(POOL_FILE, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            g = (row.get("group") or "").strip()
            id_ = (row.get("id") or "").strip()
            if not id_ or g not in GROUP_ORDER:
                continue
            # 兼容两种写法：拆行后的单值，或 Excel 里偶尔写回的 | 合并
            depts = [d.strip() for d in (row.get("depts") or "").split("|") if d.strip()]
            key = (g, id_)
            item = _seen.get(key)
            if item is None:
                item = {
                    "id": id_,
                    "depts": [],
                    "style": row.get("style") or "",
                    "focus": row.get("focus") or "",
                    "shared": row.get("shared") or "",
                }
                _seen[key] = item
                pool.setdefault(g, []).append(item)
            for d in depts:
                item["depts"].append(d)
            # 向前填充 style/focus/shared（首行填值，后续行可能留空）
            if not item["style"]:
                item["style"] = row.get("style") or ""
            if not item["focus"]:
                item["focus"] = row.get("focus") or ""
            if not item["shared"]:
                item["shared"] = row.get("shared") or ""
    return pool


def load_ops(days, group_keys=("quant", "famous", "other", "retail")):
    """展开所有操作记录为扁平列表；每条带 dept（具体营业部）、player（选手代号）。

    展示层合并（2026-09-07 v1 归纳法）：
      · quant 组（外资量化/机构量化/量化打板）合并为虚拟席位「量化席位」；
      · other 组（含未匹配兜底）合并为虚拟席位「其他」；
      · famous 知名游资按独立 id 展示。
    落盘 json 仍按池内 id 归因存储，不动数据源。
    """
    ops = []
    for d in days:
        for g in group_keys:
            for pid, info in d.get(g, {}).items():
                depts = info.get("depts", [])
                if g == "quant":
                    player = "量化席位"
                elif g == "other":
                    # other 组内「其他」为兜底合并；「机构北向」等独立 id 保留原名
                    player = "其他" if pid == "其他" else pid
                else:
                    player = pid
                for op in info.get("ops", []):
                    ops.append({
                        "group": g,
                        "player": player,
                        "dept": op.get("dept") or (depts[0] if depts else ""),
                        "stock": op.get("name", ""),
                        "code": op.get("code", ""),
                        "date": d["date"],
                        "buy": op.get("buy", 0) or 0,
                        "sell": op.get("sell", 0) or 0,
                        "net": op.get("net", 0) or 0,
                        "chg": op.get("chg"),
                    })
    return ops


def build_v1(ops):
    """视图1：聚合到 (player, stock)；同时收集该行实际动用的营业部（c1 席位名下浅色展示）
    与标的最近一次上榜日的涨跌幅（c2 标的旁展示）"""
    d = OrderedDict()
    for op in ops:
        k = (op["player"], op["stock"])
        if k not in d:
            d[k] = {"group": op["group"], "player": op["player"],
                    "stock": op["stock"], "code": op["code"], "by_date": {}, "depts": [],
                    "chg": None, "chg_date": None}
        bd = d[k]["by_date"]
        e = bd.setdefault(op["date"], {"buy": 0, "sell": 0, "net": 0})
        e["buy"] += op["buy"]
        e["sell"] += op["sell"]
        e["net"] += op["net"]
        if op["dept"] and op["dept"] not in d[k]["depts"]:
            d[k]["depts"].append(op["dept"])
        if op.get("chg") is not None and (d[k]["chg_date"] is None or op["date"] > d[k]["chg_date"]):
            d[k]["chg"] = op["chg"]; d[k]["chg_date"] = op["date"]
    return list(d.values())


def build_v2(ops):
    """视图2：聚合到 (stock, player) — c2 显示席位（player）。
    同一只票下同一席位只 1 行（即使多 dept 上榜），避免 c2 出现同名多行。
    合并虚拟席位（量化席位/机构北向/其他/国泰海通上海分）额外收集 subs：
    {dept: {date: {net,buy,sell}}}——按「营业部×日期」展开成表格明细行（2026-09-14 用户要求：
    点击 ▾ 在表格内按行展开，每个营业部一行、金额落到各自日期列，替代此前的汇总浮层）
    """
    d = OrderedDict()
    for op in ops:
        k = (op["stock"], op["player"])
        if k not in d:
            d[k] = {"stock": op["stock"], "code": op["code"],
                    "player": op["player"], "group": op["group"],
                    "depts": [], "by_date": {}, "subs": {},
                    "chg": None, "chg_date": None, "chg_by": {}}
        row = d[k]
        if op["dept"] not in row["depts"]:
            row["depts"].append(op["dept"])
        bd = row["by_date"]
        e = bd.setdefault(op["date"], {"buy": 0, "sell": 0, "net": 0})
        e["buy"] += op["buy"]
        e["sell"] += op["sell"]
        e["net"] += op["net"]
        if op.get("chg") is not None and (row["chg_date"] is None or op["date"] > row["chg_date"]):
            row["chg"] = op["chg"]; row["chg_date"] = op["date"]
        # 逐日涨跌幅（该股当日表现，与席位无关）——用于视图②块首「当日涨跌幅」行
        if op.get("chg") is not None:
            row["chg_by"][op["date"]] = op["chg"]
        dept_map = row["subs"].setdefault(op["dept"] or "(未识别)", {})
        sub = dept_map.setdefault(op["date"], {"net": 0, "buy": 0, "sell": 0})
        sub["net"] += op["net"]
        sub["buy"] += op["buy"]
        sub["sell"] += op["sell"]
    return list(d.values())


# ============== 渲染 ==============
def op_cell(op, maxabs=1, extra_cls="", on_board=None, show_ob=False):
    """单元格：净额突出 + 强度条（背景横向条，零额外行高）+ 买/卖次要。

    extra_cls：附加 CSS 类（当前留空；日期组名写在 `<tr data-grps>` 上，
      见 2026-09-17 瘦身：原先每个 td 上带 `grp-gN`（全站 47270 次 ≈ 283.6 KB），
      而 theme 里没有任何 CSS 规则消费该 class，属「用 class 当数据载体」的浪费。
      2026-09-18 第 29 轮：日期组折叠已删除，data-grps 仅保留为分组信息载体。）

    on_board：该 (标的, 日期) 的**日度榜**状态（2026-09-18 第 29 轮扩为三态）。区分三类「空」：
      · on_board is False   → 该票当日**根本没出榜** → 整格标「未出榜」（不做压暗：用户明确
        不喜欢整列压暗的显示方式，只用文字标注）
      · on_board == "multi" → 该票当日**只上了累计型榜** → 整格标「仅累计榜」
        （席位记录在 ③ 段，日度矩阵无一格数据；用户 09-18 以博汇科技 09-17 指出）
      · on_board == "day"   → 该票当日**上了日度榜**，只是这个席位没出现 → 显示干净的 `—`
      传 None 时同样显示 `—`（向后兼容，如累计型榜区块）。
    show_ob：是否渲染标注文案。

    ⛔ **2026-09-18 修正（第 23 轮）—— 口径从「每块 1 个」改为「逐列标注」**：
      用户 09-16 原话「不用重复那么多次未出榜，一天合并显示一次就可以」，我原先理解为
      「**每块只标一次**」并实现了 `ob_slot_date`（只选第一个出空格的日期列）——**理解错了**。
      用户 09-18 用两张截图纠正，正确语义是两个正交的轴：
        · **横向**：每个「该票未出榜」的日期列**都要标**，一个都不能省
                  （沐曦股份连续 9 天未出榜 → 9 列全标）；
        · **纵向**：同一日期列**不要被重复标 N 次**。
      ⭐ 去重粒度 = **(标的 code, 日期)** —— 「是否出榜」是**标的一级的事实**
      （`board_by[date]` 装当日全市场上榜票），与席位无关（`render_pivot` 的 `ob_stock_seen`）：
        · 视图②（key1=stock，多席位行共享同一只票）→ **只有块内首行标**；
          否则 6 个席位行 × 同样 8 个空列 = 同列被标 6 次（用户 09-16 抱怨的噪音）；
        · 视图①（key1=player，**每行是不同票**）→ **每行都标**；
          若按「块内首行」去重，一块 10 只票里只有第 1 只能标，通鼎/平潭全被吞（用户 09-18 截图）。
      实测样例：沐曦股份 09-16~09-04 连续 9 天未出榜 → **9 列全标**（原先只标 1 列）。
      ⚠️ 三个曾经踩过的坑（都会导致「该标没标」或「重复刷屏」）：
        ① `show_ob=(i == 0)` —— 视图② 块首是 chg_row、不经 op_cell，`i==0` 落到第二个
           数据行且该行 on_board 常为 True → 标注数恒为 0（死代码）；
        ② `show_ob=not is_virtual` —— 若某块**只有虚拟行**（沐曦股份 09-17 仅出现在
           量化席位/机构北向/其他三个聚合桶里），全块 72 个空格全是裸 `—`；
        ③ 按 `key2`（席位）去重 —— 视图② 每个席位行都标同一批空列 → 同列重复 6 次。
    """
    add = f" {extra_cls}" if extra_cls else ""
    if not op:
        if on_board is False:
            if show_ob:
                return f'<td class="e o">{SHOW_OB_HTML}</td>'
            return '<td class="e o">—</td>'
        if on_board == "multi":
            if show_ob:
                return f'<td class="e o">{SHOW_MO_HTML}</td>'
            return '<td class="e o">—</td>'
        return '<td class="e">—</td>'
    net = op.get("net", 0)
    arrow = "↑" if net >= 0 else "↓"
    buy = op.get("buy", 0)
    sell = op.get("sell", 0)
    # 强度条：宽度 ∝ |净额| / 该视图最大绝对值，上限 100%。
    # 方向色改由 .u / .d 两个类承载（原先写成 style="--bar:var(--bar-up)"，每格多 17 字节）
    w = min(100.0, abs(net) / maxabs * 100) if maxabs else 0
    dir_cls = "u" if net >= 0 else "d"
    return (f'<td class="c {dir_cls}" style="--w:{w:.1f}%">'
            f'<span class="net {cls(net)}">{arrow}{fmt_amt(net)}</span>'
            f'<span class="bs">买{fmt_amt(buy)}·卖{fmt_amt(sell)}</span></td>')


def build_date_groups(dates_desc):
    """把日期列表按连续 N_DATES_PER_GROUP 个一组倒序打包。
    返回 [{g:"g0", dates:[...], label:"2026-09-01 ~ 2026-08-28", count:N}, ...]（最新一组在前）
    """
    groups = []
    for i in range(0, len(dates_desc), N_DATES_PER_GROUP):
        chunk = dates_desc[i:i + N_DATES_PER_GROUP]
        groups.append({
            "g": f"g{i // N_DATES_PER_GROUP}",
            "dates": chunk,
            "label": f"{chunk[-1]} ~ {chunk[0]}",
            "count": len(chunk),
        })
    return groups


def _grp_seq_for(dates_desc, date_to_grp):
    """生成行级 `data-grps` 的值（2026-09-17 瘦身）。

    - 全部日期列同组 → 只返回组名（如 "g0"），JS 侧整体套用（游程编码，省 ~190 KB）
    - 跨组 → 返回逗号分隔的按列序序列（如 "g0,g0,g1,g1"）

    没有日期列时返回空串（该 tr 不带 data-grps，JS 会跳过）。
    """
    seq = [date_to_grp[d] for d in dates_desc if d in date_to_grp]
    if not seq:
        return ""
    if len(set(seq)) == 1:
        return seq[0]
    return ",".join(seq)


def max_abs_net(rows):
    """视图内净额绝对值上限，用于强度条归一化"""
    m = 0.0
    for r in rows:
        for v in r["by_date"].values():
            m = max(m, abs(v.get("net", 0)))
    return m or 1.0


# 展示层合并的虚拟席位（c2 ▾ 可展开营业部明细行的对象）
VIRTUAL_PLAYERS = ("量化席位", "机构北向", "其他", "国泰海通上海分",
                   "散户通道", "非营业部")


def group_badge(g):
    if g == "quant":
        return '<span class="badge b-quant">量化</span>'
    if g == "famous":
        return '<span class="badge b-famous">游资</span>'
    if g == "other":
        return '<span class="badge b-other">其他</span>'
    return '<span class="badge b-watch">待核</span>'


def render_pivot(rows, dates_desc, key1, key2, label1, label2, cls_name, badge=False,
                 collapse_stale=False, sort_mode="group", stock_chg=None, board_by=None):
    """通用 pivot 渲染。首列固定为 key1（席位/标的），每块仅首行显示（rowspan 合并）。

    Carbon 式块区分：行背景 #fff / #f4f4f4 交替（背景分层而非彩色），
    首列左侧 3px 组别竖条 + 块首行 2px 上边框。不整行染色以保住红绿数字对比度。

    v7 折叠（Excel outline 风格）：
      · 日期列分组：表头两行 —— 上行粘性分组条，每组有 [-]/[+] 独立切换，
        默认仅展开最新一组（g0）；下行为单日 th 带 data-grp 标记供折叠联动。
      · 个股行折叠（collapse_stale=True）：每个席位块分两段（近期/历史），
        中间插入 `<tr class="grp-bar">[+] N 条历史</tr>`，默认隐藏历史段；
        块首行永远可见（否则席位整块消失）。
      · rowspan 重算：服务端按「默认可见行数」输出，展开/收起时由 JS 重算。
    """
    if not rows:
        return '<div class="empty">暂无数据</div>'
    latest = dates_desc[0]
    maxabs = max_abs_net(rows)
    # 按 key1 聚合排序：同 key1 的所有行连续排列成一个块
    # 展示顺序（2026-09-07 用户拍板）：知名游资 → 其他（小游资/小席位）→ 量化
    g_order = {"famous": 0, "other": 1, "quant": 2, "retail": 3, None: 4}
    date_idx = {d: i for i, d in enumerate(dates_desc)}

    def _on_board(code, d):
        """该标的在日期 d 是否上了龙虎榜（见模块级 on_board_val）。"""
        return on_board_val(board_by, code, d)
    k1p = {}
    for r in rows:
        k = r[key1]
        g = g_order.get(r.get("group"), 3)
        info = k1p.setdefault(k, {"g": g, "net_latest": 0.0, "last_idx": len(dates_desc), "net_last": 0.0})
        info["g"] = min(info["g"], g)
        info["net_latest"] = max(info["net_latest"], abs(r["by_date"].get(latest, {}).get("net", 0)))
        for dte, e in r["by_date"].items():
            if e.get("net"):
                idx = date_idx.get(dte, len(dates_desc))
                if idx < info["last_idx"]:
                    info["last_idx"] = idx
                    info["net_last"] = abs(e["net"])
    if sort_mode == "recent":
        # 视图②：当日（最新交易日）有交易的块排前排（按当日净额绝对值降序），
        # 当日无交易的沉后（按最近一次活跃日贴近度，越近越前）
        rows.sort(key=lambda r: (
            0 if k1p[r[key1]]["net_latest"] else 1,
            -k1p[r[key1]]["net_latest"],
            k1p[r[key1]]["last_idx"],
            -k1p[r[key1]]["net_last"],
            r[key1],
        ))
    else:
        # 视图①：组序（游资→其他→量化）→ 组内最新日净额绝对值降序
        rows.sort(key=lambda r: (
            k1p[r[key1]]["g"],
            -k1p[r[key1]]["net_latest"],
            r[key1],
            -abs(r["by_date"].get(latest, {}).get("net", 0)),
        ))

    # 切块（同 key1 连续）
    blocks, prev = [], None
    for r in rows:
        if r[key1] != prev:
            blocks.append([])
            prev = r[key1]
        blocks[-1].append(r)

    recent_days = set(dates_desc[:N_RECENT_STOCK_DAYS])
    date_groups = build_date_groups(dates_desc)
    # 每日期对应组映射（用于给 td 打 data-grp）
    date_to_grp = {d: g["g"] for g in date_groups for d in g["dates"]}
    # 日期组名的**行级载体**（按列序，逗号分隔，如 "g0,g0,g0,g1,g1"；全部同组时只写一次）。
    # 2026-09-17 瘦身：原先每个数据格都带 `grp-gN` class（全站 47270 处 ≈ 283.6 KB），
    # 但 theme 里没有任何 CSS 消费它 —— 唯一消费者是 JS 的正则解析。
    # 改为每个 tr 只写一次 data-grps，JS 按 td 的「数据格出现序号」索引取组名。
    grp_seq = _grp_seq_for(dates_desc, date_to_grp)

    # ---- 表头：两行 th ----
    # 上行：粘性分组条（横跨该组的所有日期列）
    # 2026-09-18 第 29 轮：**移除日期组收起/展开按钮**（用户：「日期上的收起展开按钮似乎
    # 已经不适合现在的模式了，可以连同相关功能一起删掉」）。
    # 原先这里输出 <button class="grp-btn">（含 .arr +/−、.lab、.cnt），并靠 aria-expanded
    # 驱动 JS 的 grpHide 折叠整组日期列（折叠组走绝对定位挤到表头右缘）。
    # 现改为**纯文本标签**：日期列全部常显，不再有任何折叠态。
    # 连带删除：JS 的 initGrpHide/grpHide/applyDateGroups/syncDateGroupBtns/点击委托、
    #          lhb_ui.js 的日期组部分 syncDisclosureControls + 镜像表头按钮转交、
    #          theme 里 .grp-th.is-collapsed / --lhb-collapse-index 全部规则。
    # 保留：th.grp-th 本身（分组横幅的底色与右边框仍在，只是不可点）。
    grp_ths = []
    for gi, g in enumerate(date_groups):
        grp_ths.append(
            f'<th scope="colgroup" class="grp-th" data-grp="{g["g"]}" '
            f'colspan="{g["count"]}">'
            f'<span class="grp-lab">{g["label"]}</span>'
            f'<span class="grp-cnt">{g["count"]}日</span>'
            f'</th>'
        )
    # 下行：单日 th（仅 c1/c2 + 单日）
    day_ths = []
    for d in dates_desc:
        grp = date_to_grp[d]
        # 给 c1/c2 占位的占位 th（与上行 colspan 对齐）
        day_ths.append(f'<th scope="col" class="dt" data-grp="{grp}">{d}</th>')
    # c1/c2 占位 th 跨两行
    placeholder_c1 = f'<th scope="col" class="c1 grp-spn" rowspan="2">{label1}</th>'
    placeholder_c2 = f'<th scope="col" class="c2 grp-spn" rowspan="2">{label2}</th>'

    out = [
        # 手机端横向滚动提示（桌面端 CSS 隐藏 .scroll-hint）
        f'<div class="scroll-hint"></div>'
        f'<div class="scroll"><table class="matrix {cls_name}" '
        f'data-collapse="{1 if collapse_stale else 0}"><thead>'
        # 第一行：c1/c2 占位 + 分组条
        f'<tr class="grp-row">{placeholder_c1}{placeholder_c2}{"".join(grp_ths)}</tr>'
        # 第二行：单日 th（无 c1/c2，被上面 rowspan）
        f'<tr class="day-row">{"".join(day_ths)}</tr>'
        f'</thead><tbody>'
    ]

    for bidx, block in enumerate(blocks, start=1):
        # 块级营业部汇总（仅视图① badge=True：c1 席位名下浅色小字展示实际动用的营业部）
        depts_html = ""
        if badge:
            blk_depts = []
            for r in block:
                for dp in r.get("depts", []):
                    if dp and dp not in blk_depts:
                        blk_depts.append(dp)
            if blk_depts:
                esc = lambda s: s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                n_all = len(blk_depts)
                show = blk_depts[:5] if n_all > 6 else blk_depts
                tail = f'<span class="more">…等 {n_all} 家</span>' if n_all > 6 else ""
                depts_html = ('<div class="c1-depts">' + "<br/>".join(esc(x) for x in show) + tail + "</div>")

        # 逐行判定是否「近期无操作」—— 仅 collapse_stale=True 时启用
        if collapse_stale:
            recent_set = set(dates_desc[:N_RECENT_STOCK_DAYS])
            stale = [not (set(r["by_date"].keys()) & recent_set) for r in block]
            stale[0] = False                      # 块首行永远可见
            n_hidden = sum(stale)
            n_visible = len(block) - n_hidden
        else:
            stale = [False] * len(block)
            n_hidden = 0
            n_visible = len(block)

        if collapse_stale and n_hidden:
            # 段切：近期段（fresh）+ 分组条 + 历史段（stale）
            fresh_block = [r for r, s in zip(block, stale) if not s]
            stale_block = [r for r, s in zip(block, stale) if s]
        else:
            fresh_block = block
            stale_block = []

        # ---- 「未出榜」标注（2026-09-18 第 23 轮修正：逐列标注） ----
        # 原先的 `ob_slot_date`（全块只选 1 个日期列标一次）是对用户 2026-09-16
        # 「不用重复那么多次未出榜，一天合并显示一次就可以」的**误读**。正确语义是两个轴：
        #   · **横向**：每个「该票未出榜」的日期列**都要标**，一个都不能省
        #              （沐曦股份连续 9 天未出榜 → 9 列全标）；
        #   · **纵向**：同一日期列**不要被重复标 N 次**。
        # ⭐ 去重粒度 = **(标的 code, 日期)** —— 因为「是否出榜」是**标的一级的事实**
        #    （`board_by[date]` 装的是当日全市场上榜票），与席位无关：
        #   · 视图②（key1=stock，多席位行共享同一只票）：**只有块内首行标**。
        #     若不按标的去重，则 6 个席位行 × 同样的 8 个空列 = 同列被标 6 次（用户 09-16
        #     抱怨的噪音，实测通鼎互联块 6 行各标 8 列）。
        #   · 视图①（key1=player，每行是**不同的**票）：**每行都标**。
        #     若按「块内首行」去重，则一块 10 只票里只有第 1 只标得上，通鼎互联/平潭发展
        #     全被吞掉 —— 正是用户 09-18 截图指出的问题。
        # 落地：`ob_stock_seen` 记录本块已标注过的标的 code；视图② 同一 code 只标首行，
        #      视图① 每行 code 都不同故逐行生效。
        ob_stock_seen = set()

        # ---- 逐日涨跌幅（仅视图②：该股当日表现，覆盖全部日期列） ----
        # 优先用 json 的 stock_chg（东财日线，含**非上榜日**，见 lhb_seat_track.fetch_stock_chg）；
        # 回退到各 op 自带的上榜日 chg（旧数据无 stock_chg 时）
        chg_by = {}
        if key1 == "stock":
            for r in block:
                for dt, c in (r.get("chg_by") or {}).items():
                    if c is not None:
                        chg_by[dt] = c
            for r in block:
                for dt, c in ((stock_chg or {}).get(r.get("code")) or {}).items():
                    if c is not None:
                        chg_by[dt] = c

        # ---- 近期段（c1 rowspan 跨本段行数 = n_visible，分组条不计入） ----
        for i, r in enumerate(fresh_block):
            is_first = (i == 0)
            blk = "blk-b" if bidx % 2 == 0 else "blk-a"
            # 聚合虚拟席位行（量化席位/机构北向/其他/国泰海通上海分）：可展开营业部明细行
            is_virtual = ("player" in r and r["player"] in VIRTUAL_PLAYERS and r.get("subs"))
            sub_key = f"g{bidx}r{i}" if is_virtual else ""
            gcls = f" g-{r['group']}" if r.get("group") else ""
            # 视图②：块首先输出「当日涨跌幅」行（承载 c1，rowspan = 本行 + 近期段行数）
            # blk-start（块首 2px 上边框）随之移到该行；首行席位行不再带 blk-start 与 c1
            if is_first and key1 == "stock":
                out.append(chg_row(r, bidx, blk, gcls, len(fresh_block) + 1,
                                   dates_desc, date_to_grp, chg_by, board_by,
                                   stock_anchor_code=r["code"]))
            tr = [f'<tr class="{blk}{" blk-start" if (is_first and key1 != "stock") else ""}"'
                  f' data-blk="{bidx}"']
            tr.append(' data-sec="fresh"')                       # 标记属于近期段
            tr.append(f' data-grps="{grp_seq}"')                 # 日期组名（按列序，见 op_cell 注释）
            tr.append('>')
            if is_first and key1 != "stock":
                # 注意：c1 的 rowspan 只跨「近期段」行数；分组条用单独 tr 处理 rowspan
                # 2026-09-10 用户要求：不再在席位名后显示「游资/量化/其他」组徽章（组别靠左侧竖条颜色区分）
                tr.append(c1_head(r, key1, len(fresh_block), gcls, depts_html))
            if key2 == "stock":
                tr.append(stock_cell(r))
            elif "player" in r:
                depts = r.get("depts") or [r.get("dept", "")]
                if is_virtual:
                    tr.append(f'<td class="c2" title="点 + 在表格内按营业部×日期展开明细">'
                              f'{r["player"]}'
                              f'<button type="button" class="exp-btn" data-sub="{sub_key}" aria-expanded="false" '
                              f'aria-label="展开营业部明细行" '
                              f'onclick="event.stopPropagation();LHBsubToggle(this)">+</button></td>')
                else:
                    tr.append(f'<td class="c2" title="{"&#10;".join(depts)}">'
                              f'{r["player"]}</td>')
            else:
                tr.append(f'<td class="c2">{short_dept(r["dept"])}</td>')
            # 本行是否本块内该标的的首次出现（纵向去重粒度 = (标的, 日期)，见 ob_stock_seen）
            row_code = r.get("code")
            row_show_ob = row_code not in ob_stock_seen
            for d in dates_desc:
                # 「未出榜」= 该标的**当日根本没上龙虎榜**（on_board is False），与此不同：
                # 「出榜了，但这个席位没出现在榜单上」→ 干净的 `—`。两者必须可区分（用户 09-16 要求）。
                # ⚠️ 2026-09-18（第 23 轮）：横向逐列全标；纵向按「标的」去重（视图① 逐行生效、
                #    视图② 只首行生效）。详见 ob_stock_seen 说明。
                tr.append(op_cell(r["by_date"].get(d), maxabs,
                                  on_board=_on_board(r.get("code"), d),
                                  show_ob=row_show_ob))
            tr.append("</tr>")
            out.append("".join(tr))
            ob_stock_seen.add(row_code)

            # ---- 隐藏明细行：每个营业部一行，金额落到各自日期列（点击 ▾ 展开）----
            if is_virtual:
                esc = lambda s: s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                dept_items = sorted(r["subs"].items(),
                                    key=lambda x: -sum(v["buy"] for v in x[1].values()))
                for dept_name, day_map in dept_items:
                    dtr = [f'<tr class="subrow {blk} rowhide" data-blk="{bidx}" data-sec="sub" '
                           f'data-subgroup="{sub_key}" data-grps="{grp_seq}">']
                    dtr.append(f'<td class="c2 subdet-name">{esc(dept_name)}</td>')
                    for d in dates_desc:
                        dtr.append(op_cell(day_map.get(d), maxabs,
                                           on_board=_on_board(r.get("code"), d)))
                    dtr.append("</tr>")
                    out.append("".join(dtr))

        # ---- 分组条（仅 collapse_stale 且有历史段时插入） ----
        if collapse_stale and stale_block:
            blk = "blk-b" if bidx % 2 == 0 else "blk-a"
            # 分组条横跨 c1 + c2 + 所有日期列；用 colspan 让它独占一行
            total_cols = 2 + len(dates_desc)
            bar = (
                f'<tr class="grp-bar {blk}" data-blk="{bidx}" data-sec="bar">'
                f'<td colspan="{total_cols}">'
                f'<button type="button" class="grp-bar-btn" data-grpbar="{bidx}" '
                f'aria-expanded="false" aria-label="展开 {n_hidden} 条历史">'
                f'<span class="arr">+</span>'
                f'<span class="lab">{n_hidden} 条历史</span>'
                f'<span class="hint">近 {N_RECENT_STOCK_DAYS} 日无买卖</span>'
                f'</button>'
                f'</td></tr>'
            )
            out.append(bar)

        # ---- 历史段（默认隐藏；通过 grp-bar-btn 切换） ----
        # 为了让 c1 的 rowspan 在展开后跨「近期段 + 历史段」完整，需要 JS 重算 c1.rowSpan
        for i, r in enumerate(stale_block):
            blk = "blk-b" if bidx % 2 == 0 else "blk-a"
            is_virtual = ("player" in r and r["player"] in VIRTUAL_PLAYERS and r.get("subs"))
            sub_key = f"g{bidx}h{i}" if is_virtual else ""
            tr = [f'<tr class="{blk} rowhide" data-blk="{bidx}" data-sec="hist" '
                  f'data-grps="{grp_seq}">']
            if key2 == "stock":
                tr.append(stock_cell(r))
            elif "player" in r:
                depts = r.get("depts") or [r.get("dept", "")]
                if is_virtual:
                    tr.append(f'<td class="c2" title="点 + 在表格内按营业部×日期展开明细">'
                              f'{r["player"]}'
                              f'<button type="button" class="exp-btn" data-sub="{sub_key}" aria-expanded="false" '
                              f'aria-label="展开营业部明细行" '
                              f'onclick="event.stopPropagation();LHBsubToggle(this)">+</button></td>')
                else:
                    tr.append(f'<td class="c2" title="{"&#10;".join(depts)}">'
                              f'{r["player"]}</td>')
            else:
                tr.append(f'<td class="c2">{short_dept(r["dept"])}</td>')
            row_code = r.get("code")
            row_show_ob = row_code not in ob_stock_seen
            for d in dates_desc:
                # 同 fresh 段口径：横向逐列全标，纵向按「标的」去重。
                tr.append(op_cell(r["by_date"].get(d), maxabs,
                                  on_board=_on_board(r.get("code"), d),
                                  show_ob=row_show_ob))
            tr.append("</tr>")
            out.append("".join(tr))
            ob_stock_seen.add(row_code)
            if is_virtual:
                esc = lambda s: s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                dept_items = sorted(r["subs"].items(),
                                    key=lambda x: -sum(v["buy"] for v in x[1].values()))
                for dept_name, day_map in dept_items:
                    dtr = [f'<tr class="subrow {blk} rowhide" data-blk="{bidx}" data-sec="sub" '
                           f'data-subgroup="{sub_key}" data-grps="{grp_seq}">']
                    dtr.append(f'<td class="c2 subdet-name">{esc(dept_name)}</td>')
                    for d in dates_desc:
                        dtr.append(op_cell(day_map.get(d), maxabs,
                                           on_board=_on_board(r.get("code"), d)))
                    dtr.append("</tr>")
                    out.append("".join(dtr))

    out.append("</tbody></table></div>")
    return "".join(out)


def render_multi(days, pool=None, max_days=10):
    """累计型榜（三日榜 / 严重异常期间）——**矩阵形式**（2026-09-18 第 29 轮改版）。

    ⛔ 为什么必须与日度矩阵分开：这些记录的金额是**区间累计**（连续三个交易日、
    严重异常期间等），口径与「当日发生额」完全不同，混在一起就会出现
    「消闲派当日净买 1.47亿」这类错误（实为 3 日累计）。
    数据源＝各日 json 的 `multi_day`（按 |净额| 降序 TOP60）。

    ⭐ 展示形式（用户 09-18 要求）：原先是「席位·标的·区间净额·上榜原因」的**扁平长表**，
    现改为与上方日度矩阵同构的 **pivot 矩阵**：
      · 每天一个矩阵（原生 <details>，最新一日默认 open）
      · 行 = 席位（**池内席位优先**，未匹配的营业部归到「其他」桶），按 |区间净额| 合计降序
      · 列 = 该日**涉及的标的**（按当日该股区间净额绝对值合计降序），列头带标的涨跌幅
      · 格 = 该 (席位, 标的) 的**区间净额**（红=净买 / 绿=净卖，同全站 A 股语义）
    与日度矩阵的关键差异（必须在页面上讲清）：
      ① 金额是**区间累计**，不是当日发生额 → 卡片顶部强警示
      ② 列不是日期而是**标的**（累计榜本身即跨日区间，故以标的为维度）
      ③ 不做强度条归一化（区间累计额量级与日度不可比，画条会误导）
    """
    ds = [d for d in reversed(days[-max_days:]) if (d.get("multi_day") or [])]
    if not ds:
        return ""
    total = sum(d.get("n_multi_day", 0) for d in days)

    # dept → 池内席位名 的映射（池内优先；未命中**保留营业部自己的行**，见下）
    dept2player = {}
    if pool:
        for g in ("famous", "quant", "other"):
            for item in pool.get(g, []):
                for dpt in item.get("depts", []):
                    dept2player[dpt] = item["id"]

    # ⚠️ 池覆盖率实测仅 ~10%（09-17 的 40 家营业部里只有 4 家在池内）。
    # 若把未命中的 90% 全塞进单一「其他」桶，矩阵会被压成 2~3 行、完全失去信息量
    # （实测：09-17 会从「40 席 × 9 标的」塌缩成「3 席 × 9 标的」）。
    # 故采取**双层行名**：池内命中 → 用席位名（并标记 is-pool）；未命中 → 用短化营业部名独立成行。
    def row_of(dept):
        hit = dept2player.get(dept)
        if hit:
            return hit, True
        return (short_dept(dept) or dept), False

    blocks = []
    for i, d in enumerate(ds):
        rows = d["multi_day"]
        n = d.get("n_multi_day", len(rows))

        # ---- 聚合：player × code → 区间净额（同一席位多家营业部买同一只票要合并） ----
        agg = {}          # (player, code) -> net
        meta = {}         # code -> {name, chg}
        player_net = {}   # player -> 合计 |净额|
        player_pool = {}  # player -> 是否池内席位
        code_net = {}     # code -> 合计 |净额|
        # board_codes 带当日涨跌幅（multi_day 记录本身不含 chg）→ 用作列头副信息
        bc = d.get("board_codes") or {}
        for r in rows:
            code = r.get("code") or ""
            player, is_pool = row_of(r.get("dept", ""))
            net = r.get("net", 0) or 0
            agg[(player, code)] = agg.get((player, code), 0) + net
            if code not in meta:
                m0 = bc.get(code) or {}
                meta[code] = {"name": r.get("name") or m0.get("name") or code,
                              "chg": m0.get("chg")}
            player_net[player] = player_net.get(player, 0) + abs(net)
            player_pool[player] = player_pool.get(player, False) or is_pool
            code_net[code] = code_net.get(code, 0) + abs(net)

        # ---- 列（标的）：按 |区间净额| 合计降序 ----
        codes = sorted(code_net, key=lambda c: -code_net[c])
        # ---- 行（席位）：**池内席位优先**，其余按 |净额| 合计降序 ----
        players = sorted(player_net, key=lambda p: (0 if player_pool.get(p) else 1, -player_net[p]))

        # 列头：标的 + 涨跌幅（chg 取自 board_codes；缺失时只显示名与代码）
        head = ['<th class="c1" scope="col">席位</th>']
        for c in codes:
            m = meta.get(c) or {}
            nm = m.get("name") or c
            ch = m.get("chg")
            ch_html = (f'<span class="chg {cls(ch)}">{ch:+.2f}%</span>'
                       if isinstance(ch, (int, float)) else "")
            head.append(f'<th class="c2 mc-stock" scope="col" title="{nm}（{c}）">'
                        f'<span class="mc-name">{nm}</span>'
                        f'<span class="sub-line"><span class="code">{c}</span>{ch_html}</span></th>')

        body = []
        for p in players:
            seat_cls = "c1 mc-seat is-pool" if player_pool.get(p) else "c1 mc-seat"
            tds = [f'<td class="{seat_cls}">{p}</td>']
            for c in codes:
                v = agg.get((p, c))
                if v is None:
                    tds.append('<td class="e">—</td>')
                else:
                    k = "up" if v >= 0 else "down"
                    tds.append(f'<td class="c mc-num"><span class="net {k}">{fmt_amt(v)}</span></td>')
            body.append("<tr>" + "".join(tds) + "</tr>")

        n_pool = sum(1 for p in players if player_pool.get(p))
        blocks.append(
            f"<details class='md-day'{' open' if i == 0 else ''}>"
            f"<summary>{d['date']}　{n} 条记录 · {len(players)} 个席位 × {len(codes)} 只标的"
            f"（池内命中 {n_pool}）</summary>"
            "<div class='scroll'><table class='matrix mtx-multi'><thead><tr>"
            + "".join(head) + "</tr></thead><tbody>" + "".join(body)
            + "</tbody></table></div></details>")

    return (
        '<div class="card">'
        f'<h2>累计型榜（三日 / 区间累计）<span class="gcount">'
        f'{len(ds)} 个交易日 · 共 {total} 条</span></h2>'
        '<div class="note"><b>本区金额为「区间累计」，不是当日发生额</b> —— '
        '上榜原因含「连续三个交易日 / 连续3个交易日」或「严重异常期间」时，'
        '交易所披露的是<b>该区间内</b>的买入卖出合计，与上方日度矩阵口径不同，'
        '<b>不可与日度净额相加或混用</b>。'
        '仅「日涨幅偏离7% / 日换手20% / 日振幅15%」等单日榜才计入上方矩阵；'
        '当日只上累计型榜的标的不会出现在上方矩阵中（标「仅累计榜」），请在本区查看。</div>'
        '<div class="note"><b>本区矩阵的列是「标的」而非日期</b>——'
        '累计榜本身即跨日区间口径，故按「席位 × 标的」交叉展示区间净额。'
        '<b>行名规则</b>：已入席位跟踪池的营业部显示为<b>席位名</b>（左侧竖条加粗），'
        '未入池的营业部以短化营业部名独立成行（池覆盖率约一成，若全并入「其他」桶'
        '会让矩阵塌缩成 2~3 行、失去信息量）。行序＝池内席位在前，其余按区间净额降序。</div>'
        + "".join(blocks) + "</div>"
    )


def render_pool(pool):
    """席位池配置（2026-09-16 重写：修白名单漏组 + 全量显示）。

    展示对该 pool 的**完整映射**：每个 id 列出其名下全部营业部（不截断）。
    组序与矩阵视图一致：famous（知名游资）→ other（其他，含机构北向/国泰海通上海分）
    → quant（量化）→ deprecated（已弃用）。
    已废除的 exclude/daily/node/watch 不再渲染；改为一行说明（未匹配营业部自动兜底进「其他」）。

    排版（2026-09-16 第三版）：删掉「风格/跟踪要点」后仅剩 2 列，全宽表格被拉空 →
    改为**磁贴瀑布流**（`.pool-grid` CSS columns）：每个席位一个 `.pool-tile`
    ＝ 席位名 + 名下营业部清单，打包成自成一体的视觉单元，列数随宽度自适应
    （≥1280 三列 / ≥760 两列 / 更窄单列）。
    """
    GROUP_META = (
        ("famous", "FAMOUS 知名游资", "单独成块统计，矩阵中按 id 独立展示",
         "var(--g-active)"),
        ("other", "OTHER 其他", "含「其他」兜底池 + 机构北向 + 国泰海通上海分（各自独立 id）",
         "var(--g-other)"),
        ("quant", "QUANT 量化", "外资投行 / 本土券商自营与财富通道 / 打板三类，矩阵合并为「量化席位」",
         "var(--g-quant)"),
        ("retail", "RETAIL 散户通道与非营业部", "噪音层，单独成组统计，不混入「其他」",
         "var(--gray-50)"),
        ("deprecated", "DEPRECATED 已弃用", "席位已废，上榜交易非本人操作，勿据此判断",
         "var(--gray-50)"),
    )
    parts = []
    total_ids = 0
    total_depts = 0
    for gkey, gname, gdesc, gcolor in GROUP_META:
        items = pool.get(gkey, [])
        # 组内排序：营业部数多的在前（近似活跃度），再按 id 字典序稳定
        items = sorted(items, key=lambda it: (-len(it.get("depts", [])), it["id"]))
        n_dept = sum(len(it.get("depts", [])) for it in items)
        if items:
            total_ids += len(items)
            total_depts += n_dept
        tiles = []
        for it in items:
            depts = it.get("depts", [])
            # shared 列 = 「共用通道」标记（如章盟主/葛卫东共用上海江苏路）。
            # 角标固定显示「共用」二字，完整说明挂在 title 悬停（2026-09-16：此前只显示「共用」无解释，
            # 用户不得不来问含义 —— 角标必须自解释，说明文字进 title）。
            shared = it.get("shared", "").strip()
            flag = ""
            if shared:
                tip = (shared if shared.upper() != "TRUE"
                       else "该席位与其他游资共用营业部通道，单看席位名无法确定操作主体，需按标的（个股）分辨")
                tip_attr = tip.replace('"', "&quot;")
                flag = f"<span class='pflag' title=\"{tip_attr}\">共用</span>"
            if depts:
                # `高盛*` 这类尾部 `*` = 品牌前缀匹配标记，显示时剥离并加注
                lis = "".join(
                    (f"<li>{d[:-1]}<span class='wild'>·品牌前缀</span></li>"
                     if d.endswith("*") else f"<li>{d}</li>")
                    for d in depts)
            else:
                lis = "<li class='dim'>（无）</li>"
            tiles.append(
                f"<div class='pool-tile'>"
                f"<div class='pi-head'><span class='pi-name'>{it['id']}</span>{flag}"
                f"<span class='pi-cnt'>{len(depts)} 家</span></div>"
                f"<ul class='pi-depts'>{lis}</ul>"
                f"</div>"
            )
        parts.append(
            f'<div class="card" style="border-left:3px solid {gcolor}">'
            f'<h2>{gname}<span class="gcount">{len(items)} 个 id · {n_dept} 家营业部</span></h2>'
            f'<div class="note">{gdesc}</div>'
            f"<div class='pool-grid'>{''.join(tiles)}</div></div>"
        )
    parts.append(
        '<div class="card"><h2>匹配规则与兜底</h2>'
        '<div class="note"><b>全名精确匹配</b>（2026-09-16 起）：CSV 的「营业部」列写<b>完整营业部名</b>'
        '（可省「股份有限公司 / 营业部」等装饰，但不得省略地址或分支标识），匹配时归一化后<b>全等</b>才算命中——'
        '根治了旧「子串匹配」下短关键词过匹配的问题。'
        '<br/>唯一例外：带 <span class="wild">·品牌前缀</span> 标记的用品牌前缀匹配（仅外资投行，如高盛*）。'
        '<br/><b>兜底</b>：池中未列出的营业部自动归入 OTHER 组的「其他」id（不丢弃）——'
        '新席位先落这里观察，若持续出现且成交可观，再结合龙虎榜行为与外部信息判断是否晋升具名席位。'
        '已废除分组：exclude（剔除）、daily、node、watch。</div></div>'
    )
    parts.insert(0, f'<div class="card summary"><h2>全量汇总</h2>'
                    f'<div class="note">本段为席位池的<b>完整映射</b>：'
                    f'<b>{total_ids}</b> 个 id · <b>{total_depts}</b> 个营业部 · '
                    f'{len([g for g in GROUP_ORDER if pool.get(g)])} 个分组。'
                    f'每个磁贴 = 一个 id + 名下全部营业部。'
                    f'维护文件 <b>席位跟踪池.csv</b>（Excel 可编辑）。</div>'
                    f'<div class="note"><span class="pflag">共用</span> ＝ 该席位与其他游资'
                    f'<b>共用同一营业部通道</b>（如章盟主 / 葛卫东 共用「国泰海通上海长宁区江苏路」），'
                    f'单看席位名无法确定操作主体，需结合<b>标的（个股）</b>分辨；悬停角标看具体说明。</div></div>')
    return "".join(parts)


# ============== CSS（v4 · IBM Carbon 设计语言） ==============
# 规范来源：colorspace-skill → design-md/ibm/DESIGN.md（Carbon Design System）
#   · 圆角 0（按钮/输入/卡片/表格一律矩形），唯一例外：标签 pill 24px
#   · 深度靠背景灰阶分层（#fff → #f4f4f4 → #e0e0e0），卡片与表格零阴影
#   · 阴影只给真正浮动的元素（顶部 masthead）：0 2px 6px rgba(0,0,0,.3)
#   · 单色强调 Blue 60 #0f62fe；字重封顶 600，禁用 700
#   · 间距全部落在 8px 网格上：2 / 4 / 8 / 12 / 16 / 24 / 32 / 48
#   · 微字距：14px → +0.16px，12px → +0.32px（Carbon 小字可读性秘诀）
#   · 语义色（A股红涨绿跌）与装饰色严格分离，装饰只用灰阶 + 单一蓝
CSS = """
/* ===== Carbon Token ===== */
:root{
  /* 中性灰阶（Carbon Gray） */
  --gray-100:#161616;  /* 主文字 / 深色 masthead */
  --gray-90:#262626;   /* 深色次级面 */
  --gray-80:#393939;   /* 激活态 */
  --gray-70:#525252;   /* 次要文字 */
  --gray-60:#6f6f6f;   /* 占位 / 弱化文字 */
  --gray-50:#8d8d8d;   /* 禁用 / 弱化图标 */
  --gray-30:#c6c6c6;   /* 分隔线 */
  --gray-20:#e0e0e0;   /* 卡片描边 / 表头底 */
  --gray-10:#f4f4f4;   /* Layer-01：卡片填充、交替行 */
  --gray-10-hov:#e8e8e8;
  --white:#ffffff;

  /* 交互：Carbon 单一强调色 */
  --blue-60:#0f62fe;
  --blue-70:#0043ce;
  --blue-80:#002d9c;
  --blue-10:#edf5ff;

  /* 跨视图跳转的落点高亮底色（极淡黄，与语义红绿、强调蓝都不冲突） */
  --jump-flash:#fff8e1;

  /* 语义色：A股 红=净买(涨) / 绿=净卖(跌) —— 取自 Carbon Support，绝不用于装饰
     文字用 70 级（#a2191f 7.8:1 / #0e6027 7.7:1，12px 小字达 WCAG AA）
     强度条用 60/50 级（图形元素 3:1 即达标，保留视觉冲击力） */
  --up:#a2191f;        /* Red 70   7.8:1 ✓ */
  --down:#0e6027;      /* Green 70 7.7:1 ✓ */
  --bar-up:rgba(218,30,40,.15);    /* Red 60 */
  --bar-down:rgba(36,161,72,.15);  /* Green 50 */
  --flat:#c6c6c6;

  /* 组别色：Carbon 调色板 60 级（仅作 3px 竖条 + 标签，不铺满整行） */
  --g-quant:#d02670;   /* Magenta 60 量化（合并） */
  --g-active:#0f62fe;  /* Blue 60   知名游资 */
  --g-node:#8a3ffc;    /* Purple 60 节点（保留兼容） */
  --g-watch:#007d79;   /* Teal 60   待确认（保留兼容） */
  --g-other:#6f6f6f;   /* Gray 60   其他（未晋升+未知兜底） */

  /* 字体刻度（Carbon：14px 微字距 .16，12px 微字距 .32；字重封顶 600） */
  --fs-10:10px; --fs-12:12px; --fs-14:14px; --fs-16:16px;
  --fs-20:20px; --fs-24:24px; --fs-32:32px;
  --font-sans:"IBM Plex Sans",-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif;
  --font-mono:"IBM Plex Mono",ui-monospace,"SF Mono","JetBrains Mono",Menlo,Consolas,monospace;

  /* 表格行高：统一 36px（2026-09-17 移除密度切换后不再有 28px 紧凑档） */
  --row-h:36px;
}
*{margin:0;padding:0;box-sizing:border-box}
body{background:var(--gray-10);color:var(--gray-100);font-family:var(--font-sans);line-height:1.5;font-size:var(--fs-14);letter-spacing:.16px;-webkit-font-smoothing:antialiased}
.wrap{max-width:1680px;margin:0 auto;padding:0 24px 80px}

/* ===== 顶部 masthead（Carbon：48px 深色通栏，唯一使用阴影的元素） ===== */
nav.tabs{position:sticky;top:0;z-index:100;display:flex;align-items:center;gap:0;height:48px;
  background:var(--gray-100);padding:0 16px;margin:0 -24px 24px;box-shadow:0 2px 6px rgba(0,0,0,.3)}
nav.tabs a{display:flex;align-items:center;height:48px;padding:0 16px;font-size:var(--fs-14);font-weight:400;
  letter-spacing:.16px;color:var(--gray-30);text-decoration:none;border-bottom:2px solid transparent;transition:color .15s ease}
nav.tabs a:hover{color:var(--white)}
nav.tabs a.active{color:var(--white);border-bottom-color:var(--white)}
nav.tabs a:focus-visible{outline:2px solid var(--blue-60);outline-offset:-2px}

/* ===== 页头（Carbon Heading 03 = 24px/400；display 尺寸才用 300 字重） ===== */
header.page{padding:32px 0 8px}
h1{font-size:var(--fs-24);font-weight:400;letter-spacing:0;line-height:1.33;color:var(--gray-100);display:flex;align-items:center;gap:12px}
h1::before{content:"";width:4px;height:24px;background:var(--blue-60);border-radius:0}
/* .sub 已于 2026-09-17 移除：页头那段长说明（含数据链路与分组规则）按用户要求整段删除，
   只把「红=净买 / 绿=净卖 + 覆盖区间 + 更新日」压成一行，挪进数据总览卡片的 .sem-legend。 */

/* ===== 区块标题（Carbon Heading 04 = 20px/600） ===== */
.section-title{display:flex;align-items:center;gap:12px;margin:32px 0 12px;scroll-margin-top:56px}
.section-title .no{width:24px;height:24px;background:var(--blue-60);display:flex;align-items:center;justify-content:center;
  font-size:var(--fs-12);font-weight:600;color:var(--white);border-radius:0}
.section-title .name{font-size:var(--fs-20);font-weight:600;line-height:1.4;color:var(--gray-100)}
.section-title .desc{font-size:var(--fs-12);letter-spacing:.32px;color:var(--gray-70);margin-left:auto}

/* ===== 卡片（Carbon：0 圆角、无阴影、白面浮于 #f4f4f4 页底） ===== */
.card{background:var(--white);border:0;border-radius:0;padding:24px;margin:0 0 16px}
.card h2{font-size:var(--fs-16);font-weight:600;line-height:1.375;margin-bottom:12px;color:var(--gray-100)}
.note{font-size:var(--fs-12);letter-spacing:.32px;color:var(--gray-70);line-height:1.5;margin-bottom:8px}

/* 语义图例（2026-09-17：原页头 .sub 大段说明已删，只保留红绿语义 + 覆盖范围 + 更新日） */
.sem-legend{font-size:var(--fs-12);letter-spacing:.32px;color:var(--gray-70);line-height:1.5;margin-bottom:12px}

/* ===== KPI（Carbon Tile：#f4f4f4 面、0 圆角、无阴影） ===== */
.kpi-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(132px,1fr));gap:1px;background:var(--white)}
.kpi{background:var(--gray-10);padding:16px}
.kpi .k{font-size:var(--fs-12);letter-spacing:.32px;color:var(--gray-70);font-weight:400}
.kpi .v{font-size:var(--fs-24);font-weight:600;margin-top:4px;font-family:var(--font-mono);
  font-variant-numeric:tabular-nums;letter-spacing:0;line-height:1.2;color:var(--gray-100)}

/* ===== pivot 矩阵表 ===== */
/* 列宽变量（唯一源）：--c1-w 首列宽 / --c2-w 次列宽 / --date-w 日期列宽。
   外链 theme（lhb_theme.css）会按断点覆盖这三个值 → 手机端才能整体收窄。
   此处给默认值，保证 theme 缺失时布局依然正确。
   ⚠️ td.c2 的 sticky left 必须跟随 --c1-w，否则次列会压在首列上。 */
table.matrix.v1{--c1-w:160px;--c2-w:132px;--date-w:88px}
table.matrix.v2{--c1-w:112px;--c2-w:176px;--date-w:88px}
table.matrix{font-size:var(--fs-12);border-collapse:separate;border-spacing:0;width:100%;background:var(--white)}
table.matrix th,table.matrix td{height:var(--row-h);padding:2px 6px;vertical-align:middle;border-bottom:1px solid var(--gray-20)}

/* 行背景：Carbon 背景分层，用 CSS 变量驱动（hover 只改变量，不破坏强度条） */
table.matrix tbody tr{--row-bg:var(--white);--row-bg-2:var(--gray-10)}
table.matrix tbody tr.blk-b{--row-bg:var(--gray-10);--row-bg-2:var(--gray-20)}
table.matrix tbody tr:hover{--row-bg:var(--gray-10-hov);--row-bg-2:var(--gray-30)}

/* 表头：Carbon 数据表表头 = #e0e0e0 面 + 14px/600 深字，sticky 在 masthead 之下 */
table.matrix thead th{position:sticky;top:48px;z-index:90;background:var(--gray-20);color:var(--gray-100);
  font-size:var(--fs-12);font-weight:600;letter-spacing:.32px;padding:0 6px;text-align:center;
  border-bottom:1px solid var(--gray-50);white-space:nowrap;height:32px}
/* 第二行表头 top 多 32px（grp-row 高度） */
table.matrix thead tr.day-row th{top:80px;height:32px}
/* 分组条行（grp-row）：粘性顶部，比 day-row 略高 */
table.matrix thead tr.grp-row th{height:32px;padding:0;border-bottom:1px solid var(--gray-50)}

/* 首列（席位/标的）：sticky 左，面比行深一档（背景分层），左侧 3px 组别竖条
   ⚠️ min-width 用 var(--c1-w) 而非硬编码——否则手机端断点改不动列宽（--c1-w 是唯一源） */
table.matrix th.c1{position:sticky;left:0;z-index:80;font-weight:600;padding:2px 6px;font-size:var(--fs-12);
  letter-spacing:.32px;text-align:left;border-right:1px solid var(--gray-20);white-space:nowrap;min-width:var(--c1-w,80px);
  color:var(--gray-100);background:var(--row-bg-2);border-left:3px solid var(--g-active);
  vertical-align:top}                       /* 跨多行 rowspan 时内容贴顶（否则 middle 居中导致首行 c1 空白被遮） */
table.matrix th.c1.g-quant{border-left-color:var(--g-quant)}
/* c1 席位名下浅色小字：该席位实际动用的营业部（主次分明：席位名深色 600，营业部浅灰 400） */
.c1-depts{margin-top:6px;padding-top:6px;border-top:1px solid var(--gray-20);
  font-size:11px;line-height:1.55;font-weight:400;letter-spacing:0;
  color:var(--gray-50);white-space:normal;word-break:break-all;max-width:120px}
.c1-depts .more{color:var(--gray-60);font-style:italic}
/* 标的次列 / 标的块首列：股票名一行，代码 + 当日涨跌幅另起一行（分行显示，字号放大） */
table.matrix td.c2.stock-cell{line-height:1.3}
/* 第二行「代码 + 当日涨跌幅」。
   ⚠️ 桌面端同行显示（nowrap）；手机端 --c2-w 只剩 84~96px 时一行放不下，
   涨跌幅会被挤出格右边界 → 手机档（theme 第 13 节）改为断行堆叠。
   此处保留桌面端 nowrap 原始行为。 */
table.matrix td.c2.stock-cell .sub-line{display:block;margin-top:2px;white-space:nowrap;line-height:1.3}
/* 视图② 块首列（th.c1 内的标的）：同样两行布局，股票名加粗 */
table.matrix th.c1.stock-head{line-height:1.3}
table.matrix th.c1.stock-head .stock-name{font-weight:600}
table.matrix th.c1.stock-head .sub-line{display:block;margin-top:2px;white-space:nowrap}
table.matrix th.c1.stock-head .sub-line .code{font-family:var(--font-mono);font-size:var(--fs-12);
  color:var(--gray-60);letter-spacing:0}
/* 标的旁的当日涨跌幅（该行最近一次上榜日读数）：红涨绿跌，等宽放大 */
.chg{display:inline-block;margin-left:8px;font-family:var(--font-mono);
  font-size:var(--fs-12);font-weight:600;font-variant-numeric:tabular-nums;letter-spacing:0;
  color:var(--gray-70)}
.chg.up{color:var(--up)}
.chg.down{color:var(--down)}
/* 修复：外部补丁 td[tabindex]:focus 会把 position 改成 relative，使 sticky 次列
   （left:var(--c1-w) 是粘性偏移量）在聚焦时整体右移 112px（点击个股/席位名漂移）。
   内联规则用 !important 兜底恢复 sticky，防止补丁文件被重新生成时丢修复。 */
table.matrix td.c2:focus,
table.matrix td.c2[tabindex]:focus{position:sticky !important;left:var(--c1-w) !important}

/* 视图①「标的名 → 跳到视图②」可点击链接（2026-09-17 新增）。
   视觉上保持正文样式（不加下划线、不改色），仅用极淡虚线下边框暗示可点，
   避免整张矩阵被蓝色链接淹没；hover/focus 时才亮起来。 */
table.matrix a.stock-link{color:inherit;text-decoration:none;cursor:pointer;
  border-bottom:1px dashed var(--gray-30);padding-bottom:1px;transition:color .15s ease,border-color .15s ease}
table.matrix a.stock-link:hover{color:var(--blue-60);border-bottom-color:var(--blue-60)}
table.matrix a.stock-link:focus-visible{outline:2px solid var(--blue-60);outline-offset:2px;border-radius:2px}

/* 跳转落点高亮：目标个股块闪一下淡黄底，落地即知「到了哪一块」。
   用 outline 而非背景（背景被行底色分层机制占用）。 */
table.matrix tr.is-jump-target > th.c1,
table.matrix tr.is-jump-target > td.c2{background:var(--jump-flash)!important}
table.matrix tr.is-jump-target{animation:jumpFlash 1.6s ease-out}
@keyframes jumpFlash{0%{box-shadow:inset 0 0 0 3px var(--blue-60)}100%{box-shadow:inset 0 0 0 3px transparent}}

/* 跳转后浮出的「返回 ① 席位 → 个股」按钮（视图② 专用，随滚动吸在左下） */
.jump-back{position:fixed;left:16px;bottom:24px;z-index:120;display:none;align-items:center;gap:6px;
  height:36px;padding:0 14px;border:1px solid var(--blue-60);border-radius:4px;
  background:var(--white);color:var(--blue-60);cursor:pointer;
  font-family:inherit;font-size:var(--fs-12);font-weight:600;letter-spacing:.32px;
  box-shadow:0 4px 14px rgba(23,33,43,.18)}
.jump-back.on{display:inline-flex}
.jump-back:hover{background:var(--blue-60);color:var(--white)}
.jump-back:focus-visible{outline:2px solid var(--blue-60);outline-offset:2px}

/* 视图②聚合席位行：+ / − 按钮在表格内展开明细行（每营业部一行，金额落在各自日期列），
   视觉与「[+] N 条历史」保持一致（无边框、蓝色符号）。明细行默认折叠（rowhide）。 */
table.matrix td.c2 .exp-btn{display:inline-block;margin-left:5px;width:14px;height:16px;line-height:14px;
  padding:0;font-family:var(--font-mono);font-size:12px;font-weight:600;color:var(--blue-60);
  background:transparent;border:0;border-radius:0;cursor:pointer;vertical-align:1px;text-align:center}
table.matrix td.c2 .exp-btn:hover{color:var(--gray-100)}
table.matrix td.c2 .exp-btn:focus-visible{outline:2px solid var(--blue-60);outline-offset:-2px}
/* 明细行：营业部名缩进浅色、行底色比主行浅一档，体现层级 */
table.matrix tbody tr.subrow td{background:var(--gray-10) !important}
table.matrix tbody tr.subrow td.c2.subdet-name{padding-left:18px !important;font-weight:400 !important;
  color:var(--gray-70) !important;font-size:11px !important;white-space:normal;word-break:break-all}
table.matrix th.c1.g-famous{border-left-color:var(--g-active)}
table.matrix th.c1.g-other{border-left-color:var(--g-other)}
table.matrix th.c1.g-node{border-left-color:var(--g-node)}
table.matrix th.c1.g-watch{border-left-color:var(--g-watch)}
table.matrix.v2 th.c1{border-left-color:var(--g-node)}

/* 次列（标的/营业部）：sticky 左，与行同色。c2 是单行 td，但仍垂直置顶，避免与 c1 错位
   ⚠️ left 必须 = --c1-w（c1 的实际宽度），否则次列会压在首列上；
   用变量而非硬编码，手机端断点才能同步收窄两列。 */
table.matrix td.c2{position:sticky;left:var(--c1-w,80px);z-index:80;padding:3px 8px;border-right:1px solid var(--gray-20);
  background:var(--row-bg);min-width:var(--c2-w,132px);font-weight:400;font-size:var(--fs-12);letter-spacing:.32px;color:var(--gray-100);
  vertical-align:top}
table.matrix td.c2 .code{font-family:var(--font-mono);font-size:var(--fs-12);color:var(--gray-60);letter-spacing:0}

/* 数据单元格：净额突出 + 强度条（背景条，零额外行高）+ 买/卖次要 */
/* 2026-09-17 瘦身后类名映射（旧名保留为别名，两条并列 → 双写安全）：
     cell      → c    （数据格）
     empty     → e    （空格）
     offboard  → o    （未出榜，仅在空格上）
     chgcell   → x    （涨跌幅行的格）
   改名的动机：`class="cell empty offboard"` 共 33 字符 × 全站 4 万余格，
   是全页最大的单点冗余；短名不影响语义（HTML 里可读性靠注释补足）。 */
table.matrix td.cell,table.matrix td.c{background-color:var(--row-bg);white-space:nowrap;line-height:1.35;text-align:right;
  min-width:var(--date-w,88px);
  background-image:linear-gradient(to right,var(--bar,transparent) 0,var(--bar,transparent) var(--w,0%),transparent var(--w,0%));
  background-repeat:no-repeat}
table.matrix td.cell .net,table.matrix td.c .net{display:block;font-family:var(--font-mono);font-variant-numeric:tabular-nums;
  font-size:var(--fs-12);font-weight:600;letter-spacing:0;line-height:1.3}
table.matrix td.cell .net.up,table.matrix td.c .net.up{color:var(--up)}
table.matrix td.cell .net.down,table.matrix td.c .net.down{color:var(--down)}
table.matrix td.cell .bs,table.matrix td.c .bs{display:block;font-family:var(--font-mono);font-variant-numeric:tabular-nums;
  font-size:var(--fs-10);color:var(--gray-60);font-weight:400;letter-spacing:0;line-height:1.3}
/* 强度条方向色：`--bar` 由 td 上的 .u（净买，红）/ .d（净卖，绿）决定，
   取代原先每格内联 `style="--bar:var(--bar-up)"`（6612 格 × 17 字节）。 */
table.matrix td.c.u{--bar:var(--bar-up)}
table.matrix td.c.d{--bar:var(--bar-down)}
/* 视图② 块首「当日涨跌幅」行：逐列显示该股当日涨跌幅，与下方各席位净额对齐。

   2026-09-17 改版（用户：「用现在这个红绿色作为底色填充整个单元格，
   取消灰色底色和现在的红绿色边框」）：
     ① 涨跌幅的**红/绿直接铺满整格**（span 改 display:block + width:100%）
     ② 取消 chip 的内描边（box-shadow）与 2px 圆角、取消浅粉/浅绿底
     ③ 文字改白（实底上浅色文字对比度最好；红底/绿底 × 白字 > 7:1）
     ④ 空值（未出榜 / 无数据）不铺色，回落到行底色 */
table.matrix tr.chgrow td,table.matrix tr.chgrow th.c1{
  background-color:var(--gray-20) !important;
  border-top:1px solid var(--gray-50) !important;
  border-bottom:1px solid var(--gray-50) !important}
table.matrix tr.chgrow td.cell.chgcell,
table.matrix tr.chgrow td.c.x{
  text-align:center !important;
  background-image:none !important;
  padding:0 !important}
/* 整格实底：span 撑满整个 td，红/绿铺满，白字居中 */
table.matrix tr.chgrow td.cell.chgcell > span,
table.matrix tr.chgrow td.c.x > span{
  display:block;
  width:100%;
  padding:6px 4px !important;
  font-family:var(--font-mono);
  font-size:var(--fs-12);
  font-weight:600;
  font-variant-numeric:tabular-nums;
  letter-spacing:.16px;
  line-height:1.4;
  text-align:center;
  min-width:0;
  border-radius:0;
  color:var(--white)}
/* A股语义：红涨绿跌。实底填充 + 白字 */
table.matrix tr.chgrow td.cell.chgcell .up,table.matrix tr.chgrow td.c.x .up{background:#da1e28;color:#fff}
table.matrix tr.chgrow td.cell.chgcell .down,table.matrix tr.chgrow td.c.x .down{background:#0e6027;color:#fff}
table.matrix tr.chgrow td.cell.chgcell .flat,table.matrix tr.chgrow td.c.x .flat{background:var(--gray-60);color:#fff}
/* 空值（未出榜 / 无数据）不铺色，回落到行底色 */
table.matrix tr.chgrow td.cell.chgcell > span.ob,
table.matrix tr.chgrow td.cell.chgcell > span.na,
table.matrix tr.chgrow td.c.x > span.ob,
table.matrix tr.chgrow td.c.x > span.na{
  background:none !important;color:inherit !important;box-shadow:none !important;padding:6px 4px !important}
table.matrix tr.chgrow td.c2.chg-label{color:var(--gray-80);font-size:var(--fs-12);white-space:nowrap;
  background-color:var(--gray-20);letter-spacing:.32px;text-align:left}
table.matrix td.empty,table.matrix td.e{color:var(--gray-30);text-align:center;font-size:var(--fs-12);background-color:var(--row-bg);background-image:none;
  padding-left:8px;padding-right:8px}
/* 三类「空」的文字区分（2026-09-16 用户要求，**不做背景压暗**——用户明确不喜欢那种显示方式）：
   ① 该票当日**没出榜** → 整格标「未出榜」（灰字，量小可承载真实 <span>）
   ② 该票当日**只上了累计型榜** → 整格标「仅累计榜」（2026-09-18 第 29 轮新增，
      席位明细在 ③ 段；与 ① 必须可区分，否则用户以为漏标）
   ③ 该票当日**上了日度榜**但该席位没出现 → 干净的 `—`（保持原样）
   判据在「标的×日期」级，由 op_cell(on_board=...) 传入（三态）。 */
table.matrix td.empty .ob,table.matrix td.e .ob{color:var(--gray-40,#b8bfc7);font-size:var(--fs-10);letter-spacing:.32px;
  font-weight:400;white-space:nowrap}
/* 「仅累计榜」用带边框的小 chip 与「未出榜」拉开形状差异（比只换字色更易扫读） */
table.matrix td.empty .ob.mo,table.matrix td.e .ob.mo{
  color:var(--gray-60);border:1px solid var(--gray-30);border-radius:2px;padding:0 3px;line-height:1.5;
  letter-spacing:.24px;display:inline-block}
/* 涨跌幅行的空值是「没行情数据」，语义不同 */
table.matrix td.empty .na,table.matrix td.e .na{color:var(--gray-50);font-size:var(--fs-10);letter-spacing:.32px;white-space:nowrap}

/* ===== 数据列纵向分割线（2026-09-16 用户要求「添加纵向分割线」）=====
   日期列密集，只有横向边框时纵向扫列极易串行。给数据列加低对比度竖线，
   与横向边框组成完整网格；表头/涨跌幅行同样加上，保证竖线贯通到底。 */
table.matrix thead th.dt,
table.matrix tbody td.cell,
table.matrix tbody td.empty,
table.matrix tbody td.c,
table.matrix tbody td.e{
  border-right:1px solid #ccd6e0 !important}
/* 最后一列不画（避免与表格外框重叠） */
table.matrix thead th.dt:last-child,
table.matrix tbody td.cell:last-child,
table.matrix tbody td.empty:last-child,
table.matrix tbody td.c:last-child,
table.matrix tbody td.e:last-child{
  border-right:0 !important}
/* 涨跌幅行与净额行之间的竖线要更清晰（该行自成色带，竖线断掉会显得割裂） */
table.matrix tr.chgrow td.cell.chgcell,
table.matrix tr.chgrow td.empty,
table.matrix tr.chgrow td.c.x,
table.matrix tr.chgrow td.e{
  border-right-color:#bcc9d6 !important}

/* 块边界：首行 2px 上边框（Carbon 用强分隔而非彩色） */
table.matrix tbody tr.blk-start th.c1,table.matrix tbody tr.blk-start td.c2,
table.matrix tbody tr.blk-start td.cell,table.matrix tbody tr.blk-start td.empty,
table.matrix tbody tr.blk-start td.c,table.matrix tbody tr.blk-start td.e{border-top:2px solid var(--gray-50)}

/* 表头首两列需双向 sticky（top + left） */
table.matrix thead th.c1{position:sticky;left:0;top:48px;z-index:95;text-align:left;background:var(--gray-20);padding:2px 6px}
table.matrix thead th.c2{position:sticky;left:80px;top:48px;z-index:95;background:var(--gray-20);border-right:1px solid var(--gray-50);text-align:left}
table.matrix thead th.c1.grp-spn{top:48px}
table.matrix thead th.c2.grp-spn{top:48px}

/* ===== 标签：Carbon 唯一允许圆角的元素（pill 24px） ===== */
.badge{font-size:var(--fs-10);font-weight:400;padding:2px 8px;border-radius:24px;margin-left:8px;letter-spacing:.32px;white-space:nowrap}
.b-quant{background:#fde8f0;color:var(--g-quant)}
.b-famous{background:#e8f0fe;color:var(--g-active)}
.b-other{background:#f0f0f0;color:#393939}
.b-active{background:var(--blue-10);color:var(--blue-60)}
.b-node{background:#f6f2ff;color:var(--g-node)}
.b-watch{background:#e5f5f4;color:var(--g-watch)}

/* 强度条图例 */
.legend{display:flex;align-items:center;gap:8px;font-size:var(--fs-12);letter-spacing:.32px;color:var(--gray-70);margin-top:12px}
.legend .sw{width:56px;height:8px;background:linear-gradient(to right,rgba(218,30,40,.14) 0,rgba(218,30,40,.14) 100%);border:1px solid var(--gray-20)}

/* ===== 通用表格（席位池） ===== */
table{width:100%;border-collapse:collapse;font-size:var(--fs-14);margin:8px 0}
th{color:var(--gray-100);font-weight:600;text-align:left;padding:8px 12px;border-bottom:1px solid var(--gray-30);font-size:var(--fs-12);letter-spacing:.32px;background:var(--gray-10)}
td{padding:8px 12px;border-bottom:1px solid var(--gray-20);vertical-align:top;letter-spacing:.16px}
table:not(.matrix) tbody tr:hover{background:var(--gray-10-hov)}
.empty{color:var(--gray-60);font-size:var(--fs-14);padding:16px;text-align:center}
.footer{text-align:center;color:var(--gray-60);font-size:var(--fs-12);letter-spacing:.32px;margin-top:32px;padding-top:16px;border-top:1px solid var(--gray-20)}
.scroll{overflow-x:auto}

/* ===== 席位池配置（全量映射 · 磁贴瀑布流排版，2026-09-16 v3） ===== */
.card h2 .gcount{margin-left:10px;font-size:var(--fs-12);font-weight:400;color:var(--gray-60);
  font-family:var(--font-mono);letter-spacing:.32px}
.card.summary{border-left:3px solid var(--gray-30)}
/* 瀑布流：列数随宽度自适应（Carbon 断点：1056 / 672） */
.pool-grid{columns:3;column-gap:8px;margin-top:4px}
@media (max-width:1056px){.pool-grid{columns:2}}
@media (max-width:672px){.pool-grid{columns:1}}
/* 每个席位 = 一张磁贴：整块不可跨列断开，保证「席位 + 其营业部」永远在同一视觉单元 */
.pool-tile{break-inside:avoid;-webkit-column-break-inside:avoid;page-break-inside:avoid;
  margin:0 0 8px;padding:8px 10px;background:var(--gray-10);border-left:2px solid var(--gray-30)}
.pi-head{display:flex;align-items:baseline;gap:6px;line-height:1.3}
.pi-name{font-size:var(--fs-14);font-weight:600;color:var(--gray-100);letter-spacing:.16px}
.pi-cnt{margin-left:auto;font-family:var(--font-mono);font-size:10px;color:var(--gray-60);
  white-space:nowrap;letter-spacing:0}
.pflag{flex:none;padding:0 5px;border:1px solid var(--blue-60);color:var(--blue-60);
  font-size:10px;font-weight:600;letter-spacing:0;line-height:1.5;cursor:help}
/* 营业部清单：等宽小字、逐行列出、前置 · 引导点 */
.pi-depts{list-style:none;margin:4px 0 0;padding:0;
  font-family:var(--font-mono);font-size:var(--fs-12);line-height:1.7;color:var(--gray-90)}
.pi-depts li{position:relative;padding-left:9px}
.pi-depts li:before{content:"·";position:absolute;left:0;color:var(--gray-50)}
.pi-depts li.dim{color:var(--gray-50);font-style:italic}
.pi-depts li.dim:before{content:""}
.pi-depts li .wild{color:var(--gray-50);font-size:10px;margin-left:4px}

/* ===== 累计型榜区块（三日/区间累计） ===== */
.md-day{margin:0 0 8px;border-left:2px solid var(--gray-30);background:var(--gray-10)}
.md-day>summary{cursor:pointer;padding:8px 12px;font-size:var(--fs-12);font-weight:600;
  color:var(--gray-100);letter-spacing:.32px;list-style:none}
.md-day>summary::-webkit-details-marker{display:none}
.md-day>summary:before{content:"▸ ";color:var(--blue-60);font-family:var(--font-mono)}
.md-day[open]>summary:before{content:"▾ "}
.md-day>summary:hover{background:var(--gray-10-hov)}
.md-day .scroll{padding:0 12px 8px}
.md-day table{margin:0;font-size:var(--fs-12)}
.md-day th{padding:6px 8px}
.md-day td{padding:6px 8px}
/* 2026-09-18 第 29 轮：原扁平表的 td.num / td.rsn 已随改版删除（改 pivot 矩阵后无生产者）。 */

/* --- 累计型榜矩阵（2026-09-18 第 29 轮：扁平长表 → 席位×标的 pivot） ---
   与日度矩阵同构，但**不做强度条**（区间累计额量级与日度不可比，画条会误导）。 */
table.matrix.mtx-multi{border-collapse:separate;border-spacing:0;background:var(--white);
  font-size:var(--fs-12);width:max-content;min-width:100%}
table.matrix.mtx-multi thead th{position:static;background:var(--gray-20);border-bottom:1px solid var(--gray-50);
  text-align:center;vertical-align:middle;padding:6px 8px;font-weight:600;color:var(--gray-100)}
table.matrix.mtx-multi thead th.c1{text-align:left;white-space:nowrap;min-width:96px}
/* 列头：标的（名 + 代码），可换行不撑破列宽 */
table.matrix.mtx-multi thead th.mc-stock{min-width:88px}
table.matrix.mtx-multi thead th.mc-stock .mc-name{display:block;font-size:var(--fs-12);color:var(--gray-100)}
table.matrix.mtx-multi thead th.mc-stock .sub-line{display:block;margin-top:2px}
table.matrix.mtx-multi tbody td{border-bottom:1px solid var(--gray-20);border-right:1px solid #e3e9ef;
  padding:6px 8px;text-align:center;vertical-align:middle}
table.matrix.mtx-multi tbody td.c1{text-align:left;white-space:nowrap;background:var(--gray-10);
  font-weight:600;color:var(--gray-100);border-right:1px solid var(--gray-30)}
/* 行名是虚拟桶或池内席位时的区分：池内席位左侧加粗竖条 */
table.matrix.mtx-multi tbody td.mc-seat{font-weight:600}
table.matrix.mtx-multi tbody td.mc-seat.is-pool{
  border-left:3px solid var(--blue-60);color:var(--gray-100)}
table.matrix.mtx-multi tbody td.mc-num{font-family:var(--font-mono);font-variant-numeric:tabular-nums;white-space:nowrap}
table.matrix.mtx-multi tbody td.mc-num .net{display:inline-block;padding:1px 4px;border-radius:2px}
table.matrix.mtx-multi tbody td.mc-num .net.up{color:var(--up);font-weight:600}
table.matrix.mtx-multi tbody td.mc-num .net.down{color:var(--down);font-weight:600}
table.matrix.mtx-multi tbody td.e{color:var(--gray-30)}
/* 手机端：累计榜矩阵也允许横滑，列宽再压一档 */
@media (max-width:900px){
  table.matrix.mtx-multi{font-size:var(--fs-10)}
  table.matrix.mtx-multi thead th,table.matrix.mtx-multi tbody td{padding:4px 5px}
  table.matrix.mtx-multi thead th.mc-stock{min-width:74px}
  table.matrix.mtx-multi thead th.c1{min-width:78px}
}

/* ===== 日期分组条（2026-09-18 第 29 轮：不再是按钮，纯文本标签） =====
   原为可折叠按钮（.grp-btn），现日期列常显，故改为静态标签。 */
table.matrix thead th.grp-th{padding:0;background:var(--gray-20);border-right:1px solid var(--gray-50)}
table.matrix thead th.grp-th:last-child{border-right:0}
table.matrix thead th.grp-th .grp-lab{color:var(--gray-100)}
table.matrix thead th.grp-th .grp-cnt{color:var(--gray-60);font-weight:400;font-family:var(--font-mono)}
/* 标签行：占满整个 colspan 单元格，居中排布 */
table.matrix thead th.grp-th{display:table-cell;vertical-align:middle;text-align:center;
  height:30px;font-family:inherit;font-size:var(--fs-12);font-weight:600;letter-spacing:.32px}

/* 行折叠：行级 .rowhide 默认隐藏（隐藏 tr 仍占位，需 JS 重算 rowspan） */
tr.rowhide{display:none}
/* JS 执行前的兜底：先把所有 data-sec="hist" 行隐藏，首屏不闪一下 */
table.matrix[data-collapse="1"]:not(.ready) tbody tr[data-sec="hist"]{display:none}

/* 行分组条（席位块内 [+] N 条历史 横条）：横跨 c1+c2+所有日期列 */
table.matrix tbody tr.grp-bar td{padding:0;background:var(--gray-10);border-top:1px solid var(--gray-20);
  border-bottom:1px solid var(--gray-20);height:24px;text-align:left}
table.matrix tbody tr.grp-bar.blk-b td{background:var(--gray-20)}
table.matrix tbody tr.grp-bar .grp-bar-btn{display:flex;align-items:center;gap:8px;
  width:100%;height:24px;padding:0 12px;background:transparent;border:0;border-radius:0;
  font-family:inherit;font-size:var(--fs-12);color:var(--gray-70);cursor:pointer;
  letter-spacing:.32px;text-align:left;transition:background .15s ease,color .15s ease}
table.matrix tbody tr.grp-bar .grp-bar-btn:hover{background:var(--gray-20);color:var(--gray-100)}
table.matrix tbody tr.grp-bar .grp-bar-btn:focus-visible{outline:2px solid var(--blue-60);outline-offset:-2px}
table.matrix tbody tr.grp-bar .grp-bar-btn .arr{display:inline-block;width:14px;
  font-family:var(--font-mono);font-size:var(--fs-12);font-weight:600;color:var(--blue-60);
  text-align:center}
table.matrix tbody tr.grp-bar .grp-bar-btn[aria-expanded="true"] .arr{color:var(--blue-60)}
table.matrix tbody tr.grp-bar .grp-bar-btn .lab{font-weight:600;color:var(--gray-100)}
table.matrix tbody tr.grp-bar .grp-bar-btn .hint{font-weight:400;color:var(--gray-60)}

/* 2026-09-17：.tblbar / .tbtn（表头工具条 + 「展开全部历史」按钮）已随按钮下线一并移除 */

/* ===== 响应式（2026-09-17 补手机端适配）=====
   断点：1056 / 768 / 560（Carbon 2x Grid 的 narrow 档）。
   手机端的核心矛盾是**表格宽度远超屏宽**（日期列最多 10 列 + 首两列），
   不可能靠压缩塞下 → 策略是：
     ① 稳住首两列 sticky（c1 席位/标的 + c2 名称），保证横向滚动时「行身份」始终可见
     ② 压缩单元格与字号，让单屏能看到更多日期
     ③ 非表格区域（nav / 卡片 / KPI / 池配置）改为单列堆叠
     ④ 触屏优化：去掉 hover 依赖、加大点击热区、`-webkit-overflow-scrolling:touch` 惯性滚动 */

/* 横向滚动容器提示（手机端表格可横滑，新增视觉提示条） */
.scroll-hint{display:none}

@media(max-width:1056px){
  .pool-grid{columns:2}
  .wrap{padding:0 20px 72px}
}

@media(max-width:768px){
  .wrap{padding:0 12px 64px}
  .section-title .desc{display:none}
  h1{font-size:var(--fs-20)}
  .sem-legend{font-size:var(--fs-12);line-height:1.6;padding:0 10px}
  .card{padding:12px;margin-bottom:12px}
  .kpi .v{font-size:var(--fs-20)}
  .kpi-grid{grid-template-columns:repeat(2,minmax(0,1fr))!important;gap:8px!important}

  /* nav：可横滑，收紧内边距，去 hover 依赖 */
  nav.tabs{margin:0 -12px 12px;padding:0 8px;overflow-x:auto;-webkit-overflow-scrolling:touch;
    scrollbar-width:none}
  nav.tabs::-webkit-scrollbar{display:none}
  nav.tabs a{padding:0 12px;font-size:var(--fs-12);white-space:nowrap}

  /* 表格：收紧列宽与字号。
     ⚠️ 列宽**只改 --c1-w/--c2-w/--date-w 三个变量**（绝不硬编码 min-width/left）——
     因为外链 theme 在断点里也改这三个变量，两者若用不同机制会互相打架，
     且 td.c2 的 sticky left 依赖 --c1-w，硬编码会让两列错位。 */
  table.matrix{font-size:var(--fs-12)}
  table.matrix th,table.matrix td{padding:0 4px}
  table.matrix.v1{--c1-w:104px;--c2-w:96px;--date-w:74px}
  table.matrix.v2{--c1-w:74px;--c2-w:132px;--date-w:74px}
  table.matrix td.cell .net,table.matrix td.c .net{font-size:var(--fs-12)}
  table.matrix td.cell .bs,table.matrix td.c .bs{font-size:9px}
  table.matrix tr.chgrow td.cell.chgcell > span,table.matrix tr.chgrow td.c.x > span{padding:5px 2px!important;font-size:var(--fs-12)}
  table.matrix td.empty,table.matrix td.e{font-size:var(--fs-10)}
  table.matrix td.cell .ob,
  table.matrix td.cell .na,
  table.matrix td.c .ob,
  table.matrix td.c .na{font-size:9px}

  /* 表格外层容器：允许横向滑动并给惯性 */
  .scroll-hint{display:flex;align-items:center;gap:6px;font-size:var(--fs-10);
    letter-spacing:.32px;color:var(--gray-60);margin:0 0 6px}
  .scroll-hint::before{content:"";flex:0 0 auto;width:12px;height:12px;
    background:var(--gray-30)}
  .scroll-hint::after{content:"← 左右滑动查看更多日期 →";flex:1 1 auto}

  /* 池配置：单列 */
  .pool-grid{columns:1}
  .pool-tile{break-inside:avoid}

  /* 累计型榜：横向滚动兜底 */
  details.multi table{font-size:var(--fs-12)}
}

/* 触屏设备：去 hover 副作用，加大可点区域（≥44px 触控目标） */
@media(hover:none){
  nav.tabs a:hover{color:var(--gray-30)}
  nav.tabs a.active:hover{color:var(--white)}
  .pool-tile{min-height:auto}
  table.matrix tbody tr.grp-bar .grp-bar-btn{height:32px}
  table.matrix tbody tr.grp-bar td{height:32px}
}

/* 极窄屏（≤560px，如 iPhone SE / 竖屏） */
@media(max-width:560px){
  .wrap{padding:0 8px 56px}
  .card{padding:10px}
  h1{font-size:var(--fs-20)}
  .kpi-grid{grid-template-columns:repeat(2,minmax(0,1fr))!important}
  table.matrix.v1{--c1-w:88px;--c2-w:84px;--date-w:68px}
  table.matrix.v2{--c1-w:66px;--c2-w:116px;--date-w:68px}
}
@media print{
  nav.tabs{display:none!important}
  body{background:#fff}
  table.matrix th.c1,table.matrix td.c2,table.matrix thead th{position:static}
  table.matrix thead th{top:auto}
  .scroll-hint{display:none!important}
}
@media(prefers-reduced-motion:reduce){*{transition:none!important}}
"""


# ============== 主流程 ==============
def main():
    days = load_days()
    pool = load_pool()
    if not days:
        print("无数据：请先运行 scripts/lhb_seat_track.py --backfill N")
        return
    ops = load_ops(days)
    v1 = build_v1(ops)
    v2 = build_v2(ops)
    dates_desc = list(reversed(sorted({d["date"] for d in days})))
    latest = days[-1]

    # 逐日涨跌幅汇总：{code: {date: pct}}（各日 json 的 stock_chg 合并，供视图②块首行使用）
    stock_chg = {}
    for d in days:
        for code, mp in (d.get("stock_chg") or {}).items():
            stock_chg.setdefault(code, {}).update(mp)

    # 逐日「该标的的**日度榜**状态」：{code: {date: "day"|"multi"}}（各日 json 合并）。
    # 用途：区分三类「空」——
    #   ① 该票当日**没上龙虎榜** → 该列本质「无法跟踪」→ 标「未出榜」
    #   ② 该票当日**只上了累计型榜** → 席位记录在 ③ 段，日度矩阵无一格数据 → 标「仅累计榜」
    #      （2026-09-18 第 29 轮新增；旧口径只判「有没有上过榜」，把 ② 与 ① 混为一谈，
    #       导致博汇科技 09-17 这类票大面积无标注 —— 全量实测 212 个 (标,日) 组合）
    #   ③ 该票当日**上了日度榜**，但某席位没参与 → 才是「席位未交易」（干净的 `—`）
    # "day" 判据 = 该 code 出现在**日度席位明细**里（ops 的 code 集合），
    # 即 build_v1/build_v2 真正能产出行的那些票 —— 与矩阵实际渲染内容严格同源。
    # "multi" 判据 = 在 board_codes/multi_codes 里但**不在**上面的日度集合里。
    board_by = {}
    for d in days:
        dt = d.get("date")
        # 该日**有日度席位明细**的标的集合（= 矩阵里真的会出现行的票）
        day_codes = set()
        for g in ("quant", "famous", "other"):
            for _seat, _v in (d.get(g) or {}).items():
                for _op in (_v.get("ops") or []):
                    if _op.get("code"):
                        day_codes.add(str(_op["code"]))
        # board_codes（个股榜 meta，覆盖当日全部上榜票）+ multi_codes（仅累计榜的票）
        all_codes = set((d.get("board_codes") or {}).keys()) | set(d.get("multi_codes") or [])
        for code in all_codes:
            board_by.setdefault(code, {})[dt] = "day" if code in day_codes else "multi"

    n_quant = len(latest.get("quant", {}))
    n_famous = len(latest.get("famous", {}))
    net_quant = sum(v.get("net", 0) for v in latest.get("quant", {}).values())
    net_famous = sum(v.get("net", 0) for v in latest.get("famous", {}).values())
    net_other = sum(v.get("net", 0) for v in latest.get("other", {}).values())
    # TOP3：知名游资 + 量化（多头只列净买、空头只列净卖；净额不足 3 家宁缺毋滥）
    tops = [x for x in sorted(latest.get("famous", {}).items(), key=lambda x: -x[1].get("net", 0)) if x[1].get("net", 0) > 0][:3]
    downs = [x for x in sorted(latest.get("famous", {}).items(), key=lambda x: x[1].get("net", 0)) if x[1].get("net", 0) < 0][:3]
    tops_q = [x for x in sorted(latest.get("quant", {}).items(), key=lambda x: -x[1].get("net", 0)) if x[1].get("net", 0) > 0][:3]
    downs_q = [x for x in sorted(latest.get("quant", {}).items(), key=lambda x: x[1].get("net", 0)) if x[1].get("net", 0) < 0][:3]
    kp = lambda k, v: f"<div class='kpi'><div class='k'>{k}</div><div class='v'>{v}</div></div>"
    kpis = "".join([
        kp("最新交易日", latest["date"]),
        kp("席位→个股", f"{len(v1)}"),
        kp("个股→席位", f"{len(v2)}"),
        kp("知名游资上榜", f"{n_famous}"),
        kp("量化分组上榜", f"{n_quant}"),
        kp("量化合计净额", f'<span style="color:{"var(--up)" if net_quant>=0 else "var(--down)"}">{fmt_amt(net_quant)}</span>'),
        kp("游资合计净额", f'<span style="color:{"var(--up)" if net_famous>=0 else "var(--down)"}">{fmt_amt(net_famous)}</span>'),
        kp("其他合计净额", f'<span style="color:{"var(--up)" if net_other>=0 else "var(--down)"}">{fmt_amt(net_other)}</span>'),
    ])
    top_html = "；".join(f"<b>{k}</b> {fmt_amt(v.get('net',0))}" for k, v in tops)
    down_html = "；".join(f"<b>{k}</b> {fmt_amt(v.get('net',0))}" for k, v in downs)
    top_q_html = "；".join(f"<b>{k}</b> {fmt_amt(v.get('net',0))}" for k, v in tops_q)
    down_q_html = "；".join(f"<b>{k}</b> {fmt_amt(v.get('net',0))}" for k, v in downs_q)

    # 聚合席位明细行展开 JS（非 f-string，规避花括号转义雷区）
    # 点击 ▾ → 切换紧随其后的隐藏明细行（营业部×日期），并重算所在块的 c1 rowSpan
    subs_js = r'''
function recalcBlockSpan(tbl, blk){
  var rows=tbl.querySelectorAll('tbody tr[data-blk="'+blk+'"]');
  var vis=0, c1=null;
  for(var i=0;i<rows.length;i++){
    if(!c1)c1=rows[i].querySelector('th.c1');
    if(rows[i].getClientRects().length>0)vis++;
  }
  if(c1&&vis>0)c1.rowSpan=vis;
}
function LHBsubToggle(btn){
  var tbl=btn.closest('table');
  if(!tbl)return;
  var key=btn.getAttribute('data-sub');
  var rows=tbl.querySelectorAll('tr[data-subgroup="'+key+'"]');
  if(!rows.length)return;
  var show=rows[0].classList.contains('rowhide');
  for(var i=0;i<rows.length;i++){
    if(show)rows[i].classList.remove('rowhide'); else rows[i].classList.add('rowhide');
  }
  btn.textContent=show?'−':'+';
  btn.setAttribute('aria-expanded', show?'true':'false');
  /* 全表重算 rowspan（recomputeRows 已识别 sec="sub"，按其实际可见性计数） */
  if(typeof recomputeRows==='function'){ recomputeRows(); }
  else { recalcBlockSpan(tbl, rows[0].getAttribute('data-blk')); }
}'''

    body_v1 = render_pivot(v1, dates_desc, "player", "stock", "席位 / 选手", "标的", "v1",
                           badge=True, collapse_stale=True, board_by=board_by)
    body_v2 = render_pivot(v2, dates_desc, "stock", "player", "标的", "席位", "v2",
                           badge=False, collapse_stale=False, sort_mode="recent",
                           stock_chg=stock_chg, board_by=board_by)

    # 外部 UI 改造补丁层（可选）：文件存在才注入，且必须排在内联样式/脚本之后才能覆盖
    _dir = os.path.dirname(OUT)
    _theme = os.path.join(_dir, "lhb_theme.css")
    _ui = os.path.join(_dir, "lhb_ui.js")
    theme_link = '<link rel="stylesheet" href="lhb_theme.css"/>' if os.path.exists(_theme) else ""
    ui_script = '<script src="lhb_ui.js"></script>' if os.path.exists(_ui) else ""

    html = f"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>龙虎榜席位追踪 · 游资复盘</title>
<style>{CSS}</style>
{theme_link}
</head>
<body>
<nav class="tabs" id="topnav">
  <a href="#byseat" class="active">① 席位 → 个股</a>
  <a href="#bystock">② 个股 → 席位</a>
  <a href="#multi">③ 累计型榜</a>
  <a href="#pool">席位池配置</a>
</nav>
<div class="wrap">
<header class="page">
<h1>龙虎榜追踪</h1>
</header>

<section id="byseat" style="scroll-margin-top:56px">
  <div class="section-title"><span class="no">1</span><span class="name">席位 → 个股 汇总</span></div>
  <!-- ⚠️ 卡片顺序（2026-09-18 第 24 轮）：矩阵必须排在「数据总览」**之前**。
       此前是反的（总览在前），靠 lhb_ui.js 的 setupPanelOrder() 在运行期用 JS
       把矩阵 insertBefore 到总览前面 —— 副作用是首屏先画出总览、紧接着被重排到
       下方，用户看到「数据总览闪一下然后消失」。现改为生成端直接定序，
       setupPanelOrder() 已删除。**改顺序请改这里，不要在 JS 里重排。** -->
  <div class="card"><h2>席位 → 个股 矩阵</h2>
    {body_v1}
    <div class="legend"><span class="sw"></span>单元格底色条 = 净额强度（宽度按本视图最大单笔净额归一化），颜色 + 箭头 + 长度三重编码</div>
    <div class="legend" style="margin-top:6px"><b style="color:var(--gray-60)">未出榜</b> = 该个股当日<b>未出龙虎榜</b>，整行无席位数据可看｜<b style="color:var(--gray-60)">仅累计榜</b> = 当日<b>只上了连续三日/区间型榜</b>，席位明细在 ③ 累计型榜中｜<b>—</b> = 该个股当日<b>上了日度榜</b>，但该席位未出现在榜单上</div>
  </div>
  <div class="card"><h2>数据总览</h2>
    <div class="sem-legend">A股语义：<span style="color:var(--up);font-weight:600">红=净买</span> / <span style="color:var(--down);font-weight:600">绿=净卖</span> ｜ 覆盖 {len(days)} 个交易日（{dates_desc[-1]} ~ {dates_desc[0]}）｜ 更新：{latest['date']}</div>
    <div class="kpi-grid">{kpis}</div>
    <div class="note" style="margin-top:16px"><b>多头主力 TOP3 · 知名游资</b>：{top_html or '—'}</div>
    <div class="note"><b>空头主力 TOP3 · 知名游资</b>：{down_html or '—'}</div>
    <div class="note"><b>多头主力 TOP3 · 量化</b>：{top_q_html or '—'}</div>
    <div class="note"><b>空头主力 TOP3 · 量化</b>：{down_q_html or '—'}</div></div>
</section>

<section id="bystock" style="scroll-margin-top:56px">
  <div class="section-title"><span class="no">2</span><span class="name">个股 → 席位 汇总</span></div>
  <div class="card"><h2>个股 → 席位 矩阵</h2>
    {body_v2}
    <div class="legend"><span class="sw"></span>单元格底色条 = 净额强度（宽度按本视图最大单笔净额归一化）</div>
    <div class="legend" style="margin-top:6px"><b style="color:var(--gray-60)">未出榜</b> = 该个股当日<b>未出龙虎榜</b>，整行无席位数据可看｜<b style="color:var(--gray-60)">仅累计榜</b> = 当日<b>只上了连续三日/区间型榜</b>，席位明细在 ③ 累计型榜中｜<b>—</b> = 该个股当日<b>上了日度榜</b>，但该席位未出现在榜单上</div>
  </div>
</section>

<section id="multi" style="scroll-margin-top:56px">
  <div class="section-title"><span class="no">3</span><span class="name">累计型榜（三日 / 区间）</span>
    <span class="desc">区间累计口径 · 与日度矩阵不可混用</span></div>
  {render_multi(days, pool)}
</section>

<section id="pool" style="scroll-margin-top:56px">
  <div class="section-title"><span class="no">4</span><span class="name">席位池配置</span>
    <span class="desc">维护文件：席位跟踪池.csv（Excel 可编辑）</span></div>
  {render_pool(pool)}
</section>

<div class="footer">龙虎榜席位追踪 · 由 build_lhb_dashboard.py 自动生成 ｜ 席位归属为市场习惯称法，仅供复盘参考，不构成投资建议</div>
</div>
<button type="button" class="jump-back" id="jump-back" title="回到第 1 个视图原来的位置">← 返回「① 席位 → 个股」</button>
<script>
{subs_js}
var links=[].slice.call(document.querySelectorAll('nav.tabs a'));
links.forEach(function(a){{
  a.addEventListener('click',function(){{
    links.forEach(function(x){{x.classList.remove('active');}});
    a.classList.add('active');
  }});
}});
/* ===== v7 折叠：Excel outline 风格分组 ===== */
/* 2026-09-18 第 29 轮：**日期组折叠功能已整体移除**。
   原先这里维护 grpHide 字典 + applyDateGroups()（给 th.dt / td 打 data-grp-hide）
   + syncDateGroupBtns() + 点击委托里的 .grp-btn 分支，配合 theme 的 .is-collapsed
   绝对定位把折叠组挤到表头右缘。用户判定「已不适合现在的模式」→ 全删，
   日期列改为**始终全部可见（常显）**。
   ⚠️ 只保留 `printing`（供 rowspan 重算与历史段展开时判断「打印/等效全展开」）。 */
var printing=false;

/* 行分组：按 data-sec="hist" 隐藏；c1 的 rowspan 由 fresh+可见 hist 数重算 */
/* ⚠️ aria-expanded 挂在 tr 内的 .grp-bar-btn 上，不在 tr 上。
   早期版本误读 tr 的 aria-expanded（永远为 null），导致历史行永远展开不了。 */
function barIsOpen(barRow){{
  if(printing) return true;
  var b = barRow ? barRow.querySelector('.grp-bar-btn') : null;
  return !!b && b.getAttribute('aria-expanded') === 'true';
}}
function recomputeRows(){{
  var tbls=document.querySelectorAll('table.matrix');
  [].forEach.call(tbls,function(t){{ t.classList.add('ready'); }});
  [].forEach.call(tbls,function(tbl){{
    var blocks={{}}, order=[];
    [].forEach.call(tbl.querySelectorAll('tbody tr[data-blk]'),function(tr){{
      var b=tr.getAttribute('data-blk');
      if(!blocks[b]){{ blocks[b]=[]; order.push(b); }}
      blocks[b].push(tr);
    }});
    order.forEach(function(b){{
      var rs=blocks[b], nVisible=0, nHidden=0;
      var barRow = null, barOpen = false;
      /* 先定位本块的分组条（渲染顺序：fresh → bar → hist，故 bar 必定先于 hist 被遍历到） */
      rs.forEach(function(tr){{
        if(tr.getAttribute('data-sec') === 'bar'){{ barRow = tr; barOpen = barIsOpen(barRow); }}
      }});
      rs.forEach(function(tr){{
        var sec = tr.getAttribute('data-sec');
        if(sec === 'bar'){{ tr.classList.remove('rowhide'); return; }}
        /* 聚合席位明细行（sec="sub"）：由 LHBsubToggle 独立控制 rowhide，这里只按实际可见性计入 rowspan，不得改写其折叠状态 */
        if(sec === 'sub'){{
          if(!tr.classList.contains('rowhide')) nVisible++;
          return;
        }}
        var isHist = sec === 'hist';
        var show = !isHist || barOpen;
        if(show){{ tr.classList.remove('rowhide'); nVisible++; }}
        else {{ tr.classList.add('rowhide'); nHidden++; }}
      }});
      // 重算 c1.rowSpan = nVisible（包含 fresh + 可见 hist，不含 barRow）
      var freshFirst = rs.find(function(t){{ return t.getAttribute('data-sec') === 'fresh'; }});
      if(freshFirst){{
        var c1 = freshFirst.querySelector('th.c1');
        if(c1){{ c1.rowSpan = nVisible; }}
      }}
      // 分组条按钮文字
      if(barRow){{
        var btn = barRow.querySelector('.grp-bar-btn');
        if(btn){{
          var open = printing || btn.getAttribute('aria-expanded') === 'true';
          var arr = btn.querySelector('.arr');
          if(arr) arr.textContent = open ? '\\u2212' : '+';
          btn.setAttribute('aria-expanded', open ? 'true' : 'false');
          btn.setAttribute('aria-label', (open?'收起 ':'展开 ')+nHidden+' 条历史');
        }}
      }}
    }});
  }});
}}

/* 行分组条 [+/-] 按钮（事件委托，2026-09-18 第 29 轮：日期组按钮分支已移除） */
/* ⚠️ 必须用 closest：按钮内部是 <span>，用户点到文字时 e.target 是 span 而非 button。
   早期版本只判断 t.classList.contains(...)，导致点到文字完全无反应。 */
document.addEventListener('click', function(e){{
  var t = e.target;
  if(!t || !t.closest) return;
  // 行分组条按钮（含内部 .arr / .lab / .hint span）
  var barBtn = t.closest('.grp-bar-btn');
  if(barBtn){{
    var open = barBtn.getAttribute('aria-expanded') === 'true';
    barBtn.setAttribute('aria-expanded', open ? 'false' : 'true');
    recomputeRows();
    return;
  }}
}});

/* ===== 跨视图跳转：① 席位→个股（点标的名） ⇄ ② 个股→席位（浮出返回按钮） =====
   ⚠️ 两个坑：
     ① 同一只票会在多个席位块下重复出现（视图① 每个席位一块）→ 用 data-jump 找
        **当前视图里第一个可见块**，而不是按 id 找（id 必须全站唯一，只挂在视图②）；
     ② 跳转必须走点击事件（e.preventDefault + 手动切 Tab + 滚到目标），
        不能依赖原生 `#anchor` —— 页面用 JS 接管了 Tab（panel.hidden 切换），
        目标面板 hidden 时浏览器无法滚动到它。 */
var jumpLast = null;

/* 找到视图① 里该股「第一个可见块」的首行（折叠掉的块要跳过）。 */
function findJumpSource(code){{
  var sec = document.getElementById('byseat');
  if(!sec) return null;
  var links = sec.querySelectorAll('a.stock-link[data-jump="'+code+'"]');
  for(var i=0;i<links.length;i++){{
    if(links[i].getClientRects().length) return links[i];
  }}
  return links[0] || null;
}}

/* 找到视图② 里该股块首行（带 id="s-<code>"，天然唯一）。 */
function findJumpTarget(code){{
  return document.getElementById('s-'+code);
}}

function switchPanel(panelId){{
  var nav = document.getElementById('topnav');
  if(!nav) return;
  var tab = nav.querySelector('a[aria-controls="'+panelId+'"]')
         || nav.querySelector('a[href="#'+panelId+'"]');
  if(tab) tab.click();
}}

function updateJumpBack(){{
  var btn = document.getElementById('jump-back');
  if(!btn) return;
  var sec = document.getElementById('bystock');
  // 只在视图② 且确实是从视图① 跳过来的场合显示
  var on = !!jumpLast && sec && !sec.hidden;
  btn.classList.toggle('on', on);
}}

function goJump(type, code){{
  /* ⚠️ 「返回」不需要 code（它靠 jumpLast 定位来源块），故 code 校验只对
     toStock 生效 —— 早期版本把校验放在函数首行，导致点返回直接 return、
     页面留在视图② 不动。 */
  if(type === 'toStock'){{
    if(!code) return;
    var src = findJumpSource(code);
    if(!src) return;
    // 记录来源块首行（同一票多块时，回跳要回到**用户实际点的那一块**）
    jumpLast = src.closest('tr');
    switchPanel('bystock');
    highlightJump(code);
  }} else {{
    if(!jumpLast || !document.body.contains(jumpLast)) jumpLast = null;
    var origin = jumpLast;
    switchPanel('byseat');
    if(origin){{
      requestAnimationFrame(function(){{
        requestAnimationFrame(function(){{
          origin.scrollIntoView({{block:'center', inline:'nearest'}});
          flashRow(origin);
        }});
      }});
    }}
    jumpLast = null;
  }}
  updateJumpBack();
}}

function clearJumpFx(){{
  document.querySelectorAll('tr.is-jump-target').forEach(function(tr){{
    tr.classList.remove('is-jump-target');
  }});
}}

function flashRow(tr){{
  if(!tr) return;
  tr.classList.add('is-jump-target');
  setTimeout(function(){{ tr.classList.remove('is-jump-target'); }}, 1600);
}}

function highlightJump(code){{
  var tr = findJumpTarget(code);
  if(!tr) return;
  clearJumpFx();
  // 等 Tab 切换完成（panel.hidden 生效、布局稳定）再滚动
  requestAnimationFrame(function(){{
    requestAnimationFrame(function(){{
      tr.scrollIntoView({{block:'center', inline:'nearest'}});
      flashRow(tr);
    }});
  }});
}}

document.addEventListener('click', function(e){{
  var t = e.target;
  if(!t || !t.closest) return;
  var a = t.closest('a.stock-link');
  if(a){{
    var code = a.getAttribute('data-jump');
    if(!code) return;                       // 无 code（累计型榜等）→ 交给浏览器默认行为
    e.preventDefault();
    goJump('toStock', code);
    return;
  }}
  var back = t.closest('#jump-back');
  if(back){{ e.preventDefault(); goJump('toStock_back', ''); return; }}
}});

/* Esc 也能返回；Tab 被手动切走时收起返回按钮 */
document.addEventListener('keydown', function(e){{
  if(e.key === 'Escape' && jumpLast) goJump('toStock_back', '');
}});
[].forEach.call(document.querySelectorAll('nav.tabs a'), function(a){{
  a.addEventListener('click', function(){{
    if(jumpLast && a.getAttribute('href') !== '#bystock'){{ jumpLast = null; }}
    updateJumpBack();
  }});
}});

/* 打印时自动展开全部日期列 + 全部折叠行 */
window.addEventListener('beforeprint',function(){{
  printing=true; recomputeRows();
}});
window.addEventListener('afterprint',function(){{
  printing=false; recomputeRows();
}});
recomputeRows();
</script>
{ui_script}
</body></html>"""
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"已生成: {OUT}")
    print(f"  视图①席位→个股: {len(v1)} 行（{len({r['player'] for r in v1})} 个席位 / {len({r['stock'] for r in v1})} 只票）")
    print(f"  视图②个股→席位: {len(v2)} 行（{len({r['stock'] for r in v2})} 只票 / {len({r['player'] for r in v2})} 个席位）")
    print(f"  文件大小 {os.path.getsize(OUT)/1024:.1f} KB")


if __name__ == "__main__":
    main()
