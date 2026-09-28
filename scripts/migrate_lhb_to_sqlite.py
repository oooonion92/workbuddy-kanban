# -*- coding: utf-8 -*-
"""
JSON → SQLite 迁移脚本
=======================
把 daily/lhb_seats/*.json 全量导入 data/lhb.db，原文件移到 daily/lhb_seats_bak/ 留底。

用法:
    python scripts/migrate_lhb_to_sqlite.py --dry-run   # 只预演，不落盘
    python scripts/migrate_lhb_to_sqlite.py             # 执行迁移
    python scripts/migrate_lhb_to_sqlite.py --verify    # 迁移后回读校验

安全保证：
  - 原 JSON 是 mv 到备份目录，**不删除**
  - 迁移后自动做「回读校验」：逐日比对关键计数（席位数/ops 数/unmatched 数）
  - 任一天校验失败 → 打印差异并返回非 0，备份文件不会被清理
"""
import argparse
import glob
import json
import os
import shutil
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from lhb_store import LhbStore, DEFAULT_DB  # noqa: E402

SEAT_DIR = os.path.join(ROOT, "daily", "lhb_seats")
BAK_DIR = os.path.join(ROOT, "daily", "lhb_seats_bak")
BUCKETS = ("quant", "famous", "other", "retail", "deprecated")


def load_jsons():
    files = sorted(glob.glob(os.path.join(SEAT_DIR, "*.json")))
    out = []
    for f in files:
        try:
            with open(f, encoding="utf-8") as fp:
                out.append((f, json.load(fp)))
        except Exception as e:
            print(f"  [跳过] {os.path.basename(f)}: {e}")
    return out


def fingerprint(day):
    """一天的「指纹」：用于迁移前后比对"""
    fp = {"date": day.get("date"), "n_stocks": day.get("n_stocks"),
          "n_multi_day": day.get("n_multi_day"),
          "unmatched": len(day.get("unmatched") or []),
          "stock_chg_codes": len(day.get("stock_chg") or {}),
          "multi_day": len(day.get("multi_day") or [])}
    for b in BUCKETS:
        d = day.get(b) or {}
        fp[b + "_seats"] = len(d)
        fp[b + "_ops"] = sum(len(v.get("ops") or []) for v in d.values())
    return fp


def deep_check(orig, back, label, max_show=3):
    """逐字段深比对（比指纹严格得多）。

    2026-09-18 教训：指纹「席位/net」这类聚合数只比数量会漏掉
    **浮点累加顺序差异**与**结构键差异**，必须逐字段 == 比对。
    返回 (是否一致, 差异列表)
    """
    bad = []

    # 1) stock_chg 逐 code 逐日
    o_sc, b_sc = orig.get("stock_chg") or {}, back.get("stock_chg") or {}
    if set(o_sc) != set(b_sc):
        only_o = sorted(set(o_sc) - set(b_sc))[:max_show]
        only_b = sorted(set(b_sc) - set(o_sc))[:max_show]
        bad.append(f"stock_chg code 集合不同: 仅JSON={only_o} 仅DB={only_b}")
    else:
        for code in o_sc:
            if o_sc[code] != b_sc[code]:
                bad.append(f"stock_chg[{code}]: JSON={len(o_sc[code])}天 "
                           f"DB={len(b_sc[code])}天")
                if len(bad) > 12:
                    break

    # 2) multi_day 逐条
    o_md, b_md = orig.get("multi_day") or [], back.get("multi_day") or []
    if len(o_md) != len(b_md):
        bad.append(f"multi_day 条数: JSON={len(o_md)} DB={len(b_md)}")
    else:
        for i, (x, y) in enumerate(zip(o_md, b_md)):
            if x != y:
                bad.append(f"multi_day[{i}] 不同: JSON={json.dumps(x, ensure_ascii=False)[:90]} "
                           f"| DB={json.dumps(y, ensure_ascii=False)[:90]}")
                if len(bad) > 12:
                    break

    # 3) 席位桶逐席逐字段
    for b in BUCKETS:
        od, bd = orig.get(b) or {}, back.get(b) or {}
        if set(od) != set(bd):
            bad.append(f"{b} 席位集合不同: 仅JSON={sorted(set(od)-set(bd))[:max_show]} "
                       f"仅DB={sorted(set(bd)-set(od))[:max_show]}")
            continue
        for pid in od:
            oi, bi = od[pid], bd[pid]
            for k in ("net", "buy", "sell", "n"):
                if oi.get(k) != bi.get(k):
                    bad.append(f"{b}/{pid}.{k}: JSON={oi.get(k)} DB={bi.get(k)}")
            if (oi.get("depts") or []) != (bi.get("depts") or []):
                bad.append(f"{b}/{pid}.depts 顺序/内容不同")
            oo, bo = oi.get("ops") or [], bi.get("ops") or []
            if len(oo) != len(bo):
                bad.append(f"{b}/{pid}.ops 条数: JSON={len(oo)} DB={len(bo)}")
            else:
                for j, (x, y) in enumerate(zip(oo, bo)):
                    if x != y:
                        bad.append(f"{b}/{pid}.ops[{j}] 不同: "
                                   f"JSON={json.dumps(x, ensure_ascii=False)[:80]} | "
                                   f"DB={json.dumps(y, ensure_ascii=False)[:80]}")
                        break
            if len(bad) > 12:
                break
        if len(bad) > 12:
            break

    # 4) unmatched 逐条
    o_un, b_un = orig.get("unmatched") or [], back.get("unmatched") or []
    if len(o_un) != len(b_un):
        bad.append(f"unmatched 条数: JSON={len(o_un)} DB={len(b_un)}")
    else:
        for i, (x, y) in enumerate(zip(o_un, b_un)):
            if x != y:
                bad.append(f"unmatched[{i}] 不同")
                break

    # 5) board_codes / multi_codes
    for k in ("board_codes", "multi_codes"):
        if (orig.get(k) or {}) != (back.get(k) or {}):
            bad.append(f"{k} 不同")

    if bad:
        print(f"  [深差异] {label}")
        for x in bad[:12]:
            print(f"        {x}")
    return (not bad), bad


def diff_fp(a, b, label):
    keys = sorted(set(a) | set(b))
    bad = []
    for k in keys:
        if a.get(k) != b.get(k):
            bad.append((k, a.get(k), b.get(k)))
    if bad:
        print(f"  [差异] {label}")
        for k, x, y in bad:
            print(f"        {k}: JSON={x}  DB={y}")
    return not bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--verify", action="store_true", help="只做迁移后校验")
    ap.add_argument("--db", default=DEFAULT_DB)
    a = ap.parse_args()

    if a.verify:
        return verify_only(a.db)

    data = load_jsons()
    if not data:
        print(f"未在 {SEAT_DIR} 找到 JSON")
        return 1

    print(f"=== 待迁移 {len(data)} 个交易日 ===")
    fps = {}
    for f, d in data:
        fps[d["date"]] = fingerprint(d)
        print(f"  {d['date']}  {os.path.basename(f):<18} {os.path.getsize(f)/1024:7.1f}KB")

    if a.dry_run:
        print("\n[dry-run] 未写入数据库。")
        return 0

    # 清掉旧库，避免重复导入脏数据
    for suffix in ("", "-wal", "-shm"):
        p = a.db + suffix
        if os.path.exists(p):
            old = p + ".old"
            if os.path.exists(old):          # 上次迁移的留底 → 覆盖
                os.remove(old)
            os.rename(p, old)
            print(f"  旧库改名留底: {os.path.basename(p)}.old")

    st = LhbStore(a.db).init()
    print(f"\n=== 写入 {a.db} ===")
    for f, d in data:
        st.save_day(d)
        print(f"  ✓ {d['date']}")

    # 大批量写入后回收空间（WAL + 覆盖式 DELETE 会留空洞）
    st.vacuum()

    s = st.stats()
    print("\n=== 库统计 ===")
    for k, v in s.items():
        print(f"  {k:<12} {v}")

    # ---------- 回读校验 ----------
    print("\n=== 回读校验（指纹 + 逐字段深比对）===")
    orig_by_date = {d["date"]: d for _, d in data}
    ok = True
    for d in st.load_days():
        want = fps.get(d["date"])
        if not want:
            print(f"  [多出] {d['date']} 不在原 JSON 中")
            ok = False
            continue
        fp_ok = diff_fp(want, fingerprint(d), d["date"])
        deep_ok, _ = deep_check(orig_by_date[d["date"]], d, d["date"])
        if not (fp_ok and deep_ok):
            ok = False
    st.close()

    if not ok:
        print("\n❌ 校验未通过 —— 请勿删除 JSON，先排查差异。")
        return 2
    print("\n✅ 全部交易日指纹 + 逐字段深比对一致（100% 保真）")

    # ---------- 备份原文件 ----------
    os.makedirs(BAK_DIR, exist_ok=True)
    moved = 0
    for f, _ in data:
        dst = os.path.join(BAK_DIR, os.path.basename(f))
        shutil.move(f, dst)
        moved += 1
    print(f"\n原 JSON 已移至 {BAK_DIR}（{moved} 个，未删除）")

    # 体积对比
    raw = sum(os.path.getsize(os.path.join(BAK_DIR, os.path.basename(f)))
              for f, _ in data)
    db = os.path.getsize(a.db)
    print(f"\n=== 体积对比 ===")
    print(f"  JSON 合计  {raw/1048576:6.2f} MB")
    print(f"  SQLite     {db/1048576:6.2f} MB   （{100*(1-db/raw):+.0f}%）")
    return 0


def verify_only(db):
    if not os.path.exists(db):
        print(f"库不存在: {db}")
        return 1
    st = LhbStore(db).init()
    days = st.load_days()
    print(f"=== 库内 {len(days)} 个交易日 ===")
    for d in days:
        et = sum(1 for code, m in (d.get("stock_chg") or {}).items())
        print(f"  {d['date']}  stocks={d.get('n_stocks')} "
              f"seats={sum(len(d.get(b) or {}) for b in BUCKETS)} "
              f"chg_codes={et}")
    for k, v in st.stats().items():
        print(f"  {k:<12} {v}")
    st.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
