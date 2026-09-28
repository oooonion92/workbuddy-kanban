# -*- coding: utf-8 -*-
"""把 05 复盘 html 里"双维评分"和"涨停梯队"两段密集表格重排为 chip + 卡片布局。
用法：python restyle_dense.py <file>
"""
import re, sys

def restyle_dense_tables(t):
    # ---------- 1. 双维评分（结构/情绪分明细） ----------
    # 模式：table 含 "维度/得分/构成明细" 表头，td2 用 "｜" 分隔多条记录
    # 每条记录形如："30m 缠论结构 25 分制：5（跌破中枢下沿转三卖, 8/21 中枢 ZD 3892 失守）"
    # 或："涨停家数 15 分制：9（46 家, 40-59 档）" 或 "亏钱惩罚 -3（跌停 13 家翻倍）"
    pattern_dim = r'([^\s｜｜：:]+?(?:\s+[^\s｜｜：:]+?)*?)\s*(\d+)\s*分制[：:]\s*([+\-]?\d+)\s*[（(]([^）)]+)[）)]'

    def restyle_one_score_row(td2_html):
        inner = re.sub(r'<[^>]+>', '', td2_html)
        items = re.split(r'[｜|]\s*', inner)
        items = [s.strip() for s in items if s.strip()]
        chips = []
        for it in items:
            m = re.match(pattern_dim, it)
            if m:
                label, full, score, note = m.group(1).strip(), m.group(2), m.group(3), m.group(4).strip()
                chips.append(f'''<div class="score-chip">
  <div class="score-chip-lbl">{label} <span class="score-chip-full">/{full}</span></div>
  <div class="score-chip-val"><b>{score}</b></div>
  <div class="score-chip-note">{note}</div>
</div>''')
            else:
                chips.append(f'<div class="score-chip score-chip-fallback"><div class="score-chip-note">{it[:60]}</div></div>')
        if not chips:
            return td2_html
        return '<div class="score-chips">' + ''.join(chips) + '</div>'

    # 精确定位：双维评分段（标题含"二、双维评分"或"双维评分"）+ 表头含"构成明细"
    m_score = re.search(
        r'(<h2[^>]*>[^<]*双维评分[^<]*</h2>[\s\S]*?<table[^>]*>[\s\S]*?</table>)',
        t, re.S
    )
    if m_score:
        block = m_score.group(1)
        new_block = re.sub(
            r'<tr>(.*?)</tr>',
            lambda tr_match: process_score_tr(tr_match.group(0), restyle_one_score_row),
            block, flags=re.S
        )
        t = t.replace(block, new_block, 1)

    # ---------- 2. 涨停梯队（标的列表） ----------
    # 模式：table 含 "高度/家数/标的/题材归属/强度标签" 表头
    pattern_stock = r'([\u4e00-\u9fa5A-Z]+(?:[·\u4e00-\u9fa5A-Z]+)?)\s+(\d{6})\s*[（(]([^）)]+)[）)]'

    def restyle_one_ladder_row(td2_html):
        inner = re.sub(r'<[^>]+>', '', td2_html)
        items = re.split(r'[｜|]\s*', inner)
        items = [s.strip() for s in items if s.strip()]
        cards = []
        for it in items:
            m = re.match(pattern_stock, it)
            if m:
                name, code, info = m.group(1).strip(), m.group(2), m.group(3).strip()
                parts = [p.strip() for p in re.split(r'\s*/\s*', info)]
                if len(parts) < 3:
                    parts = info.split(' / ')
                time_part = parts[0] if len(parts) >= 1 else info
                seal_part = parts[1] if len(parts) >= 2 else ''
                seal_color = '#00A870' if any(c in seal_part for c in ['亿','万']) and '封' not in seal_part else 'var(--text-tertiary)'
                blast_part = parts[2] if len(parts) >= 3 else ''
                blast_color = '#B45309' if re.search(r'\d+', blast_part) and int(re.search(r'\d+', blast_part).group() or 0) > 0 else 'var(--text-tertiary)'
                turnover = re.search(r'换手\s*([\d.]+%)', info)
                turnover_html = f'<span style="color:#B45309">换手 {turnover.group(1)}</span>' if turnover else ''
                cards.append(f'''<div class="stock-card">
  <div class="stock-card-name">{name} <span class="stock-card-code">{code}</span></div>
  <div class="stock-card-meta">
    <span class="stock-card-time">{time_part}</span>
    <span class="stock-card-seal" style="color:{seal_color}">{seal_part}</span>
    <span class="stock-card-blast" style="color:{blast_color}">{blast_part}</span>
    {turnover_html}
  </div>
</div>''')
            else:
                cards.append(f'<div class="stock-card"><div class="stock-card-name">{it[:40]}</div></div>')
        if not cards:
            return td2_html
        return '<div class="stock-cards">' + ''.join(cards) + '</div>'

    # 精确定位：涨停梯队段（标题含"涨停梯队"）+ 表头含"标的"
    m_ladder = re.search(
        r'(<h2[^>]*>[^<]*涨停梯队[^<]*</h2>[\s\S]*?<table[^>]*>[\s\S]*?</table>)',
        t, re.S
    )
    if m_ladder:
        block = m_ladder.group(1)
        new_block = re.sub(
            r'<tr>(.*?)</tr>',
            lambda tr_match: process_ladder_tr(tr_match.group(0), restyle_one_ladder_row),
            block, flags=re.S
        )
        t = t.replace(block, new_block, 1)

    return t


def process_score_tr(tr, restyle_fn):
    if '<th' in tr:
        return tr
    tds = re.findall(r'<td[^>]*>.*?</td>', tr, re.S)
    if len(tds) >= 3:
        td2 = tds[2]
        # 提取 td 内的 inner HTML，去掉首尾 <td ...> 和 </td>
        m_open = re.match(r'(<td[^>]*>)(.*)(</td>)$', td2, re.S)
        if m_open:
            inner = m_open.group(2)
            new_inner = restyle_fn(inner)
            # 保留原 td 的属性
            attr = m_open.group(1)
            new_td = attr + new_inner + '</td>'
            return tr.replace(td2, new_td, 1)
    return tr


def process_ladder_tr(tr, restyle_fn):
    if '<th' in tr:
        return tr
    tds = re.findall(r'<td[^>]*>.*?</td>', tr, re.S)
    if len(tds) >= 3:
        td2 = tds[2]
        m_open = re.match(r'(<td[^>]*>)(.*)(</td>)$', td2, re.S)
        if m_open:
            inner = m_open.group(2)
            new_inner = restyle_fn(inner)
            attr = m_open.group(1)
            new_td = attr + new_inner + '</td>'
            return tr.replace(td2, new_td, 1)
    return tr


# ---------- 3. CSS 追加 ----------
DENSE_CSS = """
<style>
/* 双维评分 chip 网格 */
.score-chips{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:8px;padding:2px 0}
.score-chip{background:#F0F4FF;border:0.5px solid #C7D2FE;border-radius:8px;padding:7px 9px;transition:transform .15s ease}
.score-chip:hover{transform:translateY(-1px);box-shadow:0 2px 6px rgba(37,99,235,.1)}
.score-chip-lbl{font-size:10px;color:#2563EB;font-weight:500;line-height:1.3}
.score-chip-full{color:var(--text-tertiary);font-weight:400}
.score-chip-val{font-size:14px;font-weight:500;color:#14171A;margin-top:1px}
.score-chip-val b{font-size:18px;color:#2563EB}
.score-chip-note{font-size:10px;color:var(--text-secondary);margin-top:2px;line-height:1.4}
.score-chip-fallback{background:var(--bg-secondary);border-color:var(--card-border)}
.score-chip-fallback .score-chip-note{color:var(--text-primary)}

/* 涨停梯队标的卡片：桌面端横向网格平铺 */
.stock-cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:8px;padding:2px 0}
.stock-card{background:rgba(255,255,255,.94);border:0.5px solid var(--card-border);border-radius:10px;padding:9px 12px;transition:transform .15s ease,border-color .15s ease}
.stock-card:hover{transform:translateY(-2px);border-color:#C7D2FE}
.stock-card-name{font-size:12.5px;font-weight:500;color:#14171A;line-height:1.3}
.stock-card-code{font-family:"SF Mono",Consolas,monospace;font-size:11px;color:var(--text-tertiary);font-weight:400;margin-left:4px}
.stock-card-meta{display:flex;flex-wrap:wrap;gap:8px;margin-top:3px;font-size:10.5px;color:var(--text-tertiary);line-height:1.4}
.stock-card-time{font-weight:500}
.stock-card-seal,.stock-card-blast{font-weight:500}

html[data-theme=dark] .score-chip{background:rgba(59,130,246,.14);border-color:rgba(59,130,246,.3)}
html[data-theme=dark] .score-chip-val b{color:#93C5FD}
html[data-theme=dark] .stock-card{background:rgba(19,28,46,.7);border-color:#1E293B}
html[data-theme=dark] .stock-card-name{color:#E6E8EE}
</style>
"""


def apply(src):
    t = open(src, encoding='utf-8').read()
    t = restyle_dense_tables(t)
    # 在第一个 </style> 之前注入 dense CSS（而不是插到 </head> 前），
    # 这样 extract_latest_report 抓取第一个 style 块时能把 dense CSS 一并带走
    if '.score-chips{' not in t:
        # 先去掉 DENSE_CSS 自身的多余 <style> 包裹（apply 用时只取内容）
        css_body = DENSE_CSS.replace('<style>', '').replace('</style>', '').strip()
        # 找到第一个 </style> 位置
        idx = t.find('</style>')
        if idx > 0:
            t = t[:idx] + '\n' + css_body + '\n' + t[idx:]
    open(src, 'w', encoding='utf-8').write(t)
    print('已重排:', src)


if __name__ == '__main__':
    apply(sys.argv[1] if len(sys.argv) > 1 else r'D:\Work buddy project\每日盯盘\daily\20260824\05_复盘与明日预案.html')
