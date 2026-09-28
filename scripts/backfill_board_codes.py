"""为 daily/lhb_seats/*.json 回填 board_codes 字段（当日全部上榜标的名单）。

背景：2026-09-16 用户指出看板需区分两类「空」——
  ① 该票当日没上龙虎榜 → 无法跟踪（列本质无意义）
  ② 该票当日上榜了但某席位没参与 → 才是「席位未交易」
原 json 只落了 n_stocks 计数，丢掉了名单 → 无法区分。

本脚本从东财 RPT_DAILYBILLBOARD_DETAILSNEW 逐日重拉个股榜（覆盖当日全部上榜票），
写入 board_codes = {code: {name, chg, reason}}。已有的字段一律不动。

用法：python scripts/backfill_board_codes.py [--dry]
"""
import json
import os
import sys
import time
import urllib.request
import urllib.parse

STORE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "daily", "lhb_seats")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"


def fetch(report, flt, page_size=500, page_number=1, retry=3):
    q = {"reportName": report, "columns": "ALL", "filter": flt,
         "pageNumber": str(page_number), "pageSize": str(page_size),
         "sortColumns": "", "sortTypes": "", "source": "WEB", "client": "WEB"}
    url = "https://datacenter-web.eastmoney.com/api/data/v1/get?" + urllib.parse.urlencode(q)
    for i in range(retry):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": "https://data.eastmoney.com/"})
            with urllib.request.urlopen(req, timeout=25) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception:
            if i == retry - 1:
                return {}
            time.sleep(1.5)
    return {}


def fetch_all(report, flt, page_size=500):
    out, pn = [], 1
    while True:
        j = fetch(report, flt, page_size, pn)
        res = (j or {}).get("result") or {}
        data = res.get("data") or []
        out.extend(data)
        if pn >= (res.get("pages") or 1) or not data:
            break
        pn += 1
    return out


def main():
    dry = "--dry" in sys.argv
    files = sorted(f for f in os.listdir(STORE) if f.endswith(".json"))
    print(f"共 {len(files)} 个数据文件" + ("（dry-run）" if dry else ""))
    for fn in files:
        path = os.path.join(STORE, fn)
        d = json.load(open(path, encoding="utf-8"))
        date = d.get("date")
        if not date:
            print(f"  {fn}: 无 date 字段，跳过")
            continue
        if d.get("board_codes"):
            print(f"  {fn} ({date}): 已有 board_codes（{len(d['board_codes'])} 只），跳过")
            continue
        rows = fetch_all("RPT_DAILYBILLBOARD_DETAILSNEW",
                         f"(TRADE_DATE<='{date}')(TRADE_DATE>='{date}')")
        bc = {}
        for x in rows:
            c = x.get("SECURITY_CODE")
            if not c:
                continue
            bc[c] = {
                "name": x.get("SECURITY_NAME_ABBR", c),
                "chg": x.get("CHANGE_RATE"),
                "reason": (x.get("EXPLANATION") or "")[:24],
            }
        if not bc:
            print(f"  {fn} ({date}): ⚠️ 东财返回 0 只，跳过（保留原样）")
            continue
        old_n = d.get("n_stocks") or 0
        d["board_codes"] = bc
        d["n_stocks"] = len(bc)
        if not dry:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(d, f, ensure_ascii=False, indent=1)
        flag = "" if len(bc) == old_n else f"  ⚠️ n_stocks {old_n} → {len(bc)}"
        print(f"  {fn} ({date}): +{len(bc)} 只{flag}")


if __name__ == "__main__":
    main()
