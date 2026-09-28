# 视觉层交付（D1）—— 与现网完全一致的看板渲染代码

> 原则：**一行视觉逻辑都不改**。本目录是交付时刻 `scripts/` 的快照副本；P0 仓库化后以 git 仓库为准，本目录用于独立审阅与直接部署。

## 文件清单

| 文件 | 角色 | 视觉相关改动 |
|---|---|---|
| `build_dashboard.py` | 详细看板唯一完整产物（6 段：市场总览/大盘结构/评分与情绪/题材与资金/明日推演/历史总览） | 仅 ROOT 参数化（env `KANBAN_ROOT` 或 `--root`，默认值=本地现网路径） |
| `build_entry.py` | 主入口（覆盖式，唯一最新入口） | 零改动 |
| `md_to_replay.py` | 05md → replay.html 融合渲染 | 零改动 |
| `_fix_block_order.js` | 搬移段序（「大盘结构分析」块），渲染链固定步骤 | 零改动（待并入 md_to_replay，见迁移指南待办） |

配套输入契约见 `../docs/数据契约-渲染器输入.md`；打分依赖 `sector_taxonomy.py`（46 L1 归口，随 git 仓库走）。

## 使用

```bash
KANBAN_ROOT=/srv/kanban python3 visual/build_dashboard.py          # 或 --root /srv/kanban
KANBAN_ROOT=/srv/kanban python3 visual/md_to_replay.py --dates 2026-09-28
KANBAN_ROOT=/srv/kanban python3 visual/build_entry.py
```

## 「和现在一样」的验收方法（P3 双跑期每日执行）

1. 同一数据包，本地与云端各渲染一次 → `diff` 两份 HTML **必须为空**（时间戳行除外）；
2. 375 / 768 / 1440 px 三档 viewport 截图比对（沿用现有移动端口径）；
3. 服务器须安装与本地一致的中文字体（或确认 CSS 字体栈回退可接受），否则字宽差异会造成像素级漂移——这是唯一允许的已知差异，出现时按"字体问题"处理，不改代码。
