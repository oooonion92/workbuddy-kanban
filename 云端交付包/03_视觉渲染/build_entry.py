# -*- coding: utf-8 -*-
"""
主入口看板生成器 build_entry.py
================================
产出 `daily/entry.html` —— 与 `daily/dashboard.html` **完全独立的另一个网址**。

排版（自上而下，共 5 块）：
  ① 市场总览      —— 客观数据 + **并排双图（情绪评分 | 涨停家数×连板高度）** + **大盘全局主观分析**（全页唯一主观块）
  ② 闸门 红黄蓝
  ③ 板块热力图
  ④ 连板梯队

设计约定：
  - 底层取数/绘图**复用** `build_dashboard`（import 使用，不修改其行为），保证两页数据同源
  - 除 ① 之外的所有块**只放客观数据**，不写主观判断
  - **主观块只做盘面分析**，不写自我反思/思考过程（我错在哪、教训、为什么）——2026-09-21 用户规则
  - 指数表「成交额」必须带 vs 前一交易日环比 —— 2026-09-21 用户规则（主看板同步）
  - 当前阶段 = **排版绘制**（layout-first）：把 5 块的骨架与真实数据接上，
    不追求信息完整度与移动端打磨
"""
import os
import re
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_dashboard as bd  # noqa: E402  复用取数与绘图底层

OUT = os.path.join(bd.DAILY, "entry.html")

# 图表窗口与主看板一致（新口径 8/14 起）
CHART_X_MIN = "2026-08-14"


# ---------------------------------------------------------------- 小工具
def sec_title(no, color, name, desc):
    """区块标题（与主看板同款 section-title 结构）"""
    return (f'<div class="section-title"><span class="no" style="background:{color}">{no}</span>'
            f'<span class="name">{name}</span><span class="desc">{desc}</span></div>')


def pick_block(html, open_tag):
    """按完整开标签取出一整个块（div 深度配对，避免正则被嵌套打断）

    ⚠️ 融合源的 `extract_latest_report()` 有两个来源：
      - `ext["hero"]` = `<div class="hero">` 起、到「下一个官方锚点」止
        → **含** 决策摘要圆环 + `card bd-blue`(数据摘要) + `card bd-green`(对应解读)
      - `ext["body"]` = 从 `<!-- ========== 大盘结构分析` 起
        → **不含**「一、决策摘要」（它在 body 起点之前，会被切掉）
    所以取「决策摘要/解读卡」必须从 `ext["hero"]` 里取，不能从 blocks 里取。
    """
    i = html.find(open_tag)
    if i < 0:
        return ""
    depth, j, n = 0, i, len(html)
    while j < n:
        if html[j] == '<':
            if html.startswith('<div', j):
                depth += 1
                j = html.find('>', j) + 1
                continue
            if html.startswith('</div>', j):
                depth -= 1
                j += 6
                if depth == 0:
                    return html[i:j]
                continue
            j = html.find('>', j) + 1
            continue
        j += 1
    return html[i:]


def pick_card(html, kind):
    """取 `<div class="card <kind>">` 整块"""
    return pick_block(html, f'<div class="card {kind}">')


def pct(v):
    """涨跌幅带符号 + 红涨绿跌着色"""
    if v is None:
        return "-"
    c = "up" if v > 0 else ("down" if v < 0 else "")
    return f'<span class="{c}">{v:+.2f}%</span>'


def load_day_json(date_str):
    """直接读当日 复盘数据.json —— 补 collect() 未暴露的字段（指数/跌停/炸板率/分项）"""
    p = os.path.join(bd.DAILY, date_str.replace("-", ""), "复盘数据.json")
    if not os.path.exists(p):
        return {}
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


# ---------------------------------------------------------------- ① 市场总览
def block_mkt(rec, dj, hero, subj_html, pair_html, mom):
    """市场总览 = 决策摘要圆环 + 指数（成交额带环比）+ 核心指标 + 涨停/高度合图 + 大盘全局主观分析"""
    # 核心指标 chips
    lim_up = dj.get("limit_up", rec.get("limit_up"))
    lim_dn = dj.get("limit_down", "-")
    mx = dj.get("max_board", rec.get("max_board"))
    sd = dj.get("sentiment_detail", {}) or {}
    exp = (sd.get("explosion_rate") or {}).get("raw", "-")
    chips = [
        ("涨停家数", lim_up, ""),
        ("跌停家数", lim_dn, ""),
        ("炸板率", f"{exp}%" if exp != "-" else "-", ""),
        ("最高板", f"{mx} 板" if mx != "-" else "-", ""),
    ]
    chip_html = "".join(
        f'<div style="flex:1 1 150px;min-width:130px;background:var(--card2);'
        f'border:1px solid var(--card-border);border-radius:12px;padding:12px 14px">'
        f'<div style="color:var(--text3);font-size:11.5px">{k}</div>'
        f'<div class="num" style="font-size:20px;font-weight:600;margin-top:2px">{v}</div></div>'
        for k, v, _ in chips)

    # 指数表（「成交额」列带 vs 前一日环比 —— 2026-09-21 规则）
    idx = dj.get("indices", {}) or {}
    rows = ""
    for name, d in idx.items():
        m = mom.get(name)
        if m is None:
            m = mom.get(bd.IDX_SHORT.get(name, ""))
        mom_html = bd.mom_span(m) if m is not None else ""
        rows += (f'<tr><td><b>{name}</b></td>'
                 f'<td class="num">{d.get("close", "-")}</td>'
                 f'<td>{pct(d.get("chg"))}</td>'
                 f'<td class="num">{d.get("amount_yi", "-")} 亿{mom_html}</td></tr>')
    idx_table = (f'<div class="scroll"><table>'
                 f'<tr><th>指数</th><th>收盘</th><th>涨跌</th><th>成交额（环比）</th></tr>'
                 f'{rows}</table></div>') if rows else \
                '<div style="color:var(--text3);font-size:12.5px">（指数数据未接入）</div>'

    subj = subj_html or \
        '<div style="color:var(--text3);font-size:12.5px">（主观分析未接入：需当日融合源）</div>'

    return (sec_title(1, "#2563EB", "市场总览", "客观数据 + 大盘全局主观分析")
            + hero
            + f'<div class="card">{idx_table}</div>'
            + f'<div class="card" style="display:flex;gap:10px;flex-wrap:wrap">{chip_html}</div>'
            # 涨停家数（次轴：高度柱状）—— 合并主次坐标轴，置于上方核心指标之下
            + pair_html
            # 主观块：不套 card（融合源的解读卡自带 card bd-green，套了会出现双层边框）
            + f'<div style="margin:16px 0">'
              f'<div class="tag" style="background:rgba(180,83,9,.1);color:#B45309;'
              f'border:1px solid rgba(180,83,9,.3)">主观 · 大盘全局分析（仅盘面）</div>'
              f'{subj}</div>')


# ---------------------------------------------------------------- ② 闸门
def block_gate(recs):
    return (sec_title(2, "#7C5CFC", "闸门", "红=可开仓 · 黄=仅验证 · 蓝=防守")
            + '<div class="card">'
            + bd.gate_timeline(recs)
            + '<div class="note">逐交易日闸门读数，来自当日复盘落盘的 gate 字段。</div>'
            + '</div>')


# ---------------------------------------- 市场总览内的并排双图：情绪评分 | 涨停×连板
def pair_row(totals, structs, sents, lims, boards, x_max):
    """两张图**并排**（视觉平衡）：左＝情绪评分折线，右＝涨停家数×连板高度（主次坐标轴）。

    ⚠️ 并排时每张图只占半个容器宽 → **设计宽必须同步缩小**。
    若沿用整栏的 w=960，并排后缩放比会掉到 ≈0.5，图内 12px 文字实际只渲染 ≈6px（不可读）。
    经验值：设计宽 ≈ 半栏宽（w=560）→ 缩放比≈0.8~1.3、文字 10~15px 可读。
    ⚠️ 并排图传 `max_w=0`＝**不限宽、铺满半栏**（限宽会在宽屏下留下左右空白，看着「没填满」）。
    """
    W, H = 560, 300
    score = bd.svg_combined_chart([
        {"points": totals,  "color": "#DC2626", "yaxis": "left", "label": "总分",   "stroke_width": 3},
        {"points": structs, "color": "#2563EB", "yaxis": "left", "label": "结构分", "stroke_width": 1.5},
        {"points": sents,   "color": "#16A34A", "yaxis": "left", "label": "情绪分", "stroke_width": 1.5, "dash": True},
    ], w=W, h=H, x_min=CHART_X_MIN, x_max=x_max, single_axis=True, max_w=0)
    combo = bd.svg_combined_chart([
        {"points": lims,   "color": "#F59E0B", "yaxis": "left",  "label": "涨停家数(家)", "kind": "line"},
        {"points": boards, "color": "#E03131", "yaxis": "right", "label": "最高连板(板)", "kind": "bar", "opacity": 0.55},
    ], w=W, h=H, x_min=CHART_X_MIN, x_max=x_max, max_w=0)

    def _card(title, tag, sub, svg):
        t = (f'<span class="tag" style="background:rgba(37,99,235,.1);color:#2563EB">{tag}</span>'
             if tag else '')
        return (f'<div class="card" style="margin:0"><h2>{title} {t}</h2>'
                f'<div style="color:#6B7280;font-size:12px;margin-bottom:8px">{sub}</div>{svg}</div>')

    return ('<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(340px,1fr));'
            'gap:12px;margin:16px 0">'
            + _card("情绪评分", "0-100 同量纲", "总分粗实线 · 结构分实线 · 情绪分虚线", score)
            + _card("涨停家数与连板高度", "主次坐标轴",
                    "左轴 涨停家数(家) 折线 · 右轴 最高连板(板) 柱状（opacity 0.55）· 红=强", combo)
            + '</div>')


# ---------------------------------------------------------------- ③ 板块热力图
def block_theme(recs):
    return (sec_title(3, "#0E8A5F", "板块热力图", "近若干交易日 · 景气度矩阵")
            + '<div class="card">'
              '<div style="color:#6B7280;font-size:12px;margin-bottom:8px">'
              '颜色深浅＝景气度（0-100） · 红=高景气，绿=低景气 · 悬停查看当日分与原始指标<br>'
              '数据源：OneDrive 全量A股按细分行业聚合（≈280 板块/日） · '
              '行＝窗口内累计成交额 TOP18 的 <b>L1 方向</b>（显式分类表归口） · 列＝最近 10 个交易日<br>'
              '<b>格内＝3 日平滑分</b>（0.5/0.3/0.2）；基准口径＝当日截面分位'
              '（强度30/资金30/宽度20/量能20；中位恒≈50）→ <b>横向与纵向均可比</b>；'
              '当日整体强弱见底部「当日中位涨幅」条</div>'
            + bd.sector_heatmap(recs)
            + '</div>')


# ---------------------------------------------------------------- ④ 连板梯队
def block_ladder(ladder_html, date=None):
    """连板梯队（融合表）：情绪定位速览（主观，来自融合源）+ 涨停质量分表（客观逐票）+ 天梯判读。

    2026-09-21 融合：原「手写天梯表」与「涨停质量分表」是同一对象的两个视角，已并为一卡三区
    —— 手写表的「涨停/20cm」聚合行不再逐票重复（由质量分表覆盖），只保留其结论句；
    「反核/炸板/炸板池」等未涨停角色行保留（机器侧无此数据）。
    """
    return (sec_title(4, "#C92A2A", "连板梯队", "情绪定位 + 涨停质量分（融合）")
            + bd.zt_quality_table(date, ladder_html=ladder_html))


# ------------------------------------------------- 详细看板日期选择器
def day_picker_html():
    """题头右侧「详细看板」入口：按日期跳转到 daily/{YYYYMMDD}/dashboard.html。

    这些每日快照由 `build_dashboard.py --snapshot YYYYMMDD` 生成
    （正常构建也会自动留当日一份到 daily/{最新交易日}/dashboard.html）；
    历史日快照的叙事来自 `scripts/md_to_replay.py` 由当日 05 复盘 md 重建的融合源，
    故**显示的是当日叙事**，不会套用最新一日。
    """
    import glob as _glob
    snaps = sorted(_glob.glob(os.path.join(bd.DAILY, "20*", "dashboard.html")))
    if not snaps:
        return ''
    opts = ['<option value="">选择日期…</option>']
    for p in reversed(snaps):                      # 最新在前
        d = os.path.basename(os.path.dirname(p))
        opts.append(f'<option value="{d}/dashboard.html">{d[:4]}-{d[4:6]}-{d[6:8]}</option>')
    return ('<div style="display:flex;align-items:center;gap:8px;flex:0 0 auto">'
            '<span style="font-size:12px;color:var(--text3);white-space:nowrap">详细看板</span>'
            '<select onchange="if(this.value)location.href=this.value" '
            'style="font-size:12.5px;padding:6px 10px;border:1px solid var(--card-border);'
            'border-radius:8px;background:var(--card);color:var(--text);cursor:pointer">'
            + ''.join(opts) + '</select></div>')


# ---------------------------------------------------------------- 主流程
def main():
    recs = bd.collect()
    if not recs:
        print("✗ 没有可用交易日数据")
        return 1
    dates = [r["date"] for r in recs]
    last = recs[-1]
    x_max = dates[-1]

    totals = bd.trim_series([(r["date"], r["total"]) for r in recs])
    structs = bd.trim_series([(d, r["structure"]) for d, r in zip(dates, recs)])
    sents = bd.trim_series([(d, r["sentiment"]) for d, r in zip(dates, recs)])
    lims = bd.trim_series([(d, r["limit_up"]) for d, r in zip(dates, recs)])
    boards = bd.trim_series([(d, r["max_board"]) for d, r in zip(dates, recs)])

    ext = bd.extract_latest_report()
    hero, ladder, subj, ext_style = "", "", "", ""
    if ext:
        ext_style = ext["style"] + "\n" + bd.SEMANTIC_OVERRIDE
        # 圆环 + 决策摘要/解读卡都在 ext["hero"] 里，分别取出
        hero = pick_block(ext["hero"], '<div class="hero">')
        subj = pick_card(ext["hero"], "bd-green")     # 主观 · 大盘全局分析
        blocks = bd.classify_blocks(bd.split_blocks(ext["body"]))
        ladder = blocks.get("ladder", "")             # 四、情绪定位与连板天梯

    dj = load_day_json(last["date"])
    mom = bd.amount_mom_map(recs)   # 成交额 vs 前一日环比

    # 导航只保留 3 项（闸门不设跳转入口，板块仍在页内作为第 2 段）
    nav_items = [("mkt", "市场总览"), ("theme", "板块热力图"), ("ladder", "连板梯队")]
    nav = chr(10).join(f'  <a href="#{k}">{v}</a>' for k, v in nav_items)
    picker = day_picker_html()

    html = f"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>盯盘复盘 · 主入口</title>
<style>
{ext_style}
{bd.BASE_CSS}
</style>
</head>
<body><div class="wrap">
<h1>盯盘复盘 · 主入口</h1>
<div style="display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap">
<nav class="tabs" id="topnav" style="flex:1 1 auto">
{nav}
</nav>
{picker}
</div>

<section id="mkt">{block_mkt(last, dj, hero, subj, pair_row(totals, structs, sents, lims, boards, x_max), mom)}</section>
<section id="gate">{block_gate(recs)}</section>
<section id="theme">{block_theme(recs)}</section>
<section id="ladder">{block_ladder(ladder, last["date"])}</section>

<div class="footer">数据源：通达信 ｜ 更新：{x_max} ｜ 本页与主看板 dashboard.html 为两个独立网址 ｜ 仅供复盘参考，不构成投资建议</div>
</div>
</body></html>
"""

    with open(OUT, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"主入口已生成: {OUT}（{len(html.encode('utf-8'))} 字节，{len(recs)} 个交易日）")
    print(f"  主观分析: {'已接入' if subj else '未接入'} ｜ 连板梯队: {'已接入' if ladder else '未接入'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
