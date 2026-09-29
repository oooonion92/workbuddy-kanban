# -*- coding: utf-8 -*-
"""连板滚动跟踪表 —— 首板 ＋ 晋级者（跟到断板）的单一日志。

设计（用户 2026-09-24 定稿）
---------------------------
每天**一张带日期的表** `连板跟踪_{T}.csv`，一张表活三步：

  ① 建档  T 日收盘     `init`  → 当日首板 ＋ 上一张表里「已晋级」的标的（身位+1）
  ② 竞价  T+1 09:25    `auction` → 填竞价块：实际开盘%/偏离/竞价评价（超/符合/不及）
  ③ 复盘  T+1 收盘     `eod`  → 填表现块：收盘涨幅/主力净额/是否晋级
                                同时本表「晋级」的行 → 成为 T+1 那张表的建档输入

循环：`init(09-24)` → `auction(09-25)` → `eod(09-25)` ＋ `init(09-25)` → …

⛔ 竞价三档的两套口径（**不可混用、表内有列标注**）
  · **首板** → 1J2 冻结模型（`assets/models.json`）：偏离 = 实际开盘% − 预期开盘%，
    与 `residual_low/high` 比较 → 不及 / 符合 / 超预期。
  · **2 板及以上** → 项目既有「质量分档基准」`daily/{T}/竞价预期基准.json`
    （`build_auction_baseline.py` 产出，逐票含 `不及_阈值`/`超_阈值`）。
    该文件缺失时**三档留空并标注「基准缺失」**，不拍阈值。

⛔ 产物一律 CSV，不产出 HTML。原始数据只读；不改模型、不改冻结快照。
"""
from pathlib import Path
import argparse
import csv
import io
import json
import math
import re
import sys

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import score  # noqa: E402

BUNDLE = json.loads(score.MODEL.read_text(encoding='utf-8'))

POOL_DIR = r'D:\OneDrive\Stock\短线数据采集'
DETAILS_DIR = r'D:\OneDrive\Stock\details'

COLS = [
    # ── ① 个股信息
    '建档日', '来源', '代码', '名称', '所属行业', '身位', '连板数',
    # ── ② 当日评分
    '首板建档日', '首板晋级分', '首板排名', '前20%', '建档日质量分',
    '预期开盘%', '常态下界%', '常态上界%', '价格上限约束',
    # ── ③ 竞价评价
    '竞价日', '竞价基准口径', '竞价基准值%', '不及阈值', '超阈值',
    '实际开盘%', '偏离百分点', '竞价评价', '竞价超预期分', '1J2门',
    '量能标签', '竞昨比%', '竞价金额万元',
    # ── ④ 收盘表现跟踪
    '表现日', '收盘涨幅%', '最高涨幅%', '最低涨幅%', '开盘至收盘变动(pp)',
    '是否晋级', '表现日连板数', '换手%', '成交额万元', '主力净额万元',
    # ── ⑤ 建档日封板细节（辅助，需要时右拉查看）
    '建档日收盘价', '建档日首封', '建档日炸板次数', '建档日封单强度%',
    '建档日换手%', '建档日主力占比%', '建档日成交额万元', '量能预期对数',
]


def code6(v):
    s = str(v).strip().replace('="', '').replace('"', '')
    return re.sub(r'\.0$', '', s).zfill(6)


def num(v):
    try:
        f = float(str(v).replace(',', ''))
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def level_of(lbc):
    lbc = int(lbc or 0)
    return '首板' if lbc <= 1 else ('2板' if lbc == 2 else ('3板' if lbc == 3 else '高位板'))


def blank_row():
    return {c: '' for c in COLS}


def load_track(p):
    """读已有跟踪表（保持列序）；不存在返回空 list"""
    p = Path(p)
    if not p.is_file():
        return []
    with p.open(encoding='utf-8-sig') as f:
        return [dict(r) for r in csv.DictReader(f)]


def save_track(rows, p):
    """写跟踪表。⚠️ 目标被 Excel/预览占用时（Windows 锁），降级另存为 <名>.new.csv 并显式告警。"""
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    body = []
    with io.StringIO(newline='') as buf:
        w = csv.DictWriter(buf, fieldnames=COLS, extrasaction='ignore')
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, '') for c in COLS})
        body = buf.getvalue()
    try:
        with p.open('w', encoding='utf-8-sig', newline='') as f:
            f.write(body)
        print(f'  → 写入 {p}  （{len(rows)} 行）')
        return p
    except PermissionError:
        alt = p.with_name(p.stem + '.new.csv')
        with alt.open('w', encoding='utf-8-sig', newline='') as f:
            f.write(body)
        print(f'  ⚠️ 目标被占用（Excel/预览未关闭）→ 已降级另存 {alt}（{len(rows)} 行）；'
              f'关闭占用后请把 .new.csv 改名覆盖目标，或重跑本命令', file=sys.stderr)
        return alt


def read_zt_pool(day):
    """涨停池 → {代码: {...}}（用于建档日的封板特征 与 晋级判定）"""
    p = Path(POOL_DIR) / day / 'zt_pool.csv'
    if not p.is_file():
        return {}
    out = {}
    with p.open(encoding='utf-8-sig') as f:
        for r in csv.DictReader(f):
            c = code6(r.get('代码') or '')
            if not re.fullmatch(r'\d{6}', c):
                continue
            out[c] = {
                'name': (r.get('名称') or '').strip(),
                'ind': (r.get('所属行业') or '').strip() or None,
                'lbc': int(num(r.get('连板数')) or 0),
                'close': num(r.get('最新价')),
                'first_time': (r.get('首次封板时间') or '').strip(),
                'opened': int(num(r.get('炸板次数')) or 0),
                'seal': num(r.get('封板资金')),
                'fcap': num(r.get('流通市值')),
                'amt': num(r.get('成交额')),
                'turn': num(r.get('换手率')),
            }
    return out


def read_details(day):
    """全A明细 → {代码: 指标}"""
    cand = sorted(Path(DETAILS_DIR).glob(f'全部*{day}*'))
    if not cand:
        return {}, None
    cand.sort(key=lambda p: (p.suffix.lower() != '.xlsx', p.name))
    d = score.table(cand[0])
    d['代码'] = d['代码'].astype(str).map(code6)
    out = {}
    for _, r in d.iterrows():
        out[r['代码']] = {k: num(r.get(k)) for k in
                          ['涨幅%', '最高', '最低', '总金额', '昨成交额', '主力净额', '换手Z', '开盘%', '今开', '细分行业']}
    return out, cand[0]


def read_auction_snapshot(p):
    """标准竞价快照 CSV → {代码: {...}}"""
    t = score.table(p)
    t['代码'] = t['代码'].astype(str).map(code6)
    out = {}
    for _, r in t.iterrows():
        out[r['代码']] = {'open': num(r.get('开盘价')), 'prev': num(r.get('参考昨收')),
                          'amt': num(r.get('竞价金额元')), 'date': str(r.get('竞价日期') or ''),
                          'time': str(r.get('快照时间') or '')}
    return out


def read_baseline(day):
    """竞价预期基准.json → {代码: {...}}（2板及以上用）"""
    p = Path(day)
    if not p.is_file():
        return {}
    try:
        j = json.loads(p.read_text(encoding='utf-8'))
    except Exception:                                       # noqa: BLE001
        return {}
    return {code6(s.get('code')): s for s in (j.get('stocks') or []) if s.get('code')}


def read_quality(day):
    """涨停质量.json → {代码: 质量分}（五维绝对尺度，**全市场通用，含 2 板及以上**）

    来源 `scripts/build_zt_quality.py`：封板时间25 + 牢固度25 + 大单流入20 + 板块共振20 + 量能结构10。
    ⛔ 与「首板晋级分」（只对首板、是晋级概率）**不是同一把尺子，禁比大小**。
    """
    p = Path(day)
    if not p.is_file():
        return {}
    try:
        j = json.loads(p.read_text(encoding='utf-8'))
    except Exception:                                       # noqa: BLE001
        return {}
    return {code6(s.get('code')): s.get('score') for s in (j.get('stocks') or []) if s.get('code')}


# ══════════════════════════════════════════════ ① init：建档
def cmd_init(a):
    T = score.date(a.date)
    eod = Path(a.eod)
    st = score.table(eod / 'eod_state.csv')
    st['代码'] = st['代码'].astype(str).map(code6)
    zt = read_zt_pool(T)
    det, _ = read_details(T)
    # eod = daily/{T}/first_board/{T}_eod  →  质量分在 daily/{T}/涨停质量.json
    qp = Path(a.quality) if a.quality else (eod.parent.parent / '涨停质量.json')
    qual = read_quality(qp)
    if not qual:
        print(f'  ⚠️ 未找到 {qp} → 「建档日质量分」留空（2 板及以上将没有分数）'
              f'；先跑 build_sector_full.py → build_zt_quality.py（严格串行）', file=sys.stderr)

    rows = []

    def fill_seal(row, c):
        z = zt.get(c) or {}
        d = det.get(c) or {}
        row['建档日首封'] = z.get('first_time') or ''
        row['建档日炸板次数'] = z.get('opened', '')
        ss = (z.get('seal') / z.get('fcap') * 100) if (z.get('seal') and z.get('fcap')) else None
        row['建档日封单强度%'] = round(ss, 3) if ss is not None else ''
        row['建档日换手%'] = round(z['turn'], 2) if z.get('turn') is not None else ''
        amt = z.get('amt')
        if amt is None:
            amt = (d.get('总金额') * 10000) if d.get('总金额') is not None else None
        row['建档日成交额万元'] = round(amt / 10000, 2) if amt else ''
        q = qual.get(c)
        row['建档日质量分'] = q if q is not None else ''
        if num(row.get('建档日收盘价')) is None:
            row['建档日收盘价'] = z.get('close') if z.get('close') is not None else ''
        if d.get('主力净额') is not None and d.get('总金额'):
            row['建档日主力占比%'] = round(d['主力净额'] / d['总金额'] * 100, 2)
        if not row.get('所属行业'):
            row['所属行业'] = z.get('ind') or d.get('细分行业') or ''

    # ── 当日首板（1J2 冻结模型）
    for _, r in st.iterrows():
        c = r['代码']
        row = blank_row()
        row.update({
            '建档日': T, '来源': '首板新建', '代码': c, '名称': (r.get('名称') or '').strip(),
            '所属行业': (r.get('所属行业') or '').strip(), '身位': '首板',
            '连板数': int(num(r.get('连板数')) or 1), '首板建档日': T,
            '首板晋级分': round(num(r.get('首板晋级分')), 2) if num(r.get('首板晋级分')) is not None else '',
            '首板排名': int(num(r.get('首板排名')) or 0),
            '前20%': '是' if str(r.get('每日前20%')).lower() in ('true', '1', '1.0') else '否',
            '预期开盘%': round(num(r.get('预期开盘%')), 2) if num(r.get('预期开盘%')) is not None else '',
            '常态下界%': round(num(r.get('常态下界%')), 2) if num(r.get('常态下界%')) is not None else '',
            '常态上界%': round(num(r.get('常态上界%')), 2) if num(r.get('常态上界%')) is not None else '',
            '价格上限约束': '是' if str(r.get('价格上限约束')).lower() in ('true', '1', '1.0') else '否',
            '建档日收盘价': num(r.get('最新价')) if num(r.get('最新价')) is not None else '',
            '量能预期对数': round(num(r.get('量能预期对数')), 4) if num(r.get('量能预期对数')) is not None else '',
        })
        fill_seal(row, c)
        rows.append(row)

    # ── 上一张表里已晋级的标的（身位+1）
    n_up = 0
    if a.prev:
        prev = load_track(a.prev)
        for r in prev:
            if str(r.get('是否晋级')) != '晋级':
                continue
            c = code6(r.get('代码'))
            lbc = int(num(r.get('表现日连板数')) or (num(r.get('连板数')) or 1) + 1)
            row = blank_row()
            row.update({
                '建档日': T, '来源': f"由{Path(a.prev).stem.replace('连板跟踪_', '')}晋级",
                '代码': c, '名称': r.get('名称') or (zt.get(c) or {}).get('name', ''),
                '所属行业': r.get('所属行业') or (zt.get(c) or {}).get('ind') or '',
                '身位': level_of(lbc), '连板数': lbc,
                '首板建档日': r.get('首板建档日') or r.get('建档日'),
                '首板晋级分': r.get('首板晋级分'), '首板排名': r.get('首板排名'), '前20%': r.get('前20%'),
                '预期开盘%': '', '常态下界%': '', '常态上界%': '', '价格上限约束': '',
            })
            fill_seal(row, c)
            rows.append(row)
            n_up += 1

    if not rows:
        raise ValueError(f'{T} 既无合格首板、也无晋级标的，空表不写')
    out = Path(a.out) if a.out else eod.parent / f'连板跟踪_{T}.csv'
    print(f'建档 {T}：首板 {len(rows) - n_up} 只 ＋ 晋级 {n_up} 只 = {len(rows)} 只')
    save_track(rows, out)
    return 0


# ══════════════════════════════════════════════ ② auction：竞价评价
def cmd_auction(a):
    snap = read_auction_snapshot(a.snapshot)
    if not snap:
        raise ValueError('竞价快照为空')
    base = read_baseline(a.baseline) if a.baseline else {}
    rows = load_track(a.track)
    if not rows:
        raise ValueError(f'跟踪表为空或不存在：{a.track}')

    lo_t, hi_t = BUNDLE['residual_low'], BUNDLE['residual_high']
    vlo, vhi = BUNDLE['volume_low'], BUNDLE['volume_high']
    ref = np.asarray(BUNDLE['residual_reference'], float)

    n_pend = n_noprice = n_noamt = 0
    for r in rows:
        c = code6(r['代码'])
        q = snap.get(c)
        if not q or q['open'] is None or q['prev'] is None or q['open'] <= 0 or q['prev'] <= 0:
            n_noprice += 1
            continue
        av = (q['open'] / q['prev'] - 1) * 100
        amt_wan = (q['amt'] / 10000) if q['amt'] is not None else None
        r['竞价日'] = a.date
        r['实际开盘%'] = round(av, 3)
        r['竞价金额万元'] = round(amt_wan, 2) if amt_wan is not None else ''

        # ── 竞昨比（只需建档日成交额）
        day_amt_wan = num(r.get('建档日成交额万元'))
        jjzb = (amt_wan / day_amt_wan * 100) if (amt_wan is not None and day_amt_wan) else None
        if jjzb is not None:
            r['竞昨比%'] = round(jjzb, 3)

        # ── 三档判定：首板=1J2 冻结模型；2板及以上=质量分档基准
        if str(r.get('身位')) == '首板':
            exp = num(r.get('预期开盘%'))
            if exp is None:
                r['竞价基准口径'] = '基准缺失'
                n_pend += 1
            else:
                dev = av - exp
                r['竞价基准口径'] = '1J2冻结模型'
                r['竞价基准值%'] = exp
                r['不及阈值'] = round(lo_t, 2)
                r['超阈值'] = round(hi_t, 2)
                r['偏离百分点'] = round(dev, 3)
                r['竞价超预期分'] = round(float(np.searchsorted(ref, dev, side='right')) / len(ref) * 100, 1)
                r['竞价评价'] = ('不及预期' if dev < lo_t else ('超预期' if dev > hi_t else '符合预期'))
                # 量能标签（1J2 模型口径：ln(1+竞昨比) − 量能预期对数）
                ve = num(r.get('量能预期对数'))
                if jjzb is None or ve is None:
                    r['量能标签'] = '量能待核验'
                    n_noamt += 1
                else:
                    vr = math.log1p(jjzb) - ve
                    r['量能标签'] = '缩量' if vr < vlo else ('放量' if vr > vhi else '正常量')
                # ── 1J2 实质四门（首板专属；取数范围门已默认关闭）
                reasons = []
                px_close = num(r.get('建档日收盘价'))
                lim = (math.floor(px_close * 1.1 * 100 + 0.5) / 100) if px_close else None
                if lim and q['open'] >= lim * 0.995:
                    reasons.append('非涨停竞价')
                if not (2.0 <= av <= 8.5):
                    reasons.append(f'旧涨幅门({av:+.2f}%)')
                if jjzb is None or not (3.0 <= jjzb <= 90.0):
                    reasons.append('旧竞昨比门')
                if dev < lo_t:
                    reasons.append('预期门')
                r['1J2门'] = '通过' if not reasons else '未通过：' + '；'.join(reasons)
        else:
            b = base.get(c)
            if not b or b.get('超_阈值') is None:
                r['竞价基准口径'] = '基准缺失'
                n_pend += 1
            else:
                r['竞价基准口径'] = '质量分档基准'
                r['竞价基准值%'] = b.get('预期_中位')
                r['不及阈值'] = b.get('不及_阈值')
                r['超阈值'] = b.get('超_阈值')
                mid = num(b.get('预期_中位'))
                if mid is not None:
                    r['偏离百分点'] = round(av - mid, 3)
                r['竞价评价'] = ('不及预期' if av < float(b['不及_阈值'])
                                 else ('超预期' if av > float(b['超_阈值']) else '符合预期'))
            r['量能标签'] = '（无基准）'

    save_track(rows, a.track)
    print(f'竞价评价：填价 {sum(1 for r in rows if r.get("实际开盘%") != "")} 行 · '
          f'无价格 {n_noprice} 行 · 基准缺失 {n_pend} 行 · 量能待核验 {n_noamt} 行')
    print('  ⚠️ 首板＝1J2 冻结模型口径；2 板及以上＝质量分档基准口径 —— 两套口径不可混用（表内有列标注）')
    return 0


# ══════════════════════════════════════════════ ③ eod：当日表现 + 晋级
def cmd_eod(a):
    T = score.date(a.date)
    det, src = read_details(T)
    zt = read_zt_pool(T)
    if not zt:
        print(f'⚠️ 缺 {T} 涨停池 → 「是否晋级」无法判定', file=sys.stderr)
    rows = load_track(a.track)
    if not rows:
        raise ValueError(f'跟踪表为空或不存在：{a.track}')
    print(f'表现日 {T}  明细源 {src.name if src else "（缺失）"}  涨停池 {len(zt)} 只')

    for r in rows:
        c = code6(r['代码'])
        d = det.get(c) or {}
        cl = d.get('涨幅%')
        hi, lo = d.get('最高'), d.get('最低')
        if cl is None:
            continue
        r['表现日'] = T
        r['收盘涨幅%'] = round(cl, 2)
        base = (hi / (1 + cl / 100)) if (hi is not None and (1 + cl / 100) != 0) else None
        if base:
            r['最高涨幅%'] = round((hi / base - 1) * 100, 2)
            r['最低涨幅%'] = round((lo / base - 1) * 100, 2) if lo is not None else ''
        r['换手%'] = round(d['换手Z'], 2) if d.get('换手Z') is not None else ''
        r['成交额万元'] = round(d['总金额'], 2) if d.get('总金额') is not None else ''
        r['主力净额万元'] = round(d['主力净额'], 2) if d.get('主力净额') is not None else ''
        av = num(r.get('实际开盘%'))
        if av is not None:
            r['开盘至收盘变动(pp)'] = round(cl - av, 2)
        z = zt.get(c)
        if zt:
            r['是否晋级'] = '晋级' if (z and z['lbc'] >= 2) else '未晋级'
            r['表现日连板数'] = z['lbc'] if z else ''

    save_track(rows, a.track)
    up = sum(1 for r in rows if r.get('是否晋级') == '晋级')
    got = sum(1 for r in rows if r.get('收盘涨幅%') != '')
    print(f'复盘完成：取到表现 {got} 行 · 晋级 {up} 行（晋级者将成为下一张表的建档输入）')
    return 0


# ══════════════════════════════════════════════ ⓿ snapshot：quotes.json → 标准竞价快照
def cmd_snapshot(a):
    """纯机械转录：quotes.json → 标准竞价快照 CSV（不做任何打分）。"""
    ed = score.date(a.date)
    raw = json.loads(Path(a.quotes).read_text(encoding='utf-8'))
    rows, skipped = [], []
    for k, v in raw.items():
        if not re.fullmatch(r'\d{1,6}', str(k).strip()):
            continue
        if not isinstance(v, dict):
            skipped.append(code6(k)); continue
        if 'HQInfo' in v and isinstance(v['HQInfo'], dict):
            h = v['HQInfo']
            o, pc, amt = h.get('Open'), h.get('Close'), h.get('Amount')
            dt, tm = str(h.get('HQDate') or ''), h.get('HQTime')
        else:
            o, pc, amt = v.get('open'), v.get('prev_close'), v.get('amount')
            dt, tm = str(v.get('date') or ''), v.get('time')
        c = code6(k)
        o, pc, amt = num(o), num(pc), num(amt)
        d = re.sub(r'[^0-9]', '', dt) or ed
        t = re.sub(r'[^0-9]', '', str(tm)).zfill(6)
        t = f'{t[:2]}:{t[2:4]}:{t[4:]}' if len(t) == 6 else ''
        if o is None or o <= 0 or pc is None or pc <= 0 or amt is None or t < '09:25:00':
            skipped.append(c); continue
        rows.append({'代码': c, '竞价日期': d, '快照时间': f'{d[:4]}-{d[4:6]}-{d[6:]} {t}',
                     '开盘价': o, '参考昨收': pc, '竞价金额元': amt})
    rows.sort(key=lambda r: r['代码'])
    if not rows:
        raise ValueError('quotes.json 无有效行（检查 09:25 后时间窗与字段名）')
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open('w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=['代码', '竞价日期', '快照时间', '开盘价', '参考昨收', '竞价金额元'])
        w.writeheader(); w.writerows(rows)
    print(f'  → 竞价快照 {out}（{len(rows)} 行）'
          + (f'  被拒 {sorted(set(skipped))}' if skipped else ''))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)

    s = sub.add_parser('snapshot', help='⓿ quotes.json → 标准竞价快照 CSV（纯机械转录）')
    s.add_argument('--date', required=True, help='竞价日')
    s.add_argument('--quotes', required=True)
    s.add_argument('--out', required=True)
    s.set_defaults(fn=cmd_snapshot)

    i = sub.add_parser('init', help='① 收盘建档：当日首板 ＋ 上一表晋级者')
    i.add_argument('--date', required=True)
    i.add_argument('--eod', required=True, help='当日 eod 目录（含 eod_state.csv）')
    i.add_argument('--prev', help='上一张跟踪表 连板跟踪_{T-1}.csv')
    i.add_argument('--quality', help='涨停质量.json（默认 daily/{T}/涨停质量.json，即 eod 目录的上两级）')
    i.add_argument('--out')
    i.set_defaults(fn=cmd_init)

    u = sub.add_parser('auction', help='② 次日竞价：填竞价块并判三档')
    u.add_argument('--track', required=True, help='连板跟踪_{T}.csv')
    u.add_argument('--date', required=True, help='竞价日')
    u.add_argument('--snapshot', required=True, help='标准竞价快照 CSV')
    u.add_argument('--baseline', help='竞价预期基准.json（2板及以上用）')
    u.set_defaults(fn=cmd_auction)

    e = sub.add_parser('eod', help='③ 当日收盘：填表现块并判晋级')
    e.add_argument('--track', required=True)
    e.add_argument('--date', required=True)
    e.set_defaults(fn=cmd_eod)

    a = ap.parse_args()
    try:
        return a.fn(a)
    except (ValueError, KeyError, FileNotFoundError) as exc:
        print('输入校验失败：' + str(exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
