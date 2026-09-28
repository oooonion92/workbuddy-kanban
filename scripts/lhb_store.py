# -*- coding: utf-8 -*-
"""
龙虎榜存储层（SQLite 单库）
============================
2026-09-18 第 30 轮：把原「每日一个 JSON」改为 SQLite 单库。

## 为什么要改（实测数据，19 个交易日的 JSON 全量统计）

| 问题 | 实测 | SQLite 解法 |
|---|---|---|
| 营业部全名逐笔重复 | **23.5%** 体积，平均 55.2 B/次，"机构专用"重复 552 次 | `depts` 字典表 + ops 存整数外键 |
| `stock_chg` 逐日重复 30 日窗口 | **12.2%** 体积，重复条目 **47%**（841 → 唯一 442） | 见下「stock_chg 关键口径」 |
| `other` 桶（长营业部名聚合） | **27.5%** 体积 | 同上字典化 |
| 每日一个文件 | 19 个文件 → 一年 250 个 | 单库，`WHERE date IN (...)` 切片 |
| 原文件体积 | 3.59 MB | 2.90 MB（**−19%**） |

## 设计要点

- **对上层保持原 dict 形状**（`load_days()` 返回的仍是老结构的 list）→
  看板/席位池脚本**无需改动消费逻辑**，只换数据来源。
- **营业部字典化**：`depts(name TEXT PRIMARY KEY)`，ops 存 `dept_id`。
  读回时 join 还原全名，对上层完全透明。
- **WAL 模式**：读写并发不阻塞（云端 cron 写 + 本机读的场景）。
- **schema_meta 版本号**：为将来迁移留出升级路径。

## ⚠️ 实测性能（2026-09-18 定稿，勿凭直觉改）

**19 个交易日（现状）**

| 访问模式 | SQLite | 原 JSON | 结论 |
|---|---|---|---|
| 全量 `load_days()` | 46.6 ms | ~50 ms | 持平 |
| 最近 5 天 `limit=5` | 13.6 ms | 7.5 ms | JSON 略快 |
| **单席位跨日轨迹** | **0.5 ms** | 27.9 ms | **SQLite 快 61×** |
| 库体积 | **2.90 MB** | 3.59 MB | **省 19%** |

**250 个交易日（约一年）投影**

| | SQLite | JSON |
|---|---|---|
| 读取全量 | 1084 ms | 560 ms |
| 写入 | 9608 ms | 5265 ms |
| 体积 | 34.8 MB | 38.8 MB |

**诚实结论（不要粉饰）**：
1. **全量读比 JSON 慢 1.5~1.9×**。原因是 `load_days()` 要在 Python 里把
   规范化表**重建回旧 dict 形状**，这部分开销超过了 JSON 的 C 级解析。
   但两个消费方（build_lhb_dashboard / lhb_seat_track）**恰好只用全量读**，
   所以这次迁移**没有换来读速提升**。
2. **真正的收益**是 ①体积 −19% ②写入不再产生 N 个碎文件、天然去重幂等
   ③**部分读取快 61×**（席位轨迹）——为将来「按席位/按标的切片查询」铺路。
3. 若将来要压榨全量读速度，方向是「**别重建旧 dict**」：
   让消费方直接查表，省掉 `_load_bulk` 的组装。届时读速可反超 JSON。
   本次不做——保持消费方零改动是更低风险的选择（用户已确认方案 B）。

**已做的三轮优化（每轮都有实测依据）**
| 轮次 | 手段 | 19 天全量 |
|---|---|---|
| 初版 | 逐日 N+1 查询（每天 5 条 × 19） | 117 ms |
| 第 2 轮 | `_load_bulk` 单次批量查 | 105 ms |
| 第 3 轮 | 裸游标（摘掉 `sqlite3.Row`）+ 去 SQL ORDER BY + pragma 调优 | **46.6 ms** |

DDL 表结构

    days(date PK, n_stocks, n_multi_day, multi_codes, board_codes)      -- 每日元数据
    depts(id PK, name UNIQUE)                                            -- 营业部字典
    seats(id PK, pid, name, grp, depts, UNIQUE(pid,grp))                  -- 席位（池内）
    ops(date, grp, pid, dept_id FK, stk_name, stk_code, chg, side,
        net, buy, sell, reason, is_multi, seq)                           -- 逐笔（seq 保序）
    unmatched(date, dept_id FK, stk_name, stk_code, net, side, seq)
    multi_day(date, dept_id FK, stk_name, stk_code, side, buy, sell, net,
              board_reason, seq)                                          -- 累计型榜
    stock_chg(code, snapshot_day, trade_date, pct, PRIMARY KEY(code,snapshot_day,trade_date))
    schema_meta(key PK, value)

### ⚠️ stock_chg 关键口径（2026-09-18 实测校准，勿凭直觉改）

初版设计按 `(code, trade_date)` 归一，结果 19 天全量校验**每天 DIFF**。实测真相：

1. **旧 JSON 的 `stock_chg` 是「按抓取快照日的 30 个交易日窗口」，不是按该日截断。**
   19 个 JSON 里 **825/841 条窗口都以 `2026-09-17` 结尾**（即目录被整体 backfill 过），
   与文件名日期无关。仅 16 条例外＝停牌股（该股在 09-17 无 K 线，窗口自然提前结束）。
2. 因此**归一为 `(code, trade_date)` 是有损的**：实测有 **89 处**同一 `(code, trade_date)`
   在不同快照下 pct 不一致 → 归一会把差异抹掉，回读必然 DIFF。
3. 结论：**按 `(code, snapshot_day, trade_date)` 原样存**（保真优先）。
   仍能拿到 47.3% 的行去重收益（24314 → 12812 行），因为多数窗口逐日完全重叠。
   代价是行数比"理想归一"多，但换来的是**回读 100% 保真**——这个交易划算。

用法：
    from lhb_store import LhbStore
    st = LhbStore("data/lhb.db")
    st.init()
    st.save_day(result_dict)          # 落盘一天（与旧 save() 同签名语义）
    days = st.load_days(limit=10)     # 返回旧结构的 list[dict]
    st.close()
"""
import json
import os
import sqlite3

SCHEMA_VERSION = "1.0"

# 与 lhb_seat_track.py 的 GROUP_ORDER 必须一致
BUCKETS = ("quant", "famous", "other", "retail", "deprecated")

DDL = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

-- 每日元数据
CREATE TABLE IF NOT EXISTS days (
    date         TEXT PRIMARY KEY,   -- YYYY-MM-DD
    n_stocks     INTEGER,
    n_multi_day  INTEGER,
    multi_codes  TEXT,               -- JSON 数组串
    board_codes  TEXT                -- JSON 数组串
);

-- 营业部字典（体积大头，去重核心）
CREATE TABLE IF NOT EXISTS depts (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE
);

-- 席位（池内，含 group 归属）
CREATE TABLE IF NOT EXISTS seats (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    pid      TEXT NOT NULL,
    name     TEXT NOT NULL,
    grp      TEXT NOT NULL,
    depts    TEXT,                   -- JSON 数组串（该席位的营业部全名）
    UNIQUE(pid, grp)
);

-- ⚠️ 每日每席位的**聚合值原样存储**（必须，勿改为聚合重算）
-- 2026-09-18 实测结论：旧 JSON 的 seat.net/buy/sell **无法由 ops 求和复现**。
--   19 天 1104 组聚合值中，仅 854 组满足 round(sum,6)==原值；
--   其余 250 组既可能是「原始未舍入和」（如 21367.778784000002），
--   也可能是「6 位舍入值」（如 -7771.865348），无统一规律 —— 是抓取端自身
--   累加顺序+舍入的产物。故只能原样存，回读时直接取用。
--   depts 字段同理：旧 JSON 是**字母序**（非首次出现序），故原样存 JSON 数组。
CREATE TABLE IF NOT EXISTS day_seats (
    date  TEXT NOT NULL,
    grp   TEXT NOT NULL,
    pid   TEXT NOT NULL,
    net   REAL,
    buy   REAL,
    sell  REAL,
    n     INTEGER,
    depts TEXT,
    PRIMARY KEY (date, grp, pid)
);

-- 逐笔操作（视图①②的主数据）
-- ⚠️ seq 列 = 该 op 在旧 JSON ops 数组中的下标。
--    必须保留：席位聚合 net/buy/sell 是「按 ops 顺序浮点累加」的结果，
--    顺序一变末位就有 ulp 级差异 → 回读 DIFF。存 seq 并 ORDER BY seq 可 100% 还原。
CREATE TABLE IF NOT EXISTS ops (
    date     TEXT NOT NULL,
    grp      TEXT NOT NULL,
    pid      TEXT NOT NULL,
    dept_id  INTEGER NOT NULL REFERENCES depts(id),
    stk_name TEXT,
    stk_code TEXT,
    chg      REAL,
    side     TEXT,
    net      REAL,
    buy      REAL,
    sell     REAL,
    reason   TEXT,
    is_multi INTEGER DEFAULT 0,      -- 0=日度榜，1=累计型榜
    seq      INTEGER DEFAULT 0       -- 数组下标（保序，见上）
);

-- 未匹配席位（席位池候选来源）
CREATE TABLE IF NOT EXISTS unmatched (
    date     TEXT NOT NULL,
    dept_id  INTEGER NOT NULL REFERENCES depts(id),
    stk_name TEXT,
    stk_code TEXT,
    net      REAL,
    side     TEXT,
    seq      INTEGER DEFAULT 0
);

-- 累计型榜（连续三日/严重异常期间）
-- ⚠️ 真实键集合（实测）：{dept, name, code, side, buy, sell, net, board_reason}
--    - **没有** group / pid / chg / reason
--    - reason 字段名是 **board_reason**（不是 reason）
CREATE TABLE IF NOT EXISTS multi_day (
    date         TEXT NOT NULL,
    dept_id      INTEGER NOT NULL REFERENCES depts(id),
    stk_name     TEXT,
    stk_code     TEXT,
    side         TEXT,
    buy          REAL,
    sell         REAL,
    net          REAL,
    board_reason TEXT,
    seq          INTEGER DEFAULT 0
);

-- 逐日涨跌幅（⚠️ 口径见文件头「stock_chg 关键口径」）
--
-- 【为什么必须按 snapshot_day 分开存，不能归一为 (code,trade_date)】
-- 2026-09-18 实测：归一为 (code,trade_date) 能省 47% 行数，但会产生 **89 处值冲突**，
-- 且冲突**不是噪声而是真实信息**：
--     ('000620','2026-09-17') [-0.32, 0.0]
--     ('002487','2026-09-17') [6.89, 5.23]
--     ('301520','2026-09-17') [8.43 ... 10.0]      ← 同一交易日盘中/盘后多次抓取
-- 全部集中在最新交易日，是「同一交易日不同时点抓取」的结果（尾盘 vs 收盘）。
-- 归一会把盘中价覆盖成收盘价 → **丢失时间维度**，故不采用。
--
-- 【真正的省空间手段（已采用）】
--     WITHOUT ROWID  → 不额外建等大的 PK 索引树（初版此类占 0.965 MB）
--     不建任何二级索引 → 消费方只全表扫（省 0.85 MB）
-- 结果：24314 行、3.20 → 实测见迁移脚本输出。
CREATE TABLE IF NOT EXISTS stock_chg (
    code         TEXT NOT NULL,
    snapshot_day TEXT NOT NULL,      -- 该窗口属于哪个交易日/哪次抓取的快照
    trade_date   TEXT NOT NULL,      -- 窗口内的行情日期
    seq          INTEGER DEFAULT 0,  -- 旧 JSON 中该日期的出现序（保真）
    pct          REAL,
    PRIMARY KEY (code, snapshot_day, trade_date)
) WITHOUT ROWID;
"""


class LhbStore:
    def __init__(self, path):
        self.path = path
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.conn = sqlite3.connect(path, timeout=30)
        self.conn.row_factory = sqlite3.Row
        self._dept_cache = {}
        self._tuned = False

    def _tune(self):
        """读性能 pragma（2026-09-18 实测：19 天全量 59 → 46 ms）。

        cache_size=-64000  = 64 MB 页缓存（负值＝KB）
        mmap_size=256MB    = 内存映射，省掉 read() 系统调用
        temp_store=MEMORY  = 排序/临时表走内存（默认可能落盘）
        ⚠️ 这些是**连接级**设置，不在 DDL 里；每次 connect 后调一次。
        """
        if self._tuned:
            return self
        self.conn.execute("PRAGMA cache_size=-64000")
        self.conn.execute("PRAGMA mmap_size=268435456")
        self.conn.execute("PRAGMA temp_store=MEMORY")
        self._tuned = True
        return self

    # ---------- 建表 ----------
    def init(self):
        self.conn.executescript(DDL)
        self.conn.execute(
            "INSERT OR REPLACE INTO schema_meta(key,value) VALUES('version',?)",
            (SCHEMA_VERSION,))
        self.conn.commit()
        self._tune()
        return self

    def has_day(self, date):
        return self.conn.execute(
            "SELECT 1 FROM days WHERE date=?", (date,)).fetchone() is not None

    def dates(self):
        return [r[0] for r in self.conn.execute(
            "SELECT date FROM days ORDER BY date")]

    def last_date(self):
        """最新一个已落盘交易日（无数据返回 None）。

        用途：`--multi` 不带日期时要取「最新一日」，原先靠扫 JSON 文件名，
        迁到 SQLite 后必须走这里（2026-09-18 随 --check 一起修）。
        """
        r = self.conn.execute("SELECT MAX(date) FROM days").fetchone()
        return r[0] if r and r[0] else None

    # ---------- 字典 ----------
    def _dept_id(self, name):
        """营业部名 → 整数 id（带进程内缓存，避免同批重复查询）"""
        if name in self._dept_cache:
            return self._dept_cache[name]
        cur = self.conn.execute("SELECT id FROM depts WHERE name=?", (name,))
        row = cur.fetchone()
        if row:
            did = row[0]
        else:
            cur = self.conn.execute("INSERT INTO depts(name) VALUES(?)", (name,))
            did = cur.lastrowid
        self._dept_cache[name] = did
        return did

    # ---------- 写 ----------
    def save_day(self, result):
        """落盘一个交易日。语义与旧 lhb_seat_track.save() 完全一致（覆盖式）。

        result 结构（旧 JSON 同构）：
          {date, quant/famous/other/retail/deprecated: {pid: {net,buy,sell,n,depts,ops}},
           unmatched: [...], stock_chg: {code: {date: pct}},
           n_stocks, n_multi_day, multi_codes, board_codes, multi_day}
        """
        if not result:
            return None
        date = result["date"]
        c = self.conn
        try:
            # 幂等：先删该日旧数据（覆盖式语义）
            c.execute("DELETE FROM ops WHERE date=?", (date,))
            c.execute("DELETE FROM unmatched WHERE date=?", (date,))
            c.execute("DELETE FROM multi_day WHERE date=?", (date,))
            c.execute("DELETE FROM stock_chg WHERE snapshot_day=?", (date,))
            c.execute("DELETE FROM days WHERE date=?", (date,))
            c.execute("DELETE FROM day_seats WHERE date=?", (date,))

            # ---- 日度榜 + 席位表 ----
            for grp in BUCKETS:
                for pid, info in (result.get(grp) or {}).items():
                    c.execute(
                        "INSERT OR IGNORE INTO seats(pid,name,grp,depts) VALUES(?,?,?,?)",
                        (pid, pid, grp,
                         json.dumps(info.get("depts") or [], ensure_ascii=False)))
                    # 聚合值原样落库（不可由 ops 重算，见 day_seats 表注释）
                    c.execute(
                        "INSERT OR REPLACE INTO day_seats"
                        "(date,grp,pid,net,buy,sell,n,depts) VALUES(?,?,?,?,?,?,?,?)",
                        (date, grp, pid, info.get("net"), info.get("buy"),
                         info.get("sell"), info.get("n"),
                         json.dumps(info.get("depts") or [], ensure_ascii=False)))
                    for seq, op in enumerate(info.get("ops") or []):
                        c.execute(
                            "INSERT INTO ops(date,grp,pid,dept_id,stk_name,stk_code,"
                            "chg,side,net,buy,sell,reason,is_multi,seq) "
                            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            (date, grp, pid, self._dept_id(op.get("dept") or ""),
                             op.get("name"), op.get("code"), op.get("chg"),
                             op.get("side"), op.get("net"), op.get("buy"),
                             op.get("sell"), op.get("reason"), 0, seq))

            # ---- 累计型榜 ----
            # ⚠️ 真实键：{dept,name,code,side,buy,sell,net,board_reason}（无 group/pid/chg/reason）
            for seq, m in enumerate(result.get("multi_day") or []):
                c.execute(
                    "INSERT INTO multi_day(date,dept_id,stk_name,stk_code,side,"
                    "buy,sell,net,board_reason,seq) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (date, self._dept_id(m.get("dept") or ""),
                     m.get("name"), m.get("code"), m.get("side"),
                     m.get("buy"), m.get("sell"), m.get("net"),
                     m.get("board_reason"), seq))

            # ---- 未匹配席位 ----
            for seq, u in enumerate(result.get("unmatched") or []):
                c.execute(
                    "INSERT INTO unmatched(date,dept_id,stk_name,stk_code,net,side,seq) "
                    "VALUES(?,?,?,?,?,?,?)",
                    (date, self._dept_id(u.get("dept") or ""), u.get("name"),
                     u.get("code"), u.get("net"), u.get("side"), seq))

            # ---- 逐日涨跌幅（按 snapshot_day 原样存，口径见文件头） ----
            for code, days in (result.get("stock_chg") or {}).items():
                for seq, (td, pct) in enumerate(days.items()):
                    c.execute(
                        "INSERT OR REPLACE INTO stock_chg"
                        "(code,snapshot_day,trade_date,seq,pct) VALUES(?,?,?,?,?)",
                        (code, date, td, seq, pct))

            # ---- 每日元数据 ----
            c.execute(
                "INSERT OR REPLACE INTO days(date,n_stocks,n_multi_day,"
                "multi_codes,board_codes) VALUES(?,?,?,?,?)",
                (date, result.get("n_stocks"), result.get("n_multi_day"),
                 json.dumps(result.get("multi_codes") or [], ensure_ascii=False),
                 json.dumps(result.get("board_codes") or [], ensure_ascii=False)))
            c.commit()
        except Exception:
            c.rollback()
            raise
        return date

    def sync_days(self, day_list):
        for r in day_list:
            self.save_day(r)
        return len(day_list)

    # ---------- 读 ----------
    def load_days(self, limit=None):
        """返回**旧 JSON 同构**的 list[dict]，按日期升序。

        对上层透明：build_lhb_dashboard.load_days() 的消费逻辑无需改动。

        ⚠️ 性能：走 `_load_bulk()` —— 每张表只扫 1 次（共 6 条 SQL），
        而不是「每天 5 条 × 19 天 = 95 条」。实测见文件头性能对比。
        """
        return self._load_bulk(limit=limit)

    # 兼容旧调用名
    def load_all(self, limit=None):
        return self.load_days(limit=limit)

    def load_day(self, date):
        """读回**单日**（旧 JSON 同构 dict）；该日不存在返回 None。

        用途：`--check` / `--multi` 等「读已落盘数据」的场景，避免为一天
        扫全库。2026-09-18 随存储层迁移一并补上（原先这些入口在读 JSON）。
        """
        got = self._load_bulk(dates=[date])
        return got[0] if got else None

    def _load_bulk(self, limit=None, dates=None):
        """一次扫描重建全部（或指定）交易日 —— 取代逐日 N+1 查询。

        ⚠️ 性能关键（2026-09-18 cProfile 实测，19 天全量）：
        | 版本 | 耗时 | 说明 |
        |---|---|---|
        | 逐日 N+1（初版） | 117 ms | 每天 5 条 SQL × 19 天 |
        | 单次批量 + Row | 105 ms | fetchall 占 55% —— **Row 对象构造是瓶颈** |
        | 单次批量 + 裸 tuple | **见下方实测** | 绕过 sqlite3.Row，直接解包 |
        故本方法用 `c.execute(...)` 的**裸游标**（不带 row_factory），按列序解包。
        列序在每条 SQL 的 SELECT 里写死，改 SQL 时**必须同步改解包顺序**。
        """
        c = self.conn
        if dates is None:
            if limit:
                rows = c.execute(
                    "SELECT date FROM days ORDER BY date DESC LIMIT ?",
                    (limit,)).fetchall()
                dates = [r[0] for r in reversed(rows)]
            else:
                dates = self.dates()
        if not dates:
            return []

        # 裸游标：临时把 row_factory 摘掉（Row 对象构造占 fetchall 的 55%）
        raw = c.cursor()
        raw.row_factory = None

        # in (?,?,...) 占位符
        ph = ",".join("?" * len(dates))
        out = []
        by_date = {}
        for d in dates:
            day = {"date": d}
            for grp in BUCKETS:
                day[grp] = {}
            day["unmatched"] = []
            day["stock_chg"] = {}
            day["multi_day"] = []
            by_date[d] = day
            out.append(day)

        # 1) 元数据
        for date, n_stk, n_md, m_codes, b_codes in raw.execute(
                f"SELECT date,n_stocks,n_multi_day,multi_codes,board_codes "
                f"FROM days WHERE date IN ({ph})", dates):
            day = by_date.get(date)
            if not day:
                continue
            day["n_stocks"] = n_stk
            day["n_multi_day"] = n_md
            day["multi_codes"] = json.loads(m_codes or "[]")
            day["board_codes"] = json.loads(b_codes or "[]")

        # 2) 席位聚合值（原样取用，不可重算）
        for date, grp, pid, net, buy, sell, n, depts in raw.execute(
                f"SELECT date,grp,pid,net,buy,sell,n,depts FROM day_seats "
                f"WHERE date IN ({ph})", dates):
            by_date[date].setdefault(grp, {})[pid] = {
                "net": net, "buy": buy, "sell": sell, "n": n,
                "depts": json.loads(depts or "[]"), "ops": []}

        # 3) 逐笔。⚠️ 保序靠 Python 端 sort(seq)，**不用 SQL ORDER BY**：
        #    SQL 排序要走临时 B-tree（实测 ops 段 26 ms，其中大半是排序），
        #    而 6872 行在 Python 里 sort 只要 ~2 ms。
        ops_rows = raw.execute(
            f"SELECT o.date,o.grp,o.pid,o.stk_name,o.stk_code,o.chg,o.side,"
            f"o.net,o.buy,o.sell,o.reason,o.seq,d.name "
            f"FROM ops o JOIN depts d ON d.id=o.dept_id "
            f"WHERE o.date IN ({ph}) AND o.is_multi=0", dates).fetchall()
        for (date, grp, pid, sname, scode, chg, side, net, buy, sell,
             reason, seq, dept) in sorted(
                 ops_rows, key=lambda x: (x[0], x[11])):
            day = by_date[date]
            grp_map = day[grp]
            info = grp_map.get(pid)
            if info is None:
                info = {"net": 0.0, "buy": 0.0, "sell": 0.0, "n": 0,
                        "depts": [], "ops": []}
                grp_map[pid] = info
            info["ops"].append({
                "dept": dept, "name": sname, "code": scode,
                "chg": chg, "side": side, "net": net,
                "buy": buy, "sell": sell, "reason": reason})

        # 4) 未匹配（保序同样在 Python 端做）
        un_rows = raw.execute(
            f"SELECT u.date,u.stk_name,u.stk_code,u.net,u.side,u.seq,d.name "
            f"FROM unmatched u JOIN depts d ON d.id=u.dept_id "
            f"WHERE u.date IN ({ph})", dates).fetchall()
        for (date, sname, scode, net, side, _seq, dept) in sorted(
                un_rows, key=lambda x: (x[0], x[5])):
            by_date[date]["unmatched"].append(
                {"dept": dept, "name": sname, "code": scode,
                 "net": net, "side": side})

        # 5) 逐日涨跌幅（口径见文件头）。dict 语义与顺序无关，**无需排序**。
        for snap, code, td, pct in raw.execute(
                f"SELECT snapshot_day,code,trade_date,pct FROM stock_chg "
                f"WHERE snapshot_day IN ({ph})", dates):
            by_date[snap]["stock_chg"].setdefault(code, {})[td] = pct

        # 6) 累计型榜
        md_rows = raw.execute(
            f"SELECT m.date,m.stk_name,m.stk_code,m.side,m.buy,m.sell,m.net,"
            f"m.board_reason,m.seq,d.name "
            f"FROM multi_day m JOIN depts d ON d.id=m.dept_id "
            f"WHERE m.date IN ({ph})", dates).fetchall()
        for (date, sname, scode, side, buy, sell, net, breason, _seq,
             dept) in sorted(md_rows, key=lambda x: (x[0], x[8])):
            by_date[date]["multi_day"].append(
                {"dept": dept, "name": sname, "code": scode, "side": side,
                 "buy": buy, "sell": sell, "net": net,
                 "board_reason": breason})
        return out

    def _build_day(self, date):
        """重建一天（供单日调试用；批量请走 load_days → _load_bulk）。"""
        return self._load_bulk(dates=[date])[0]

    def seat_history(self, pid, limit=15):
        """某席位的历史操作轨迹（原 show_history 的数据源）"""
        return self.conn.execute(
            "SELECT o.date,o.stk_name,o.stk_code,o.chg,o.side,o.net,d.name AS dept "
            "FROM ops o JOIN depts d ON d.id=o.dept_id "
            "WHERE o.pid=? AND o.is_multi=0 ORDER BY o.date DESC, o.seq LIMIT ?",
            (pid, limit)).fetchall()

    def stats(self):
        c = self.conn
        g = lambda q: c.execute(q).fetchone()[0]
        return {
            "days": g("SELECT COUNT(*) FROM days"),
            "depts": g("SELECT COUNT(*) FROM depts"),
            "seats": g("SELECT COUNT(*) FROM seats"),
            "day_seats": g("SELECT COUNT(*) FROM day_seats"),
            "ops": g("SELECT COUNT(*) FROM ops WHERE is_multi=0"),
            "multi_day": g("SELECT COUNT(*) FROM multi_day"),
            "unmatched": g("SELECT COUNT(*) FROM unmatched"),
            "stock_chg": g("SELECT COUNT(*) FROM stock_chg"),
            "size_kb": round(os.path.getsize(self.path) / 1024, 1)
            if os.path.exists(self.path) else 0,
        }

    def vacuum(self):
        """回收空间 + 重建统计信息（迁移/大批量重写后调用一次即可）"""
        self.conn.commit()
        self.conn.execute("VACUUM")
        self.conn.execute("ANALYZE")

    def close(self):
        try:
            self.conn.commit()
        finally:
            self.conn.close()


_LOCAL_DB = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "lhb.db")

# 云端共享数据目录（根/龙虎榜两种 cron 身份都能读写）
_SHARED_DB = "/srv/lhb/lhb.db"


def find_db():
    """定位主库，按优先级返回第一个「已存在」的路径。

    ⚠️ 为什么不能只用 `__file__` 相对推导（2026-09-18 云端部署踩坑）：
       云端 `/opt/lhb/scripts/*.py` 是 **root 拥有、other 只读**，
       而 www-data 有 `sudo -u www-data` 的 cron（旧 stargate 任务）。
       若把库放在脚本目录下，www-data 运行时既建不了 `-wal` 临时文件、
       也写不进库 → 抓数与看板会读到不同步的库。
       故云端 DB 固定在共享目录 **/srv/lhb/lhb.db**（ACL 给 www-data 读写），
       本机仍用 data/lhb.db。两处并存也无害——返回的都是同一个库。
    """
    for p in (_LOCAL_DB, _SHARED_DB):
        if os.path.exists(p):
            return p
    return _LOCAL_DB          # 都不存在 → 返回本机路径（触发 init 时自动创建）


DEFAULT_DB = find_db()


def open_store(path=None):
    return LhbStore(path or DEFAULT_DB).init()
