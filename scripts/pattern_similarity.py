import json, math

base_dir = r"C:/Users/xc92/.workbuddy/projects/d-Work buddy project-每日盯盘/8035331c-3e13-4d80-b939-e8f0f9829851/tool-results"
f_sh = base_dir + r"/mcp-tdx-connector-tdx_kline-1789021866150-103c3b.txt"
f_sz = base_dir + r"/mcp-tdx-connector-tdx_kline-1789021903939-8edc04.txt"

def load(f):
    txt = open(f, encoding="utf-8").read()
    j = json.JSONDecoder().raw_decode(txt[txt.index("{"):])[0]
    m = {}
    for r in j["Rows"]:
        m[r["Data"]] = {"c": float(r["Close"]), "a": float(r["Amount"]),
                        "h": float(r["High"]), "l": float(r["Low"]), "o": float(r["Open"])}
    return m

sh, sz = load(f_sh), load(f_sz)
dates = sorted(set(sh) & set(sz))
dates = [d for d in dates if "20240924" <= d < "20260910"]
print(f"样本 {dates[0]} -> {dates[-1]}，共 {len(dates)} 根（已剔除今日未收盘）")

close = {d: sh[d]["c"] for d in dates}
two_mkt = {d: sh[d]["a"] + sz[d]["a"] for d in dates}   # 两市合计成交额（元）

print(f"当前 20 日日均两市成交额: {sum(two_mkt[d] for d in dates[-20:])/20/1e8:,.0f} 亿")
print(f"当前 5 日日均: {sum(two_mkt[d] for d in dates[-5:])/5/1e8:,.0f} 亿 | 前 5 日日均: {sum(two_mkt[d] for d in dates[-10:-5])/5/1e8:,.0f} 亿")

# ---------- 一、2024-09-24 以来主要波段（ZigZag，阈值 5%） ----------
print("\n=== 2024-09-24 以来主要波段（摆动幅度 ≥5%）===")
legs = []
pivots = [(dates[0], close[dates[0]])]
direction = 0
ext_i = 0
TH = 0.05
for i, d in enumerate(dates):
    if i == 0:
        continue
    if direction >= 0:
        if close[d] > close[dates[ext_i]]:
            ext_i = i
        elif close[d] < close[dates[ext_i]] * (1 - TH):
            pivots.append((dates[ext_i], close[dates[ext_i]]))
            direction = -1
            ext_i = i
    if direction <= 0:
        if close[d] < close[dates[ext_i]]:
            ext_i = i
        elif close[d] > close[dates[ext_i]] * (1 + TH):
            pivots.append((dates[ext_i], close[dates[ext_i]]))
            direction = 1
            ext_i = i
pivots.append((dates[ext_i], close[dates[ext_i]]))
for k in range(len(pivots) - 1):
    d0, p0 = pivots[k]
    d1, p1 = pivots[k + 1]
    legs.append((d0, d1, (p1 / p0 - 1) * 100, p0, p1))
    print(f"  {d0} → {d1}   {p0:7.2f} → {p1:7.2f}   {(p1/p0-1)*100:+6.2f}%")

# ---------- 二、多窗口形态匹配 ----------
def match(N, topk=4):
    cur = dates[-N:]
    cc = [close[d] for d in cur]
    b = cc[0]
    cp = [x / b - 1 for x in cc]
    mcp = sum(cp) / N
    cp = [x - mcp for x in cp]
    cavg = sum(two_mkt[d] for d in cur) / N
    ca = [two_mkt[d] / cavg - 1 for d in cur]

    res = []
    for i in range(0, len(dates) - N - 20):
        seg = dates[i:i + N]
        c = [close[d] for d in seg]
        p = [x / c[0] - 1 for x in c]
        mp = sum(p) / N
        p = [x - mp for x in p]
        aavg = sum(two_mkt[d] for d in seg) / N
        a = [two_mkt[d] / aavg - 1 for d in seg]
        dp = math.sqrt(sum((x - y) ** 2 for x, y in zip(p, cp)) / N)
        da = math.sqrt(sum((x - y) ** 2 for x, y in zip(a, ca)) / N)
        dist = dp * 7 + da * 3
        mm = sum(p) / N
        mc = sum(cp) / N
        num = sum((x - mm) * (y - mc) for x, y in zip(p, cp))
        den = math.sqrt(sum((x - mm) ** 2 for x in p) * sum((y - mc) ** 2 for y in cp))
        corr = num / den if den else 0
        res.append((dist, corr, i, seg))
    res.sort(key=lambda x: x[0])
    return res

for N in (10, 20, 30):
    cur = dates[-N:]
    c = [close[d] for d in cur]
    print(f"\n{'='*70}\n=== 窗口 N={N} 日 | 当前段 {cur[0]}~{cur[-1]} "
          f"区间 {(c[-1]/c[0]-1)*100:+.2f}% | 期间最大回撤 "
          f"{(min(c)/max(c)-1)*100:.2f}% | 日均量 {sum(two_mkt[d] for d in cur)/N/1e8:,.0f} 亿 ===")
    res = match(N)
    for dist, corr, i, seg in res[:4]:
        end = i + N - 1
        c0 = close[dates[end]]
        cf = [close[d] for d in seg]
        print(f"  ▸ {seg[0]}~{seg[-1]} 距离 {dist:.3f} 相关 {corr:+.2f} "
              f"区间 {(cf[-1]/cf[0]-1)*100:+.2f}% 回撤 {(min(cf)/max(cf)-1)*100:.2f}% "
              f"日均量 {sum(two_mkt[d] for d in seg)/N/1e8:,.0f} 亿")
        out = []
        for k in (5, 10, 20):
            jj = end + k
            if jj < len(dates):
                nx = dates[jj]
                out.append(f"+{k}日 {(close[nx]/c0-1)*100:+.2f}% (量 {sum(two_mkt[d] for d in dates[end+1:jj+1])/k/1e8:,.0f}亿)")
        print("       后续: " + " | ".join(out))
