# -*- coding: utf-8 -*-
"""30 分钟级别拆解：ZigZag 摆动 + 缠论中枢 + 30m MACD 背驰
用法: python mini_level.py <threshold_A> <threshold_BC>
"""
import json, os, sys

R = r'C:/Users/xc92/.workbuddy/projects/d-Work buddy project-每日盯盘/8035331c-3e13-4d80-b939-e8f0f9829851/tool-results'
FILES = [
    ('上证 000001', 'mcp-tdx-connector-tdx_kline-1789117154632-9763d5.txt', 0.010),
    ('创业板指 399006', 'mcp-tdx-connector-tdx_kline-1789117154544-4c2d00.txt', 0.022),
    ('科创50 000688', 'mcp-tdx-connector-tdx_kline-1789117154631-9e3e5c.txt', 0.022),
]


def load(fn):
    t = open(os.path.join(R, fn), encoding='utf-8').read()
    o, _ = json.JSONDecoder().raw_decode(t[t.index('{'):])
    bars = []
    for r in o['Rows']:
        bars.append({
            'd': r['Data'], 't': str(r.get('Time', '')),
            'o': float(r['Open']), 'h': float(r['High']),
            'l': float(r['Low']), 'c': float(r['Close']),
            'v': float(r.get('Volume', 0)),
        })
    if bars and bars[0]['d'] > bars[-1]['d']:
        bars.reverse()
    return bars


def zigzag(bars, thr):
    piv, trend = [], 0
    hi = lo = bars[0]['c']; hi_i = lo_i = 0
    for i, b in enumerate(bars):
        if trend == 1:
            if b['h'] >= hi: hi, hi_i = b['h'], i
            if b['l'] <= hi * (1 - thr):
                piv.append((hi_i, hi, 'H')); trend = -1; lo, lo_i = b['l'], i
        elif trend == -1:
            if b['l'] <= lo: lo, lo_i = b['l'], i
            if b['h'] >= lo * (1 + thr):
                piv.append((lo_i, lo, 'L')); trend = 1; hi, hi_i = b['h'], i
        else:
            if b['h'] > hi: hi, hi_i = b['h'], i
            if b['l'] < lo: lo, lo_i = b['l'], i
            if b['h'] >= lo * (1 + thr):
                piv.append((lo_i, lo, 'L')); trend = 1; hi, hi_i = b['h'], i
            elif b['l'] <= hi * (1 - thr):
                piv.append((hi_i, hi, 'H')); trend = -1; lo, lo_i = b['l'], i
    if trend == 1: piv.append((hi_i, hi, 'H'))
    elif trend == -1: piv.append((lo_i, lo, 'L'))
    return piv


def ema(v, n):
    k = 2.0 / (n + 1); out = [v[0]]
    for x in v[1:]: out.append(out[-1] + k * (x - out[-1]))
    return out


def macd(c):
    ef, es = ema(c, 12), ema(c, 26)
    dif = [a - b for a, b in zip(ef, es)]
    dea = ema(dif, 9)
    return dif, dea, [(a - b) * 2 for a, b in zip(dif, dea)]


def tslab(b):
    return '%s %s' % (b['d'][4:], b['t'][:4] if b['t'] else '')


for name, fn, thr in FILES:
    bars = load(fn)
    c = [b['c'] for b in bars]
    dif, dea, hist = macd(c)
    piv = zigzag(bars, thr)
    px = bars[-1]['c']

    print('=' * 78)
    print('%s   30min  bars=%d  %s ~ %s' % (name, len(bars), tslab(bars[0]), tslab(bars[-1])))
    print('  收盘 %s = %.2f   30m MACD: DIF %.3f / DEA %.3f / 柱 %.3f'
          % (tslab(bars[-1]), px, dif[-1], dea[-1], hist[-1]))
    seg = [p for p in piv if p[0] >= len(bars) - 8 * 22]   # 近 22 个交易日
    print('  --- 近 22 交易日 30m 摆动（阈值 %.1f%%）共 %d 个拐点 ---' % (thr * 100, len(seg)))
    prev = None
    for idx, price, typ in seg:
        if prev is not None:
            chg = (price / prev[1] - 1) * 100
            print('    %s %-12s %10.2f  (%+.2f%%)   ← 自 %s'
                  % (tslab(bars[idx]), '高' if typ == 'H' else '低', price, chg, tslab(bars[prev[0]])))
        else:
            print('    %s %-12s %10.2f  (起点)' % (tslab(bars[idx]), '高' if typ == 'H' else '低', price))
        prev = (idx, price, typ)

    # 缠论中枢：连续 3 笔重叠
    print('  --- 30m 中枢（连续 3 笔重叠区间）---')
    for k in range(len(seg) - 3):
        p = seg[k:k + 4]
        legs = [(p[j][1], p[j + 1][1]) for j in range(3)]
        zg = min(max(a, b) for a, b in legs)
        zd = max(min(a, b) for a, b in legs)
        if zg > zd:
            print('    中枢 %s~%s : ZD %.2f / ZG %.2f  (宽 %.2f%%)'
                  % (tslab(bars[p[0][0]]), tslab(bars[p[3][0]]), zd, zg, (zg / zd - 1) * 100))

    # 底背驰：最近两个低点 价格 vs DIF
    lows = [p for p in seg if p[2] == 'L']
    if len(lows) >= 2:
        a, b_ = lows[-2], lows[-1]
        print('  --- 30m 底背驰比对（最近两个摆动低点）---')
        print('    前低 %s %.2f  DIF %.3f   |   后低 %s %.2f  DIF %.3f'
              % (tslab(bars[a[0]]), a[1], dif[a[0]], tslab(bars[b_[0]]), b_[1], dif[b_[0]]))
        print('    价格 %s / DIF %s   →  %s'
              % ('创新低' if b_[1] < a[1] else '未创新低',
                 '抬高' if dif[b_[0]] > dif[a[0]] else '走低',
                 '构成底背驰 ✓' if (b_[1] < a[1] and dif[b_[0]] > dif[a[0]]) else '无底背驰 ✗'))
    print()
