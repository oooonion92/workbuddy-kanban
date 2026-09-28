# -*- coding: utf-8 -*-
"""
云端部署 setup_remote.py
=======================
把龙虎榜自动化整条链路部署到 Lighthouse 服务器，并建立每日定时任务。

部署内容（2026-09-19 起：独立目录 /opt/lhb，与盯盘主项目彻底解耦）：
  /opt/lhb/                         ← 龙虎榜工作根目录（**只放龙虎榜必需文件**）
    ├── scripts/lhb_seat_track.py
    ├── scripts/build_lhb_dashboard.py
    ├── scripts/lhb_store.py        ← 存储层模块（必需）
    ├── 席位跟踪池.csv               ← 唯一需要人工维护的文件
    ├── 席位跟踪池.bak.csv           ← 上传前自动生成的历史累计版本备份
    ├── 云端数据.md                  ← 人工情报（席位实时分析结论，人工维护）
    ├── data/lhb.db                 ← 主数据源（SQLite 单库，唯一真身）
    ├── daily/lhb_dashboard.html    ← 看板产物
    ├── daily/lhb_theme.css / lhb_ui.js
    └── run_daily.sh                ← 每日流水线入口

  ⛔ 不再上传 daily/lhb_seats/*.json（历史 JSON）：
     SQLite 单库已是唯一真身，兜底只会在「主库损坏」这一种情形下生效，
     为此每天多传几十个文件不划算。需回滚时从本机重传即可（--upload --json）。

远端 Python 脚本的路径已改为「脚本自身位置」相对推导（见 lhb_seat_track.py
/ build_lhb_dashboard.py 的 _find_db()），因此部署到任意目录都能自动找到 data/lhb.db。

定时任务：cron 每天 17:10（工作日）跑 run_daily.sh

用法:
    python scripts/deploy/setup_remote.py --check       # 只检查远端环境
    python scripts/deploy/setup_remote.py --upload      # 传代码与数据（默认不含历史 JSON）
    python scripts/deploy/setup_remote.py --upload --json   # 额外传历史 JSON 作兜底
    python scripts/deploy/setup_remote.py --cron        # 装定时任务
    python scripts/deploy/setup_remote.py --all         # 全做
    python scripts/deploy/setup_remote.py --run         # 立刻手动跑一次验证
    python scripts/deploy/setup_remote.py --logs        # 看上次运行日志
"""
import argparse
import os
import posixpath
import sys

import paramiko

HOST = "111.230.143.251"
PORT = 22
USER = "root"
KEY = os.path.expanduser("~/.ssh/lighthouse_kanban")

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LOCAL_DAILY = os.path.join(ROOT, "daily")

REMOTE_ROOT = "/opt/lhb"
WEB_ROOT = "/var/www/kanban"
CRON_SCHEDULE = "10 17 * * 1-5"   # 工作日 17:10（用户 2026-09-17 指定）

RUN_SH = """#!/bin/bash
# 龙虎榜每日流水线：抓数 → 归因落盘 → 生成看板 → 发布到 web 目录
#
# ⚠️ 时区铁律（2026-09-17 首跑踩坑）：
#   服务器默认 UTC，比东八区晚 8 小时。lhb_seat_track.py 的 last_trade_days()
#   用 datetime.date.today() 取"今天" → UTC 下当日 18:00（=北京次日 02:00 前的 09-17 10:00Z）
#   会被算成前一天，默认只回溯 4 个自然日 → **漏掉最新交易日**。
#   实测：09-17 首跑抓到的是 09-14，不是当天。
#   修复：① 强制 TZ=Asia/Shanghai  ② 显式把「当天日期」作为参数传给脚本
set -e
cd {root}
export PYTHONIOENCODING=utf-8
export TZ=Asia/Shanghai
PY=$(command -v python3 || echo /usr/bin/python3)
LOG={root}/run.log
TODAY=$(date '+%Y-%m-%d')

echo "===== $(date '+%F %T') 开始（目标日期 $TODAY）=====" >> $LOG
# 显式传当日日期：脚本对无数据的日期会自动跳过，同时传入前 3 日做兜底补抓
D1=$(date -d "$TODAY -1 day"  '+%Y-%m-%d')
D2=$(date -d "$TODAY -2 day"  '+%Y-%m-%d')
D3=$(date -d "$TODAY -3 day"  '+%Y-%m-%d')
$PY scripts/lhb_seat_track.py $TODAY,$D1,$D2,$D3   >> $LOG 2>&1

# ---------- 完整性自检 + 补抓（2026-09-17 加，因定时改为 17:10 掐点） ----------
# 背景：龙虎榜披露是渐进的（实测 16:40 仅 36 只 → 17:10 才补齐 63 只）。
# 17:10 掐点执行有拿到"半成品"的风险 → 跑完自动比对「落盘标的数 vs 东财实时标的数」，
# 缺漏则等待后重抓当日，最多重试 3 次（间隔 180s ≈ 覆盖到 17:20）。
if [ "$(date '+%u')" -le 5 ]; then
  for i in 1 2 3; do
    CHK=$($PY scripts/lhb_seat_track.py --check "$TODAY" 2>&1 | tail -3)
    echo "[自检 $i/3] $CHK" >> $LOG
    if echo "$CHK" | grep -q "✅ 已齐"; then
      echo "[自检] 数据完整，无需补抓" >> $LOG
      break
    fi
    if [ "$i" -lt 3 ]; then
      echo "[自检] 检测到缺漏，等待 180s 后重抓 $TODAY" >> $LOG
      sleep 180
      $PY scripts/lhb_seat_track.py "$TODAY" >> $LOG 2>&1
    else
      echo "[自检] ⚠️ 重试 3 次仍有缺漏 —— 请次日复核，或手动执行：" >> $LOG
      echo "        cd {root} && python3 scripts/lhb_seat_track.py $TODAY" >> $LOG
    fi
  done
fi

# ---------- 产出通路（2026-09-18 重构） ----------
# ⚠️ web 发布原先用 `chown -R www-data:www-data {web}` —— 但 {web} 是「盯盘主项目」
#    与「龙虎榜」**共用**的站点根，整目录 chown 会波及主项目文件。
#    改为：只把 lhb 子目录及其文件交给 www-data。
$PY scripts/build_lhb_dashboard.py >> $LOG 2>&1

mkdir -p {web}/lhb
for f in lhb_dashboard.html lhb_theme.css lhb_ui.js; do
  [ -f daily/$f ] && cp -f daily/$f {web}/lhb/$f
done
chown -R www-data:www-data {web}/lhb 2>/dev/null || true
chmod 755 {web}/lhb
echo "===== $(date '+%F %T') 完成 =====" >> $LOG
""".replace("{web}", WEB_ROOT).replace("{root}", REMOTE_ROOT)


def connect():
    cli = paramiko.SSHClient()
    cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    cli.connect(HOST, port=PORT, username=USER, key_filename=KEY, timeout=20)
    return cli


def sh(cli, cmd, timeout=300, quiet=False):
    stdin, stdout, stderr = cli.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode("utf-8", "replace")
    err = stderr.read().decode("utf-8", "replace")
    code = stdout.channel.recv_exit_status()
    if not quiet and out.strip():
        print(out.rstrip())
    if err.strip() and code != 0:
        print(f"  [stderr] {err.rstrip()[:500]}")
    return code, out, err


def local_wal_checkpoint(db_path):
    """上传前把 WAL 落进主库，返回可打印的说明字符串。

    ⚠️ 2026-09-18 新增：SQLite 用了 journal_mode=WAL，新写入可能还留在
    `lhb.db-wal` 里。**只传 lhb.db 会丢最新数据**（云端看到的是旧快照）。
    checkpoint(TRUNCATE) 会把 -wal 全部合并回主库并截断，之后单传主库即完整。

    ⚠️ 直接用 Python 的 sqlite3，**不要走 subprocess**（本机 shell 残缺，
       subprocess 找不到可执行文件会抛 WinError 2）。
    """
    import sqlite3
    try:
        c = sqlite3.connect(db_path, timeout=15)
        c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        c.close()
        return f"[本机] WAL checkpoint 完成: {os.path.basename(db_path)}"
    except Exception as e:
        return f"[本机] WAL checkpoint 失败（继续上传）: {e}"


def db_survey(db_path):
    """读本机主库统计，上传后与云端比对（确保数据真的上去了）。"""
    import sqlite3
    out = {}
    try:
        c = sqlite3.connect(db_path, timeout=15)
        for t in ("days", "depts", "seats", "day_seats", "ops", "unmatched",
                  "multi_day", "stock_chg"):
            try:
                out[t] = c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            except Exception:
                out[t] = -1
        try:
            out["last_day"] = c.execute("SELECT MAX(date) FROM days").fetchone()[0]
        except Exception:
            out["last_day"] = "?"
        c.close()
    except Exception as e:
        out["error"] = str(e)
    return out


# ---------- 远端公共数据目录 /srv/lhb 的准备 ----------
# 为什么要独立目录（2026-09-18 踩坑）：
#   ① 原方案把 DB 放脚本目录下 /opt/lhb/data/ → 命中 paramiko 的
#      `path … known_hosts mismatch` 误报警（传输其实完全正常，但极易误判为损坏）
#   ② root 的 umask 022 会让目录变 755，www-data(33) 建不了 WAL 临时文件
#      → 用 ACL 给 www-data 读写权限，保留 root 的所有权（不 chown 整个目录，
#        否则 root 后续写文件会因 setgid 归属变化而混乱）
DATA_DIR = "/srv/lhb"
DB_REMOTE = f"{DATA_DIR}/lhb.db"


def remote_find_old_db(cli):
    """在云端自动定位旧位置（代码目录下）的 lhb.db。

    ★ 铁律：本地 SQLite（sqlite3 CLI 与 Python sqlite3 都是）执行
      `VACUUM INTO` 产出的库，会丢失 sqlite_master 里的 schema 记录
      （实测 sqlite_master 为空）→ **绝不可用 VACUUM INTO 搬库**。
      搬库只能用 `cp`（对持锁/重写的库不安全）或 SQLite 官方安全 API
      `Connection.backup()`。这里搬的是「上一次上传后未被写过的旧库」，
      用 `cp` 是安全的。
    """
    probes = [
        f"test -f {REMOTE_ROOT}/data/lhb.db "
        f"&& cp -f {REMOTE_ROOT}/data/lhb.db {REMOTE_ROOT}/data/lhb.db.bak "
        f"&& mv -f {REMOTE_ROOT}/data/lhb.db {DB_REMOTE} "
        f"&& echo 'MOVED:{REMOTE_ROOT}/data/lhb.db' || true",
    ]
    for p in probes:
        code, out, err = sh(cli, p, quiet=True)
        if "MOVED:" in out:
            print(f"  ✓ 旧库已搬运：{out.strip().split('MOVED:')[1]} → {DB_REMOTE}")
            return True
    print(f"  （未发现旧位置 DB，视为首次部署）")
    return False


def prepare_data_dir(cli):
    """建 /srv/lhb 并让 www-data 能读写（供其 cron 运行时建 WAL）。

    ⚠️ 服务器**没有 setfacl**（实测 `setfacl: command not found`）→ 不能用 ACL。
       改用「组归属 + setgid」：目录归 root:www-data 且 2775
       （setgid 让新建文件自动继承 www-data 组），库文件 664 → 两方都能写。
       这样既不放弃 root 所有权，也满足 www-data 的读写需求。
    """
    sh(cli, "id www-data >/dev/null 2>&1 && echo 'www-data 存在' || echo '⚠️ 无 www-data 用户'")
    sh(cli, f"mkdir -p {DATA_DIR}", quiet=True)
    # setgid(2775) 保证目录内新建文件自动属 www-data 组
    sh(cli, f"chgrp www-data {DATA_DIR} 2>/dev/null; chmod 2775 {DATA_DIR}", quiet=True)
    sh(cli, f"ls -ld {DATA_DIR}")


def check_env(cli):
    print("=== 远端环境检查 ===")
    sh(cli, "python3 --version; echo '--- 路径 ---'; which python3; "
            "echo '--- 时区 ---'; timedatectl 2>/dev/null | head -3 || date; "
            "echo '--- 磁盘 ---'; df -h /opt | tail -1; "
            "echo '--- cron ---'; systemctl is-active cron 2>/dev/null || systemctl is-active crond 2>/dev/null")


def local_db_info(db_path):
    """本机库的 (最后交易日, 天数)"""
    import sqlite3
    try:
        c = sqlite3.connect(db_path, timeout=15)
        last = c.execute("SELECT MAX(date) FROM days").fetchone()[0]
        n = c.execute("SELECT COUNT(*) FROM days").fetchone()[0]
        c.close()
        return last, n
    except Exception:
        return None, 0


def remote_db_info(cli):
    """云端库的 (最后交易日, 天数)；库不存在返回 (None, 0)"""
    probe = ("import sqlite3;c=sqlite3.connect('" + DB_REMOTE + "');"
             "print(c.execute('SELECT MAX(date) FROM days').fetchone()[0] or '-',"
             "c.execute('SELECT COUNT(*) FROM days').fetchone()[0])")
    code, out, err = sh(cli, f'python3 -c "{probe}"', quiet=True)
    parts = out.strip().split()
    if len(parts) == 2:
        return parts[0], int(parts[1])
    return None, 0


def upload(cli, sftp, with_json=False, pool_only=False, force_db=False):
    print("\n=== 上传代码与数据 ===")
    # ⛔ 关键：DB 放【独立数据目录】/srv/lhb，且【两批文件各自显式带路径】。
    #    若 DB 放 /opt/lhb/data/，会命中 paramiko 的 known_hosts 误报告警
    #    （"path … known_hosts mismatch"），易被误判为传输损坏。
    #    显式路径还有个好处：DB 位置与 REMOTE_ROOT 解耦，将来搬代码目录不动数据。
    for d in [f"{REMOTE_ROOT}/scripts", f"{REMOTE_ROOT}/daily", f"{WEB_ROOT}/lhb"]:
        sh(cli, f"mkdir -p {d}", quiet=True)
    prepare_data_dir(cli)
    if with_json:
        sh(cli, f"mkdir -p {REMOTE_ROOT}/daily/lhb_seats", quiet=True)

    # ---------- 上传前：云端现有文件留底（人工维护文件的回滚点） ----------
    # ⚠️ 本机 → 云端是**单向覆盖**。CSV 席位池在云端可能被人工改过，
    #    覆盖前必须留底，否则改动不可恢复。
    sh(cli, f"[ -f {REMOTE_ROOT}/席位跟踪池.csv ] && "
            f"cp -f {REMOTE_ROOT}/席位跟踪池.csv {REMOTE_ROOT}/席位跟踪池.bak.csv "
            f"&& echo 'csv backup ok' || true")
    remote_find_old_db(cli)

    # ---------- 第一批：脚本 + 人工维护文件 ----------
    files = [
        (os.path.join(ROOT, "scripts", "lhb_seat_track.py"), f"{REMOTE_ROOT}/scripts/lhb_seat_track.py"),
        (os.path.join(ROOT, "scripts", "build_lhb_dashboard.py"), f"{REMOTE_ROOT}/scripts/build_lhb_dashboard.py"),
        # ⚠️ 2026-09-18 第 30 轮：存储层改 SQLite 单库，lhb_store.py 是必需模块
        (os.path.join(ROOT, "scripts", "lhb_store.py"), f"{REMOTE_ROOT}/scripts/lhb_store.py"),
        (os.path.join(ROOT, "席位跟踪池.csv"), f"{REMOTE_ROOT}/席位跟踪池.csv"),
    ]
    # 人工情报文件：存在才传（席位实时分析结论）
    for opt in ("云端数据.md",):
        p = os.path.join(ROOT, opt)
        if os.path.isfile(p):
            files.append((p, f"{REMOTE_ROOT}/{opt}"))
        else:
            print(f"  [跳过] 本机无 {opt}（云端保留旧版）")

    # ⚠️ 主数据源 = SQLite 单库
    #    2026-09-18 起取代「每日一个 JSON」。上传前必须 checkpoint WAL，
    #    否则 -wal 里未落主库的事务会丢（云端看到的是旧数据）。
    db_local = os.path.join(ROOT, "data", "lhb.db")
    have_db = os.path.isfile(db_local) and not pool_only
    if pool_only:
        print("  [--pool] 只上传代码与席位池，跳过主库")
    elif have_db:
        print("  " + local_wal_checkpoint(db_local))
        # ⛔ 防「数据倒退」：云端每天 17:10 在跑，本地若没有下行同步就会落后。
        #    盲目覆盖会把云端新数据打回旧快照
        #    （2026-09-24 实测：本地 09-17 / 19 天，云端 09-24 / 24 天）。
        if not force_db:
            cl_last, cl_n = local_db_info(db_local)
            rm_last, rm_n = remote_db_info(cli)
            if rm_last and rm_last != "-" and cl_last and rm_last > cl_last:
                print(f"  ⛔ 已拒绝上传主库 —— 云端 {rm_last}（{rm_n} 天）比本地 {cl_last}（{cl_n} 天）更新")
                print(f"     覆盖会丢失云端 {cl_n} ~ {rm_n} 天之间的数据。确需强推请加 --force-db")
                have_db = False
            else:
                print(f"  主库新旧检查：本地 {cl_last}（{cl_n} 天）/ 云端 {rm_last}（{rm_n} 天）→ 放行")
    else:
        print("  [警告] 未找到 data/lhb.db，将回退上传历史 JSON")

    # 兜底：历史 json 默认**不传**（DB 已是唯一真身）；需回滚时加 --json
    json_n = 0
    seat_dir = os.path.join(LOCAL_DAILY, "lhb_seats")
    if with_json and os.path.isdir(seat_dir):
        for fn in sorted(os.listdir(seat_dir)):
            if fn.endswith(".json"):
                files.append((os.path.join(seat_dir, fn), f"{REMOTE_ROOT}/daily/lhb_seats/{fn}"))
                json_n += 1

    # 看板与主题（当前版本，作为初始状态）
    # ⚠️ --pool 模式跳过：云端产物由云端 cron 生成，本地产物可能已落后
    #    （2026-09-24 实测本地 html 停在 09-18，上传会把云端新版打回旧版）
    if not pool_only:
        for name, remote_name in [("lhb_dashboard.html", "lhb_dashboard.html"),
                                  ("lhb_theme.css", "lhb_theme.css"),
                                  ("lhb_ui.js", "lhb_ui.js")]:
            p = os.path.join(LOCAL_DAILY, name)
            if os.path.isfile(p):
                files.append((p, f"{REMOTE_ROOT}/daily/{remote_name}"))
    else:
        print("  [--pool] 跳看板产物（云端 cron 会自行生成）")

    ok = 0
    for local, remote in files:
        if not local or not os.path.isfile(local):
            print(f"  [跳过] 不存在 {local or remote}")
            continue
        sftp.put(local, remote + ".tmp")
        sftp.posix_rename(remote + ".tmp", remote)
        ok += 1
        print(f"  ✓ {os.path.basename(local)}  ({os.path.getsize(local)/1024:.1f}KB)")

    # ---------- 第二批：主库（先写临时文件再原子替换，避免云端读到半个库） ----------
    if have_db:
        sftp.put(db_local, DB_REMOTE + ".tmp")
        sftp.posix_rename(DB_REMOTE + ".tmp", DB_REMOTE)
        # 权限：root:www-data 664 → root 可写、www-data 可读写（setgid 目录已保组继承）
        sh(cli, f"chgrp www-data {DB_REMOTE} 2>/dev/null; chmod 664 {DB_REMOTE}", quiet=True)
        ok += 1
        print(f"  ✓ lhb.db  ({os.path.getsize(db_local)/1024:.1f}KB) → {DB_REMOTE}")
        sh(cli, f"rm -f {DB_REMOTE}-wal {DB_REMOTE}-shm", quiet=True)  # 清掉可能残留的旧 WAL

    if json_n:
        print(f"  （含历史 JSON 兜底 {json_n} 个）")

    # run_daily.sh
    sh(cli, f"cat > {REMOTE_ROOT}/run_daily.sh << 'RUNEOF'\n{RUN_SH}RUNEOF", quiet=True)
    sh(cli, f"chmod +x {REMOTE_ROOT}/run_daily.sh", quiet=True)
    print(f"  ✓ run_daily.sh（已置可执行）")
    print(f"\n共上传 {ok + 1} 个文件")

    # ---------- 上传后核对：云端 DB 统计须与本机一致 ----------
    if have_db:
        local_st = db_survey(db_local)
        print("\n--- 数据库核对（本机） ---")
        print(f"  {local_st}")
        print("--- 数据库核对（云端） ---")
        probe = (
            "python3 -c \"import sqlite3,json,os;"
            f"c=sqlite3.connect('{DB_REMOTE}');"
            "r={t:c.execute('SELECT COUNT(*) FROM '+t).fetchone()[0] "
            "for t in ['days','depts','seats','day_seats','ops','unmatched','multi_day','stock_chg']};"
            "r['last_day']=c.execute('SELECT MAX(date) FROM days').fetchone()[0];"
            f"r['size_mb']=round(os.path.getsize('{DB_REMOTE}')/1048576,2);print(json.dumps(r))\""
        )
        sh(cli, probe)
        print("\n--- 云端文件清单 ---")
        sh(cli, f"ls -lh {REMOTE_ROOT}/ | grep -v '^total'; echo '--- {DATA_DIR} ---'; ls -lh {DATA_DIR}/")


def install_cron(cli):
    print("\n=== 安装定时任务 ===")
    cron_line = f"{CRON_SCHEDULE} cd {REMOTE_ROOT} && {REMOTE_ROOT}/run_daily.sh\n"
    # 幂等：先删旧任务（含旧路径 /opt/kanban 与 stargate 脚本），再加新的
    sh(cli, "crontab -l 2>/dev/null "
            "| grep -v 'lhb/run_daily.sh' "
            "| grep -v 'kanban/run_daily.sh' > /tmp/ct || true", quiet=True)
    sh(cli, f"echo '{cron_line.strip()}' >> /tmp/ct", quiet=True)
    sh(cli, "crontab /tmp/ct && rm -f /tmp/ct", quiet=True)
    print("当前 crontab：")
    sh(cli, "crontab -l")


def run_now(cli):
    print("\n=== 立刻手动执行一次（验证链路）===")
    # ⚠️ run_daily.sh 自带 `set -e`：任一步失败会中断，但 exec_command 仍返回 0，
    #    所以必须靠末尾的 ___DONE___ 哨兵判断是否真正跑到底。
    code, out, err = sh(cli, f"cd {REMOTE_ROOT} && bash run_daily.sh && echo '___DONE___'", timeout=900)
    ok = "___DONE___" in out
    print(f"\n执行结果：{'✅ 完整跑完' if ok else '❌ 中途失败（见 run.log）'}")
    print("\n--- run.log 末尾 ---")
    sh(cli, "tail -30 /opt/lhb/run.log")
    if not ok:
        print("\n--- 云端脚本自检 ---")
        sh(cli, f"cd {REMOTE_ROOT} && python3 -c \""
                "import sys;sys.path.insert(0,'scripts');import lhb_store as s;print('lhb_store OK',s.SCHEMA_VERSION)\"")
        sh(cli, f"cd {REMOTE_ROOT} && python3 scripts/lhb_seat_track.py --check $(TZ=Asia/Shanghai date '+%Y-%m-%d')", timeout=180)


def show_logs(cli):
    print("=== 运行日志（末尾 40 行）===")
    sh(cli, "tail -40 /opt/lhb/run.log 2>/dev/null || echo '(暂无日志)'")
    print("\n=== crontab ===")
    sh(cli, "crontab -l 2>/dev/null || echo '(无)'")
    print("\n=== 已发布文件 ===")
    sh(cli, f"ls -lh {WEB_ROOT}/lhb/")
    print("\n=== 云端主库统计 ===")
    sh(cli, "python3 -c \"import sqlite3,json;"
            "c=sqlite3.connect('/srv/lhb/lhb.db');"
            "r={t:c.execute('SELECT COUNT(*) FROM '+t).fetchone()[0] "
            "for t in ['days','depts','seats','day_seats','ops','multi_day','stock_chg']};"
            "r['last_day']=c.execute('SELECT MAX(date) FROM days').fetchone()[0];print(json.dumps(r))\"")
    sh(cli, "ls -lh /srv/lhb/")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--upload", action="store_true")
    ap.add_argument("--json", action="store_true", help="上传历史 JSON 兜底（默认不传）")
    ap.add_argument("--pool", action="store_true",
                    help="只传代码与席位跟踪池.csv，不碰主库（改席位池后用）")
    ap.add_argument("--force-db", action="store_true",
                    help="强制上传主库，即使本地比云端旧（默认拒绝，防数据倒退）")
    ap.add_argument("--cron", action="store_true")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--logs", action="store_true")
    ap.add_argument("--all", action="store_true")
    a = ap.parse_args()
    if not any([a.check, a.upload, a.cron, a.run, a.logs, a.all]):
        a.check = True

    if a.all:
        a.check = a.upload = a.cron = True

    if not os.path.isfile(KEY):
        print(f"找不到密钥 {KEY}")
        return 1

    cli = connect()
    print(f"已连接 {HOST}（{USER}）\n")
    try:
        sftp = cli.open_sftp()
        if a.check:
            check_env(cli)
        if a.upload:
            upload(cli, sftp, with_json=a.json, pool_only=a.pool, force_db=a.force_db)
        if a.cron:
            install_cron(cli)
        if a.run:
            run_now(cli)
        if a.logs:
            show_logs(cli)
        sftp.close()
    finally:
        cli.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
