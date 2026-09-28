# -*- coding: utf-8 -*-
"""
看板推送脚本 push_kanban.py
============================
把本机生成的看板文件上传到 Lighthouse 服务器（111.230.143.251）。

用法:
    python scripts/deploy/push_kanban.py              # 推送主入口 + 最新详细看板 + 龙虎榜
    python scripts/deploy/push_kanban.py --entry      # 只推主入口（仍会自动带最新详细看板）
    python scripts/deploy/push_kanban.py --lhb        # 只推龙虎榜
    python scripts/deploy/push_kanban.py --main       # 只推主看板跳转存根（自动带最新详细看板）
    python scripts/deploy/push_kanban.py --no-snapshots   # 不推详细看板快照（仅活页文件）
    python scripts/deploy/push_kanban.py --all-snapshots  # 推全部历史详细看板（体积大）
    python scripts/deploy/push_kanban.py --dry-run    # 只列出要传什么，不真传

依赖: paramiko  (pip install paramiko)

⭐ 上传逻辑（2026-09-23 用户固化，长期有效，勿改）
  ┌ 主入口  日 entry.html                      → 远端 /entry.html          【活页·每次覆盖，只保留最新一份】
  ├ 详细看板 daily/{date}/dashboard.html      → 远端 /{date}/dashboard.html【每日独立·**永不覆盖**】
  ├ 跳转存根 daily/dashboard.html             → 远端 /dashboard.html       【保云端旧链接，指向最新一日】
  └ 龙虎榜   daily/lhb_*                      → 远端 /lhb/*
  ⇒ 即「**主入口 + 每日单独一个 html**」：主入口单文件滚动更新；详细看板按交易日各自成文、历史不覆盖。
  ⚠️ 因此**「推主入口 / 主看板」时自动附带最新一日详细看板**（否则主入口日期选择器跳转 404）。

设计要点:
  - 走 SSH/SFTP，无需在服务器上再跑任何服务
  - 上传后自动 gzip 已开启(nginx 侧)，无需手动压缩
  - 原子替换：先传 .tmp 再 mv，避免手机端读到半个文件
"""
import os
import sys
import argparse

HOST = "111.230.143.251"
PORT = 22
USER = "root"
KEY_PATH = os.path.expanduser("~/.ssh/lighthouse_kanban")
REMOTE_ROOT = "/var/www/kanban"

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DAILY = os.path.join(ROOT, "daily")

# (本机路径, 远端相对路径)
# ⚠️ 2026-09-18 回滚：三页架构（detail/holdings）已废弃，单页看板为唯一入口
# ⚠️ 主看板含持仓段（只保留技术结构位，无成本数值）—— 分享前请自行确认
# ⭐ 2026-09-23 用户固化上传逻辑：见文件头「⭐ 上传逻辑」——主入口覆盖、详细看板每日独立不覆盖
TARGETS = {
    # 「跳转存根」：≈1.1 KB，meta refresh 指向最新一日详细看板 → 保住云端旧链接 /dashboard.html
    "main": [
        (os.path.join(DAILY, "dashboard.html"), "dashboard.html"),
    ],
    # ⭐ 主入口落地页（活页）：**只有最新一版，每次覆盖**
    #   跳转关系：entry.html 顶部「详细看板」日期选择器 → {date}/dashboard.html（每日独立快照）
    #   ⇒ 推 entry / main 时**必须**连带最新一日快照，否则历史跳转 404（由 need_snap 自动保证）
    "entry": [
        (os.path.join(DAILY, "entry.html"), "entry.html"),
    ],
    "lhb": [
        (os.path.join(DAILY, "lhb_dashboard.html"), "lhb/lhb_dashboard.html"),
        (os.path.join(DAILY, "lhb_theme.css"), "lhb/lhb_theme.css"),
        (os.path.join(DAILY, "lhb_ui.js"), "lhb/lhb_ui.js"),
    ],
}


def snapshot_jobs():
    """详细看板每日独立产物：daily/{date}/dashboard.html → 远端 {date}/dashboard.html。
    ⭐ **永不覆盖**——每个交易日各自成文，历史日期重新推送也是同路径同内容。
    默认只推**最新 1 个交易日**（主入口日期选择器的当前跳转目标）；
    历史补推用 --all-snapshots。"""
    import glob
    out = []
    for d in sorted(glob.glob(os.path.join(DAILY, "20*"))):
        f = os.path.join(d, "dashboard.html")
        if os.path.isfile(f):
            date = os.path.basename(d)
            out.append((date, f, f"{date}/dashboard.html"))
    return out


def human(n):
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f}{u}"
        n /= 1024
    return f"{n:.1f}TB"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--main", action="store_true", help="只推主看板（详细看板活页）")
    ap.add_argument("--entry", action="store_true", help="只推主入口落地页")
    ap.add_argument("--lhb", action="store_true", help="只推龙虎榜看板")
    ap.add_argument("--snapshots", action="store_true",
                    help="附带推每日详细看板（默认已开启，保留此开关仅为向后兼容）")
    ap.add_argument("--no-snapshots", action="store_true",
                    help="不推详细看板快照（仅推活页文件；一般不使用）")
    ap.add_argument("--all-snapshots", action="store_true", help="推全部历史详细看板（体积大，慎用）")
    ap.add_argument("--dry-run", action="store_true", help="只列出待传文件")
    args = ap.parse_args()

    if args.main or args.entry or args.lhb:
        groups = [g for g, on in (("main", args.main), ("entry", args.entry), ("lhb", args.lhb)) if on]
    else:
        groups = ["main", "entry", "lhb"]

    jobs = []
    for g in groups:
        for local, remote in TARGETS[g]:
            if os.path.isfile(local):
                jobs.append((local, remote))
            else:
                print(f"  [跳过] 本机不存在: {local}")

    # ⭐ 详细看板快照：**每日独立、永不覆盖**；默认随任何一次推送一并带上最新一日
    #   （主入口日期选择器 → {date}/dashboard.html；不带则云端跳转 404）
    #   2026-09-23 起：**默认开启**（含 --entry 单独推送的场景），--no-snapshots 可关闭
    need_snap = not args.no_snapshots
    if need_snap:
        snaps = snapshot_jobs()
        if not args.all_snapshots and snaps:
            snaps = snaps[-1:]          # 默认只推最新一日
        for _date, local, remote in snaps:
            jobs.append((local, remote))
        if snaps:
            if args.all_snapshots:
                print(f"  附带详细看板 {len(snaps)} 个交易日（全量历史，永不覆盖）")
            else:
                print(f"  附带详细看板最新一日：{snaps[-1][0]}（每日独立文件，永不覆盖）")

    if not jobs:
        print("没有可推送的文件。")
        return 1

    print(f"待推送 {len(jobs)} 个文件 → {HOST}:{REMOTE_ROOT}")
    for local, remote in jobs:
        print(f"  {human(os.path.getsize(local)):>8}  {os.path.basename(local)} → {remote}")

    if args.dry_run:
        print("\n[dry-run] 未实际传输。")
        return 0

    try:
        import paramiko
    except ImportError:
        print("\n缺少 paramiko，请先安装：")
        print("  C:\\Users\\xc92\\.workbuddy\\binaries\\python\\versions\\3.13.12\\python.exe -m pip install paramiko")
        return 2

    if not os.path.isfile(KEY_PATH):
        print(f"\n找不到密钥 {KEY_PATH}，请先完成密钥配置。")
        return 3

    cli = paramiko.SSHClient()
    cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    print(f"\n连接 {HOST} ...")
    cli.connect(HOST, port=PORT, username=USER, key_filename=KEY_PATH, timeout=15)
    sftp = cli.open_sftp()

    # 确保远端目录存在
    for remote in {os.path.dirname(r) for _, r in jobs if os.path.dirname(r)}:
        path = f"{REMOTE_ROOT}/{remote}"
        try:
            sftp.stat(path)
        except IOError:
            print(f"  建目录 {path}")
            sftp.mkdir(path)

    ok = 0
    for local, remote in jobs:
        tmp = f"{REMOTE_ROOT}/{remote}.tmp"
        final = f"{REMOTE_ROOT}/{remote}"
        size = os.path.getsize(local)
        sftp.put(local, tmp)
        # 原子替换
        sftp.posix_rename(tmp, final)
        sftp.chmod(final, 0o644)
        print(f"  ✓ {os.path.basename(local)} ({human(size)})")
        ok += 1

    sftp.close()
    cli.close()
    print(f"\n完成 {ok}/{len(jobs)}。")
    print(f"  主入口（最新）：  http://{HOST}/entry.html")
    snaps = snapshot_jobs()
    if snaps:
        print(f"  详细看板（最新）：http://{HOST}/{snaps[-1][2]}")
        print(f"  历史看板（不覆盖）：http://{HOST}/{{date}}/dashboard.html   已有 {len(snaps)} 个交易日")
    print(f"  兼容旧链接：      http://{HOST}/dashboard.html  → 跳转存根")
    return 0


if __name__ == "__main__":
    sys.exit(main())
