# -*- coding: utf-8 -*-
"""30 分钟波浪计数：双阈值分层 —— 浪级骨架(tier1) + 次浪细节(tier2)
关键：小级别数浪必须先按浪级设 ZigZag 阈值，把次级别噪声归并掉，否则浪级混乱
"""
import json, os

R = r'C:/Users/xc92/.workbuddy/projects/d-Work buddy project-每日盯盘/8035331c-3e13-4d80-b939-e8f0f9829851/tool-results'
CASES = [
    ('上证 000001', 'mcp-tdx-connector-tdx_kline-1789117154632-9763d5.txt', 0.018, 0.010),
    ('创业板指 399006', 'mcp-tdx-connector-tdx_kline-1789117154544-4c2d00.txt', 0.040, 0.022),
    ('科创50 000688', 'mcp-tdx-connector-tdx_kline-1789117154631-9e3e5c.txt', 0.045, 0.022),
]


def load(fn):
    t = open(os.path.join(R, fn), encoding='utf-8').read()
    o, _ = json.JSONDecoder().raw_decode(t[t.index('{'):])
    bars = [{'d': r['Data'], 't': str(r.get('Time', '')), 'h': float(r['High']),
             'l': float(r['Low']), 'c': float(r['Close'])} for r in o['Rows']]
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


def ts(b):
    return '%s%s' % (b['d'][4:], b['t'][:4] if b['t'] else '')


for name, fn, t1, t2 in CASES:
    bars = load(fn)
    dif, dea, hist = macd([b['c'] for b in bars])
    print('=' * 82)
    print('%s   30min %s ~ %s   收盘 %.2f' % (name, ts(bars[0]), ts(bars[-1]), bars[-1]['c']))
    print('  30m MACD: DIF %.2f / DEA %.2f / 柱 %.2f' % (dif[-1], dea[-1], hist[-1]))

    for label, thr in (('【浪级骨架 tier1 %.1f%%】' % (t1*100), t1),
                       ('【次浪细节 tier2 %.1f%%】' % (t2*100), t2)):
        piv = zigzag(bars, thr)
        seg = [p for p in piv if p[0] >= len(bars) - 8 * 22]
        print('\n  %s  近22交易日 %d 个拐点' % (label, len(seg)))
        prev = None
        for idx, price, typ in seg:
            tag = '高' if typ == 'H' else '低'
            if prev is None:
                print('    %-10s %s %10.2f   起点' % (ts(bars[idx]), tag, price))
            else:
                chg = (price / prev[1] - 1) * 100
                arrow = '↘' if chg < 0 else '↗'
                print('    %-10s %s %10.2f   %s %+7.2f%% (%+8.2f)   DIF %7.2f'
                      % (ts(bars[idx]), tag, price, arrow, chg, price - prev[1], dif[idx]))
            prev = (idx, price, typ)
    print()
