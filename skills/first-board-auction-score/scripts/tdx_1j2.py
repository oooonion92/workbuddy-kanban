# -*- coding: utf-8 -*-
"""1J2 取数清单：从冻结的收盘评分里导出**次日竞价待取代码**。

定位（2026-09-24 收敛）
----------------------
本脚本只保留 `plan` 一个动作：读昨晚冻结的 `eod_state.csv`，导出待取代码清单。
**打分的活已交给 `board_tracker.py`**（在滚动跟踪表上直接做竞价评价），
**竞价快照的生成交给 `board_tracker.py snapshot`**，故此处不再保留 `run`。

  # 收盘后或次日盘前
  python scripts/tdx_1j2.py plan --eod <eod输出目录> --out <输出目录>\\fetch

⛔ 默认**全量**（全部合格首板）。2026-09-24 起不再默认「前 40%」——
   09-23 实测 7 只「超预期」中 6 只排在前 20% 之外，取前段会系统性漏掉。
⛔ 全量取数**只取 HQInfo**（`hasHQInfo=1`，**不要**开 `hasCalcInfo`），
   否则 30+ 只的返回体积会拖慢 09:25 窗口。

语言模型的角色只有「机械转录」：把 `tdx_quotes` 返回的
`HQInfo.Open / Close / Amount / HQDate / HQTime` 写成 quotes.json，
**严禁挑选标的、排序、增删股票、改动数值或补充缺失值**。
"""
from pathlib import Path
import argparse
import csv
import json
import math
import re
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import score  # noqa: E402


def code6(v):
    s = str(v).strip().replace('="', '').replace('"', '')
    return re.sub(r'\.0$', '', s).zfill(6)


def setcode_of(c):
    return 1 if c.startswith('6') else 0


def cmd_plan(a):
    ed = Path(a.eod)
    st = score.table(ed / 'eod_state.csv')
    st['首板晋级分'] = score.numeric(st['首板晋级分'])
    st['首板排名'] = score.numeric(st['首板排名'])
    st['预期开盘%'] = score.numeric(st['预期开盘%'])
    n = len(st)
    full = a.all or a.top_pct >= 1.0
    cut = n if full else max(1, math.ceil(n * a.top_pct))
    top = st[st['首板排名'] <= cut]
    rows = []
    for _, r in top.sort_values('首板晋级分', ascending=False).iterrows():
        c = code6(r['代码'])
        eo = r['预期开盘%']
        rows.append({'代码': c, 'setcode': setcode_of(c), '名称': r['名称'],
                     '首板晋级分': round(float(r['首板晋级分']), 2),
                     '首板排名': int(r['首板排名']),
                     '预期开盘%': (round(float(eo), 2) if eo == eo and eo is not None else '')})
    out = Path(a.out) if a.out else ed / 'tdx_fetch'
    out.mkdir(parents=True, exist_ok=True)
    with (out / 'fetch_list.csv').open('w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=['代码', 'setcode', '名称', '首板晋级分', '首板排名', '预期开盘%'])
        w.writeheader(); w.writerows(rows)
    meta = json.loads((ed / 'manifest.json').read_text(encoding='utf-8'))
    (out / 'fetch_protocol.md').write_text(
        '# TDX 竞价取数协议（机械执行，不做判断）\n\n'
        f'- **目标竞价日**：{meta["evaluation_date"]}\n'
        f'- **取数范围**：{("全部合格首板（全量）" if full else f"前 {int(a.top_pct * 100)}%（首板排名 ≤ {cut}）")}'
        f'，共 **{len(rows)} 只**（合格首板总数 {n}）\n'
        '- **时间窗**：目标日 **09:25:00 之后**（正式开盘价已确定）；09:25 前的虚拟撮合价无效\n\n'
        '## 逐票动作（唯一允许的动作）\n\n'
        '```\n对 fetch_list.csv 每一行：\n'
        '  tdx_quotes(code=<代码>, setcode=<setcode>, hasHQInfo=1)\n'
        '  把 HQInfo 的 Open / Close / Amount / HQDate / HQTime 写入 quotes.json：\n'
        '    {"<代码>": {"open": .., "prev_close": .., "amount": .., "date": "..", "time": ".."}}\n'
        '```\n'
        '· 只取 HQInfo（**不要**开 hasCalcInfo，全量时能省大量返回体积）\n'
        '· 金额单位＝元；时间 HHMMSS\n\n'
        '## 下一步\n\n'
        '```\npython scripts/board_tracker.py snapshot --date <竞价日> --quotes quotes.json --out <快照.csv>\n'
        'python scripts/board_tracker.py auction  --track 连板跟踪_<T>.csv --date <竞价日> --snapshot <快照.csv> [--baseline <T>/竞价预期基准.json]\n```\n\n'
        '## ⛔ 禁止事项\n\n'
        '- 禁止挑选标的、排序、加减股票 — 名单已由冻结模型给出\n'
        '- 禁止改动 `open` / `prev_close` / `amount` / `time` 任何数值\n'
        '- 禁止用盘中现价替代开盘价；禁止用昨日竞价代替今日竞价\n'
        '- 取不到的行**留空**，脚本会列为「无价格」，**不得按不及预期处理**\n',
        encoding='utf-8')
    print(json.dumps({'fetch_list': str(out / 'fetch_list.csv'), 'protocol': str(out / 'fetch_protocol.md'),
                      'range': 'all' if full else f'top{int(a.top_pct * 100)}%', 'cut_rank': cut,
                      'rows': len(rows), 'codes': [r['代码'] for r in rows]}, ensure_ascii=False))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('plan', help='导出次日竞价待取代码清单（默认全量）')
    p.add_argument('--eod', required=True)
    p.add_argument('--out')
    p.add_argument('--top-pct', type=float, default=1.0,
                   help='取数范围占合格首板比例；默认 1.0＝全量。设 0.4 则只取前40%%')
    p.add_argument('--all', action='store_true', help='全量（等同 --top-pct 1.0）')
    p.set_defaults(fn=cmd_plan)
    a = ap.parse_args()
    try:
        return a.fn(a)
    except (ValueError, KeyError, FileNotFoundError) as exc:
        print('输入校验失败：' + str(exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
