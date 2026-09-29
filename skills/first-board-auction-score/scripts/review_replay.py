# -*- coding: utf-8 -*-
"""竞价回放复盘：把「前晚首板晋级分」与「次日实际开盘」的对照统计一次跑完。

用法
----
  python scripts/review_replay.py --eod <eod输出目录> --replay <replay输出目录> [--out <报告csv>]

前置：先用 details-replay 生成回放评价
  python scripts/score.py auction --eod <eod> --date <竞价日> \
         --input <details>\\全部Ａ股<竞价日>.xls --kind details-replay --replay --out <replay目录>

输出（stdout）：全样本秩相关 / 五分位 / 前20%对比 / 极端个体 / 超预期名单 / 旧门过滤效果 / 封板因子相关性
并写出逐票明细 CSV，供归档与跨日累积。

⛔ 本脚本只做统计，不改分、不改数、不做收益推断。结论中必须标注「历史回放」。
"""
from pathlib import Path
import argparse
import sys

import numpy as np
import pandas as pd


def spearman(x, y):
    return pd.Series(np.asarray(x, dtype=float)).rank().corr(pd.Series(np.asarray(y, dtype=float)).rank())


def pearson(x, y):
    x, y = np.asarray(x, dtype=float), np.asarray(y, dtype=float)
    if len(x) < 3:
        return float('nan')
    return float(np.corrcoef(x, y)[0, 1])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--eod', required=True, help='eod 输出目录（含 eod_state.csv）')
    ap.add_argument('--replay', required=True, help='details-replay 输出目录（含 竞价评价.csv）')
    ap.add_argument('--out', help='逐票明细 CSV 输出路径，默认写到 replay 目录')
    a = ap.parse_args()

    eod, rep = Path(a.eod), Path(a.replay)
    z = pd.read_csv(eod / 'eod_state.csv', dtype=str, encoding='utf-8-sig')
    r = pd.read_csv(rep / '竞价评价.csv', dtype=str, encoding='utf-8-sig')
    for c in ['首板晋级分', '首板排名', '预期开盘%', '炸板次数', '封板资金', '成交额', '流通市值', '连板数', '最新价']:
        if c in z:
            z[c] = pd.to_numeric(z[c], errors='coerce')
    for c in ['实际开盘%', '偏离百分点', '竞价超预期分', '竞价额占昨成交额%']:
        r[c] = pd.to_numeric(r[c], errors='coerce')

    d = z.merge(r[['代码', '实际开盘%', '偏离百分点', '竞价超预期分', '竞价评价', '量能标签', '竞价额占昨成交额%']],
                on='代码', how='inner', validate='one_to_one').sort_values('首板排名').reset_index(drop=True)
    n = len(d)
    if n == 0:
        print('输入校验失败：eod 与 replay 无交集', file=sys.stderr)
        return 2
    s, a_, o = d['首板晋级分'].values, d['实际开盘%'].values, d['偏离百分点'].values
    print(f'样本 n={n}   （历史回放，非 09:25 实时快照）')

    print('\n===== A. 全样本秩相关 =====')
    print(f'首板晋级分 ~ 实际开盘%     : Spearman={spearman(s, a_):+.3f}  Pearson={pearson(s, a_):+.3f}')
    print(f'首板晋级分 ~ 竞价超预期分  : Spearman={spearman(s, d["竞价超预期分"].values):+.3f}  '
          f'Pearson={pearson(s, d["竞价超预期分"].values):+.3f}')
    print('  ⚠️ 预期开盘% 与首板分出自同一模型 → 分数高则预期被抬高 → 超预期分对高分标的有系统性偏低。')

    print('\n===== B. 按首板分 5 等分：实际开盘% 中位数 =====')
    d['五分位'] = pd.qcut(d['首板晋级分'].rank(method='first'), 5, labels=['Q5最低', 'Q4', 'Q3', 'Q2', 'Q1最高'])
    print(d.groupby('五分位', observed=True).agg(
        只数=('代码', 'size'), 首板分中位=('首板晋级分', 'median'), 实际开盘中位=('实际开盘%', 'median'),
        偏离中位=('偏离百分点', 'median'), 超预期分中位=('竞价超预期分', 'median')).round(3).to_string())

    print('\n===== C. 前20% vs 其余 =====')
    top = d[d['每日前20%'].astype(str).str.lower().isin(['true', '1', '1.0'])]
    rest = d[~d['代码'].isin(top['代码'])]
    for tag, sub in [('前20%', top), ('其余80%', rest)]:
        print(f'{tag}: n={len(sub)} 实际开盘% 均值={sub["实际开盘%"].mean():+.3f} 中位={sub["实际开盘%"].median():+.3f} '
              f'| 偏离均值={sub["偏离百分点"].mean():+.3f} | 超预期分中位={sub["竞价超预期分"].median():.1f} '
              f'| 上涨开盘占比={(sub["实际开盘%"] > 0).mean() * 100:.1f}%')

    print('\n===== D. 评价分布 =====')
    print(d['竞价评价'].value_counts().to_string())
    cols = ['首板排名', '代码', '名称', '首板晋级分', '预期开盘%', '实际开盘%', '偏离百分点', '竞价超预期分', '竞价评价', '量能标签']
    print('\n-- 实际开盘% TOP5 --')
    print(d.nlargest(5, '实际开盘%')[cols].round(3).to_string(index=False))
    print('\n-- 实际开盘% BOTTOM5 --')
    print(d.nsmallest(5, '实际开盘%')[cols].round(3).to_string(index=False))
    sup = d[d['竞价评价'] == '超预期']
    if len(sup):
        inside = int(sup['每日前20%'].astype(str).str.lower().isin(['true', '1', '1.0']).sum())
        print(f'\n-- 超预期 {len(sup)} 只：其中 {inside} 只在前20%内、{len(sup) - inside} 只在前20%之外 --')
        print(sup[cols + ['竞价额占昨成交额%']].round(3).to_string(index=False))

    print('\n===== E. 各条门的过滤效果（过门 vs 不过门的实际开盘%） =====')
    gates = {'旧涨幅门 2%~8.5%': d['实际开盘%'].between(2.0, 8.5),
             '旧竞昨比门 3%~90%': d['竞价额占昨成交额%'].between(3.0, 90.0),
             '预期门 ≥-3.2959': d['偏离百分点'] >= -3.2959383007202194,
             '取数范围门 前40%': d['首板排名'] <= max(1, __import__('math').ceil(n * 0.40)),
             '取数范围门 前20%': d['首板排名'] <= max(1, __import__('math').ceil(n * 0.20))}
    gsum = []
    for name, mask in gates.items():
        gsum.append({'门': name, '过门只数': int(mask.sum()),
                     '过门实际开盘均值': round(d.loc[mask, '实际开盘%'].mean(), 3),
                     '不过门实际开盘均值': round(d.loc[~mask, '实际开盘%'].mean(), 3),
                     '过门开盘>0占比': f"{d.loc[mask, '实际开盘%'].gt(0).mean() * 100:.1f}%",
                     '不过门开盘>0占比': f"{d.loc[~mask, '实际开盘%'].gt(0).mean() * 100:.1f}%"})
    print(pd.DataFrame(gsum).to_string(index=False))
    print('被旧涨幅门拒绝者中 实际开盘%<2 且 >0（可能误杀的低吸）：',
          int(((d['实际开盘%'] < 2.0) & (d['实际开盘%'] > 0) & ~gates['旧涨幅门 2%~8.5%']).sum()), '只')
    print('被旧涨幅门拒绝者中 实际开盘%>8.5（一字/超高开，买不到，非误杀）：',
          int(((d['实际开盘%'] > 8.5) & ~gates['旧涨幅门 2%~8.5%']).sum()), '只')

    print('\n===== E2. 取数范围扫描（成本 vs 候选质量） =====')
    import math as _m
    rows2 = []
    for pct in [0.20, 0.30, 0.40, 0.50, 0.60, 1.00]:
        cut = max(1, _m.ceil(n * pct))
        sub = d[d['首板排名'] <= cut]
        keep = sub[sub['实际开盘%'].between(2.0, 8.5) & sub['竞价额占昨成交额%'].between(3.0, 90.0)
                   & (sub['偏离百分点'] >= -3.2959383007202194)]
        rows2.append({'取数范围': f'前{int(pct * 100)}%' if pct < 1 else '全部',
                      '取数只数': cut, '候选数': len(keep),
                      '其中超预期': int((keep['偏离百分点'] > 2.5239430446936617).sum()),
                      '候选实际开盘均值': round(keep['实际开盘%'].mean(), 3) if len(keep) else np.nan,
                      '候选首板分中位': round(keep['首板晋级分'].median(), 2) if len(keep) else np.nan})
    print(pd.DataFrame(rows2).to_string(index=False))
    print('\n===== F. 封板特征 vs 竞价结果（Spearman） =====')
    d['封单占成交'] = pd.to_numeric(d['封板资金'], errors='coerce') / pd.to_numeric(d['成交额'], errors='coerce')
    d['封单占流通'] = pd.to_numeric(d['封板资金'], errors='coerce') / pd.to_numeric(d['流通市值'], errors='coerce')
    for c in ['炸板次数', '封板资金', '成交额', '封单占成交', '封单占流通']:
        if c in d:
            cc = pd.to_numeric(d[c], errors='coerce').values
            print(f'{c:<8}: 实际开盘% {spearman(cc, a_):+.3f}   超预期分 {spearman(cc, d["竞价超预期分"].values):+.3f}')

    out = Path(a.out) if a.out else rep / '复盘明细.csv'
    keep = [c for c in ['首板排名', '代码', '名称', '首板晋级分', '每日前20%', '预期开盘%', '实际开盘%',
                        '偏离百分点', '竞价超预期分', '竞价评价', '量能标签', '所属行业'] if c in d]
    d[keep].to_csv(out, index=False, encoding='utf-8-sig')
    print(f'\n明细已写 {out}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
