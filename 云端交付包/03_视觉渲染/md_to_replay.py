# -*- coding: utf-8 -*-
"""
05 复盘 md → 融合源 HTML（为历史交易日重建「当日」看板叙事）
================================================================
**为什么需要它**：`build_dashboard` 的叙事段落（决策摘要／指数概况／双维评分／题材／自选股／
操作评价／候选池／预案）全部取自「融合源」，而融合源此前**只有最新一日**
（`.workbuddy/tmp/replay_latest.html`，手工撰写）。
若历史日快照直接复用最新融合源 → **会把最新一日的叙事套到历史日上**（严重误导）。
本脚本把 `daily/{YYYYMMDD}/05_复盘与明日预案.md` 转换成**与融合源同构**的 HTML：
  · 同样的官方锚点 `<!-- ========== N、… ========== -->`（十段）
  · 同样的 `<div class="hero">` 圆环（用当日 复盘数据.json，弧长 238.8×(1−分/100)）
  · 同样的 `<style>` 与 legacy 锚点「大盘结构分析」
  · 决策摘要末段包 `<div class="card bd-green">` → 供主入口取「主观 · 大盘全局分析」

产出：`daily/{YYYYMMDD}/replay.html`（历史融合源，不进 daily 根目录）

用法：
  python scripts/md_to_replay.py --dates 2026-09-17,2026-09-18
  python scripts/md_to_replay.py --range 20260814-20260918
"""
import argparse
import glob
import html as _html
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DAILY = os.path.join(ROOT, "daily")

SEC_ORDER = ["一、决策摘要", "二、指数与市场概况", "三、双维评分", "四、情绪定位与连板天梯",
             "五、题材主线与板块景气", "六、自选股与持仓", "七、大盘结构补充 · 股指期货跟踪",
             "八、操作评价与纪律复盘", "九、明日候选池", "十、次日预案"]

STYLE = """<style>
  :root{--up:#E03131;--down:#0E8A5F;--hold:#B45309;--text:#14171A;--text2:#4B5563;--text3:#9CA3AF;
        --card:#FFFFFF;--card2:#F5F6F8;--card-border:#E4E7EB}
  body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;
       font-size:15px;color:var(--text);line-height:1.7;margin:0}
  .card{background:var(--card);border:1px solid var(--card-border);border-radius:14px;padding:16px 18px;margin:14px 0}
  .card.bd-blue{border-left:4px solid #2563EB}
  .card.bd-green{border-left:4px solid #0E8A5F}
  .hero{display:flex;gap:22px;align-items:center;flex-wrap:wrap;margin:14px 0}
  .rings{display:flex;gap:18px;flex-wrap:wrap}
  .ring-box{display:flex;flex-direction:column;align-items:center;gap:6px}
  .ring-box .lbl{font-size:11.5px;color:var(--text2);text-align:center;max-width:120px}
  .hero-right{flex:1 1 320px;min-width:260px}
  .hero-right .big{font-size:17px;line-height:1.6}
  table{border-collapse:collapse;width:100%;font-size:13.5px}
  th{background:var(--card2);color:var(--text2);font-size:12px;text-align:left;padding:7px 9px;border-bottom:1px solid var(--card-border)}
  td{padding:7px 9px;border-bottom:1px solid #F0F0F0;vertical-align:top}
  .up{color:var(--up)}.down{color:var(--down)}.hold{color:var(--hold)}
  ul.clean{list-style:none;padding-left:0;margin:10px 0}
  ul.clean li{position:relative;padding-left:14px;margin:5px 0}
  ul.clean li:before{content:"·";position:absolute;left:2px;color:var(--text3)}
  h2{font-size:15px;font-weight:700;margin:0 0 12px}
  p{margin:8px 0}
  .note{background:var(--card2);border-radius:10px;padding:10px 12px;font-size:13px;color:var(--text2);margin:10px 0}
</style>"""


# ---------------------------------------------------------------- md → html
def inline(t):
    """行内转换：**粗体** → <b>（含红涨绿跌语义色，保守处理）"""
    t = _html.escape(t, quote=False)
    t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t)
    return t


def md_block_to_html(lines):
    """把一段 md（无标题）转成 HTML：表格 / 无序列表 / 有序列表 / 段落"""
    out, i = [], 0
    while i < len(lines):
        ln = lines[i]
        if ln.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].startswith("|"):
                rows.append(lines[i])
                i += 1
            cells = [[c.strip() for c in r.strip().strip("|").split("|")] for r in rows]
            body = [c for c in cells if not all(re.fullmatch(r":?-{2,}:?", x or "") for x in c)]
            if body:
                out.append("<div class=\"scroll\"><table>")
                for k, r in enumerate(body):
                    tag = "th" if k == 0 else "td"
                    out.append("<tr>" + "".join(f"<{tag}>{inline(c)}</{tag}>" for c in r) + "</tr>")
                out.append("</table></div>")
            continue
        if ln.startswith(("- ", "* ")):
            out.append("<ul class=\"clean\">")
            while i < len(lines) and lines[i].startswith(("- ", "* ")):
                out.append(f"<li>{inline(lines[i][2:].strip())}</li>")
                i += 1
            out.append("</ul>")
            continue
        if re.match(r"^\s*\d+\.\s", ln):
            out.append("<ul class=\"clean\">")
            while i < len(lines) and re.match(r"^\s*\d+\.\s", lines[i]):
                out.append(f"<li>{inline(re.sub(r'^\\s*\\d+\\.\\s', '', lines[i]))}</li>")
                i += 1
            out.append("</ul>")
            continue
        if ln.strip():
            out.append(f"<p>{inline(ln.strip())}</p>")
        i += 1
    return "\n".join(out)


def split_md(md):
    """按 ## 标题切段 → {标题: [行…]}，标题保留「N、名称」形式"""
    secs, cur = {}, None
    pre = []
    for ln in md.split("\n"):
        m = re.match(r"^##\s+(.+?)\s*$", ln)
        if m:
            cur = m.group(1).strip()
            secs[cur] = []
            continue
        (secs[cur] if cur else pre).append(ln)
    return pre, secs


def _sec_key(title):
    """把 md 标题对齐到官方段名（如 '六、自选股与持仓（L2 第 0 步 · 纯技术判读，成本不参与）' → '六、自选股与持仓'）"""
    for k in SEC_ORDER:
        pref = k.split("、")[0] + "、"
        if title.startswith(pref):
            return k
    return title


def rings_html(rec, prev):
    """圆环：弧长 238.8×(1−分/100)，三项（总分/结构/情绪）"""
    def ring(val, mx, lbl, color="#E03131"):
        v = val if isinstance(val, (int, float)) else 0
        off = 238.8 * (1 - max(0.0, min(1.0, v / mx)))
        return (f'<div class="ring-box"><svg width="92" height="92" viewBox="0 0 92 92">'
                f'<circle cx="46" cy="46" r="38" fill="none" stroke="#E4E7EB" stroke-width="9"/>'
                f'<circle cx="46" cy="46" r="38" fill="none" stroke="{color}" stroke-width="9" '
                f'stroke-linecap="round" stroke-dasharray="238.8" stroke-dashoffset="{off:.2f}" '
                f'transform="rotate(-90 46 46)"/>'
                f'<text x="46" y="50" text-anchor="middle" fill="{color}" font-size="17" '
                f'font-weight="700">{int(v)}</text></svg><div class="lbl">{lbl}</div></div>')

    def d(cur, pre, mx):
        if pre is None or cur is None:
            return ""
        try:
            # ⚠️ 分值可能是 float（JSON 里 47.0）→ 差值必须取整再用 :+d 格式化
            d = int(round(float(cur) - float(pre)))
            return f"（昨 {int(pre)}，{d:+d}）" if mx == 100 else f"（昨 {int(pre)}）"
        except (TypeError, ValueError):
            return ""

    t, s, e = rec.get("total"), rec.get("structure"), rec.get("sentiment")
    pt = prev.get("total") if prev else None
    ps = prev.get("structure") if prev else None
    pe = prev.get("sentiment") if prev else None
    gate = (rec.get("gate") or "").strip()
    return ("<div class=\"rings\">"
            + ring(t, 100, f"总分 {t}/100{d(t, pt, 100)}")
            + ring(s, 50, f"结构 {s}/50{d(s, ps, 50)}")
            + ring(e, 50, f"情绪 {e}/50{d(e, pe, 50)}")
            + "</div>"
            + ("<div class=\"hero-right\"><div class=\"big\">闸门 " + _html.escape(gate) + "</div></div>"
               if gate else ""))


def build(day, records):
    d8 = day.replace("-", "")
    md_path = os.path.join(DAILY, d8, "05_复盘与明日预案.md")
    if not os.path.isfile(md_path):
        return None, f"缺 {md_path}"
    md = open(md_path, encoding="utf-8").read()
    pre, secs = split_md(md)
    rec = next((r for r in records if r["date"].replace("-", "") == d8), None)
    if rec is None:
        return None, f"复盘数据.json 无 {day}"
    idx = records.index(rec)
    prev = records[idx - 1] if idx > 0 else None

    parts = [STYLE, "<!-- ========== 一、决策摘要 ========== -->"]
    parts.append(f'<div class="hero">{rings_html(rec, prev)}</div>')
    # 决策摘要正文：前若干段留 bd-blue，末段包 bd-green（供主入口取"主观·大盘全局分析"）
    ds_lines = secs.get("一、决策摘要") or pre
    body_html = md_block_to_html(ds_lines)
    paras = re.findall(r"<p>.*?</p>", body_html, re.S)
    if len(paras) >= 2:
        head = "".join(paras[:-1])
        tail = paras[-1]
    else:
        head, tail = body_html, ""
    parts.append(f'<div class="card bd-blue">{head}</div>')
    if tail:
        parts.append(f'<div class="card bd-green">{tail}</div>')
    parts.append("<!-- ========== 大盘结构分析 ========== -->")
    for k in SEC_ORDER[1:]:
        hit = next((t for t in secs if _sec_key(t) == k), None)
        parts.append(f"<!-- ========== {k} ========== -->")
        if hit is not None:
            parts.append(f'<div class="card"><h2>{_html.escape(hit)}</h2>'
                         + md_block_to_html(secs[hit]) + "</div>")
    # 其余未对齐的段（md 里多出来的标题）附在末尾，避免丢内容
    matched = {_sec_key(t) for t in secs}
    extra = [t for t in secs if _sec_key(t) not in SEC_ORDER]
    for t in extra:
        parts.append(f"<!-- ========== {t} ========== -->")
        parts.append(f'<div class="card"><h2>{_html.escape(t)}</h2>' + md_block_to_html(secs[t]) + "</div>")
    out = "\n".join(parts) + "\n<div class=\"footer\">由 md_to_replay.py 自 05 复盘 md 重建</div>\n"
    dst = os.path.join(DAILY, d8, "replay.html")
    with open(dst, "w", encoding="utf-8") as f:
        f.write(out)
    return dst, f"{len(out.encode('utf-8'))} 字节 · 段 {len(secs)} · 未对齐 {len(extra)}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dates")
    ap.add_argument("--range")
    args = ap.parse_args()
    sys.path.insert(0, HERE)
    import build_dashboard as bd
    records = bd.collect()
    days = []
    if args.range:
        a, b = args.range.split("-")
        # ⚠️ 日期取 md 的**父目录名**（不是文件名），文件名恒为 05_复盘与明日预案.md
        days = [os.path.basename(os.path.dirname(p))
                for p in sorted(glob.glob(os.path.join(DAILY, "*", "05_复盘与明日预案.md")))
                if a <= os.path.basename(os.path.dirname(p)) <= b]
    elif args.dates:
        days = [d.strip().replace("-", "") for d in args.dates.split(",") if d.strip()]
    else:
        ap.error("需给 --dates 或 --range")
    ok = 0
    for d8 in days:
        dst, note = build(d8, records)
        if dst:
            ok += 1
            print(f"  ✓ {d8} → {os.path.relpath(dst, ROOT)}（{note}）")
        else:
            print(f"  ✗ {d8}: {note}")
    print(f"共 {ok}/{len(days)} 个交易日重建完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
