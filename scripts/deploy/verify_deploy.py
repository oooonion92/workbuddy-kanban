# -*- coding: utf-8 -*-
"""云端看板部署自检：下载远端文件，与本地比对（大小 / 关键锚点 / gzip 效果）"""
import hashlib
import os
import sys
import urllib.request

BASE = "http://111.230.143.251"
ROOT = r"D:\Work buddy project\每日盯盘\daily"

PAIRS = [
    ("dashboard.html", "dashboard.html", [b"<!DOCTYPE html", b"</html>"]),
    ("lhb/lhb_dashboard.html", "lhb_dashboard.html", [b"<!DOCTYPE html", b"</html>"]),
    ("lhb/lhb_theme.css", "lhb_theme.css", [b"table.matrix", b"--lhb-"]),
    ("lhb/lhb_ui.js", "lhb_ui.js", [b"function"]),
]


def fetch(path, gzip_enc=False):
    req = urllib.request.Request(BASE + "/" + path)
    if gzip_enc:
        req.add_header("Accept-Encoding", "gzip")
    with urllib.request.urlopen(req, timeout=60) as r:
        body = r.read()
        enc = r.headers.get("Content-Encoding", "-")
        return body, enc, r.headers.get("Content-Length")


print("=" * 66)
print(f"{'文件':<26}{'本地':>10}{'远端':>12}{'编码':>8}{'一致性':>10}")
print("=" * 66)

allok = True
for remote, localname, anchors in PAIRS:
    local = os.path.join(ROOT, localname)
    lsize = os.path.getsize(local)
    try:
        body, enc, clen = fetch(remote)
    except Exception as e:
        print(f"{remote:<26}{lsize:>10,}{'ERR':>12}{'-':>8}{str(e)[:20]:>10}")
        allok = False
        continue

    lh = hashlib.md5(open(local, "rb").read()).hexdigest()
    rh = hashlib.md5(body).hexdigest()
    same = "✓ 一致" if lh == rh else "✗ 不一致"

    miss = [a.decode() for a in anchors if a not in body]
    if miss:
        same += f" 缺{miss}"
        allok = False

    print(f"{remote:<26}{lsize:>10,}{len(body):>12,}{enc:>8}{same:>10}")

print("=" * 66)

# gzip 压缩比
print("\n【gzip 压缩效果】")
for remote, _, _ in PAIRS:
    try:
        raw, _, _ = fetch(remote, gzip_enc=False)
        gz, enc, _ = fetch(remote, gzip_enc=True)
        ratio = (1 - len(gz) / len(raw)) * 100 if raw else 0
        print(f"  {remote:<26} 原始 {len(raw):>9,}B → gzip {len(gz):>8,}B  (省 {ratio:.1f}%)  [{enc}]")
    except Exception as e:
        print(f"  {remote:<26} 失败: {e}")

# 内容红线：成本保密（看板需分享给朋友，不得含成本/浮盈字样）
print("\n【成本保密复检】")
for remote, localname, _ in PAIRS:
    local = os.path.join(ROOT, localname)
    if not localname.endswith((".html", ".js")):
        continue
    txt = open(local, encoding="utf-8", errors="ignore").read()
    hits = [w for w in ("浮盈", "浮亏", "安全垫", "我的成本", "持仓成本") if w in txt]
    print(f"  {localname:<26} {'⚠️ 命中 ' + str(hits) if hits else '✓ 无成本字样'}")

print("\n结论:", "✅ 全部通过" if allok else "❌ 存在不一致，见上")
sys.exit(0 if allok else 1)
