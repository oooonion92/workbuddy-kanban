# -*- coding: utf-8 -*-
"""
风格对比总览页 —— 生成 风格对比.html（项目根目录，单页上下排列 4 个风格）
====================================================================
同一份样本数据（最近交易日 活跃组前 N 席位 × 各前 M 标的），4 个主题依次渲染，
每个主题用 CSS 变量作用域（.theme-xxx）切换，组件 CSS 共用一套。
用途：滚动一屏直接看全 4 个风格差异，挑一个落地。
用法：python scripts/build_style_compare.py
"""
import json, os, re, glob
from collections import defaultdict

if hasattr(__import__("sys"), "stdout"):
    import sys
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STORE_DIR = os.path.join(ROOT, "daily", "lhb_seats")
OUT = os.path.join(ROOT, "风格对比.html")

SEATS = 3      # 抽几个席位
STOCKS = 4     # 每席位抽几个标的


# ============ 数据 ============
def load_latest():
    fs = sorted(glob.glob(os.path.join(STORE_DIR, "*.json")))
    if not fs:
        return None
    with open(fs[-1], encoding="utf-8") as fp:
        return json.load(fp)


def make_rows(latest, min_stocks=3):
    """抽样：优先挑票多的席位（样本才够饱满），每席取 STOCKS 只票。
    策略：先筛出「不重复标的数 ≥ min_stocks」的席位，按席位净额绝对值排序取前 SEATS 个；
    若这类席位不足，再放宽到全部席位兜底。"""
    def uniq_stocks(info):
        return {o.get("name", "") for o in info.get("ops", [])}

    def seat_abs(item):
        return abs(sum(o.get("net", 0) for o in item[1].get("ops", [])))

    all_items = list(latest.get("daily", {}).items())
    rich = [it for it in all_items if len(uniq_stocks(it[1])) >= min_stocks]
    pool = rich if len(rich) >= SEATS else all_items
    pool.sort(key=seat_abs, reverse=True)

    rows = []
    picked = []
    for pid, info in pool:
        if len(picked) >= SEATS:
            break
        sub = defaultdict(lambda: {"buy": 0, "sell": 0, "net": 0, "code": ""})
        for o in info.get("ops", []):
            k = o.get("name", "")
            sub[k]["buy"] += o.get("buy", 0)
            sub[k]["sell"] += o.get("sell", 0)
            sub[k]["net"] += o.get("net", 0)
            sub[k]["code"] = o.get("code", "")
        if not sub:
            continue
        picked.append((pid, len(sub)))
        for stock, v in sorted(sub.items(), key=lambda kv: -abs(kv[1]["net"]))[:STOCKS]:
            rows.append({"player": pid, "stock": stock, "code": v["code"],
                         "net": v["net"], "buy": v["buy"], "sell": v["sell"]})
    return rows, picked


def fmt(v):
    if not v:
        return "0"
    return f"{v/10000:.2f}亿" if abs(v) >= 10000 else f"{v:.0f}万"


# ============ 主题变量（每个主题只定义变量，组件 CSS 共用） ============
THEMES = [
    {
        "id": "carbon", "no": "A", "name": "IBM Carbon v5",
        "tag": "当前版本 · 白底灰阶 · 数据密度派",
        "philo": "圆角 0（标签 pill 例外）· 字重封顶 600 · 8px 网格 · 单色 Blue 60 强调 · "
                 "背景灰阶分层代替阴影 · 默认行高 36px / 紧凑档 28px · 强度条+密度切换",
        "vars": """
  --bg:#f4f4f4; --surface:#ffffff; --surface-2:#f4f4f4; --surface-3:#e0e0e0;
  --ink:#161616; --g70:#525252; --g60:#6f6f6f; --g50:#8d8d8d; --g30:#c6c6c6; --g20:#e0e0e0;
  --accent:#0f62fe; --accent-soft:#edf5ff; --g-active:#0f62fe;
  --up:#a2191f; --down:#0e6027; --up-bg:rgba(218,30,40,.15); --down-bg:rgba(36,161,72,.15);
  --row-h:36px; --pad:20px; --pad-cell:8px; --radius:0px; --radius-card:0px;
  --fs-body:13px; --fs-cell:12px; --fs-sub:12px; --fs-kpi:24px; --fs-title:24px;
  --fw-title:400; --fw-cell:600; --ls-body:.16px; --ls-sub:.32px; --lh-body:1.5;
  --shadow:0 1px 2px rgba(0,0,0,.06);
  --bar-h:8px;
""",
    },
    {
        "id": "linear", "no": "B", "name": "Linear.app",
        "tag": "备选 · 暗色紫蓝 · 工程密度派",
        "philo": "暗底 #08090a · 紫蓝 #5e6ad2 单一强调 · 12px 等宽数字 · 行高 28px 极致紧凑 · "
                 "圆角 6-8px · 暗室盯盘不刺眼 · 密度感比 Carbon 再高一档",
        "vars": """
  --bg:#08090a; --surface:#0f1011; --surface-2:#161719; --surface-3:#1c1e21;
  --ink:#f7f8f8; --g70:#b4b8bf; --g60:#8a8f98; --g50:#62666d; --g30:#3d3f44; --g20:#2a2c30;
  --accent:#5e6ad2; --accent-soft:rgba(94,106,210,.14); --g-active:#5e6ad2;
  --up:#ff6b6b; --down:#5cd6a0; --up-bg:rgba(255,107,107,.18); --down-bg:rgba(92,214,160,.18);
  --row-h:28px; --pad:20px; --pad-cell:8px; --radius:6px; --radius-card:8px;
  --fs-body:13px; --fs-cell:12px; --fs-sub:12px; --fs-kpi:18px; --fs-title:22px;
  --fw-title:600; --fw-cell:500; --ls-body:-.01em; --ls-sub:.02em; --lh-body:1.5;
  --shadow:0 0 0 1px var(--g20);
  --bar-h:8px;
""",
    },
    {
        "id": "coinbase", "no": "C", "name": "Coinbase",
        "tag": "备选 · 蓝白高对比 · 金融权威派",
        "philo": "蓝 #0052ff 主色 · 大字号 KPI · 12px 圆角卡片 · 扁平化 · 专业金融机构感 · "
                 "行高 40px 偏宽松 · ⚠️ 原版是美式绿涨红跌，落地需做色交换",
        "vars": """
  --bg:#ffffff; --surface:#f7f9fc; --surface-2:#eef2f7; --surface-3:#e6eaf0;
  --ink:#050f19; --g70:#5b616e; --g60:#8a94a6; --g50:#a8b1bf; --g30:#d8dde3; --g20:#e6eaf0;
  --accent:#0052ff; --accent-soft:#e5edff; --g-active:#0052ff;
  --up:#cf202f; --down:#02b196; --up-bg:rgba(207,32,47,.10); --down-bg:rgba(2,177,150,.10);
  --row-h:40px; --pad:24px; --pad-cell:12px; --radius:8px; --radius-card:12px;
  --fs-body:14px; --fs-cell:13px; --fs-sub:13px; --fs-kpi:22px; --fs-title:28px;
  --fw-title:700; --fw-cell:600; --ls-body:0; --ls-sub:.02em; --lh-body:1.5;
  --shadow:0 1px 3px rgba(5,15,25,.08);
  --bar-h:8px;
""",
    },
    {
        "id": "notion", "no": "D", "name": "Notion",
        "tag": "备选 · 白底清雅 · 文档阅读派",
        "philo": "暖白 #fafaf9 · Inter 字体 · 字重封顶 500（高级感来源）· 圆角 3-4px · "
                 "极细分隔线 · 呼吸感最强 · 打印/截图友好 · 但强度条偏弱需重做",
        "vars": """
  --bg:#fafaf9; --surface:#ffffff; --surface-2:#f7f7f5; --surface-3:#f1f1ef;
  --ink:#37352f; --g70:#787774; --g60:#9b9a97; --g50:#b4b2ad; --g30:#dfddd8; --g20:#ebebea;
  --accent:#2eaadc; --accent-soft:rgba(46,170,220,.12); --g-active:#2eaadc;
  --up:#d44c47; --down:#4d9375; --up-bg:rgba(212,76,71,.10); --down-bg:rgba(77,147,117,.10);
  --row-h:32px; --pad:24px; --pad-cell:12px; --radius:4px; --radius-card:4px;
  --fs-body:14px; --fs-cell:13px; --fs-sub:13px; --fs-kpi:20px; --fs-title:30px;
  --fw-title:700; --fw-cell:500; --ls-body:-.003em; --ls-sub:.02em; --lh-body:1.6;
  --shadow:0 0 0 1px var(--g20);
  --bar-h:8px;
""",
    },
]

# ============ 共用组件 CSS（全部走 CSS 变量） ============
COMPONENT_CSS = """
*{margin:0;padding:0;box-sizing:border-box}
body{background:#e8e8e8;color:#161616;font-family:-apple-system,BlinkMacSystemFont,"Inter","Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;font-size:14px;line-height:1.5}
.toc{position:sticky;top:0;z-index:200;background:#161616;color:#fff;padding:0 24px;display:flex;align-items:center;gap:4px;height:48px;box-shadow:0 2px 6px rgba(0,0,0,.3);overflow-x:auto}
.toc .t{font-size:13px;font-weight:600;margin-right:16px;white-space:nowrap;opacity:.6}
.toc a{color:#c6c6c6;text-decoration:none;font-size:13px;padding:0 14px;height:48px;display:flex;align-items:center;white-space:nowrap;border-bottom:2px solid transparent}
.toc a:hover{color:#fff}
.hero{max-width:1400px;margin:0 auto;padding:28px 24px 8px}
.hero h1{font-size:22px;font-weight:600;line-height:1.3}
.hero .d{font-size:13px;color:#525252;margin-top:6px;line-height:1.6}
.hero .d b{color:#161616}

/* ---- 主题块：变量作用域 ---- */
.theme{background:var(--bg);color:var(--ink);font-family:-apple-system,BlinkMacSystemFont,"Inter","Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;
  font-size:var(--fs-body);line-height:var(--lh-body);letter-spacing:var(--ls-body);margin-bottom:28px;padding:32px 0 40px}
.theme .inner{max-width:1400px;margin:0 auto;padding:0 24px}
.theme .hd{display:flex;align-items:baseline;gap:12px;margin-bottom:6px;flex-wrap:wrap}
.theme .no{width:26px;height:26px;background:var(--accent);color:#fff;border-radius:var(--radius);
  display:flex;align-items:center;justify-content:center;font-size:13px;font-weight:600;flex:none}
.theme h2{font-size:var(--fs-title);font-weight:var(--fw-title);line-height:1.25;color:var(--ink)}
.theme .tagline{font-size:var(--fs-sub);color:var(--accent);font-weight:600;letter-spacing:var(--ls-sub)}
.theme .philo{font-size:var(--fs-sub);color:var(--g70);line-height:1.6;margin:8px 0 18px;max-width:1000px;letter-spacing:var(--ls-sub)}

.theme .card{background:var(--surface);border-radius:var(--radius-card);padding:var(--pad);margin-bottom:14px;box-shadow:var(--shadow)}
.theme .card h3{font-size:13px;font-weight:600;color:var(--g70);margin-bottom:10px;letter-spacing:var(--ls-sub)}
.theme .kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}
.theme .kpi{background:var(--surface-2);border-radius:var(--radius);padding:14px 16px}
.theme .kpi .k{font-size:11px;color:var(--g70);font-weight:500;letter-spacing:var(--ls-sub)}
.theme .kpi .v{font-size:var(--fs-kpi);font-weight:600;font-family:ui-monospace,"SF Mono",Consolas,monospace;
  margin-top:4px;font-variant-numeric:tabular-nums;color:var(--ink);line-height:1.2}

.theme table{width:100%;border-collapse:separate;border-spacing:0;font-size:var(--fs-cell);background:transparent;margin-top:6px}
.theme th{background:var(--surface-3);color:var(--g70);font-weight:600;padding:8px var(--pad-cell);
  font-size:11px;letter-spacing:var(--ls-sub);text-align:right;border-bottom:1px solid var(--g30);white-space:nowrap}
.theme th.c1,.theme th.c2{text-align:left}
.theme td{height:var(--row-h);padding:2px var(--pad-cell);vertical-align:middle;
  border-bottom:1px solid var(--g20);color:var(--ink);font-size:var(--fs-cell)}
.theme td.c1{font-weight:600;color:var(--ink);border-left:3px solid var(--g-active);background:var(--surface-2);white-space:nowrap}
.theme td.c2{color:var(--ink);white-space:nowrap}
.theme td.c2 .code{font-family:ui-monospace,Consolas,monospace;font-size:10px;color:var(--g60);margin-left:6px}
.theme td.cell{text-align:right;background-image:linear-gradient(to right,var(--bar,transparent) 0,var(--bar,transparent) var(--w,0%),transparent var(--w,0%));
  background-repeat:no-repeat;font-variant-numeric:tabular-nums}
.theme .cell .net{display:block;font-weight:var(--fw-cell);line-height:1.3;font-family:ui-monospace,"SF Mono",Consolas,monospace}
.theme .cell .net.up{color:var(--up)}
.theme .cell .net.down{color:var(--down)}
.theme .cell .bs{display:block;font-size:10px;color:var(--g60);line-height:1.3;font-family:ui-monospace,Consolas,monospace}
.theme .legend{font-size:11px;color:var(--g70);margin-top:10px;display:flex;align-items:center;gap:8px;letter-spacing:var(--ls-sub)}
.theme .legend .sw{width:48px;height:var(--bar-h);background:var(--up-bg);border-radius:2px}

.theme .traits{display:flex;gap:8px;flex-wrap:wrap;margin-top:12px}
.theme .traits span{font-size:11px;padding:3px 10px;background:var(--accent-soft);color:var(--accent);
  border-radius:12px;font-weight:600;letter-spacing:var(--ls-sub)}

@media(max-width:900px){
  .toc a{padding:0 10px;font-size:12px}
  .theme .inner{padding:0 16px}
  .hero{padding:20px 16px 8px}
}
"""

TRAITS = {
    "carbon": ["圆角 0", "字重封顶 600", "8px 网格", "单色 Blue 60", "36/28px 双密度", "灰阶分层"],
    "linear": ["暗底 #08090a", "紫蓝 #5e6ad2", "28px 极紧", "圆角 6-8px", "等宽数字", "工程密度感"],
    "coinbase": ["蓝 #0052ff", "大字号 KPI", "12px 圆角", "40px 宽松", "扁平化", "⚠️红绿需反色"],
    "notion": ["暖白 #fafaf9", "字重封顶 500", "圆角 3-4px", "32px 中密", "极细分隔线", "⚠️强度条偏弱"],
}


def render_theme(t, rows, latest_date, maxabs):
    body = ['<table><thead><tr><th class="c1">席位 / 选手</th><th class="c2">标的</th>'
            f'<th>净额 · {latest_date}</th><th>买卖分布</th></tr></thead><tbody>']
    for r in rows:
        net = r["net"]
        arrow = "↑" if net >= 0 else "↓"
        w = min(100.0, abs(net) / maxabs * 100)
        bar = "var(--up-bg)" if net >= 0 else "var(--down-bg)"
        body.append(
            f'<tr><td class="c1">{r["player"]}</td>'
            f'<td class="c2">{r["stock"]}<span class="code">{r["code"]}</span></td>'
            f'<td class="cell" style="--bar:{bar};--w:{w:.1f}%">'
            f'<span class="net {"up" if net>=0 else "down"}">{arrow}{fmt(net)}</span>'
            f'<span class="bs">买{fmt(r["buy"])}·卖{fmt(r["sell"])}</span></td>'
            f'<td class="cell" style="--bar:{bar};--w:{min(100.0, abs(r["buy"])/maxabs*100):.1f}%">'
            f'<span class="net up">买 {fmt(r["buy"])}</span>'
            f'<span class="bs">占样本强度比</span></td></tr>')
    body.append('</tbody></table>')

    traits = "".join(f"<span>{x}</span>" for x in TRAITS[t["id"]])
    return f"""
<section class="theme theme-{t['id']}" id="{t['id']}" style="scroll-margin-top:56px">
  <div class="inner">
    <div class="hd">
      <span class="no">{t['no']}</span>
      <h2>{t['name']}</h2>
      <span class="tagline">{t['tag']}</span>
    </div>
    <div class="philo">{t['philo']}</div>
    <div class="traits">{traits}</div>

    <div class="card" style="margin-top:18px">
      <h3>概览 KPI</h3>
      <div class="kpis">
        <div class="kpi"><div class="k">样本日期</div><div class="v">{latest_date}</div></div>
        <div class="kpi"><div class="k">席位（抽样）</div><div class="v">{SEATS}</div></div>
        <div class="kpi"><div class="k">标的（抽样）</div><div class="v">{SEATS*STOCKS}</div></div>
        <div class="kpi"><div class="k">样本最大净额</div><div class="v">{fmt(maxabs)}</div></div>
      </div>
    </div>

    <div class="card">
      <h3>矩阵样本（同数据 · 四风格对照）</h3>
      {"".join(body)}
      <div class="legend"><span class="sw"></span>单元格底色条 = 净额强度（按样本最大值归一化），颜色 + 箭头 + 长度三重编码</div>
    </div>
  </div>
</section>"""


def main():
    latest = load_latest()
    if not latest:
        print("无数据：请先运行 scripts/lhb_seat_track.py")
        return
    rows, picked = make_rows(latest)
    if not rows:
        print("样本为空")
        return
    seat_desc = "、".join(f"{p[0]}({p[1]}票)" for p in picked)
    latest_date = latest["date"]
    maxabs = max(abs(r["net"]) for r in rows) or 1

    vars_css = "\n".join(
        f".theme-{t['id']}{{ {t['vars']} }}" for t in THEMES)
    bodies = "\n".join(render_theme(t, rows, latest_date, maxabs) for t in THEMES)
    toc = "".join(
        f'<a href="#{t["id"]}">{t["no"]} · {t["name"]}</a>' for t in THEMES)

    html = f"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>龙虎榜看板 · 4 风格对比</title>
<style>{COMPONENT_CSS}
{vars_css}
</style>
</head>
<body>

<nav class="toc"><span class="t">风格对比</span>{toc}</nav>

<div class="hero">
  <h1>龙虎榜看板 · 4 个设计风格对比</h1>
  <div class="d">下方 4 个区块展示<b>完全相同的样本数据</b>，只有设计语言不同。滚动即可逐项对比：
  <b>背景氛围 / 数字重量 / 行高密度 / 强度条可见性 / 圆角与分隔</b>。</div>
  <div class="d" style="margin-top:4px">样本：{latest_date} · 席位 {seat_desc} · 共 {len(rows)} 行 · 样本最大净额 {fmt(maxabs)}</div>
</div>

{bodies}

</body>
</html>"""
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"已生成: {OUT}")
    print(f"  样本 {latest_date}：{SEATS} 席位 × {STOCKS} 票 = {len(rows)} 行")
    print(f"  文件大小 {os.path.getsize(OUT)/1024:.1f} KB")


if __name__ == "__main__":
    main()
