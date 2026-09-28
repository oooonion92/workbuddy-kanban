# -*- coding: utf-8 -*-
"""
四个版本并排预览：Carbon(当前基准) + 三个备选风格
仅取一个交易日样本（最近日 8-31）的前 2 个席位 × 3 个标的，写到 .workbuddy/tmp/style_preview/
用途：用户从视觉直接挑风格，不要文字描述
"""
import json, os, re, glob
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STORE_DIR = os.path.join(ROOT, "daily", "lhb_seats")
OUT_DIR = os.path.join(ROOT, ".workbuddy", "tmp", "style_preview")
SAMPLES = 2  # 席位数
STOCKS_PER = 3  # 每个席位取几个标的

os.makedirs(OUT_DIR, exist_ok=True)


def load_latest():
    fs = sorted(glob.glob(os.path.join(STORE_DIR, "*.json")))
    if not fs:
        return None
    with open(fs[-1], encoding="utf-8") as fp:
        data = json.load(fp)
    return data


def short_dept(name):
    s = name
    s = s.replace("股份有限公司", "").replace("有限责任公司", "").replace("有限公司", "")
    s = re.sub(r"(证券营业部|营业部|证券分公司|分公司)$", "", s)
    return s.strip()


def make_rows(latest):
    """抽样：取 daily 组前 SAMPLES 个席位，每个席位前 STOCKS_PER 个标的"""
    rows = []
    latest_date = latest["date"]
    g_order = {"daily": 0, "node": 1, "watch": 2}
    daily_items = sorted(latest.get("daily", {}).items(),
                         key=lambda x: -sum(o.get("net", 0) for o in x[1].get("ops", [])))
    seen_seat = 0
    for pid, info in daily_items:
        if seen_seat >= SAMPLES:
            break
        seen_seat += 1
        ops = info.get("ops", [])
        # 聚合 (player, stock) 取前日数据
        sub = defaultdict(lambda: {"buy": 0, "sell": 0, "net": 0, "code": ""})
        for o in ops:
            k = o.get("name", "")
            sub[k]["buy"] += o.get("buy", 0)
            sub[k]["sell"] += o.get("sell", 0)
            sub[k]["net"] += o.get("net", 0)
            sub[k]["code"] = o.get("code", "")
        # 取绝对值最大的几个
        ranked = sorted(sub.items(), key=lambda kv: -abs(kv[1]["net"]))[:STOCKS_PER]
        for stock, v in ranked:
            rows.append({
                "player": pid, "group": "daily",
                "stock": stock, "code": v["code"],
                "net": v["net"], "buy": v["buy"], "sell": v["sell"],
            })
    return rows, latest_date


# ============ 四个版本的 CSS ============

CARBON_CSS = """
:root{
  --bg:#f4f4f4; --surface:#fff; --ink:#161616; --gray-70:#525252; --gray-60:#6f6f6f; --gray-50:#8d8d8d;
  --gray-30:#c6c6c6; --gray-20:#e0e0e0; --gray-10:#f4f4f4;
  --blue-60:#0f62fe;
  --up:#a2191f; --down:#0e6027;
  --up-bg:rgba(218,30,40,.15); --down-bg:rgba(36,161,72,.15);
  --g-active:#0f62fe;
  --row-h:36px;
}
*{margin:0;padding:0;box-sizing:border-box}
body{background:var(--bg);color:var(--ink);font-family:"IBM Plex Sans",-apple-system,"Segoe UI","PingFang SC",sans-serif;font-size:13px;letter-spacing:.16px;line-height:1.5}
.wrap{max-width:1280px;margin:0 auto;padding:24px 32px 80px}
header.page{padding:24px 0 8px}
.tag{display:inline-block;font-size:11px;padding:2px 8px;background:var(--g-active);color:#fff;font-weight:600;margin-bottom:12px;letter-spacing:.32px}
h1{font-size:24px;font-weight:400;line-height:1.33;display:flex;align-items:center;gap:12px;color:var(--ink)}
h1::before{content:"";width:4px;height:24px;background:var(--blue-60)}
.sub{color:var(--gray-70);font-size:12px;letter-spacing:.32px;line-height:1.5;margin-top:8px}
.card{background:var(--surface);padding:20px;margin-bottom:16px}
.kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:1px;background:var(--surface)}
.kpi{background:var(--gray-10);padding:16px}
.kpi .k{font-size:12px;color:var(--gray-70)}
.kpi .v{font-size:24px;font-weight:600;font-family:"IBM Plex Mono",ui-monospace,Consolas;margin-top:4px;font-variant-numeric:tabular-nums;line-height:1.2}
table{width:100%;border-collapse:separate;border-spacing:0;font-size:12px;background:var(--surface);margin-top:16px}
th{background:var(--gray-20);color:var(--ink);font-weight:600;padding:8px;font-size:12px;letter-spacing:.32px;text-align:left;border-bottom:1px solid var(--gray-50);text-align:right}
th.c1,th.c2{text-align:left}
td{height:var(--row-h);padding:2px 8px;vertical-align:middle;border-bottom:1px solid var(--gray-20);font-size:12px}
tr:nth-child(even) td,tr:nth-child(even) th.c1{background:var(--gray-10)}
th.c1{border-left:3px solid var(--g-active);font-weight:600;min-width:96px}
td.c2{min-width:144px}
td.cell{text-align:right;background-image:linear-gradient(to right,var(--bar,transparent) 0,var(--bar,transparent) var(--w,0%),transparent var(--w,0%));background-repeat:no-repeat}
.cell .net{display:block;font-family:"IBM Plex Mono",ui-monospace;font-variant-numeric:tabular-nums;font-weight:600;line-height:1.3}
.cell .net.up{color:var(--up)}
.cell .net.down{color:var(--down)}
.cell .bs{display:block;font-family:"IBM Plex Mono",ui-monospace;font-size:10px;color:var(--gray-60);line-height:1.3}
.legend{font-size:12px;color:var(--gray-70);margin-top:8px;display:flex;align-items:center;gap:8px;letter-spacing:.32px}
.legend .sw{width:48px;height:8px;background:linear-gradient(to right,var(--up-bg) 0,var(--up-bg) 100%);border:1px solid var(--gray-20)}
"""

LINEAR_CSS = """
:root{
  --bg:#08090a; --surface:#0f1011; --surface-2:#161719; --ink:#f7f8f8; --gray-70:#b4b8bf; --gray-60:#8a8f98;
  --gray-50:#62666d; --gray-30:#3d3f44; --gray-20:#2a2c30;
  --accent:#5e6ad2; --accent-2:#7e85f5;
  --up:#ff6b6b; --up-bg:rgba(255,107,107,.18); --down:#5cd6a0; --down-bg:rgba(92,214,160,.18);
  --g-active:#5e6ad2;
  --row-h:28px;
}
*{margin:0;padding:0;box-sizing:border-box}
body{background:var(--bg);color:var(--ink);font-family:-apple-system,BlinkMacSystemFont,"Inter","PingFang SC","Microsoft YaHei",sans-serif;font-size:13px;letter-spacing:-.01em;line-height:1.5}
.wrap{max-width:1280px;margin:0 auto;padding:24px 32px 80px}
header.page{padding:24px 0 8px}
.tag{display:inline-block;font-size:11px;padding:2px 8px;background:var(--accent);color:#fff;font-weight:500;margin-bottom:12px;border-radius:4px;letter-spacing:.02em}
h1{font-size:22px;font-weight:600;line-height:1.3;display:flex;align-items:center;gap:12px}
.sub{color:var(--gray-70);font-size:12px;line-height:1.5;margin-top:8px}
.card{background:var(--surface);border:1px solid var(--gray-20);border-radius:8px;padding:18px 20px;margin-bottom:12px}
.kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:8px}
.kpi{background:var(--surface-2);border:1px solid var(--gray-20);border-radius:6px;padding:12px 14px}
.kpi .k{font-size:11px;color:var(--gray-60);font-weight:500}
.kpi .v{font-size:18px;font-weight:600;font-family:ui-monospace,"SF Mono",Consolas,monospace;margin-top:2px;font-variant-numeric:tabular-nums;color:var(--ink)}
table{width:100%;border-collapse:separate;border-spacing:0;font-size:12px;background:transparent;margin-top:16px}
thead th{background:var(--surface-2);color:var(--gray-70);font-weight:500;padding:8px;font-size:11px;text-align:right;border-bottom:1px solid var(--gray-20);letter-spacing:.02em}
thead th.c1,thead th.c2{text-align:left}
td{height:var(--row-h);padding:0 8px;vertical-align:middle;border-bottom:1px solid var(--gray-20);color:var(--ink);font-size:12px}
td.c1{font-weight:500;color:var(--ink);border-left:2px solid var(--g-active);background:var(--surface)}
td.c2{color:var(--ink);font-weight:400}
td.cell{text-align:right;background-image:linear-gradient(to right,var(--bar,transparent) 0,var(--bar,transparent) var(--w,0%),transparent var(--w,0%));background-repeat:no-repeat;font-family:ui-monospace,"SF Mono",Consolas,monospace;font-variant-numeric:tabular-nums}
.cell .net{display:block;font-weight:500;line-height:1.3}
.cell .net.up{color:var(--up)}
.cell .net.down{color:var(--down)}
.cell .bs{display:block;font-size:10px;color:var(--gray-60);line-height:1.3}
.legend{font-size:11px;color:var(--gray-60);margin-top:10px;display:flex;align-items:center;gap:8px}
.legend .sw{width:48px;height:8px;background:linear-gradient(to right,var(--up-bg) 0,var(--up-bg) 100%);border-radius:2px}
"""

COINBASE_CSS = """
:root{
  --bg:#ffffff; --surface:#f7f9fc; --surface-2:#eef2f7; --ink:#050f19; --gray-70:#5b616e; --gray-60:#8a94a6;
  --gray-30:#d8dde3; --gray-20:#e6eaf0;
  --accent:#0052ff; --accent-2:#003ecb; --accent-soft:#e5edff;
  --up:#cf202f; --up-bg:rgba(207,32,47,.10); --down:#02b196; --down-bg:rgba(2,177,150,.10);
  --g-active:#0052ff;
  --row-h:40px;
}
*{margin:0;padding:0;box-sizing:border-box}
body{background:var(--bg);color:var(--ink);font-family:-apple-system,BlinkMacSystemFont,"Inter","PingFang SC","Microsoft YaHei",sans-serif;font-size:14px;line-height:1.5}
.wrap{max-width:1280px;margin:0 auto;padding:32px 32px 80px}
header.page{padding:16px 0 16px}
.tag{display:inline-block;font-size:11px;padding:3px 10px;background:var(--accent);color:#fff;font-weight:600;margin-bottom:12px;border-radius:4px;letter-spacing:.02em}
h1{font-size:28px;font-weight:700;line-height:1.2;letter-spacing:-.02em;color:var(--ink)}
.sub{color:var(--gray-70);font-size:13px;line-height:1.5;margin-top:8px;max-width:780px}
.card{background:var(--surface);border-radius:12px;padding:24px;margin-bottom:16px}
.kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}
.kpi{background:var(--surface-2);border-radius:8px;padding:14px 16px}
.kpi .k{font-size:12px;color:var(--gray-70);font-weight:500}
.kpi .v{font-size:22px;font-weight:700;font-family:ui-monospace,"SF Mono",Consolas,monospace;margin-top:4px;font-variant-numeric:tabular-nums;color:var(--ink)}
table{width:100%;border-collapse:collapse;font-size:13px;background:transparent;margin-top:16px}
th{background:var(--surface-2);color:var(--ink);font-weight:600;padding:10px 12px;font-size:12px;text-align:right;border-bottom:1px solid var(--gray-30)}
th.c1,th.c2{text-align:left}
td{height:var(--row-h);padding:6px 12px;vertical-align:middle;border-bottom:1px solid var(--gray-20)}
td.c1{font-weight:600;color:var(--ink);border-left:3px solid var(--g-active);background:var(--surface)}
td.c2{color:var(--ink)}
td.cell{text-align:right;background-image:linear-gradient(to right,var(--bar,transparent) 0,var(--bar,transparent) var(--w,0%),transparent var(--w,0%));background-repeat:no-repeat;font-variant-numeric:tabular-nums}
.cell .net{display:block;font-weight:600;line-height:1.3}
.cell .net.up{color:var(--up)}
.cell .net.down{color:var(--down)}
.cell .bs{display:block;font-size:11px;color:var(--gray-60);font-weight:400}
.legend{font-size:12px;color:var(--gray-70);margin-top:12px;display:flex;align-items:center;gap:8px}
.legend .sw{width:48px;height:8px;background:linear-gradient(to right,var(--up-bg) 0,var(--up-bg) 100%);border-radius:2px}
"""

NOTION_CSS = """
:root{
  --bg:#fafaf9; --surface:#ffffff; --surface-2:#f1f1ef; --ink:#37352f; --gray-70:#787774; --gray-60:#9b9a97;
  --gray-30:#dfddd8; --gray-20:#ebebea;
  --accent:#2eaadc; --g-active:#2eaadc;
  --up:#d44c47; --up-bg:rgba(212,76,71,.10); --down:#4d9375; --down-bg:rgba(77,147,117,.10);
  --row-h:32px;
}
*{margin:0;padding:0;box-sizing:border-box}
body{background:var(--bg);color:var(--ink);font-family:-apple-system,BlinkMacSystemFont,"Inter","Segoe UI","PingFang SC",sans-serif;font-size:14px;line-height:1.6;letter-spacing:-.003em}
.wrap{max-width:1280px;margin:0 auto;padding:32px 48px 80px}
header.page{padding:16px 0 16px}
.tag{display:inline-block;font-size:11px;padding:3px 10px;background:var(--accent);color:#fff;font-weight:500;margin-bottom:12px;border-radius:3px;letter-spacing:.02em}
h1{font-size:30px;font-weight:700;line-height:1.2;letter-spacing:-.02em;color:var(--ink)}
.sub{color:var(--gray-70);font-size:13px;line-height:1.6;margin-top:8px;max-width:780px}
.card{background:var(--surface);border:1px solid var(--gray-20);border-radius:4px;padding:20px 24px;margin-bottom:16px}
.kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:1px;background:var(--gray-30);border-radius:4px;overflow:hidden}
.kpi{background:var(--surface);padding:14px 16px}
.kpi .k{font-size:11px;color:var(--gray-70);font-weight:500;letter-spacing:.02em}
.kpi .v{font-size:20px;font-weight:600;font-family:ui-monospace,"SF Mono",Consolas,monospace;margin-top:2px;font-variant-numeric:tabular-nums;color:var(--ink)}
table{width:100%;border-collapse:collapse;font-size:13px;background:transparent;margin-top:12px}
th{color:var(--gray-70);font-weight:500;padding:6px 12px;font-size:11px;text-align:right;border-bottom:1px solid var(--gray-30)}
th.c1,th.c2{text-align:left}
td{height:var(--row-h);padding:6px 12px;vertical-align:middle;border-bottom:1px solid var(--gray-20);font-size:13px}
td.c1{font-weight:600;color:var(--ink);border-left:2px solid var(--g-active)}
td.c2{color:var(--ink)}
td.cell{text-align:right;background-image:linear-gradient(to right,var(--bar,transparent) 0,var(--bar,transparent) var(--w,0%),transparent var(--w,0%));background-repeat:no-repeat;font-variant-numeric:tabular-nums}
.cell .net{display:block;font-weight:500;line-height:1.3}
.cell .net.up{color:var(--up)}
.cell .net.down{color:var(--down)}
.cell .bs{display:block;font-size:11px;color:var(--gray-60);font-weight:400}
.legend{font-size:12px;color:var(--gray-70);margin-top:10px;display:flex;align-items:center;gap:8px}
.legend .sw{width:48px;height:8px;background:linear-gradient(to right,var(--up-bg) 0,var(--up-bg) 100%);border-radius:2px}
"""


def fmt(v):
    if v is None or v == 0:
        return "0"
    a = abs(v)
    if a >= 10000:
        return f"{v/10000:.2f}亿" if v >= 0 else f"{v/10000:.2f}亿"
    return f"{v:.0f}万"


def build_one(style_name, css, tag_color, philosophy, rows, latest_date):
    maxabs = max(abs(r["net"]) for r in rows) or 1
    body = ['<table><thead><tr>'
            '<th class="c1">席位</th><th class="c2">标的</th>'
            f'<th>净额</th><th>买卖分布</th>'
            f'</tr></thead><tbody>']
    for r in rows:
        net = r["net"]
        arrow = "↑" if net >= 0 else "↓"
        w = min(100.0, abs(net) / maxabs * 100)
        bar = "var(--up-bg)" if net >= 0 else "var(--down-bg)"
        cell = (f'<td class="cell" style="--bar:{bar};--w:{w:.1f}%">'
                f'<span class="net {"up" if net>=0 else "down"}">{arrow}{fmt(net)}</span>'
                f'<span class="bs">买{fmt(r["buy"])}·卖{fmt(r["sell"])}</span></td>')
        body.append(f'<tr><td class="c1">{r["player"]}</td>'
                    f'<td class="c2">{r["stock"]} <span style="color:var(--gray-60);font-size:11px;font-family:monospace">{r["code"]}</span></td>'
                    f'{cell}'
                    f'<td style="text-align:right;font-size:11px;color:var(--gray-60)">本周累计</td></tr>')
    body.append('</tbody></table>')

    html = f"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8"/>
<title>{style_name} · 预览</title>
<style>{css}</style>
</head>
<body><div class="wrap">
<header class="page">
  <span class="tag">{tag_color}</span>
  <h1>{style_name}</h1>
  <div class="sub">{philosophy}</div>
</header>

<div class="card">
  <div class="kpis">
    <div class="kpi"><div class="k">样本日期</div><div class="v">{latest_date}</div></div>
    <div class="kpi"><div class="k">席位（抽样）</div><div class="v">{SAMPLES}</div></div>
    <div class="kpi"><div class="k">标的（抽样）</div><div class="v">{SAMPLES * STOCKS_PER}</div></div>
    <div class="kpi"><div class="k">参考强度条</div><div class="v">↑↓</div></div>
  </div>
</div>

<div class="card">
  {"".join(body)}
  <div class="legend"><span class="sw"></span>单元格底色条=净额强度（按样本最大值归一化），色+箭头+长三重编码</div>
</div>
</div></body></html>
"""
    return html


def main():
    latest = load_latest()
    if not latest:
        print("无数据")
        return
    rows, latest_date = make_rows(latest)

    plans = [
        ("A · Carbon v5 (当前)",  CARBON_CSS,   "#0f62fe",   "IBM Carbon 数据密度派 · 白底灰阶分层 · 字重封顶 600 · 单色 Blue 60 · 8px 网格 · 强度条+密度切换 · 上一轮上线版本"),
        ("B · Linear.app (备选)", LINEAR_CSS,   "#5e6ad2",   "暗色工程派 · 紫蓝 #5e6ad2 单一强调 · 12px 等宽数字 · 28px 极紧凑行高 · 圆角 6-8px · 暗室盯盘不刺眼"),
        ("C · Coinbase (备选)",   COINBASE_CSS, "#0052ff",   "金融蓝派 · 蓝 #0052ff 主色 · 大字号 KPI · 12px 圆角卡片 · 扁平 · ⚠️ 美式红绿与 A股相反，需做色交换"),
        ("D · Notion (备选)",     NOTION_CSS,   "#2eaadc",   "白底清雅派 · Inter 字体 · 字重封顶 500 · 圆角 3-4px · 呼吸感强 · 打印/截图友好 · 强度条需重做"),
    ]
    for name, css, color, philo in plans:
        # 安全文件名
        fn = name.split("·")[0].strip()  # "A" "B" "C" "D"
        sub_name = name.split("·")[1].strip().split(" ")[0]  # "Carbon" "Linear.app" "Coinbase" "Notion"
        sub_name = sub_name.lower().replace(".", "").replace("(", "").replace(")", "")
        out_path = os.path.join(OUT_DIR, f"preview-{fn.lower()}-{sub_name}.html")
        with open(out_path, "w", encoding="utf-8") as fp:
            fp.write(build_one(name, css, color, philo, rows, latest_date))
        print(f"已生成: {out_path}")

    print(f"\n打开 {OUT_DIR} 内 4 个文件对比。")


if __name__ == "__main__":
    main()
