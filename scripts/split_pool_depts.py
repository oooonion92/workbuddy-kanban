# -*- coding: utf-8 -*-
"""把 席位跟踪池.csv 的 depts 列从「| 合并」拆成「每营业部一行」。

输出格式（用户 xlsx Sheet2 偏好）：
- 同一个 id（席位）下的每个营业部独占一行，id 与 group 重复；
- depts 列只写单个营业部，不再用 | 分隔；
- style/focus/shared 只在每个 id 的第一行填写，后续行留空（向前填充由 load_pool 负责）。

exclude 组（噪音关键词）不拆，保持每行独立。
"""
import csv
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "席位跟踪池.csv")


def main():
    rows_out = []
    fieldnames = None
    with open(SRC, encoding="utf-8-sig", newline="") as f:
        rdr = csv.DictReader(f)
        fieldnames = list(rdr.fieldnames)
        for row in rdr:
            g = (row.get("group") or "").strip()
            id_ = (row.get("id") or "").strip()
            dept_cell = (row.get("depts") or "").strip()
            style = (row.get("style") or "").strip()
            focus = (row.get("focus") or "").strip()
            shared = (row.get("shared") or "").strip()

            depts = [d.strip() for d in dept_cell.split("|") if d.strip()]
            if not depts:
                depts = [dept_cell] if dept_cell else [""]

            for i, d in enumerate(depts):
                rows_out.append({
                    "group": g,
                    "id": id_,
                    "depts": d,
                    "style": style if i == 0 else "",
                    "focus": focus if i == 0 else "",
                    "shared": shared if i == 0 else "",
                })

    with open(SRC, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, lineterminator="\n")
        w.writeheader()
        w.writerows(rows_out)

    # 统计
    n_rows = len(rows_out)
    n_multi = sum(1 for r in rows_out if r["depts"])
    print(f"完成：共 {n_rows} 行（原始含 | 的行已拆分为多行）")
    # 打印被拆分的席位（同 id 出现多次的）
    from collections import Counter
    c = Counter((r["group"], r["id"]) for r in rows_out)
    for (g, pid), cnt in c.items():
        if cnt > 1:
            print(f"  [{g}] {pid} -> {cnt} 个营业部")


if __name__ == "__main__":
    main()
