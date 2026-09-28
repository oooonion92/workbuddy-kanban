# -*- coding: utf-8 -*-
"""
盯盘复盘看板生成器 build_dashboard.py
读取 daily/YYYYMMDD/ 下的复盘数据（优先 复盘数据.json，回退历史 md/html 提取），
聚合渲染 daily/dashboard.html —— 唯一看板入口，四分区：
  ① 今日决策（最新复盘决策摘要）② 当日复盘（九段式全文）
  ③ 趋势洞察（跨日图表）④ 历史总览（评分总表）
风格：券商终端浅色（红涨绿跌，A股语义）。最新复盘正文自动换肤融合。
用法：python build_dashboard.py [--root D:/Work buddy project/每日盯盘]
"""
import os
import re
import sys
import json
import html as _html

ROOT = os.environ.get("KANBAN_ROOT", r"D:\Work buddy project\每日盯盘")
if "--root" in sys.argv:
    ROOT = sys.argv[sys.argv.index("--root") + 1]

DAILY = os.path.join(ROOT, "daily")
OUT = os.path.join(DAILY, "dashboard.html")          # 唯一看板入口
# 板块池（总看板热力图用）：归并同一方向的多种写法
SECTOR_POOL = ["PCB/覆铜板", "创新药/CXO", "AI/算力", "半导体/存储/封测", "光模块/CPO",
               "光纤光缆", "机器人/宇树链", "军工/航天", "金属/贵金属/稀土", "电力/风电",
               "农林牧渔/粮食", "消费/CPI"]

# 热力图行数上限（2026-09-21 换用全量打分后引入：每日 ≈285 个板块，无法全画）
HEAT_TOP_N = 18
# 复盘融合源：优先 .workbuddy/tmp/replay_latest.html（每日复盘任务写入，不进 daily 目录，
# 保证 daily 下只有一个 HTML 入口 = dashboard.html），回退 daily/YYYYMMDD/05_复盘与明日预案.html（兼容历史）
TMP = os.path.join(ROOT, ".workbuddy", "tmp")
REPLAY_SRC = os.path.join(TMP, "replay_latest.html")

# ---------- 换肤：深色终端 → 浅色券商（红涨绿跌） ----------
SKIN = [
    ("#0f0f23", "#F5F6F8"), ("#1a1a2e", "#FFFFFF"), ("#14142a", "#F8F9FB"),
    ("#1b1b38", "#EFF1F5"), ("#232046", "#EFEDFA"), ("#20203a", "#F7F8FA"),
    ("#2d2d44", "#E4E7EB"), ("#e2e8f0", "#14171A"), ("#94a3b8", "#6B7280"),
    ("#cbd5e1", "#4B5563"), ("#5b6b85", "#9CA3AF"), ("#5f5e5a", "#9CA3AF"),
    ("rgba(45,45,68,.55)", "rgba(228,231,235,.8)"),
    ("#00d4aa", "#E03131"), ("#ff4757", "#00A870"), ("#ffa502", "#F59E0B"),
    ("#4ade80", "#E03131"), ("#60a5fa", "#2563EB"), ("#a5b4fc", "#7C5CFC"),
    ("#667eea", "#7C5CFC"), ("#764ba2", "#6D4BC5"), ("#3b82f6", "#2563EB"),
    ("#22c55e", "#00A870"), ("#c7d2fe", "#6D5BD0"), ("#0f6e56", "#0E8A5F"),
    ("rgba(102,126,234,.06)", "rgba(124,92,252,.07)"),
    ("rgba(102,126,234,.25)", "rgba(124,92,252,.28)"),
    ("rgba(0,212,170,.15)", "rgba(224,49,49,.1)"),
    ("rgba(0,212,170,.4)", "rgba(224,49,49,.3)"),
    ("rgba(255,165,2,.12)", "rgba(245,158,11,.1)"),
    ("rgba(255,165,2,.4)", "rgba(245,158,11,.3)"),
    ("rgba(59,130,246,.15)", "rgba(37,99,235,.08)"),
    ("rgba(59,130,246,.35)", "rgba(37,99,235,.25)"),
    ("rgba(34,197,94,.12)", "rgba(0,168,112,.08)"),
    ("rgba(34,197,94,.3)", "rgba(0,168,112,.25)"),
]
# 语义类覆盖（红涨绿跌；tag-green 解读标签保持绿）
SEMANTIC_OVERRIDE = """
.up{color:#E03131}.down{color:#00A870}.hold{color:#B45309}
.purple{color:#7C5CFC}.blue{color:#2563EB}.green{color:#E03131}
.sig-strong{background:rgba(224,49,49,.08);color:#C92A2A;border:1px solid rgba(224,49,49,.3)}
.sig-mid{background:rgba(245,158,11,.1);color:#B45309;border:1px solid rgba(245,158,11,.3)}
.sig-gate{background:rgba(37,99,235,.08);color:#2563EB;border:1px solid rgba(37,99,235,.25)}
.tag-blue{background:rgba(37,99,235,.08);color:#2563EB;border:1px solid rgba(37,99,235,.25)}
.tag-green{background:rgba(0,168,112,.08);color:#0E8A5F;border:1px solid rgba(0,168,112,.25)}
.tag-purple{background:rgba(124,92,252,.08);color:#7C5CFC;border:1px solid rgba(124,92,252,.25)}
.card h2{font-size:17px;font-weight:700;margin-bottom:12px;display:flex;align-items:center;gap:8px;color:#14171A}
.tag{margin-left:8px}
/* ---------- 字号阶梯统一（2026-09-21 用户反馈「有的很大，有的正常」）----------
   本块在 ext.style 之后、BASE_CSS 之前生效（BASE_CSS 未定义 .big → 本规则胜出，两页同享）。
   目标阶梯：h1 21 / KPI 大数 20 / 区块标题名 17 = 闸门陈述 17 / 卡片 h2 15 / 正文与表格 13.5 / 表头·说明 12 / 小标签 11
   修订点：闸门陈述原为 20px，与页面 h1（21px）和 KPI 大数（20px）几乎同大，层级混乱 → 压到 17px 与区块标题同级。 */
.hero-right .big{font-size:17px}
"""


def skin_light(text):
    """把深色终端风格 HTML/样式转成浅色券商风格（字符串级色值替换）"""
    for a, b in SKIN:
        text = text.replace(a, b)
    return text


def gate_color(g):
    """闸门颜色识别 v3 语义版（2026-09-07，用户指正 9/4"🔴 退潮加速"被误标红色）：
    图例语义：红=可开仓 / 黄=仅验证 / 蓝=防守。
    1) 文字「红/黄/蓝路径」最优先（8月标准格式与 9/2）
    2) emoji 不直接决定颜色——🔴 是作者警示标记，不是"红路径可开仓"：
       语义判定：未确认→黄；退潮含企稳/修复→黄、退潮无企稳→蓝；确认/确立→红
    3) 孤立 emoji 兜底：🟡🟠→黄、🔵→蓝、🔴→蓝（警示默认防守）、🟢→绿"""
    g = (g or "").strip()
    if not g:
        return None  # 无数据
    for word, c in (("蓝路径", "#2563EB"), ("黄路径", "#F59E0B"), ("红路径", "#E03131")):
        if word in g[:10]:
            return c
    if "未确认" in g:
        return "#F59E0B"          # 如 9/7"修复日确立（…突破未确认）"→ 仅验证
    if "退潮" in g[:12]:
        if "企稳" in g[:14] or "修复" in g[:14]:
            return "#F59E0B"      # 如 9/3"退潮中继·弱企稳"→ 仅验证
        return "#2563EB"          # 如 9/4"退潮加速（破位+晋级率崩塌）"→ 防守
    if ("确认" in g or "确立" in g) and not g.lstrip().startswith("未"):
        return "#E03131"
    emoji_map = {"🟡": "#F59E0B", "🟠": "#F59E0B", "🔵": "#2563EB", "🔴": "#2563EB", "🟢": "#0E8A5F"}
    for e, c in emoji_map.items():
        if g.startswith(e):
            return c
    head = g.lstrip("🟡🟠🔴🔵🟢🟣⚪⚫✅⚠ \t·—－-0123456789.")[:6]
    if "黄" in head:
        return "#F59E0B"
    if "红" in head:
        return "#E03131"
    if "蓝" in head:
        return "#2563EB"
    return None  # 不识别


def gate_label(g):
    """闸门短标签：直接由 gate_color 结论反推，保证与色块颜色永远一致。"""
    c = gate_color(g)
    return {"#E03131": "红", "#F59E0B": "黄", "#2563EB": "蓝"}.get(c, (g or "-")[:1])


def trim_records(records, field):
    """返回 records 中指定字段齐全的最长连续段。
    连续定义：原 records 列表索引相邻 + 日期差=1 或周五→周一(差=3)。
    字段齐全：值非 None/非空字符串/非空 dict。"""
    from datetime import date

    def is_valid(r):
        v = r.get(field)
        if v is None:
            return False
        if isinstance(v, str) and not v.strip():
            return False
        if isinstance(v, dict) and not v:
            return False
        return True

    valid = [(i, r) for i, r in enumerate(records) if is_valid(r)]
    if len(valid) <= 1:
        return [r for _, r in valid]
    runs = [[valid[0]]]
    for j in range(1, len(valid)):
        prev_i, prev_r = valid[j - 1]
        cur_i, cur_r = valid[j]
        date_ok = False
        if cur_i == prev_i + 1:
            d0 = date.fromisoformat(prev_r["date"])
            d1 = date.fromisoformat(cur_r["date"])
            gap = (d1 - d0).days
            if gap == 1 or (gap == 3 and d0.weekday() == 4):
                date_ok = True
        if date_ok:
            runs[-1].append(valid[j])
        else:
            runs.append([valid[j]])
    longest = max(runs, key=len)
    return [r for _, r in longest]


# ---------- 图表数据：剔除 None + 取最长连续段（允许跨周末 ≤3 天） ----------
def trim_series(points):
    """points = [(date_str, value)]；过滤 None + 切除开头孤点（保留最长连续段）。
    连续定义：相邻 gap=1（相邻日）或 gap=3 且前一日是周五（跨周末）；其他视为断档。
    返回清洗后的 (date, value) 列表。"""
    from datetime import date
    pts = [(d, v) for d, v in points if v is not None]
    if len(pts) <= 1:
        return pts
    runs = [[pts[0]]]

    def is_consecutive(d0, d1):
        gap = (date.fromisoformat(d1) - date.fromisoformat(d0)).days
        if gap == 1:
            return True
        if gap == 3 and date.fromisoformat(d0).weekday() == 4:  # 周五→周一
            return True
        return False

    for i in range(1, len(pts)):
        if is_consecutive(pts[i - 1][0], pts[i][0]):
            runs[-1].append(pts[i])
        else:
            runs.append([pts[i]])
    return max(runs, key=len)


# ---------- 板块名归一化（防止重复行/交集概念并列） ----------
SECTOR_ALIAS = {
    # 同一方向多种名称 → 标准名
    # ⚠️ 2026-09-21 口径拆分：原名「光通信/CPO」实际只映射到「光纤光缆」11 只，
    #    而光模块龙头（中际旭创/新易盛/天孚/太辰光/光迅…）全在「网络接配及塔设」里
    #    → 名字与成分不符（用户体感落差即源于此）。现拆为两个方向：
    #      「光模块/CPO」＝网络接配及塔设 + 光通信/光模块/光器件（算力属性）
    #      「光纤光缆」  ＝光纤光缆（通信周期/海缆属性）
    "光通信/通信设备": "光模块/CPO",
    "光通信": "光模块/CPO",
    "光通信/CPO": "光模块/CPO",
    "CPO概念": "光模块/CPO",
    "CPO": "光模块/CPO",
    "光模块": "光模块/CPO",
    "光器件": "光模块/CPO",
    "网络接配及塔设": "光模块/CPO",
    "光纤光缆": "光纤光缆",
    "PCB": "PCB/覆铜板",
    "PCB概念": "PCB/覆铜板",
    "PCB/覆铜板": "PCB/覆铜板",
    "PCB/算力硬件": "PCB/覆铜板",
    "PCB/CPO": "PCB/覆铜板",
    "覆铜板": "PCB/覆铜板",
    "HDI": "PCB/覆铜板",
    "电子化学品/PCB材料": "PCB/覆铜板",
    "半导体": "半导体/存储/封测",
    "半导体/存储": "半导体/存储/封测",
    "存储芯片": "半导体/存储/封测",
    "半导体/存储/封测": "半导体/存储/封测",
    "封测": "半导体/存储/封测",
    "种植业": "农林牧渔/粮食",
    "种植业/农林牧渔（粮食）": "农林牧渔/粮食",
    "农林牧渔": "农林牧渔/粮食",
    "种业": "农林牧渔/粮食",
    "养殖业": "农林牧渔/粮食",
    "化肥/农用化工": "农林牧渔/粮食",
    "医药": "创新药/CXO",
    "医药/CXO": "创新药/CXO",
    "CXO": "创新药/CXO",
    "创新药/医药": "创新药/CXO",
    "创新药": "创新药/CXO",
    "机器人": "机器人/宇树链",
    "机器人/宇树链": "机器人/宇树链",
    "宇树": "机器人/宇树链",
    "减速机": "机器人/宇树链",
    "激光设备/军工": "军工/航天",
    "军工": "军工/航天",
    "国防军工": "军工/航天",
    "军工/航天": "军工/航天",
    "商业航天": "军工/航天",
    "航天装备": "军工/航天",
    "贵金属": "金属/贵金属/稀土",
    "贵金属/黄金": "金属/贵金属/稀土",
    "黄金（避险）": "金属/贵金属/稀土",
    "黄金/避险": "金属/贵金属/稀土",
    "稀土永磁": "金属/贵金属/稀土",
    "稀土/小金属": "金属/贵金属/稀土",
    "稀有金属": "金属/贵金属/稀土",
    "铝/有色": "金属/贵金属/稀土",
    "银行（防御）": "银行（防御）",
    "银行": "银行（防御）",
    "银行(防御)": "银行（防御）",     # 兼容英文括号
    "银行/大金融": "银行（防御）",
    "煤炭": "煤炭/能源（防御）",
    "煤炭/能源(防御)": "煤炭/能源（防御）",  # 兼容英文括号
    "AI算力": "AI/算力",
    "AI/算力": "AI/算力",
    "AI/算力硬件": "AI/算力",
    "AI算力硬件": "AI/算力",
    "人工智能": "AI/算力",
    "玻璃基板": "玻璃基板",
    "粮食/农业/化肥": "农林牧渔/粮食",
    "煤炭/焦炭": "煤炭/能源（防御）",
    "医药生物（疫苗/创新药/CXO）": "创新药/CXO",
    "消费": "消费/CPI",
    "农业/粮食": "农林牧渔/粮食",
    "农业/种植": "农林牧渔/粮食",
    "农业/种业链": "农林牧渔/粮食",
    "农业/化肥": "农林牧渔/粮食",
    "化肥/农资": "农林牧渔/粮食",
    "化工/化肥": "农林牧渔/粮食",
    "粮食": "农林牧渔/粮食",
    "液冷": "AI/算力",
    "液冷/AI硬件": "AI/算力",
    "AI硬件": "AI/算力",
    "AI钱包": "AI/算力",
    "数字货币": "AI/算力",
    "数字货币/AI钱包": "AI/算力",
    "IT服务": "AI/算力",
    "计算机/IT服务": "AI/算力",
    # 风电 → 电力/风电（与"电力"标准池行对齐，避免 风电设备/电力/风电/电力 三行分裂）
    "风电设备": "电力/风电",
    "电力/风电": "电力/风电",
    "风电": "电力/风电",
    "电力": "电力/风电",
    "电力/绿电": "电力/风电",
    "电网设备/特高压": "电力/风电",
    # 券商 → 归入银行（防御）大金融
    "券商": "银行（防御）",
    "银行/券商": "银行（防御）",
    # 黄金变体补充
    "黄金": "金属/贵金属/稀土",
    "黄金/有色": "金属/贵金属/稀土",
    "工业金属/铜": "金属/贵金属/稀土",
    "金属/铜钽钨": "金属/贵金属/稀土",
    "锂电": "金属/贵金属/稀土",
    "锂电/碳酸锂": "金属/贵金属/稀土",
    "碳酸锂": "金属/贵金属/稀土",
    "贵金属/黄金": "金属/贵金属/稀土",
    "黄金/贵金属": "金属/贵金属/稀土",
    "创新药/CXO": "创新药/CXO",
    # ---- 池外独立方向归并（2026-09-16 补齐：这些方向历史出现过多次，
    #      若不归并会各占一行，导致矩阵行数膨胀 + 同一方向分裂成多行） ----
    "AI教育": "AI应用/传媒",
    "AI教育/DeepSeek": "AI应用/传媒",
    "AI教育/算力调度": "AI应用/传媒",
    "AI软件/企业应用": "AI应用/传媒",
    "出版传媒": "AI应用/传媒",
    "出版传媒/AI应用": "AI应用/传媒",
    "影视短剧": "AI应用/传媒",
    "计算机/软件": "AI应用/传媒",
    "折叠屏/AI眼镜": "消费电子/折叠屏",
    "折叠屏/消费电子": "消费电子/折叠屏",
    "消费电子": "消费电子/折叠屏",
    "汽车零部件": "汽车零部件",
    "地产链": "地产链",
    "房地产": "地产链",
    "化学原料/无机盐": "化工/材料",
    "化工": "化工/材料",
    # 2026-09-21 补：全量打分按「细分行业」聚合后暴露的漏别名
    # （不补会出现「同一方向两行」—— 口径文件三节明确要求排查）
    "铜": "金属/贵金属/稀土",
    "铝": "金属/贵金属/稀土",
    "稀土": "金属/贵金属/稀土",
    "钨钼": "金属/贵金属/稀土",
    "小金属": "金属/贵金属/稀土",
    "证券": "银行（防御）",
    "证券Ⅱ": "银行（防御）",
}
# 别名表按 key 长度降序固化（最长键优先，见 normalize_sector_name 说明）
_ALIAS_BY_LEN = sorted(SECTOR_ALIAS.items(), key=lambda kv: -len(kv[0]))
# 标准板块池（板块矩阵固定行数）
STANDARD_SECTOR_POOL = [
    "半导体/存储/封测", "光通信/CPO", "PCB/覆铜板", "机器人/宇树链",
    "创新药/CXO", "AI/算力", "军工/航天", "金属/贵金属/稀土",
    "农林牧渔/粮食", "消费/CPI",
    "银行（防御）", "煤炭/能源（防御）",
    "玻璃基板",
]

def normalize_sector_name(name):
    """板块名归一化（合并归纳第一步）：去括号后缀 → 全角转半角 → 精确别名 → 前后缀匹配。
    目标：把每日 JSON 里口语化的板块名（如"医药/CXO（mRNA疫苗）"）归并到标准池，
    防止同名不同写法产生大量池外新行导致热力图无限膨胀。"""
    if not name:
        return ""
    n = str(name).strip()
    # 去括号内后缀（中英文括号），如"（mRNA疫苗）"；再清残留括号
    n = re.sub(r"[（(][^（）()]*[）)]", "", n).strip()
    n = n.replace("（", "").replace("）", "").replace("(", "").replace(")", "").strip()
    if not n:
        return str(name).strip()
    # 清尾部状态词后缀，如"电力/绿电(新)"→"电力/绿电"、"化学原料/无机盐(持续)"→"化学原料/无机盐"
    # ⚠️ 2026-09-16：历史 JSON 里存在"(持续)""(新)"等口语化状态标记，不去除会各占一行
    n = re.sub(r"[（(](?:持续|新|旧|观察|跟踪|退潮|启动|二次启动|首日|补涨)[）)]\s*$", "", n).strip()
    # 清"持续/新"这类裸状态词尾巴（无括号情形）
    n = re.sub(r"(?:持续|观察|跟踪)$", "", n).strip(" /")
    if not n:
        return str(name).strip()
    # 精确匹配别名
    if n in SECTOR_ALIAS:
        return SECTOR_ALIAS[n]
    # 前缀/后缀匹配：如"人工智能（整体）"已去括号→"人工智能"→AI/算力；
    # "CPO/光通信" 以"光通信"结尾→光通信/CPO；"黄金/贵金属" 以"贵金属"结尾→金属/贵金属/稀土
    # ⚠️ 2026-09-16 修复：原实现按 dict 插入顺序遍历，短键会先命中并吞掉长键
    #   （如 "PCB/覆铜板/CPO" 被 "PCB" 先截走，结果反而漏掉更精确的 "PCB/覆铜板"）。
    #   改为按 key 长度降序匹配 —— 最长键优先，保证最精确的归并结果胜出。
    for k, v in _ALIAS_BY_LEN:
        if k and (n.startswith(k) or n.endswith(k)):
            return v
    return n


def mmdd(date_str):
    """兼容 YYYYMMDD 与 YYYY-MM-DD 两种日期格式，返回 M/D 字符串（按需补零）"""
    d = (date_str or "").replace("-", "")
    if len(d) >= 8:
        return f"{int(d[4:6])}/{int(d[6:8])}"
    return date_str


# ---------- 数据提取 ----------
def norm_num(v):
    m = re.search(r"[-+]?\d+(?:\.\d+)?", str(v))
    return float(m.group()) if m else None


def extract_from_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return {
            "date": d.get("date", ""),
            "structure": norm_num(d.get("structure_score")),
            "sentiment": norm_num(d.get("sentiment_score")),
            "sentiment_old": None,
            "total": norm_num(d.get("total_score")),
            "gate": d.get("gate", ""),
            "limit_up": norm_num(d.get("limit_up")),
            "max_board": norm_num(d.get("max_board")),
            "sectors": d.get("sectors", []),
            "sd": d.get("structure_detail", {}),
        }
    except Exception:
        return None


def extract_from_md_or_html(path):
    try:
        with open(path, encoding="utf-8") as f:
            txt = f.read()
    except Exception:
        return None
    # 日期统一为 YYYY-MM-DD 格式（与 json 保持一致），保证 records 按字符串排序就是按时间排序
    dn = os.path.basename(os.path.dirname(path))
    date_str = "{}-{}-{}".format(dn[:4], dn[4:6], dn[6:8]) if len(dn) == 8 else dn
    d = {"date": date_str, "structure": None,
         "sentiment": None, "total": None, "gate": "", "limit_up": None,
         "max_board": None, "sectors": extract_sectors_from_html(txt),
         "sentiment_old": None, "sd": {}}
    m = re.search(r"(?:情绪温度计|温度计|温度)\s*(?:≈|=|为|：|:)?\s*(\d{1,3})", txt)
    if m:
        d["sentiment_old"] = float(m.group(1))
    m = re.search(r"情绪分[（(]0-50[)）][^0-9]*?(\d{1,2})\s*分", txt)
    if m:
        d["sentiment"] = float(m.group(1))
    m = re.search(r"结构分[（(]0-50[)）][^0-9]*?(\d{1,2})\s*分", txt)
    if m:
        d["structure"] = float(m.group(1))
    m = re.search(r"总分[（(]0-100[)）][^0-9]*?(\d{1,3})\s*分", txt)
    if m:
        d["total"] = float(m.group(1))
    m = re.search(r"涨停\s*(\d{1,3})\s*家", txt)
    if m:
        d["limit_up"] = float(m.group(1))
    m = re.search(r"最高\s*(\d{1,2})\s*板", txt)
    if m:
        d["max_board"] = float(m.group(1))
    m = re.search(r"(红|黄|蓝)路径", txt)
    if m:
        d["gate"] = m.group(1) + "路径"
    return d


def extract_sectors_from_html(txt):
    """从复盘 HTML 中提取「景气板块池 TOP5」表格。
    匹配"景气板块池 TOP5"之后的第一个 <table>，逐行解析 板块名/评分/阶段/资金方向。
    没有该段则返回空列表（让矩阵留空，不填 50 占位）。"""
    m = re.search(r'景气板块池\s*TOP\d+', txt)
    if not m:
        return []
    after = txt[m.end():]
    tm = re.search(r'<table[^>]*>(.*?)</table>', after, re.S)
    if not tm:
        return []
    rows = re.findall(r'<tr[^>]*>(.*?)</tr>', tm.group(1), re.S)
    sectors = []
    for r in rows[1:]:  # 跳表头
        tds = re.findall(r'<td[^>]*>(.*?)</td>', r, re.S)
        if len(tds) < 4:
            continue
        name = re.sub(r'<[^>]+>', '', tds[0]).strip()
        sc = norm_num(tds[1])
        phase = re.sub(r'<[^>]+>', '', tds[2]).strip()
        fund_dir = re.sub(r'<[^>]+>', '', tds[3]).strip()
        if name and sc is not None:
            sectors.append({"name": name, "score": sc,
                            "phase": phase, "fund_dir": fund_dir})
    return sectors


def collect():
    records = []
    if not os.path.isdir(DAILY):
        return records
    for day in sorted(os.listdir(DAILY)):
        if not re.fullmatch(r"\d{8}", day):
            continue
        day_dir = os.path.join(DAILY, day)
        rec = None
        jpath = os.path.join(day_dir, "复盘数据.json")
        if os.path.exists(jpath):
            rec = extract_from_json(jpath)
        if rec is None:
            for name in ("05_复盘与明日预案.html", "05_复盘与明日预案.md"):
                p = os.path.join(day_dir, name)
                if os.path.exists(p):
                    rec = extract_from_md_or_html(p)
                    if rec and any(v is not None for v in
                                   (rec["structure"], rec["sentiment"], rec["total"],
                                    rec["sentiment_old"])):
                        break
                    rec = None
        if rec is None or rec.get("sentiment_old") is None:
            p4 = os.path.join(day_dir, "04_收盘初判.md")
            if os.path.exists(p4):
                r4 = extract_from_md_or_html(p4)
                if r4 and r4.get("sentiment_old") is not None:
                    if rec is None:
                        rec = r4
                    else:
                        rec["sentiment_old"] = r4["sentiment_old"]
        if rec:
            rec["date"] = rec.get("date") or day
            records.append(rec)
    records.sort(key=lambda r: r["date"])
    return records


# ---------- 成交额环比（当日 vs 前一交易日）----------
# 指数短名映射：融合源表格里写作「上证/深证/创业板/科创50」，复盘数据.json 里写作全名
IDX_SHORT = {
    "上证指数": "上证",
    "深证成指": "深证",
    "创业板指": "创业板",
    "科创50": "科创50",
    "科创板50": "科创50",
}


def load_day_json(date_str):
    """读当日 复盘数据.json（原始 dict）。

    collect() 只暴露看板需要的少数字段（见 extract_from_json），
    indices / limit_down / sentiment_detail 等需要原始 dict 的调用方走这里。
    """
    p = os.path.join(DAILY, str(date_str).replace("-", ""), "复盘数据.json")
    if not os.path.exists(p):
        return {}
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def amount_mom_map(records):
    """{指数名(全名与短名都可命中): 成交额环比 %} —— 当日 vs 前一交易日。

    规则（2026-09-21 用户要求）：指数表「成交额」列必须带与前一日的对比。
    """
    if not records or len(records) < 2:
        return {}
    cur = (load_day_json(records[-1]["date"]) or {}).get("indices", {}) or {}
    prev = (load_day_json(records[-2]["date"]) or {}).get("indices", {}) or {}
    out = {}
    for full, d in cur.items():
        a = (d or {}).get("amount_yi")
        b = (prev.get(full) or {}).get("amount_yi")
        try:
            a, b = float(a), float(b)
        except (TypeError, ValueError):
            continue
        if not b:
            continue
        mom = (a - b) / b * 100.0
        out[full] = mom
        if full in IDX_SHORT:
            out[IDX_SHORT[full]] = mom
    return out


def mom_span(p, size=12):
    """环比的 HTML 片段：红涨绿跌（A股语义）"""
    cls = "up" if p > 0 else ("down" if p < 0 else "")
    if cls:
        return f'<span class="{cls}" style="font-size:{size}px;margin-left:6px">{p:+.1f}%</span>'
    return f'<span style="font-size:{size}px;margin-left:6px;color:var(--text3)">{p:+.1f}%</span>'


def enrich_index_amount_mom(html, records):
    """给融合源指数表的「成交额」列追加 vs 前一日环比（表格原地加固，不动其他列）。

    融合源的指数表是 05 复盘里的手写 HTML，列数不固定 → 先按表头定位「成交」列，
    再逐行替换该列，避免写死列号。
    """
    if not html or len(records) < 2:
        return html
    mom = amount_mom_map(records)
    if not mom:
        return html
    m = re.search(r'<table[^>]*>.*?</table>', html, re.S)
    if not m:
        return html
    table = m.group(0)
    rows = re.findall(r'<tr[^>]*>.*?</tr>', table, re.S)
    if len(rows) < 2:
        return html
    ths = re.findall(r'<th[^>]*>.*?</th>', rows[0], re.S)
    heads = [re.sub(r'<[^>]+>', '', h).strip() for h in ths]
    amt_i = next((i for i, h in enumerate(heads) if '成交' in h), None)
    if amt_i is None:
        return html

    new_table = table
    # ① 表头标注环比
    if amt_i < len(ths) and '环比' not in heads[amt_i]:
        new_h = re.sub(r'(成交[^<]*)', r'\1（环比）', ths[amt_i], count=1)
        if new_h != ths[amt_i]:
            new_table = new_table.replace(ths[amt_i], new_h, 1)
    # ② 逐行补环比
    for r in rows[1:]:
        tds = re.findall(r'<td[^>]*>.*?</td>', r, re.S)
        if len(tds) <= amt_i:
            continue
        name = re.sub(r'<[^>]+>', '', tds[0]).strip()
        key = next((k for k in mom if k and k in name), None)
        if not key:
            continue
        new_td = re.sub(r'</td>\s*$', mom_span(mom[key]) + '</td>', tds[amt_i])
        if new_td == tds[amt_i]:
            continue
        new_table = new_table.replace(r, r.replace(tds[amt_i], new_td, 1), 1)
    return html.replace(table, new_table, 1)


def split_blocks(body):
    """按注释锚点切块：<!-- SECTION:N 标题 --> 或 <!-- ========== 标题 ========== -->。
    返回 {标题: html}"""
    parts = re.split(r'(<!--\s*(?:SECTION:\d+\s+|=+\s*)?[^=]+?(?:=+\s*)?-->)', body)
    blocks, cur = {}, None
    for p in parts:
        m = re.match(r'<!--\s*(?:SECTION:\d+\s+|=+\s*)?(.+?)\s*(?:=+\s*)?-->', p.strip())
        if m:
            cur = m.group(1).strip()
            blocks[cur] = ""
        elif cur and p.strip():
            blocks[cur] += p
    return blocks


def classify_blocks(blocks):
    """把切块按主题归类，返回 {key: html}；key: hero/structure/index/score/ladder/
    theme/watchlist/lhb/review/pool/plan"""
    km = {}
    for name, html in blocks.items():
        if "决策摘要" in name:
            km["hero"] = html
        elif "大盘结构" in name and "股指期货" not in name:
            km["structure"] = html
        elif "股指期货" in name:
            # ⚠️ 2026-09-21 修复：段名「七、大盘结构补充 · 股指期货跟踪」也含「大盘结构」，
            #    原 `elif "大盘结构" in name` 会把**当期指段覆盖缠论段** → 段二「大盘结构」
            #    静默显示期指表、当日缠论分析整块丢失（09-18 版已复现）。改为单列 key。
            km["futures"] = html
        elif "指数与市场" in name:
            km["index"] = html
        elif "双维评分" in name or "评分" in name:
            km["score"] = html
        elif "涨停梯队" in name or "连板天梯" in name:
            km["ladder"] = html
        elif "题材主线" in name:
            km["theme"] = html
        elif "自选股" in name:
            km["watchlist"] = html
        elif "龙虎榜" in name:
            continue  # 2026-08-31 起龙虎榜独立成页，看板不再承载
        elif "操作" in name:
            km["review"] = html
        elif "候选池" in name:
            km["pool"] = html
        elif "预案" in name:
            km["plan"] = html
    return km


def extract_latest_report(src_path=None):
    """从复盘提取 style / hero / 正文（已换肤浅色）。

    · `src_path` 给定时**只用该文件**（每日快照用 `daily/{date}/replay.html`，
      见 `scripts/md_to_replay.py` —— 历史日若复用最新融合源会把最新叙事套到历史日）
    · 否则：优先 `.workbuddy/tmp/replay_latest.html`，回退 `daily/{date}/replay.html`
      → 再回退 `daily/{date}/05_复盘与明日预案.html`（兼容历史）
    """
    candidates = []
    if src_path:
        candidates.append(("explicit", src_path))
    else:
        if os.path.exists(REPLAY_SRC):
            candidates.append(("replay_src", REPLAY_SRC))
        if os.path.isdir(DAILY):
            days = sorted(d for d in os.listdir(DAILY) if re.fullmatch(r"\d{8}", d))
            for day in reversed(days):
                for fn in ("replay.html", "05_复盘与明日预案.html"):
                    p = os.path.join(DAILY, day, fn)
                    if os.path.exists(p):
                        candidates.append((day, p))
                        break
                else:
                    continue
                break
    for label, p in candidates:
        try:
            txt = open(p, encoding="utf-8").read()
        except Exception:
            continue
        m = re.search(r"<style>(.*?)</style>", txt, re.S)
        style = m.group(1) if m else ""
        # hero 区：从 <div class="hero"> 到其后第一个官方锚点（通用写法，取"下一个 <!-- ========== 锚点"）。
        # ⚠️ 2026-09-16 修复：原正则写死 `(?=\n<!-- ========== 大盘结构分析)`，但十段式版式中
        #   「一、决策摘要 / 大盘结构分析」两个锚点位于 <div class="hero"> **之前**（作页头前缀），
        #   hero 之后的下一个锚点其实是「二、指数与市场概况」→ 原 lookahead 永远失配、
        #   决策摘要圆环被静默整块丢弃。现改为「到下一个锚点为止」，对版式变动免疫。
        m = re.search(r'(<div class="hero">.*?)(?=\n<!-- =+ )', txt, re.S)
        if not m:
            # 兜底：hero 之后没有任何锚点（文件截断等），取到 footer/body 结束
            m = re.search(r'(<div class="hero">.*?)(?=<div class="footer"|</div></body>|</body>)', txt, re.S)
        hero = m.group(1) if m else ""
        m = re.search(r'(<!-- ========== 大盘结构分析.*?)(?=<div class="footer"|</div></body>|</body>)', txt, re.S)
        if not m:
            m = re.search(r'(<div class="card bd-blue">.*?)(?=<div class="footer"|</div></body>|</body>)', txt, re.S)
        body = m.group(1) if m else ""
        if not body and not hero:
            continue
        # ⚠️ 2026-09-16 新增自检：任一要素提取失败都显式告警，不允许静默降级
        #   （此前 hero 正则失配 → 决策摘要圆环整块消失且无任何提示）
        _miss = [k for k, v in (("style", style), ("hero", hero), ("body", body)) if not v]
        if _miss:
            print(f"  ⚠️ 融合源 {label} 提取告警：{'/'.join(_miss)} 为空（版式锚点可能不符合约定）")
        return {"date": label, "style": skin_light(style), "hero": skin_light(hero),
                "body": skin_light(body)}
    return None


# ---------- 图表（浅色系，红涨绿跌） ----------
def svg_line_chart(points, w=620, h=180, color="#2563EB", label="总分"):
    if not points or all(v is None for _, v in points):
        return f'<div style="color:#6B7280;padding:24px;text-align:center">暂无 {label} 数据</div>'
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    vals = [v for v in ys if v is not None]
    if not vals:
        return f'<div style="color:#6B7280;padding:24px;text-align:center">暂无 {label} 数据</div>'
    vmin, vmax = min(vals), max(vals)
    pad = max((vmax - vmin) * 0.15, 1)
    vmin, vmax = vmin - pad, vmax + pad
    step = w / max(len(xs) - 1, 1)
    # Y 轴刻度（4 档：最小/中下/中上/最大，避开 0 取整保证可读）
    def nice(v):
        if v >= 10:
            return int(round(v))
        return round(v, 1)
    y_ticks = []
    # 强制首末 + 中间三档
    for ratio in [1.0, 0.75, 0.5, 0.25, 0.0]:
        v = vmin + (vmax - vmin) * ratio
        y_ticks.append((ratio, v))

    def to_xy(i, v):
        if v is None:
            return None
        x = 40 + i * step
        y = h - 22 - (v - vmin) / (vmax - vmin) * (h - 50)
        return x, y

    pts = [to_xy(i, v) for i, v in enumerate(ys)]
    segs, cur = [], []
    for p in pts:
        if p is None:
            if len(cur) >= 2:
                segs.append(cur)
            cur = []
        else:
            cur.append(p)
    if len(cur) >= 2:
        segs.append(cur)
    paths = []
    for s in segs:
        dd = "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in s)
        paths.append(f'<path d="{dd}" fill="none" stroke="{color}" stroke-width="2" stroke-linejoin="round"/>')
    for i, (lbl, v) in enumerate(zip(xs, ys)):
        p = to_xy(i, v)
        if p:
            x, y = p
            tip = f'{label} {lbl[5:].replace("-", "/")}: {v}'
            paths.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4" fill="{color}" data-tip="{tip}" style="cursor:pointer"/>')
            # X 轴标签：日期格式 "YYYY-MM-DD" → "MM-DD"（更可读）
            short = lbl[5:] if len(lbl) >= 10 else lbl  # "2026-08-11" -> "08-11"
            short = short.replace("-", "/")
            paths.append(f'<text x="{x:.1f}" y="{h-6}" text-anchor="middle" font-size="10" fill="#6B7280">{short}</text>')
    # 5 条水平网格线 + 左侧 Y 轴刻度值
    grid_lines = []
    for ratio, v in y_ticks:
        y = h - 22 - (v - vmin) / (vmax - vmin) * (h - 50)
        grid_lines.append(f'<line x1="40" y1="{y:.1f}" x2="{w}" y2="{y:.1f}" stroke="#E5E7EB" stroke-width="0.5"/>')
        grid_lines.append(f'<text x="34" y="{y+3:.1f}" text-anchor="end" font-size="12" fill="#6B7280">{nice(v)}</text>')
    grid = "".join(grid_lines)
    return f'<svg viewBox="0 0 {w} {h}" width="100%" style="display:block">{grid}{"".join(paths)}</svg>'


def svg_combined_chart(series, w=620, h=300, x_min=None, x_max=None, pad_top=24, single_axis=False,
                       max_w=None):
    """多系列叠加图。
    single_axis=True 时所有系列共用左 Y 轴（0-100），不画右 Y 轴（用于量纲统一的指标）。
    series = [{"points":[(date_str, val)], "color":"#xxx", "yaxis":"left"|"right",
               "label":"...", "dash":False, "kind":"line"|"bar", "stroke_width":2}, ...]

    ⚠️ 尺寸口径（2026-09-21 修正）：**必须 max-width 限宽**。
    此前只有 `width="100%"` + viewBox 720，而 `.wrap` 最大宽 1560px →
    图表被拉伸到 ~1480px、**SVG 内文字随之放大约 2 倍**（10px 实际渲染 ≈20px），
    同时日期标签互相重叠。现改为 `width:100%;max-width:<w>px`：
    宽屏下封顶在 w（缩放比 1.0，文字＝设计字号），窄屏等比缩小（标签同步缩小，仍不重叠）。

    `max_w=0` = **不限宽**，铺满容器（半栏并排图用；此时缩放比随容器变化，文字随之轻微缩放）。
    **默认 w=620 对齐「半栏卡片」实际宽度**（duo 两列里容器 ≈550~730）→ 缩放比≈1.0、图内文字＝设计 12px；
    整栏大图请显式传更大的 w。自检：`svg.getBoundingClientRect().width / viewBox.width` 应在 0.9~1.15。
    """
    if x_min is None or x_max is None:
        all_dates = []
        for s in series:
            for d, v in s["points"]:
                if v is not None:
                    all_dates.append(d)
        if not all_dates:
            return '<div style="color:#6B7280;padding:24px;text-align:center">暂无数据</div>'
        x_min, x_max = min(all_dates), max(all_dates)
    # 收集所有有数据的日期，按时间排序，并按索引均分 X 位置（限制在 [x_min, x_max] 窗口内，
    # 保证折线/柱状等所有系列与 X 轴刻度日期统一）
    data_dates = sorted(set(d for s in series for d, v in s["points"] if v is not None))
    if x_min is not None:
        data_dates = [d for d in data_dates if d >= x_min]
    if x_max is not None:
        data_dates = [d for d in data_dates if d <= x_max]
    n = len(data_dates)
    # 左右留白按宽度等比（2026-09-21 修正）
    # 原写死 `plot_l, plot_r = 88, w-60`：在半栏并排图（w=520）上吃掉 28% 宽度，图表看着「没填满」。
    # 改为按 w 取比例 + 保底：左侧留给 Y 轴标签（3 位数 @12px ≈22px）+ 半柱宽；右侧留给右轴标签 + 半柱宽。
    plot_l = max(46, int(round(w * 0.085)))
    plot_r = w - max(26, int(round(w * 0.045)))
    plot_t, plot_b = pad_top, h - 30
    has_bar = any(s.get("kind") == "bar" for s in series)
    left_min, left_max = 0, 100
    # 自适应当前折线数据最大值（向上取 10 倍数并加 padding），防止数据冒出去
    left_line_max = max((v for s in series if s.get("yaxis", "left") == "left" and s.get("kind", "line") == "line"
                          for d, v in s["points"] if v is not None), default=None)
    if left_line_max is not None and left_line_max > 100:
        left_max = int((left_line_max + 9) // 10 * 10)
    # 含 bar 系列时：右轴自适配用 bar 实际数据最大值 + 1（柱顶留空），避免 0-50 浪费
    bar_vals = [v for s in series if s.get("kind") == "bar" for d, v in s["points"] if v is not None]
    right_min, right_max = 0, max((max(bar_vals) + 1) if bar_vals else 0, 5)
    day_w = (plot_r - plot_l) / max(n - 1, 1)
    # 含 bar 系列时提前计算柱宽，给左/右 Y 轴标签偏移让位
    has_bar = any(s.get("kind") == "bar" for s in series)
    if has_bar:
        bar_w_est = max(4, day_w * 0.4)
        left_label_dx = bar_w_est / 2 + 6
        right_label_dx = bar_w_est / 2 + 6
    else:
        left_label_dx = 4
        right_label_dx = 4

    def to_x(ds):
        idx = data_dates.index(ds)
        return plot_l + idx * day_w
    def to_yL(v):
        return plot_b - (v - left_min) / (left_max - left_min) * (plot_b - plot_t)
    def to_yR(v):
        return plot_b - (v - right_min) / (right_max - right_min) * (plot_b - plot_t)

    grid, paths, legend = [], [], []
    # 左 Y 刻度（5 档）—— x 偏移感知柱宽：含 bar 系列时保证标签在首柱左侧，避免重叠
    for ratio in [1.0, 0.75, 0.5, 0.25, 0.0]:
        v = left_min + (left_max - left_min) * ratio
        y = to_yL(v)
        grid.append(f'<line x1="{plot_l}" y1="{y:.1f}" x2="{plot_r}" y2="{y:.1f}" stroke="#E5E7EB" stroke-width="0.5"/>')
        grid.append(f'<text x="{plot_l - left_label_dx}" y="{y+3:.1f}" text-anchor="end" font-size="12" fill="#6B7280">{int(round(v))}</text>')
    # 右 Y 刻度（3 档）—— single_axis 模式不画
    if not single_axis:
        for ratio in [1.0, 0.5, 0.0]:
            v = right_min + (right_max - right_min) * ratio
            y = to_yR(v)
            grid.append(f'<text x="{plot_r + right_label_dx}" y="{y+3:.1f}" font-size="12" fill="#6B7280">{int(round(v))}</text>')
    # X 轴：仅画有数据且落在 [x_min, x_max] 窗口内的日期（每个数据日等距）
    # 日期标签防重叠：按 day_w 与「08/14」@12px 的近似宽度算抽稀步长（网格线仍逐日画）；
    # 末位日期（最新交易日）始终保留。等比缩放下标签与间距同步缩放 → 任意屏宽都不重叠。
    _lbl_w, _step = 38.0, 1
    while day_w * _step < _lbl_w and _step < 16:
        _step += 1
    for _i, ds in enumerate(data_dates):
        if x_min is not None and ds < x_min:
            continue
        if x_max is not None and ds > x_max:
            continue
        x = to_x(ds)
        grid.append(f'<line x1="{x:.1f}" y1="{plot_t}" x2="{x:.1f}" y2="{plot_b}" stroke="#F0F0F0" stroke-width="0.5"/>')
        if (n - 1 - _i) % _step == 0:
            grid.append(f'<text x="{x:.1f}" y="{h-8}" text-anchor="middle" font-size="12" fill="#6B7280">{ds[5:].replace("-","/")}</text>')
    # 画每条线（kind: "line" 折线，kind: "bar" 柱状）
    bar_w = max(4, day_w * 0.4)
    for s in series:
        kind = s.get("kind", "line")
        coords = []
        for ds, v in s["points"]:
            if v is None:
                continue
            if x_min is not None and ds < x_min:
                continue
            if x_max is not None and ds > x_max:
                continue
            x, y = to_x(ds), (to_yL(v) if s["yaxis"] == "left" else to_yR(v))
            coords.append((x, y, ds, v))
        if kind == "bar":
            base_y = (to_yL(0) if s["yaxis"] == "left" else to_yR(0))
            bar_opacity = s.get("opacity", 0.85)
            for x, y, ds, v in coords:
                h_rect = max(base_y - y, 1)
                tip = f'{s["label"]} {ds[5:].replace("-", "/")}: {v}'
                paths.append(
                    f'<rect x="{x - bar_w/2:.1f}" y="{y:.1f}" width="{bar_w:.1f}" height="{h_rect:.1f}" '
                    f'fill="{s["color"]}" opacity="{bar_opacity}" rx="3" data-tip="{tip}" style="cursor:pointer"/>'
                )
            # 柱状图顶端标数值（小字）
            for x, y, ds, v in coords:
                paths.append(
                    f'<text x="{x:.1f}" y="{y - 3:.1f}" text-anchor="middle" font-size="11" fill="{s["color"]}">{int(v)}</text>'
                )
            continue
        if len(coords) >= 2:
            dash = ' stroke-dasharray="4,3"' if s.get("dash") else ""
            sw = s.get("stroke_width", 2)
            d_path = "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y, _, _ in coords)
            paths.append(f'<path d="{d_path}" fill="none" stroke="{s["color"]}" stroke-width="{sw}" stroke-linejoin="round"{dash}/>')
        sw = s.get("stroke_width", 2)
        for x, y, ds, v in coords:
            tip = f'{s["label"]} {ds[5:].replace("-", "/")}: {v}'
            paths.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{max(2.5, sw)}" fill="{s["color"]}" data-tip="{tip}" style="cursor:pointer"/>')
    # 图例（顶部横排）
    # ⚠️ 步进必须按「实际字宽」算：原实现用 len(label)*7，对中文字符严重低估
    #    （「涨停家数(家)」按 7px 算只有 49px，实际 @12px 约 68px）→ 两个图例互相叠字。
    def _text_w(t):
        return sum(12.0 if ord(c) > 0x2E80 else 6.6 for c in t)
    lx = plot_l
    ly = 6
    for s in series:
        dash = ' stroke-dasharray="4,3"' if s.get("dash") else ""
        legend.append(f'<line x1="{lx}" y1="{ly+5}" x2="{lx+16}" y2="{ly+5}" stroke="{s["color"]}" stroke-width="2"{dash}/>')
        legend.append(f'<text x="{lx+19}" y="{ly+9}" font-size="12" fill="#6B7280">{s["label"]}</text>')
        lx += 19 + _text_w(s["label"]) + 18
    _cap = "" if max_w == 0 else f"max-width:{(w if max_w is None else max_w)}px;"
    return (f'<svg viewBox="0 0 {w} {h}" preserveAspectRatio="xMidYMid meet" '
            f'style="display:block;width:100%;{_cap}height:auto;margin:0 auto">'
            f'{"".join(legend)}{"".join(grid)}{"".join(paths)}</svg>')



def load_sector_full(date_str):
    """读当日「板块全量打分」（daily/{YYYYMMDD}/板块全量.json）。

    2026-09-21 起热力图的**首选数据源**：来自 OneDrive 每日全量A股明细聚合，
    覆盖 ≈285 个板块（旧口径＝05 复盘手写的 5 行）。
    返回 [{name, score, amt_yi, tier, ...}]；文件不存在时返回 None（调用方回退旧 sectors）。
    """
    if not date_str:
        return None
    p = os.path.join(DAILY, str(date_str).replace("-", ""), "板块全量.json")
    if not os.path.exists(p):
        return None
    try:
        with open(p, encoding="utf-8") as f:
            j = json.load(f)
    except Exception:
        return None
    out = []
    for b in j.get("boards") or []:
        # ⚠️ 不二次归一：`板块全量.json` 里的 name 已经是 L1 方向（scripts/sector_taxonomy.py 归口），
        #    再过一遍 SECTOR_ALIAS 会把方向名重新映射错（历史遗留别名表只服务旧 复盘数据.json）
        nm = b.get("name")
        if not nm:
            continue
        out.append({"name": nm, "score": b.get("score"), "amt_yi": b.get("amt_yi"),
                    "tier": b.get("tier"), "raw_names": b.get("raw_names"),
                    "members": b.get("members"),
                    "chg_w": b.get("chg_w"), "excess": b.get("excess"),
                    "net_yi": b.get("net_yi"), "phase": b.get("phase"),
                    "score_smooth": b.get("score_smooth")})
    return out or None


def load_sector_market(date_str):
    """读当日 `板块全量.json` 的 market 块（当日 β：中位涨幅/中位量能/上涨板块占比/档位）。

    v2 打分做了截面中性化 → 分数里**不含**当日整体强弱，
    故必须把 β 单独取出来在热力图底部呈现，否则普涨日与普跌日看起来结构一样。
    """
    if not date_str:
        return None
    p = os.path.join(DAILY, str(date_str).replace("-", ""), "板块全量.json")
    if not os.path.exists(p):
        return None
    try:
        with open(p, encoding="utf-8") as f:
            j = json.load(f)
    except Exception:
        return None
    return j.get("market") or None


def parse_ladder(ladder_html):
    """拆解融合源手写「四、情绪定位与连板天梯」。

    手写表有两类行：
      · 「涨停 / 20cm 板」聚合行 —— 描述的是**已涨停**的票，逐票明细已由质量分表覆盖 → **不再重复渲染**
      · 「反核 / 炸板 / 炸板池」角色行 —— 描述的是**未涨停**的票（反核、炸板、曾涨停后炸板），
        机器侧无对应数据 → **必须保留**
    返回 (角色行, 聚合结论, 天梯判读)；无融合源时返回 (None, None, None)。
      · 角色行   = 反核/炸板/炸板池 → 逐行保留（机器侧无此数据）
      · 聚合结论 = 「涨停/20cm 板」聚合行的**结论句**（逐票明细已由质量分表覆盖，只留判断）
    """
    import re as _re
    if not ladder_html:
        return None, None, None
    rows, aggs = [], []
    for tr in _re.findall(r"<tr>(.*?)</tr>", ladder_html, _re.S):
        tds = _re.findall(r"<td[^>]*>(.*?)</td>", tr, _re.S)
        if len(tds) < 4:
            continue
        lv = _re.sub(r"<[^>]+>", "", tds[0]).strip()
        if not lv or lv == "层级":
            continue
        if lv in ("反核", "炸板", "炸板池"):
            rows.append((lv, tds[1].strip(), tds[2].strip(), tds[3].strip()))
        else:
            read = _re.sub(r"<[^>]+>", "", tds[3]).strip()
            who = tds[1].strip()
            if read:
                # ⚠️ 必须连「标的」一起保留：只留读数会让结论句丢主语
                #    （曾出现「涨停：封单 7.17 亿」—— 不知道是谁的封单）
                aggs.append((lv, who, read))
    note = ""
    m = _re.search(r'<div class="note">(.*?)</div>', ladder_html, _re.S)
    if m:
        note = m.group(1).strip()
    return (rows or None), (aggs or None), (note or None)


def _short_names(who_html, keep=3):
    """标的列的紧凑渲染：去标签 → 按 / 切分 → 超 keep 个则「前 keep 个 等 N 只」"""
    import re as _re
    # ⚠️ 必须【先去标签再切分】：闭合标签 </b> 里含 "/"，先切会把一段切成两段乱码
    #    （实测「电科思仪 301689（<b>+20%</b>）」被切成 4 段，编号也错）
    plain = _re.sub(r"<[^>]+>", "", who_html or "")
    names = [x.strip() for x in _re.split(r"[/／]", plain)]
    names = [x for x in names if x]
    if not names:
        return ""
    if len(names) <= keep:
        return " / ".join(names)
    return " / ".join(names[:keep]) + f" 等 {len(names)} 只"


def _twenty_cm(code):
    """20cm 阵营：创业板 300/301、科创 688/689"""
    c = (code or "").strip()
    return c.startswith(("300", "301", "688", "689"))


# 持仓相关表述的净化（2026-09-21 原则）
#   持仓改由本地 md（持仓跟踪.md）轻量跟踪；看板为推送到轻量云做准备 → 不含持仓内容。
HOLDING_TOKENS = ("沪电", "药明", "英维克", "588710", "002463", "603259", "002837")
# 持仓相关「揭示性」短语（命中即整块丢弃）
HOLDING_PHRASES = HOLDING_TOKENS + ("持仓四笔", "持仓纪律", "持仓端", "持仓最弱",
                                    "最强持仓", "持仓跟踪", "持仓为主", "watchlist",)

# ⚠️ 不要把「清仓位/执行级/减半」加进块级关键词：它们也会出现在「新规则」句子里，
#    加进去会把整条规则 <li> 连带删掉（实测导致复盘沉淀的规则列表消失）。
#    持仓纪律块本身已由 ① 的『持仓纪律』前缀键 + tail(含 ul) 整块移除。


def _drop_keyed_blocks(h, phrases):
    """按块删除：`<p>` / `<li>` / `<tr>` 三种块，文本命中 phrases 即整块去掉。

    ⚠️ 这里坚持「非贪婪匹配到第一个同名闭合标签」——这三种标签不会嵌套，安全；
    而用 `<div>` 做块会被嵌套 div 骗到（早期踩过）。
    """
    pat = re.compile("|".join(map(re.escape, phrases)))

    def _repl(m):
        return "" if pat.search(m.group(0)) else m.group(0)

    for tag in ("p", "li", "tr"):
        h = re.sub(rf"<{tag}\b[^>]*>.*?</{tag}>", _repl, h, flags=re.S)
    return h


def _clean_note_clauses(h, phrases):
    """`<div class="note">` 内做**子句级**净化：只删含持仓词的子句，保留该 note 的其余判读。

    为什么不做整块删除：note 常是「板块判读/纪律复盘」这类含多条洞察的文字，
    整块删会连带丢掉与持仓无关的内容（如「PCB 价升量出、不具主线资格」）。
    """
    pat = re.compile("|".join(map(re.escape, phrases)))

    def _fix(m):
        inner = m.group(1)
        parts = re.split(r"(?<=[，。；、])", inner)
        kept = [p for p in parts if not pat.search(p)]
        return '<div class="note">' + "".join(kept) + "</div>"

    h = re.sub(r'<div class="note">(.*?)</div>', _fix, h, flags=re.S)
    # 同样处理 <div class="divider"> 与 <li>：**先做子句级净化，再交给块级删除**
    #   （否则「…已写入 watchlist 长期固化。」会把整条规则 <li> 连带删掉）
    h = re.sub(r'<div class="divider">(.*?)</div>', _fix, h, flags=re.S)
    h = re.sub(r'<li\b[^>]*>(.*?)</li>', lambda m: '<li>' + "".join(
        q for q in re.split(r"(?<=[，。；、])", m.group(1)) if not pat.search(q)) + '</li>',
        h, flags=re.S)
    return h


REVIEW_EMOTION = ("涨停", "跌停", "炸板", "连板", "晋级", "高度", "情绪", "退潮",
                   "反核", "一字", "承接", "梯队", "封板")
REVIEW_THEME = ("主力", "净流入", "净流出", "净额", "量能", "放量", "缩量", "价升量出",
                "资金", "板块", "题材", "方向", "权重")


def split_review(sec_review):
    """段六「预案复盘」按主题拆解 → (段三部分, 段四部分)。

    2026-09-21 用户：段六不再单独成段，内容按主题并入
      段三（评分与情绪/连板梯队）= 情绪·连板类规则 + 命中核对表 + 折叠的完整叙述
      段四（题材与资金）        = 题材·资金类规则
    判定方式：**按块（`<p>`/`<li>`）分类**——含「新规则/规律」的块按关键词命中数判归属；
    其余块（叙述、命中核对表）整体归段三并折叠，避免丢内容也不撑长版面。
    """
    if not sec_review:
        return "", ""
    emo, thm, rest = [], [], []
    blocks = []
    # ⚠️ 段六的内容在融合源里是 **`<div class="note">…<br>…<br>…</div>`**
    #    （规则之间用 `<br>` 分隔，**没有 `<p>`/`<li>`**）→ 必须按 `<br>` 切段。
    #    早期按 `<p>`/`<li>` 取块 → 拿到 0 个块，全部落进「其余」，段四永远为空（已踩）。
    notes = re.findall(r'<div class="note">(.*?)</div>', sec_review, re.S)
    for nt in notes:
        for seg in re.split(r'<br\s*/?>', nt):
            if re.sub(r"<[^>]+>", "", seg).strip():
                blocks.append(seg.strip())
    # 命中核对表等表格 → 归入「其余」，在段三里折叠展示
    rest += re.findall(r"<table\b[^>]*>.*?</table>", sec_review, re.S)
    if not blocks and not rest:
        rest = [sec_review]
    for b in blocks:
        txt = re.sub(r"<[^>]+>", "", b)
        if re.search(r"新规则|规律|判据|权重", txt):
            ce = sum(txt.count(k) for k in REVIEW_EMOTION)
            ct = sum(txt.count(k) for k in REVIEW_THEME)
            (thm if ct > ce else emo).append(b)
        else:
            rest.append(b)
    a = ""
    if emo or rest:
        a = '<div class="card"><h2>复盘沉淀 · 情绪与连板规则 ' \
            '<span class="tag" style="background:rgba(124,92,252,.1);color:#7C5CFC">' \
            '由「预案复盘」按主题并入</span></h2>'
        if emo:
            a += '<ul class="clean">' + "".join(f"<li>{b}</li>" for b in emo) + "</ul>"
        if rest:
            a += '<details class="fold"><summary>展开完整复盘叙述与预案命中核对</summary>' \
                  + "".join(rest) + "</details>"
        a += "</div>"
    b_ = ""
    if thm:
        b_ = '<div class="card"><h2>复盘沉淀 · 题材与资金规则 ' \
             '<span class="tag" style="background:rgba(124,92,252,.1);color:#7C5CFC">' \
             '由「预案复盘」按主题并入</span></h2><ul class="clean">' \
             + "".join(f"<li>{x}</li>" for x in thm) + "</ul></div>"
    return a, b_


def strip_holdings(h):
    """净化看板 HTML 里的持仓内容（2026-09-21 原则：持仓改本地 md 跟踪，看板/云推送不含持仓）。

    ① 删「持仓四笔」标题块（**标签无关**：divider 形式与 `<p><b>…</b></p>` 形式都认）
       及其紧随的表格块
    ② 删「持仓纪律」标题块及其紧随内容
    ③ 按块过滤含持仓个股名/揭示性短语的 `<p>`/`<li>`/`<tr>`
    ④ 「操作评价」→「预案复盘」（段六定位：AI 对昨日预案的复盘，非个人实盘操作）
    ⑤ 段标题「六、自选股与持仓」→「六、自选股」
    """
    if not h:
        return h
    label = r'<(?P<t>div|p|h[1-6]|span)[^>]*>(?:(?!(?P=t)>).)*?(?:持仓四笔|持仓纪律|持仓端).*?</(?P=t)>'
    # ⚠️ 标题块后面可能是 <table>，也可能是 <ul>（「持仓纪律」就是 <ul class="clean">）
    #    早期只吃 table → 持仓纪律的 bullet 全部残留（用户截图可见）
    tail = (r'(?:\s*<div class="scroll">.*?</table>\s*</div>'
            r'|\s*<ul[^>]*>.*?</ul>'
            r'|\s*<p[^>]*>.*?</p>)?')   # ⚠️ 只能用 ?（最多吃一个后续块）；用 * 会级联吞掉后面的规则 <ul>
    for _ in range(3):
        h2 = re.sub(label + tail, "", h, flags=re.S)
        if h2 == h:
            break
        h = h2
    # 顺序很重要：先做子句级净化（保留规则正文、只删持仓子句），再块级删除
    h = _clean_note_clauses(h, HOLDING_PHRASES)
    h = _drop_keyed_blocks(h, HOLDING_PHRASES)
    h = h.replace("八、操作评价与纪律复盘", "八、预案复盘与纪律核对")
    h = h.replace("操作评价与纪律复盘", "预案复盘与纪律核对")
    h = h.replace("操作评价", "预案复盘")
    h = h.replace("六、自选股与持仓", "六、自选股")
    return h


def extract_dir_track(html):
    """取出六段里的「方向跟踪层：…」文本（迁入题材段用），并从原处移除"""
    if not html:
        return html, None
    m = re.search(r'<div class="divider">\s*(方向跟踪层[^<]*?)</div>\s*', html)
    if not m:
        return html, None
    return html.replace(m.group(0), "", 1), m.group(1).strip()


def load_zt_quality(date_str):
    """读 daily/{date}/涨停质量.json（涨停质量分，绝对尺度五维）"""
    if not date_str:
        return None
    p = os.path.join(DAILY, str(date_str).replace("-", ""), "涨停质量.json")
    if not os.path.exists(p):
        return None
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _zt_time(t):
    s = (t or "").strip()
    return f"{s[:2]}:{s[2:4]}" if len(s) == 6 and s.isdigit() else "-"


def _score_cell(v):
    """质量分配色：绝对尺度（≥80 强 / ≥70 较好 / ≥60 一般 / ≥50 偏弱 / <50 弱）"""
    if v is None:
        return "#F8F9FA", "#9CA3AF"
    if v >= 80:
        return "#C92A2A", "#FFFFFF"
    if v >= 70:
        return "#E03131", "#FFFFFF"
    if v >= 60:
        return "#F87171", "#14171A"
    if v >= 50:
        return "#FFE5E5", "#14171A"
    if v >= 40:
        return "#E8F5E9", "#14171A"
    return "#86EFAC", "#14171A"


def _fb_score_cell(v):
    """首板晋级分配色 —— 它是**模型概率×100**（非质量分），刻度按晋级基线≈20 分设。
    ≥30 显著高于基线 / 20-30 高于基线 / 12-20 接近基线 / <12 低于基线。"""
    if v is None:
        return "#F8F9FA", "#9CA3AF"
    if v >= 30:
        return "#C92A2A", "#FFFFFF"
    if v >= 20:
        return "#E03131", "#FFFFFF"
    if v >= 12:
        return "#F87171", "#14171A"
    if v >= 6:
        return "#FFE5E5", "#14171A"
    return "#E8F5E9", "#14171A"


def _n2(v, unit=""):
    """定长 2 位小数显示（避免 5.1% / 4.39% 混排）；None → '-'"""
    if v is None:
        return "-"
    try:
        return f"{float(v):.2f}{unit}"
    except (TypeError, ValueError):
        return "-"


def zt_quality_table(date_str=None, top_first=6, ladder_html=None, top_fb=10):
    """连板梯队（融合表）：情绪定位速览（主观）+ 首板专区（1J2 冻结模型）+ 2板以上质量分 + 天梯判读。

    ⛔ **两套口径并列、禁止混用**：
      · **首板** → `首板晋级分` ＝ 100×六因子逻辑回归概率（skill `first-board-auction-score` 的
        `assets/models.json` 冻结模型）。看板直接读 skill 的 `daily/{date}/first_board/{date}_eod/`
        产物，**不在看板侧重算**，保证与 skill 侧数字完全一致。
      · **2板及以上** → `涨停质量分` ＝ 绝对尺度五维（封板时间25 + 牢固度25 + 大单流入20
        + 板块共振20 + 量能结构10），口径见 `scripts/build_zt_quality.py`。
      两者非同一定义：分数不可直接比大小。身位（连板数）单列 —— 质量 ≠ 风险。
    """
    j = load_zt_quality(date_str)
    role_rows, agg_lines, ladder_note = parse_ladder(ladder_html)
    if not j:
        # 无质量分数据时退化为原手写表（保持可用）
        return ('<div class="card">' + (ladder_html or "")
                + '<div style="color:var(--text3);font-size:12.5px">'
                  '（涨停质量分未接入：需先跑 build_zt_quality.py）</div></div>')
    mk = j.get("market") or {}
    fb = j.get("first_board") or {}
    stocks = j.get("stocks") or []
    rows = []
    rows.append('<div class="card">')
    rows.append('<h2>连板梯队 · 情绪定位 <span class="tag" style="background:rgba(37,99,235,.1);'
                'color:#2563EB">客观打分 · 首板六因子模型 ＋ 2板以上五维质量</span></h2>')
    # ── 区① 情绪定位速览（主观，来自融合源）：只保留「未涨停」的角色行
    if role_rows:
        rows.append('<div style="font-size:12px;color:#6B7280;margin:2px 0 6px">'
                    '情绪定位速览<span style="font-size:11px">（主观 · 未涨停角色，逐票明细见下表）</span></div>')
        rows.append('<div class="scroll"><table>')
        rows.append('<tr><th>角色</th><th>标的</th><th>结果</th><th>关键读数</th></tr>')
        for lv, who, res, read in role_rows:
            rows.append(f'<tr><td><b>{lv}</b></td><td>{who}</td><td>{res}</td><td>{read}</td></tr>')
        rows.append('</table></div>')
    if agg_lines:
        # 涨停/20cm 聚合行：逐票明细见下表，这里只保留其结论句，避免重复列票
        rows.append('<ul class="clean" style="margin:8px 0 0">')
        for lv, who, read in agg_lines:
            rows.append(f'<li><b>{lv}</b> · {_short_names(who)} —— {read}</li>')
        rows.append('</ul>')

    # ── 区② 首板专区（1J2 冻结模型，与 skill 同源）
    fb_rows = sorted([s for s in stocks if s.get("level") == "首板" and s.get("fb_score") is not None],
                     key=lambda x: x.get("fb_rank") or 999)
    if fb:
        n20 = fb.get("n_top20")
        rows.append(
            '<div style="color:#6B7280;font-size:12px;margin:12px 0 8px">'
            '<b>首板 · 首板晋级分</b>'
            f' <span class="tag" style="background:rgba(22,163,74,.12);color:#15803D">'
            f'1J2 冻结模型 {fb.get("model") or ""}</span>：'
            f'合格首板 <b>{fb.get("n")}</b> 家（普通10%主板，已排除 {fb.get("n_excluded")} 家）'
            f' · 模型前20% <b>{n20}</b> 家 · 分数中位 <b>{_n2(fb.get("score_median"))}</b>'
            f' · 最高 <b>{_n2(fb.get("score_max"))}</b>'
            f' · 次日预期开盘中位 <b>{_n2(fb.get("expect_open_median"), "%")}</b><br>'
            '口径：<b>首板晋级分 ＝ 100 × 六因子逻辑回归概率</b>'
            '（一字状态 / 早封 / 末封 / 封单占成交 / 封单占流通 / 炸板次数），'
            '数据源 <code>' + str(fb.get("source_dir") or "") + '</code> —— 与 skill '
            '<code>first-board-auction-score</code> 完全同源。<br>'
            '<span style="color:#B45309">⚠️ 该分只用来筛次日取数范围（前20%），<b>不是买入排序</b>；'
            '候选按「次日实际开盘%」重排。概率×100，勿与下方 2 板以上的质量分比大小。</span></div>')
        rows.append('<div class="scroll"><table>')
        rows.append('<tr><th>排名</th><th>标的</th><th>首板晋级分</th><th>前20%</th><th>预期开盘%</th>'
                    '<th>常态区间</th><th>首封</th><th>炸板</th><th>方向共振</th></tr>')
        for x in fb_rows[:top_fb]:
            bg, tc = _fb_score_cell(x.get("fb_score"))
            _fl = list(x.get("flags") or [])
            if _twenty_cm(x.get("code")):
                _fl.insert(0, "20cm")
            flags = "·".join(_fl)
            nm = f'{x.get("name")} <span style="color:#9CA3AF;font-size:11px">{x.get("code")}</span>'
            if flags:
                nm += f' <span class="tag" style="background:rgba(217,119,6,.1);color:#B45309">{flags}</span>'
            band = x.get("fb_band") or [None, None]
            band_s = (f'{_n2(band[0])}% ~ {_n2(band[1])}%' if band[0] is not None else '-')
            if x.get("capped"):
                band_s += ' <span style="color:#B45309;font-size:10px">(触上限)</span>'
            t20 = ('<span class="tag" style="background:rgba(22,163,74,.12);color:#15803D">是</span>'
                   if x.get("fb_top20") else '<span style="color:#9CA3AF">—</span>')
            rows.append(
                '<tr>'
                f'<td class="num">{x.get("fb_rank")}</td>'
                f'<td>{nm}</td>'
                f'<td style="background:{bg};color:{tc};font-weight:600;text-align:center">{_n2(x.get("fb_score"))}</td>'
                f'<td>{t20}</td>'
                f'<td class="num">{_n2(x.get("fb_expect_open"), "%")}</td>'
                f'<td class="num" style="font-size:11.5px">{band_s}</td>'
                f'<td class="num">{_zt_time(x.get("first_time"))}</td>'
                f'<td class="num">{x.get("opened")}</td>'
                f'<td>{x.get("direction")} <span style="color:#6B7280;font-size:11px">'
                f'×{x.get("dir_zt_n")} 家 · 景气 {x.get("dir_score")}</span></td>'
                '</tr>')
        if len(fb_rows) > top_fb:
            rows.append(f'<tr><td></td><td colspan="8" style="color:#6B7280;font-size:11.5px">'
                        f'首板共 {len(fb_rows)} 家，此处仅列首板晋级分前 {top_fb}（完整数据见 '
                        f'{fb.get("source_dir")}/首板评分.csv 与 涨停质量.json）</td></tr>')
        rows.append('</table></div>')

    # ── 区③ 2板及以上（绝对尺度五维质量分）
    rows.append(
        '<div style="color:#6B7280;font-size:12px;margin:14px 0 8px">'
        '<b>2 板及以上 · 涨停质量分</b>：'
        f'当日涨停 <b>{mk.get("涨停家数")}</b> 家 · 均分 <b>{mk.get("均分")}</b> · 中位 <b>{mk.get("中位分")}</b>'
        f' · 一字板 {mk.get("一字板家数")} · 尾盘板 {mk.get("尾盘板家数")}'
        f' · 平均封单强度 {mk.get("平均封单强度_pct")}%<br>'
        '口径：封板时间25 · 封板牢固度25（炸板次数15＋封单强度10） · 大单流入20（主力净额/成交额）'
        ' · 板块共振20（方向涨停家数12＋方向景气分8） · 量能结构10（换手率钟形）'
        '<span style="color:#6B7280">（绝对尺度、跨日可比；首板另有专属口径，见上表）</span></div>')
    rows.append('<div class="scroll"><table>')
    rows.append('<tr><th>身位</th><th>标的</th><th>质量分</th><th>首封</th><th>炸板</th>'
                '<th>封单/强度</th><th>主力占比</th><th>换手</th><th>方向共振</th></tr>')
    for lv in ("高位板", "3板", "2板"):
        g = [x for x in stocks if x.get("level") == lv]
        if not g:
            continue
        for i, x in enumerate(g):
            bg, tc = _score_cell(x.get("score"))
            _fl = list(x.get("flags") or [])
            if _twenty_cm(x.get("code")):
                _fl.insert(0, "20cm")
            flags = "·".join(_fl)
            nm = f'{x.get("name")} <span style="color:#9CA3AF;font-size:11px">{x.get("code")}</span>'
            if flags:
                nm += f' <span class="tag" style="background:rgba(217,119,6,.1);color:#B45309">{flags}</span>'
            rows.append(
                '<tr>'
                f'<td>{lv if i == 0 else ""}</td>'
                f'<td>{nm}</td>'
                f'<td style="background:{bg};color:{tc};font-weight:600;text-align:center">{x.get("score")}</td>'
                f'<td class="num">{_zt_time(x.get("first_time"))}</td>'
                f'<td class="num">{x.get("opened")}</td>'
                f'<td class="num">{x.get("seal_yi")} 亿 <span style="color:#6B7280;font-size:11px">'
                f'({x.get("seal_strength_pct")}%)</span></td>'
                f'<td class="num">{x.get("main_ratio_pct")}%</td>'
                f'<td class="num">{x.get("turnover")}%</td>'
                f'<td>{x.get("direction")} <span style="color:#6B7280;font-size:11px">'
                f'×{x.get("dir_zt_n")} 家 · 景气 {x.get("dir_score")}</span></td>'
                '</tr>')
    rows.append('</table></div>')
    if ladder_note:
        rows.append('<div class="note" style="margin-top:10px">' + ladder_note + '</div>')
    rows.append('<div style="color:#6B7280;font-size:10px;text-align:right;margin-top:6px">'
                '首板晋级分＝1J2 冻结模型（与 skill 同源） · 2板以上质量分＝封板绝对质量（跨日可比） · '
                '两套口径不可混用 · 身位决定参与策略 · 情绪定位速览为主观判读</div>')
    rows.append('</div>')
    return "".join(rows)


def sector_heatmap(records):
    """资金热力图（复刻参考图样式）：颜色深度表示板块景气度，红=主升/流入，绿=退潮/流出。
    每格纯色块，悬停显示分数。无数字标签。
    合并归纳约束：①板块名先归一化归并到标准池；②列数最多近 10 个交易日；
    ③池外新热点最多显示 6 个（按近 3 日最高景气度取 TOP），防止表格无限膨胀。"""
    # 1) 归一化 + 过滤无 sectors 的 record + 按日期升序
    normed_records = []
    for r in sorted(records, key=lambda x: (x.get("date") or "")):
        # 首选「板块全量打分」；无该文件时回退旧的 05 手写 sectors（向后兼容）
        _full = load_sector_full(r.get("date"))
        if _full:
            # ⚠️ 全量打分的 name 已是 L1 方向（sector_taxonomy 归口），**绝不能再过 SECTOR_ALIAS**——
            #    否则「半导体」会被旧别名表重新映射成「半导体/存储/封测」，新分类白做
            new_secs = _full
        else:
            # 旧路径（仅历史缺全量的日子）：05 手写 sectors 需要归一化
            new_secs = []
            for s in (r.get("sectors") or []):
                std = normalize_sector_name(s.get("name"))
                s2 = dict(s); s2["name"] = std
                new_secs.append(s2)
        if not new_secs:
            continue
        nr = dict(r); nr["sectors"] = new_secs
        normed_records.append(nr)

    # 列数上限：最近 10 个交易日
    heads = [r["date"] for r in normed_records][-10:]
    if not heads:
        return '<div style="color:#6B7280;padding:24px;text-align:center">暂无板块资金数据</div>'

    heads_short = [d[5:].replace("-", "-") for d in heads]  # "08-21"

    rows = STANDARD_SECTOR_POOL

    # 2) 建索引 grid[(板块, date)] = score
    grid = {}
    for r in normed_records:
        d = r["date"]
        for s in r.get("sectors") or []:
            # ⭐ 显示平滑分（0.5/0.3/0.2 三日加权）—— 抑制单日噪声；
            #    无平滑分（仅单日）时回退当日分
            sc = s.get("score_smooth")
            grid[(s["name"], d)] = sc if sc is not None else s.get("score")

    # 单元格附加值（涨幅/超额）→ hover 展示，便于判断"高分是否靠绝对涨幅撑的"
    # 单元格附加值（整条记录）→ hover 展示；格子分数用 score_smooth（平滑分，抑制单日噪声）
    cell_ext = {}
    for r in normed_records:
        d = r["date"]
        for s2 in r.get("sectors") or []:
            cell_ext[(s2["name"], d)] = s2

    def grid_keys_by_date(d):
        return [name for (name, dd) in grid.keys() if dd == d]

    def color_for(score):
        """0-100 → A股语义色：红=主升(流入多)，绿=退潮(流出/弱势)"""
        if score is None:
            return '#F8F9FA'  # 浅灰（无数据）
        # 阈值（2026-09-21 收窄）：显示值改为 3 日平滑分后分布随之收窄，
        # 原 85/75/65/50/35/25/15 会导致大量格子落进"中性色" → 整体内收 5 分
        if score >= 80:
            return '#C92A2A'  # 深红
        if score >= 70:
            return '#E03131'  # 红
        if score >= 60:
            return '#F87171'  # 浅红
        if score >= 50:
            return '#FFE5E5'  # 近白红
        if score >= 40:
            return '#E8F5E9'  # 近白绿
        if score >= 30:
            return '#86EFAC'  # 浅绿
        if score >= 20:
            return '#4ADE80'  # 中绿
        return '#16A34A'      # 深绿

    parts = []
    n_cols = len(heads)
    # 容器
    parts.append(f'<div style="background:var(--card);border:1px solid var(--card-border);border-radius:14px;padding:18px;margin-bottom:16px">')
    # 图例
    parts.append('<div style="display:flex;flex-wrap:wrap;gap:14px;align-items:center;justify-content:center;margin-bottom:14px;font-size:12px;color:#14171A">')
    # 图例口径（2026-09-21 修正）：配色实际按 score（景气度 0-100）分档，
    # 旧文案「净流出/净流入」是 v1 遗留、与配色不符，已改为景气度语义。
    for lbl, c in [
        ('低景气（颜色越深＝相对越弱）', '#4ADE80'),
        ('中性（≈50 分）', '#F8F9FA'),
        ('高景气（颜色越深＝相对越强）', '#DC2626'),
    ]:
        parts.append(f'<span style="display:flex;align-items:center;gap:5px"><span style="display:inline-block;width:16px;height:12px;background:{c};border:1px solid #E5E7EB;border-radius:2px"></span>{lbl}</span>')
    parts.append('</div>')
    # 网格：每行板块 + 1 个日期标签行 + 日期列
    # 2026-09-15 移动端适配：列宽改 minmax 保底 + 外层横向滚动容器（桌面宽屏表现不变）
    grid_tpl = (f'display:grid;grid-template-columns:170px repeat({n_cols}, minmax(42px, 1fr));'
                f'gap:4px;align-items:center;min-width:{170 + n_cols * 46}px')
    parts.append('<div class="scroll" style="-webkit-overflow-scrolling:touch">')
    parts.append(f'<div style="{grid_tpl}">')
    # 表头（日期）
    parts.append('<div></div>')
    # 选择性标签（4个均匀散布的日期标签，避免列过多全挤一起）
    step = max(1, n_cols // 4)
    for i, h in enumerate(heads_short):
        if i % step == 0 or i == n_cols - 1:
            parts.append(f'<div style="text-align:center;color:#6B7280;font-size:11px">{h}</div>')
        else:
            parts.append('<div></div>')
    # 行：每个板块（标准池 + 池外新热点 TOP6；归一化后已在池内的池外项跳过防重复）
    # ⚠️ 2026-09-16 修复：pool_set 原先直接取 STANDARD_SECTOR_POOL，但历史 JSON 里存在
    #   未归一的别名变体（如 "PCB"、"PCB/算力硬件"），它们 normalize 后仍是自身、
    #   不等于池内名，于是被当成"池外新热点"另开一行 → PCB 出现两行。
    #   现在：①pool_set 也走一遍归一化；②二次过滤时再次归一化，双保险。
    # 行选择（2026-09-21 换源后重写）
    # 旧口径每日仅 5 行（05 手写 TOP5）→ 直接用「标准池 + 池外热点」即可；
    # 新口径每日 ≈285 个板块，**无法全画**，且热力图要求行集在窗口内稳定 → 改用：
    #   行 = 「有数据的标准池项」∪「窗口内累计成交额 TOP N」
    # 以累计成交额为轴的理由：跨日最稳定、最能代表资金承载力；换源后每日板块数一致（285），
    # 行集不会逐日漂移（避免矩阵出现「今天有这行、明天没有」的断裂）。
    # 行选择（2026-09-21 二改）：**不再拼标准池**。
    # 旧做法把「标准池方向」（半导体/存储/封测）与「原始细分行业」（集成电路设计、其他专用设备）
    # 混在同一张图 → 用户观感是"杂且有重叠"。现在数据源已是统一的 L1 方向（sector_taxonomy），
    # 故 **行 = 窗口内累计成交额 TOP HEAT_TOP_N 的方向**，层级统一、名字即范围。
    amt_sum = {}
    for r in normed_records:
        for s in r.get("sectors") or []:
            nm = s.get("name")
            if nm:
                amt_sum[nm] = amt_sum.get(nm, 0.0) + (s.get("amt_yi") or 0.0)
    all_rows = [n for n, _ in sorted(amt_sum.items(), key=lambda kv: -kv[1])[:HEAT_TOP_N]]
    def text_color_for(score):
        """数字颜色：深底配白字，浅底配黑字，确保对比度"""
        if score is None:
            return '#9CA3AF'  # 浅灰
        if score >= 70 or score <= 30:
            return '#FFFFFF'  # 深色（红/绿）配白字
        return '#14171A'      # 浅色配黑字

    for sec_name in all_rows:
        parts.append(f'<div style="padding:6px 8px;color:#14171A;font-size:12px;font-weight:500;text-align:right;white-space:nowrap;overflow:hidden;text-overflow:ellipsis" title="{sec_name}">{sec_name}</div>')
        for d in heads:
            sc = grid.get((sec_name, d))
            c = color_for(sc)
            tc = text_color_for(sc)
            num = f'{sc}' if sc is not None else '·'
            _e = cell_ext.get((sec_name, d)) or {}
            lab = f'{sec_name} {d[5:].replace("-", "-")}: 平滑 {sc if sc is not None else "-"} 分'
            _raw = _e.get("score")
            if _raw is not None:
                lab += f'（当日 {_raw} 分'
            _cw, _ex = _e.get("chg_w"), _e.get("excess")
            if _cw is not None:
                lab += f' 涨 {_cw:+.2f}%'
            if _ex is not None:
                lab += f' 超额 {_ex:+.2f}%'
            _net = _e.get("net_yi")
            if _net is not None:
                lab += f' 主力 {_net:+.1f}亿'
            _ms = _e.get("members") or []
            if _ms:
                _top = max(_ms, key=lambda m: (m.get("chg_w") or -99))
                lab += f' ｜ 内部最强: {_top["name"]} {(_top.get("chg_w") or 0):+.2f}%'
            lab += '）' if _raw is not None else ''
            label = lab
            parts.append(f'<div style="background:{c};color:{tc};display:flex;align-items:center;justify-content:center;height:34px;border-radius:6px;font-size:12px;font-weight:600;cursor:pointer;transition:all .15s" data-tip="{label}" onmouseover="this.style.opacity=.7" onmouseout="this.style.opacity=1">{num}</div>')
    # 底部「当日中位涨幅」β 条：v2 打分已中性化，当日整体强弱单独一行呈现，
    # 否则普涨日与普跌日的热力图看起来结构一样、无法读出市场水位。
    mk = {}
    for d in heads:
        m = load_sector_market(d)
        if m:
            mk[d] = m
    if mk:
        parts.append('<div style="padding:6px 8px;color:#6B7280;font-size:11px;text-align:right;'
                     'white-space:nowrap;border-top:1px dashed #D1D5DB;margin-top:2px">'
                     '当日中位涨幅<span style="font-size:10px">（市场β）</span></div>')
        for d in heads:
            m = mk.get(d)
            if not m:
                parts.append('<div style="text-align:center;font-size:11px;color:#9CA3AF">·</div>')
                continue
            mc = m.get("median_chg") or 0
            col = '#E03131' if mc > 0 else ('#0E8A5F' if mc < 0 else '#6B7280')
            parts.append(
                f'<div style="text-align:center;font-size:11px;font-weight:600;color:{col}" '
                f'data-tip="{d[5:]} 中位涨幅 {mc:+.2f}% · {m.get("tier","")} · '
                f'上涨板块占比 {m.get("up_board_ratio",0):.0%}">{mc:+.2f}%</div>')
    parts.append('</div>')
    parts.append('</div>')
    parts.append('<div style="color:#6B7280;font-size:10px;text-align:right;margin-top:6px">'
                 '悬停查看分数 ｜ 分数＝当日截面分位（中位≈50，横向/纵向均可比）</div>')
    parts.append('</div>')
    return ''.join(parts)



def gate_timeline(records):
    recs = trim_records(records, "gate")
    segs = []
    for r in recs:
        g = (r.get("gate") or "").strip()
        c = gate_color(g) or "#D1D5DB"
        segs.append(f'<div style="flex:1;min-width:64px;max-width:130px;text-align:center"><div style="background:{c};height:34px;border-radius:8px;display:flex;align-items:center;justify-content:center;color:#FFFFFF;font-weight:500;font-size:12px" title="{(g or "")[:80]}">{mmdd(r["date"])}</div><div style="color:#6B7280;font-size:10px;margin-top:4px">{gate_label(g)}</div></div>')
    if not segs:
        return '<div style="color:#6B7280;padding:24px;text-align:center">暂无闸门数据</div>'
    return f'<div style="display:flex;gap:6px;flex-wrap:wrap">{chr(10).join(segs)}</div>'


def structure_table(records, recent_n=5):
    """大盘结构快照表（2026-09-10 二次精简）：
    - 只保留最近 recent_n（5）个交易日，不再渲染更早日期的折叠块（历史仍在归档与上方时序卡片中）
    - 文本框（30m结构/最后一笔/MACD/波浪）做语句精简：按分句聚合至 ~42 字，原文进 title 悬停
    - 斑马纹 + 紧凑 padding + 列宽上限，保证单行信息密度可控"""
    recs = [r for r in records if r.get("sd")]
    if not recs:
        return '<div style="color:#6B7280;padding:24px;text-align:center">暂无结构快照数据</div>'

    def brief(txt, limit=42):
        """表格内精简（2026-09-14 三次修订；09-15 补：平衡 ** 加粗标记）：
        - 原文 <= limit → 全留（完整显示）
        - 超长 → 「首句 ；…； 末句」结构，末句通常是结论（失效线/确认线/判定），必须保留
        - 仍超长 → 首句截半 + 末句，杜绝「截到一半没有结论」
        - 输出前校正 ** 配对：截断可能切断一对标记，须补齐/剔除，否则渲染出裸星号"""
        t = str(txt or "-").strip()
        if not limit or limit <= 0:        # limit<=0 = 明确不截断（2026-09-21 起）
            return _balance_md(t)
        if t in ("", "-") or len(t) <= limit:
            return _balance_md(t)
        parts = [p.strip() for p in re.split(r"[；;。]", t) if p.strip()]
        if len(parts) <= 1:
            return _balance_md(t[:limit].rstrip("，、,。 ") + "…")
        head, tail = parts[0], parts[-1]
        if head == tail:
            return _balance_md(t[:limit].rstrip("，、,。 ") + "…")
        combo = f"{head}；…；{tail}"
        if len(combo) <= limit:
            return _balance_md(combo)
        # 首句 + 尽量多的中间句 + 末句
        out = head
        for p in parts[1:-1]:
            if len(out) + len(p) + 1 <= limit - len(tail) - 5:
                out += "；" + p
            else:
                break
        combo2 = f"{out}；…；{tail}"
        if len(combo2) <= limit + 12:
            return _balance_md(combo2)
        room = max(limit - len(tail) - 4, 12)
        return _balance_md(head[:room].rstrip("，、,。 ") + "…" + tail)

    def _balance_md(s):
        """校正 markdown 加粗标记：截断后 ** 若不配对，渲染会露出裸星号。
        奇数字符串尾部补 ** 使其合法；末尾孤立的 '**…' 片段则直接剥离。"""
        if s.count("**") % 2 == 0:
            return s
        # 末尾若以 '**' + 残片 结尾且残片过短（<2 字），视作无效标记剥离
        m = re.search(r"\*\*[^*]{0,1}$", s)
        if m:
            return s[: m.start()]
        return s + "**"

    def row(r, dim=False):
        sd = r.get("sd") or {}
        gate = (r.get("gate") or "").strip() or "-"
        gc = gate_color(gate) or "#6B7280"
        # 2026-09-07 修复"歪了"：gate 原文是超长句（如"🟡 修复日确立（上证缩量收复…+涨停95家…"），
        # 之前 nowrap 整列被撑宽数百 px。改为精简主短语（首个分隔符前）+ title 悬停全文，允许换行。
        g_short = re.split(r"[（(·：:;；]", gate, maxsplit=1)[0].strip() or gate
        # 2026-09-10 配色统一：原文 emoji（🔴/🟡，9-03 起格式）与 gate_color 语义色
        # （红=可开仓/黄=仅验证/蓝=防守）会打架（如「🔴 退潮」被染成防守蓝=红点配蓝字）。
        # 统一为「同色圆点 + 同色文字」：剥离开头 emoji，改用 CSS 圆点，色值一律取 gate_color。
        g_clean = re.sub(r"^[^\u4e00-\u9fffA-Za-z0-9]+", "", g_short).strip() or g_short
        if len(g_clean) > 14:
            g_clean = g_clean[:13] + "…"
        import html as _html
        g_title = _html.escape(gate, quote=True)
        cz = f'{sd["center_zd"]:.0f}-{sd["center_zg"]:.0f}' if sd.get("center_zd") is not None and sd.get("center_zg") is not None else "-"
        dim_style = ' style="opacity:.55"' if dim else ""
        dot = (f'<span style="display:inline-block;width:7px;height:7px;border-radius:50%;'
               f'background:{gc};margin-right:6px;vertical-align:middle"></span>')

        def cell(key, limit=42, label=""):
            raw = str(sd.get(key, "-"))
            lab = f' data-label="{_html.escape(label, quote=True)}"' if label else ""
            shown = _html.escape(brief(raw, limit))
            # **重点** → <strong>（原始数据里用 markdown 加粗标注关键位，直接输出会露出星号）
            shown = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", shown)
            return (f'<td{lab} title="{_html.escape(raw, quote=True)}">'
                    f'{shown}</td>')

        return (f'<tr{dim_style}>'
                f'<td class="d" data-label="交易日">{r["date"]}</td>'
                f'<td class="g" data-label="闸门" style="color:{gc}" title="{g_title}">{dot}{_html.escape(g_clean)}</td>'
                # ⚠️ 2026-09-21：这几个单元格不再截断（原 72/104 会留「…」，
                #    用户明确要求去掉省略号 → limit=0 表示不截断，完整展示）
                + cell("chan_state", 0, "30m结构状态")
                + f'<td class="nw" data-label="中枢 ZD-ZG">{cz}</td>'
                + cell("last_stroke", 0, "最后一笔")
                + cell("macd", 0, "MACD/背驰")
                + cell("wave", 0, "波浪位置")
                + '</tr>')

    head = ("<tr>" + "".join(f"<th>{h}</th>" for h in
            ["日期", "闸门", "30m结构状态", "中枢 ZD-ZG", "最后一笔", "MACD/背驰", "波浪位置"]) + "</tr>")

    def table_wrap(body_rows):
        return f'<table><thead>{head}</thead><tbody>{body_rows}</tbody></table>'

    recent = recs[-recent_n:]
    body = table_wrap(''.join(row(r) for r in recent))
    style = ('<style>.snap-table{overflow-x:auto}'
             '.snap-table table{width:100%;min-width:920px;border-collapse:collapse;font-size:12px;table-layout:fixed}'
             '.snap-table th{text-align:left;padding:6px 10px;background:var(--blue-soft);color:var(--blue);font-weight:600;font-size:11px;border-bottom:1px solid var(--line);white-space:nowrap}'
             '.snap-table td{padding:6px 10px;border-top:1px solid rgba(228,231,235,.5);color:var(--text);line-height:1.5;vertical-align:top;word-break:break-word}'
             '.snap-table td.d,.snap-table td.nw{white-space:nowrap}'
             '.snap-table td.d{color:var(--sub)}'
             '.snap-table td.g{font-weight:500}'
             '.snap-table th:nth-child(1),.snap-table td:nth-child(1){width:7%}'
             '.snap-table th:nth-child(2),.snap-table td:nth-child(2){width:8%}'
             '.snap-table th:nth-child(3),.snap-table td:nth-child(3){width:15%}'
             '.snap-table th:nth-child(4),.snap-table td:nth-child(4){width:8%}'
             '.snap-table th:nth-child(5),.snap-table td:nth-child(5){width:14%}'
             '.snap-table th:nth-child(6),.snap-table td:nth-child(6){width:24%}'
             '.snap-table th:nth-child(7),.snap-table td:nth-child(7){width:24%}'
             '.snap-table tbody tr:nth-child(even) td{background:rgba(124,92,252,.035)}'
             '.snap-table tbody tr:hover td{background:var(--card2)}'
             '.snap-table td[title]{cursor:help}'
             '.snap-hint{display:none}</style>')
    return (f'<div class="snap-table">{style}{body}'
            f'<div class="snap-hint">← 左右滑动查看 MACD / 波浪列 →</div></div>')


def center_band(records):
    """跨日 30m 中枢 ZD-ZG 区间带：Y 轴价格刻度 + ZD/ZG 数值 + 关键价位 + 日期。
    8/18 沿用近似中枢时用 alpha=0.4 区分；与前一交易日完全重叠时错开 4px 显示。"""
    recs = trim_records(records, "sd")
    pts = []
    for r in recs:
        sd = r.get("sd") or {}
        if sd.get("center_zd") is not None and sd.get("center_zg") is not None:
            pts.append((r["date"], float(sd["center_zd"]), float(sd["center_zg"])))
    if not pts:
        return '<div style="color:#6B7280;padding:24px;text-align:center">暂无中枢区间数据</div>'
    # 2026-09-21：原 480×210 在 20+ 个交易日下日期标签必然重叠 → 加宽到 760×260
    w, h = 760, 260
    # Y 轴范围：lo-10% ~ hi+10%
    lo = min(min(zd, zg) for _, zd, zg in pts)
    hi = max(max(zd, zg) for _, zd, zg in pts)
    pad = max((hi - lo) * 0.15, 1)
    y_min, y_max = lo - pad, hi + pad
    plot_l, plot_r = 64, w - 40   # 左右各留出空间，避免 ZD/ZG 标签撞 Y 轴刻度或被裁切
    plot_t, plot_b = 22, h - 34    # 上下留边距
    plot_h = plot_b - plot_t
    step = (plot_r - plot_l) / max(len(pts) - 1, 1) if len(pts) > 1 else 0
    def yp(p):  # 价格 → y 像素
        return plot_b - (p - y_min) / (y_max - y_min) * plot_h
    parts = []
    # Y 轴刻度（5 档）+ 横向网格
    n_ticks = 5
    for k in range(n_ticks):
        p = y_min + (y_max - y_min) * k / (n_ticks - 1)
        yy = yp(p)
        parts.append(f'<line x1="{plot_l}" y1="{yy:.1f}" x2="{plot_r}" y2="{yy:.1f}" stroke="#E5E7EB" stroke-width="0.5"/>')
        parts.append(f'<text x="{plot_l-6}" y="{yy+3:.1f}" text-anchor="end" font-size="12" fill="#6B7280">{p:.0f}</text>')
    # 标签抽稀：日期与 ZG/ZD 数值均按需隔位显示
    # （2026-09-21：20+ 个交易日下 step≈31px、「8/18」@11px≈26px，再加宽也可能压字，故隔位兜底）
    npts = len(pts)
    lstep = 1
    while step * lstep < 30 and lstep < 12:
        lstep += 1
    # 中枢柱（合并同日相同区间：8/17 与 8/18 数据相同时错开 4px）
    prev = None
    for i, (d, zd, zg) in enumerate(pts):
        x = plot_l + i * step if len(pts) > 1 else (plot_l + plot_r) / 2
        if prev is not None and prev[1] == zd and prev[2] == zg:
            x += 4  # 完全重合时错开
        y_top, y_bot = yp(zg), yp(zd)
        # 8/18 沿用性质：透明度 0.4 + 斜线填充提示
        opacity = "0.45" if d == "2026-08-18" and (i > 0 and pts[i-1][1] == zd and pts[i-1][2] == zg) else "0.75"
        # 填充矩形（中枢区间带）
        parts.append(
            f'<rect x="{x-7:.1f}" y="{y_top:.1f}" width="14" height="{y_bot-y_top:.1f}" '
            f'rx="3" fill="#7C5CFC" fill-opacity="{opacity}" stroke="#7C5CFC" stroke-width="1"/>'
        )
        if (i % lstep == 0) or (i == npts - 1):
            # ZG/ZD 标签（rect 上下，整数）
            parts.append(f'<text x="{x:.1f}" y="{y_top-3:.1f}" text-anchor="middle" font-size="10.5" fill="#5B4BC4" font-weight="600">{zg:.0f}</text>')
            # ZD 标签（rect 底部正下方，整数）
            parts.append(f'<text x="{x:.1f}" y="{y_bot+12:.1f}" text-anchor="middle" font-size="10.5" fill="#5B4BC4" font-weight="600">{zd:.0f}</text>')
            # 日期（底部）
            parts.append(f'<text x="{x:.1f}" y="{h-10}" text-anchor="middle" font-size="11" fill="#14171A" font-weight="600">{mmdd(d)}</text>')
        prev = (d, zd, zg)
    return f'<svg viewBox="0 0 {w} {h}" width="100%" style="display:block">{"".join(parts)}</svg>'


def history_table(records):
    rows = []
    for r in records:
        t = r.get("total")
        band = ""
        if t is not None:
            band = "强" if t >= 75 else "偏强" if t >= 60 else "中性" if t >= 45 else "偏弱" if t >= 30 else "弱"
        gate = (r.get("gate") or "").strip()
        gc = gate_color(gate) or "#6B7280"
        s, se, lu, mb = r.get("structure"), r.get("sentiment"), r.get("limit_up"), r.get("max_board")
        def fmt(v):
            return f"{v:.0f}" if isinstance(v, (int, float)) else "-"
        rows.append(f"""<tr>
<td style="padding:7px 10px;color:#14171A;font-size:12px;white-space:nowrap">{r["date"]}</td>
<td style="padding:7px 10px;color:#C92A2A;font-size:13px;font-weight:700">{fmt(t)}</td>
<td style="padding:7px 10px;color:#14171A;font-size:12px">{band}</td>
<td style="padding:7px 10px;font-size:12px;color:{gc};font-weight:500">{gate or "-"}</td>
<td style="padding:7px 10px;color:#7C5CFC;font-size:12px">{fmt(s)}</td>
<td style="padding:7px 10px;color:#2563EB;font-size:12px">{fmt(se)}</td>
<td style="padding:7px 10px;color:#F59E0B;font-size:12px">{fmt(lu)}</td>
<td style="padding:7px 10px;color:#E03131;font-size:12px">{fmt(mb)}</td>
</tr>""")
    if not rows:
        return '<div style="color:#6B7280;padding:20px;text-align:center">暂无历史评分数据</div>'
    head = ("<tr>" + "".join(
        f"<th style='text-align:left;padding:7px 10px;color:#6B7280;font-size:11px'>{h}</th>"
        for h in ["日期", "总分", "档位", "闸门", "结构(50)", "情绪(50)", "涨停", "连板"]
    ) + "</tr>")
    return f'<div style="overflow-x:auto"><table style="border-collapse:collapse;width:100%">{head}{"".join(rows)}</table></div>'


# ---------- 渲染 ----------
BASE_CSS = """
/* 2026-09-21：三列「票/身位/结果」表对齐修正
   （原表格被 td:last-child 的右对齐规则影响 → 结果列被推到最右，列间出现大空隙）
   ⚠️ 只改对齐、**不设固定列宽**：加了固定宽度反而会把中间撑出新的空隙，
      交给 auto 布局（多余宽度归末列）→ 结果列紧随身位列，版面最紧。 */
.t-lft td:last-child, .t-lft th:last-child{text-align:left}
.t-lft th:first-child, .t-lft td:first-child{white-space:nowrap}
/* 2026-09-21：可折叠区块（历史总览等） */
details.fold{border:1px solid var(--card-border);border-radius:12px;background:var(--card);padding:10px 14px;margin:12px 0}
details.fold>summary{cursor:pointer;font-size:13.5px;font-weight:600;color:#2563EB;list-style:none}
details.fold>summary::-webkit-details-marker{display:none}
details.fold>summary:before{content:"▸ ";display:inline-block}
details.fold[open]>summary:before{content:"▾ "}
details.fold>table, details.fold>div{margin-top:10px}
*{margin:0;padding:0;box-sizing:border-box}
:root{
  --bg:#F3F6FC;--bg-grad:linear-gradient(180deg,#F8FAFF 0%,#F3F6FC 100%);
  --card:rgba(255,255,255,.94);--card-border:#E0E6F2;--card-shadow:0 2px 10px rgba(37,99,235,.05);
  --text:#14171A;--text2:#5A6B8C;--text3:#8A94AD;
  --blue:#2563EB;--blue-soft:rgba(37,99,235,.10);--purple:#7C5CFC;--purple-soft:rgba(124,92,252,.12);
  --grad:linear-gradient(135deg,#2563EB,#7C5CFC);--grad2:linear-gradient(90deg,#2563EB,#7C5CFC);
  --up:#E03131;--down:#00A870;--hold:#B45309;--gate-r:#E03131;--gate-y:#F59E0B;--gate-b:#2563EB;
  --th-bg:rgba(37,99,235,.04);--row-line:rgba(224,230,242,.55);
  --nav-bg:rgba(255,255,255,.88);--chip:#F3F4F8;
  /* 兼容旧复盘正文（05 html 内联变量别名 → 新主题） */
  --line:#E0E6F2;--card2:#F8FAFF;--sub:#5A6B8C;--text3:#8A94AD;--border:#E0E6F2;--purple2:#7C5CFC;
}
body{background:var(--bg-grad);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;line-height:1.65;font-size:15px;-webkit-font-smoothing:antialiased}
.wrap{max-width:1560px;margin:0 auto;padding:20px 24px 80px}
h1{font-size:21px;font-weight:700;letter-spacing:.5px;color:var(--text);display:flex;align-items:center;gap:10px}
h1::before{content:"";width:5px;height:24px;border-radius:3px;background:var(--grad);flex-shrink:0}
.sub{color:var(--text2);font-size:13px;margin-top:6px}
nav.tabs{position:sticky;top:8px;z-index:30;display:flex;gap:4px;background:var(--nav-bg);backdrop-filter:blur(8px);border:1px solid var(--card-border);border-radius:14px;padding:6px;margin:16px 0 6px;box-shadow:var(--card-shadow)}
/* 锚点跳转时为 sticky 导航预留空间，避免卡片标题被导航遮挡 */
[id]{scroll-margin-top:76px}
nav.tabs a{display:inline-block;padding:7px 15px;border-radius:10px;font-size:13px;font-weight:500;color:var(--text2);text-decoration:none;transition:all .25s ease;border:1px solid transparent}
nav.tabs a:hover{background:var(--blue-soft);color:var(--blue)}
nav.tabs a.active{background:var(--grad);color:#fff;box-shadow:0 2px 8px rgba(99,102,241,.25)}
.section-title{display:flex;align-items:center;gap:10px;margin:30px 0 14px}
.section-title .no{width:28px;height:28px;border-radius:9px;background:var(--grad);display:flex;align-items:center;justify-content:center;font-size:13px;font-weight:700;color:#fff;flex-shrink:0;box-shadow:0 2px 6px rgba(99,102,241,.25)}
.section-title .name{font-size:17px;font-weight:700;color:var(--text)}
.section-title .desc{font-size:12px;color:var(--text3);margin-left:auto}
.card{background:var(--card);border:1px solid var(--card-border);border-radius:16px;padding:20px 20px 18px;margin:16px 0;box-shadow:var(--card-shadow);transition:transform .25s ease,box-shadow .25s ease,border-color .25s ease}
.card:hover{transform:translateY(-2px);box-shadow:0 8px 24px rgba(37,99,235,.10);border-color:#C7D2FE}
.card h2{font-size:15px;font-weight:700;margin-bottom:12px;color:var(--text);display:flex;align-items:center;gap:8px}
table{width:100%;border-collapse:collapse;font-size:13.5px;margin:6px 0}
th{color:var(--text2);font-weight:600;text-align:left;padding:7px 8px;border-bottom:1px solid var(--card-border);font-size:12px;white-space:nowrap;background:var(--th-bg)}
td{padding:7px 8px;border-bottom:1px solid var(--row-line);vertical-align:top}
tr:last-child td{border-bottom:none}
tr{transition:background .15s ease}
tbody tr:hover{background:rgba(37,99,235,.03)}
.num{font-family:"SF Mono",Consolas,Menlo,monospace;font-variant-numeric:tabular-nums}
.mono{font-family:"SF Mono",Consolas,Menlo,monospace}
.divider{display:flex;align-items:center;gap:10px;color:var(--text2);font-size:12px;margin:14px 0 10px}
.divider::before,.divider::after{content:"";flex:1;height:1px;background:var(--card-border)}
.tag{display:inline-block;font-size:11px;font-weight:600;padding:3px 10px;border-radius:20px;letter-spacing:1px;margin-bottom:8px}
.pool{background:var(--purple-soft);border:1px solid rgba(124,92,252,.28);border-radius:12px;padding:13px 15px;margin:10px 0}
.pool .pt{font-weight:700;color:#6C5CE7;font-size:14px;margin-bottom:6px;display:flex;align-items:center;gap:6px}
ul.clean{list-style:none}
ul.clean li{padding:4px 0;padding-left:18px;position:relative;font-size:13.5px}
ul.clean li::before{content:"▸";position:absolute;left:0;color:var(--purple)}
.hit{color:var(--up);font-weight:700}.part{color:var(--hold);font-weight:700}.miss{color:var(--down);font-weight:700}
.note{font-size:12px;color:var(--text2);margin-top:10px;border-top:1px dashed #C7D2FE;padding-top:8px}
.footer{text-align:center;color:var(--text3);font-size:11.5px;margin-top:28px;padding-top:14px;border-top:1px solid var(--card-border)}
.scroll{overflow-x:auto}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:14px}
.grad-text{background:var(--grad2);-webkit-background-clip:text;background-clip:text;color:transparent}
.back-top{position:fixed;right:24px;bottom:28px;z-index:40;width:44px;height:44px;border-radius:50%;background:var(--grad);color:#fff;border:none;font-size:18px;cursor:pointer;opacity:0;pointer-events:none;transform:translateY(10px);transition:all .3s ease;box-shadow:0 4px 14px rgba(99,102,241,.35)}
.back-top.show{opacity:1;pointer-events:auto;transform:translateY(0)}
.back-top:hover{transform:translateY(-3px)}
.mode-btn{position:fixed;right:24px;bottom:80px;z-index:40;width:44px;height:44px;border-radius:50%;background:var(--card);border:1px solid var(--card-border);color:var(--text2);font-size:18px;cursor:pointer;transition:all .25s ease;box-shadow:var(--card-shadow)}
.mode-btn:hover{color:var(--blue);border-color:var(--blue)}
.chart-tip{position:absolute;pointer-events:none;background:rgba(20,23,26,.92);color:#fff;font-size:11px;padding:5px 10px;border-radius:8px;white-space:nowrap;z-index:50;opacity:0;transition:opacity .15s ease;transform:translate(-50%,-100%)}
/* 桌面满宽双栏布局：.duo 主副栏（7:5），.duo-eq 等宽栏 */
.duo{display:grid;grid-template-columns:minmax(0,7fr) minmax(0,5fr);gap:16px;align-items:start}
.duo-eq{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:16px;align-items:start}
.duo>.card,.duo-eq>.card,.duo>div,.duo-eq>div{min-width:0}
/* 阅读行宽：长文字段不超 1000px，避免满宽下一行字太长 */
.card p,.card li{max-width:1000px}
@media(max-width:1100px){
  .duo,.duo-eq{grid-template-columns:1fr}
}
/* 触屏设备：禁用悬浮位移（避免滚动时卡片抖动），保留桌面端 hover 效果 */
@media(hover:none){
  .card:hover{transform:none;box-shadow:var(--card-shadow);border-color:var(--card-border)}
  tbody tr:hover{background:transparent}
  .card{transition:none}
}
@media(max-width:768px){
  .wrap{padding:12px 10px 92px}
  .grid2{grid-template-columns:1fr}
  /* 移动端 sticky 导航更高（横滑 chip 条），锚点留位加大 */
  [id]{scroll-margin-top:92px}
  nav.tabs{overflow-x:auto;top:6px;-webkit-overflow-scrolling:touch;scrollbar-width:none;padding:5px}
  nav.tabs::-webkit-scrollbar{display:none}
  nav.tabs a{padding:6px 12px;font-size:12.5px;flex:0 0 auto;white-space:nowrap}
  h1{font-size:17px}
  h1::before{height:20px;width:4px}
  .card{padding:14px 12px;border-radius:14px}
  /* 表格：窄屏不压扁，保底宽度 + 容器横向滚动 */
  table{font-size:12.5px;min-width:480px}
  th{padding:6px 6px;font-size:11px}
  td{padding:6px 6px}
  th,td{word-break:break-word}
  .card:has(table){overflow-x:auto;-webkit-overflow-scrolling:touch}
  .scroll{-webkit-overflow-scrolling:touch}
  /* hero 区：圆环缩放 + 正文字号回收 */
  .hero{padding:14px 12px;gap:12px}
  .rings{gap:8px;flex-wrap:nowrap}
  .ring-box svg{width:72px;height:72px}
  .hero-right .big{font-size:14.5px;line-height:1.6}
  .sig{font-size:11.5px;line-height:1.5;display:inline-block;margin:3px 0}
  .section-title{margin:22px 0 10px}
  .section-title .desc{display:none}
  .card p,.card li{max-width:none}
  ul.clean li{font-size:13px;padding-left:16px}
  .divider{font-size:11.5px;margin:12px 0 8px}
  .pool{padding:11px 12px;border-radius:11px}
  .ladder-table .board{min-width:52px}
  .score-chip-note{font-size:9.5px}
  /* 悬浮按钮贴近边缘、加大触控面积 */
  .back-top{right:12px;bottom:18px;width:42px;height:42px}
  .mode-btn{right:12px;bottom:68px;width:42px;height:42px}
  /* 触屏体验 */
  *{-webkit-tap-highlight-color:transparent}
  img,svg{max-width:100%}
  /* 横向滚动提示条：仅移动端显示（快照表列宽覆盖见 BASE_CSS 末尾专用块，需双类名提权） */
  .snap-hint{display:block;text-align:center;font-size:10.5px;color:var(--blue);
    background:var(--blue-soft);border-radius:8px;padding:4px 8px;margin-top:8px;letter-spacing:.3px}
  /* 通用表格：窄屏同样改 auto 布局，避免「两字一列」竖排 */
  table:not(.snap-table table){table-layout:auto}
}
/* 小屏手机（≤420px）进一步压缩 */
@media(max-width:420px){
  .wrap{padding:10px 8px 88px}
  h1{font-size:16px}
  .card{padding:12px 10px;border-radius:12px}
  table:not(.snap-table table){font-size:12px;min-width:440px}
  .ring-box svg{width:64px;height:64px}
  .rings{gap:6px}
  .hero-right .big{font-size:14px}
  .back-top,.mode-btn{width:38px;height:38px}
}
@media print{
  nav.tabs,.back-top,.mode-btn{display:none!important}
  body{background:#fff}
  .card{box-shadow:none;break-inside:avoid}
}
/* ⚠️ 移动端快照表覆盖块（必须放在 BASE_CSS 最末）
   原因：structure_table() 会在每张卡片内部再输出一份 <style>.snap-table table{min-width:920px;table-layout:fixed}
   该内联样式在文档中**位置更靠后**，与 BASE_CSS 规则同特异性时会赢 → 移动端断点全部失效。
   解法：用双类名 div.snap-table 提升特异性（0,2,1 > 0,1,1），跨位置稳定覆盖。
   09-15 二改：窄屏弃用「横向滚动 7 列表格」，改「每交易日一张卡片」纵向堆叠，彻底消除截断。 */
@media(max-width:768px){
  /* 提示条：用 div.snap-table 后代选择器（0,2,0）压过内联的 .snap-hint{display:none}（0,1,0） */
  div.snap-table{background:none;padding:0!important}
  div.snap-table .snap-hint{display:none}
  div.snap-table table{min-width:0!important;width:100%;table-layout:auto;border-collapse:separate;border-spacing:0}
  div.snap-table thead{display:none}
  div.snap-table tbody,div.snap-table tr,div.snap-table td{display:block;width:auto!important;min-width:0!important}
  div.snap-table tr{border:1px solid var(--card-border);border-radius:12px;padding:10px 12px;margin:0 0 10px;
    background:var(--card)}
  div.snap-table tr:nth-child(even) td,div.snap-table tbody tr:nth-child(even) td{background:transparent}
  div.snap-table tbody tr:hover td{background:transparent}
  div.snap-table td{border:0!important;padding:5px 0!important;font-size:12.5px;line-height:1.6;
    white-space:normal!important;word-break:break-word;overflow-wrap:anywhere}
  /* 字段名标签 */
  div.snap-table td[data-label]::before{content:attr(data-label);display:block;
    font-size:10.5px;color:var(--text3);font-weight:600;letter-spacing:.4px;margin-bottom:2px}
  /* 首行：日期 + 闸门 同行并排 */
  div.snap-table td[data-label="交易日"]{display:inline-block;font-weight:600;color:var(--text);
    font-size:13px;padding:0!important;margin-right:10px}
  div.snap-table td[data-label="交易日"]::before{display:none}
  div.snap-table td[data-label="闸门"]{display:inline-block;padding:0!important;font-weight:600}
  div.snap-table td[data-label="闸门"]::before{display:none}
  div.snap-table td[data-label="闸门"]{position:relative;margin-bottom:6px}
  /* 闸门之后加分隔线 */
  div.snap-table td[data-label="闸门"]::after{content:"";display:block;height:1px;
    background:var(--line);margin:8px 0 4px;width:100%}
  /* 中枢与最后一笔：短字段并排 */
  div.snap-table td[data-label="中枢 ZD-ZG"]{display:inline-block;width:auto!important;
    padding:5px 0!important;margin-right:16px;white-space:nowrap!important}
  div.snap-table td[data-label="中枢 ZD-ZG"]::before{display:inline;margin-right:4px}
  div.snap-table td[data-label="最后一笔"]{padding-top:2px!important}
}
@media(max-width:420px){
  div.snap-table tr{padding:9px 10px;border-radius:11px;margin-bottom:8px}
  div.snap-table td{font-size:12px;padding:4px 0!important}
  div.snap-table td[data-label]::before{font-size:10px}
}
/* ---------- 总看板：入口按钮 / 当日简述 ---------- */
.dl-btn{display:inline-block;font-size:13px;font-weight:600;padding:9px 16px;border-radius:10px;
  background:var(--blue);color:#fff;text-decoration:none}
.dl-btn:hover{filter:brightness(1.06)}
.dl-btn.ghost{background:var(--blue-soft);color:var(--blue);border:1px solid rgba(37,99,235,.25)}
.chip{display:inline-block;border-radius:20px;background:var(--blue-soft);color:var(--blue);
  font-weight:600;text-decoration:none;padding:5px 12px}
.chip:hover{background:rgba(37,99,235,.18)}
.day-line{font-size:14px;line-height:1.75;color:var(--text);word-break:break-word}
td.d{white-space:nowrap;font-weight:600;color:var(--text)}
/* 板块热力图（总看板） */
.hm-grid{display:flex;flex-direction:column;gap:3px;min-width:max-content}
.hm-row{display:grid;grid-template-columns:132px repeat(var(--hm-cols,10),minmax(34px,1fr));gap:3px;align-items:center}
.hm-head{position:sticky;top:0;z-index:1}
.hm-dl{text-align:center;color:var(--text3);font-size:10.5px;font-weight:600}
.hm-name{font-size:11.5px;font-weight:600;color:var(--text);text-align:right;padding-right:8px;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.hm-cell{height:28px;border-radius:5px;display:flex;align-items:center;justify-content:center;
  font-size:11px;font-weight:600;cursor:pointer;transition:opacity .15s}
.hm-cell:hover{opacity:.72}
@media(max-width:768px){
  .hm-row{grid-template-columns:100px repeat(var(--hm-cols,10),minmax(30px,1fr))}
  .hm-name{font-size:11px;padding-right:6px}
  .hm-cell{height:25px;font-size:10.5px}
}
"""



def _plate(n):
    """板数格式化：6.0 → '6'"""
    n = norm_num(n)
    return "-" if n is None else (f"{n:.0f}" if float(n).is_integer() else f"{n:g}")


def summary_line(rec):
    """当日简短总结（总看板用）：日期/闸门色点 + 关键读数 + 闸门定性首句。纯数据拼装，不引入主观判断。"""
    if not rec:
        return '<div style="color:#6B7280">暂无数据</div>'
    d = rec.get("date", "")
    m = {"limit_up": rec.get("limit_up"), "max_board": rec.get("max_board"),
         "structure": rec.get("structure"), "total": rec.get("total")}
    thin = [k for k, v in m.items() if v is None]
    g = (rec.get("gate") or "").strip()
    gc = gate_color(g) or "#6B7280"
    dot = (f'<span style="display:inline-block;width:8px;height:8px;border-radius:50%;'
           f'background:{gc};margin-right:7px;vertical-align:middle"></span>')
    head = re.split(r"[（(·]", g, maxsplit=1)[0].strip() if g else ""
    head = re.sub(r"^\s*[\U0001F300-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF]+\s*", "", head) or g[:26]
    gate_line = f"{dot}<b style=\"color:{gc}\">{head}</b>"
    tail = f'<span style="color:#8A94AD;font-size:12px">（{thin[0]} 等数据缺失，不推算）</span>' if thin else ""
    return (f'<div style="margin-bottom:6px">{d} · 闸门：{gate_line}　'
            f'<b>总分 {lg_num(rec.get("total"))}/100</b>（结构 {lg_num(rec.get("structure"))} + 情绪 '
            f'{lg_num(rec.get("sentiment"))}）　涨停 <b>{lg_num(rec.get("limit_up"))}</b> 家　'
            f'跌停 {lg_num(rec.get("limit_down"))} 家　最高连板 <b>{_plate(rec.get("max_board"))}</b> 板</div>'
            f'{tail}')


def lg_num(v):
    return f"{v:.0f}" if isinstance(v, (int, float)) else "-"


def build_overview(records):
    """总看板 ★ 当日总揽：评分趋势 + 连板趋势 + 板块热力图（紧凑 SVG/网格，单文件可分享）。
    配色遵循 A 股语义：红=强/流入，绿=弱/流出。"""
    if not records:
        return '<div class="card"><div style="color:#6B7280;padding:24px;text-align:center">暂无数据</div></div>'
    last = records[-1]
    sub = lambda x: f'<div style="color:#6B7280;font-size:11.5px;margin-bottom:10px">{x}</div>'
    card = lambda title, tag, note, body: (
        f'<div class="card"><h2>{title} <span class="tag" style="background:rgba(37,99,235,.1);'
        f'color:#2563EB">{tag}</span></h2>{sub(note)}{body}</div>')

    # ---- ① 评分趋势（总分/结构/情绪 三线，0-100） ----
    def mini_lines(series_list, w=680, h=210, fixed=(0, 100), ylab="分"):
        ds = sorted({d for s in series_list for d, v in s["points"] if v is not None})
        if not ds:
            return '<div style="color:#6B7280;padding:20px;text-align:center">暂无数据</div>'
        if fixed:
            y0, y1 = fixed
        else:
            vs = [v for s in series_list for d, v in s["points"] if v is not None]
            y0, y1 = min(vs), max(vs)
        y1 = max(y1, 1); y0 = min(y0, y1 - 1)
        L, R, T, B = 38, w - 58, 14, h - 26
        n = len(ds); step = (R - L) / max(n - 1, 1)
        xp = lambda i: L + i * step
        yp = lambda v: B - (v - y0) / (y1 - y0) * (B - T)
        P = []
        for k in range(5):
            v = y0 + (y1 - y0) * k / 4
            P.append(f'<line x1="{L}" y1="{yp(v):.1f}" x2="{R}" y2="{yp(v):.1f}" stroke="#EDF0F5"/>')
            P.append(f'<text x="{L-6}" y="{yp(v)+3.5:.1f}" text-anchor="end" font-size="9.5" fill="#8A94AD">{v:.0f}</text>')
        for i, d in enumerate(ds):
            if n <= 12 or i % max(1, n // 8) == 0 or i == n - 1:
                P.append(f'<text x="{xp(i):.1f}" y="{h-8}" text-anchor="middle" font-size="9.5" fill="#14171A" font-weight="600">{mmdd(d)}</text>')
        for s in series_list:
            pts, idx = [], {d: i for i, d in enumerate(ds)}
            for d, v in s["points"]:
                if v is not None and d in idx:
                    pts.append((xp(idx[d]), yp(v), d, v))
            if len(pts) > 1:
                P.append('<polyline fill="none" stroke="%s" stroke-width="%s" stroke-linejoin="round" points="%s"%s/>'
                         % (s["color"], s.get("width", 1.6),
                            " ".join(f"{x:.1f},{y:.1f}" for x, y, _, _ in pts),
                            ' stroke-dasharray="4 3"' if s.get("dash") else ""))
            for x, y, d, v in pts:
                P.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="2.6" fill="{s["color"]}" data-tip="{d} {s["label"]} {v:.0f}{ylab}"/>')
        # 右侧图例
        for j, s in enumerate(series_list):
            yy = T + 4 + j * 17
            P.append(f'<line x1="{R+8}" y1="{yy}" x2="{R+24}" y2="{yy}" stroke="{s["color"]}" stroke-width="2.6"'
                     + (' stroke-dasharray="4 3"' if s.get("dash") else "") + '/>')
            P.append(f'<text x="{R+28}" y="{yy+3.5}" font-size="10.5" fill="#5A6B8C">{s["label"]}</text>')
        return f'<svg viewBox="0 0 {w} {h}" width="100%" style="display:block;overflow:visible">{"".join(P)}</svg>'

    s_total = trim_series([(r["date"], r["total"]) for r in records])
    s_str = trim_series([(r["date"], r["structure"]) for r in records])
    s_sen = trim_series([(r["date"], r["sentiment"]) for r in records])
    body_score = mini_lines([
        {"points": s_total, "color": "#DC2626", "label": "总分", "width": 2.6},
        {"points": s_str, "color": "#2563EB", "label": "结构分", "width": 1.5},
        {"points": s_sen, "color": "#16A34A", "label": "情绪分", "width": 1.5, "dash": True},
    ])
    c_score = card("评分趋势", "0-100 同量纲", "总分粗实线 · 结构分实线 · 情绪分虚线 · 悬停查看当日数值", body_score)

    # ---- ② 连板趋势（柱 = 最高连板；若 JSON 有梯队数据则叠折线 = 连板家数） ----
    s_board = trim_series([(r["date"], r["max_board"]) for r in records])
    s_count = trim_series([(r["date"], r.get("lianban_count")) for r in records])
    has_count = any(v is not None for _, v in s_count)
    w, h = 680, 210
    ds = [d for d, v in s_board if v is not None]
    if not ds:
        body_lb = '<div style="color:#6B7280;padding:20px;text-align:center">暂无连板数据</div>'
    else:
        L, R, T, B = 38, w - 58, 14, h - 26
        n = len(ds); step = (R - L) / max(n - 1, 1)
        bmax = max([v for _, v in s_board if v is not None] + ([v for _, v in s_count if v is not None] if has_count else []))
        bmax = max(int(bmax) + 1, 3)
        y0, y1 = 0, bmax
        xp = lambda i: L + i * step
        yp = lambda v: B - (v - y0) / (y1 - y0) * (B - T)
        bw = max(6, min(20, step * 0.44))
        P = []
        for k in range(5):
            v = y0 + (y1 - y0) * k / 4
            P.append(f'<line x1="{L}" y1="{yp(v):.1f}" x2="{R}" y2="{yp(v):.1f}" stroke="#EDF0F5"/>')
            P.append(f'<text x="{L-6}" y="{yp(v)+3.5:.1f}" text-anchor="end" font-size="9.5" fill="#8A94AD">{v:.0f}</text>')
        for i, d in enumerate(ds):
            if n <= 12 or i % max(1, n // 8) == 0 or i == n - 1:
                P.append(f'<text x="{xp(i):.1f}" y="{h-8}" text-anchor="middle" font-size="9.5" fill="#14171A" font-weight="600">{mmdd(d)}</text>')
        for i, (d, v) in enumerate(s_board):
            if v is None:
                continue
            P.append(f'<rect x="{xp(i)-bw/2:.1f}" y="{yp(v):.1f}" width="{bw:.1f}" height="{B-yp(v):.1f}" rx="2.5" '
                     f'fill="#E03131" opacity="0.72" data-tip="{d} 最高 {_plate(v)} 板"/>')
            P.append(f'<text x="{xp(i):.1f}" y="{yp(v)-4:.1f}" text-anchor="middle" font-size="9.5" fill="#C92A2A" font-weight="600">{_plate(v)}</text>')
        if has_count:
            pts = []
            for i, (d, v) in enumerate(s_count):
                if v is not None:
                    pts.append((xp(ds.index(d)) if d in ds else None, v))
            pts = [(x, yp(v), v) for x, v in pts if x is not None]
            if len(pts) > 1:
                P.append('<polyline fill="none" stroke="#F59E0B" stroke-width="2" stroke-dasharray="5 3" points="%s"/>'
                         % " ".join(f"{x:.1f},{y:.1f}" for x, y, _ in pts))
        # 图例
        P.append(f'<rect x="{R+8}" y="{T}" width="16" height="11" rx="2" fill="#E03131" opacity="0.72"/>')
        P.append(f'<text x="{R+28}" y="{T+9.5}" font-size="10.5" fill="#5A6B8C">最高连板(板)</text>')
        if has_count:
            P.append(f'<line x1="{R+8}" y1="{T+23}" x2="{R+24}" y2="{T+23}" stroke="#F59E0B" stroke-width="2" stroke-dasharray="5 3"/>')
            P.append(f'<text x="{R+28}" y="{T+26.5}" font-size="10.5" fill="#5A6B8C">连板家数</text>')
        body_lb = f'<svg viewBox="0 0 {w} {h}" width="100%" style="display:block;overflow:visible">{"".join(P)}</svg>'
    if not has_count:
        body_lb += '<div style="color:#8A94AD;font-size:11px;margin-top:6px">连板家数（梯队总数）历史 JSON 未记录，故仅显示最高连板；自 09-19 起复盘 JSON 会写入 <span class="mono">lianban_count</span> 后自动出现虚线</div>'
    c_ladder = card("连板趋势", "高度 + 家数", "红柱 = 最高连板高度（左轴 板数）· 悬停查看当日数值", body_lb)

    # ---- ③ 板块热力图（近 15 个交易日 × 板块池） ----
    normed = []
    for r in sorted(records, key=lambda x: x.get("date") or ""):
        secs = []
        for s in r.get("sectors") or []:
            s2 = dict(s); s2["name"] = normalize_sector_name(s.get("name"))
            secs.append(s2)
        if secs:
            nr = dict(r); nr["sectors"] = secs; normed.append(nr)
    heads = [r["date"] for r in normed][-15:]
    if not heads:
        body_hm = '<div style="color:#6B7280;padding:20px;text-align:center">暂无板块景气数据</div>'
    else:
        grid = {}
        for r in normed:
            for s in r.get("sectors") or []:
                grid[(s["name"], r["date"])] = s.get("score")
        poolset = {normalize_sector_name(x) for x in SECTOR_POOL}
        extra = {}
        for d in heads[-3:]:
            for (nm, dd), sc in grid.items():
                if dd == d and nm and nm not in poolset and sc is not None:
                    extra[nm] = max(extra.get(nm, 0), sc)
        rows = list(SECTOR_POOL) + [k for k, _ in sorted(extra.items(), key=lambda x: -x[1])[:5]]

        def cell_color(sc):
            if sc is None:
                return "#F8F9FA", "#C4C9D4"
            if sc >= 75:
                return "#E03131", "#FFFFFF"
            if sc >= 60:
                return "#FA9A9A", "#14171A"
            if sc >= 45:
                return "#FDE8E8", "#14171A"
            if sc >= 30:
                return "#E6F4EA", "#14171A"
            if sc >= 18:
                return "#86EFAC", "#14171A"
            return "#16A34A", "#FFFFFF"

        ncol = len(heads)
        cells = ['<div class="hm-row hm-head"><div></div>']
        step_lb = max(1, ncol // 5)
        for i, d in enumerate(heads):
            lab = mmdd(d) if (i % step_lb == 0 or i == ncol - 1) else ""
            cells.append(f'<div class="hm-dl">{lab}</div>')
        cells.append('</div>')
        for nm in rows:
            cells.append(f'<div class="hm-row"><div class="hm-name" title="{nm}">{nm}</div>')
            for d in heads:
                sc = grid.get((nm, d))
                bg, fg = cell_color(sc)
                txt = f"{sc:.0f}" if sc is not None else "·"
                cells.append(f'<div class="hm-cell" style="background:{bg};color:{fg}" '
                             f'data-tip="{nm} {d}: {txt}">{txt}</div>')
            cells.append('</div>')
        leg = "".join(
            f'<span style="display:inline-flex;align-items:center;gap:5px">'
            f'<span style="width:15px;height:11px;border-radius:2px;background:{c};border:1px solid #E5E7EB"></span>{t}</span>'
            for c, t in [("#16A34A", "退潮（弱）"), ("#E6F4EA", "中性"), ("#FDE8E8", "升温"), ("#E03131", "主升（强）")])
        body_hm = (f'<div class="scroll"><div class="hm-grid" style="--hm-cols:{ncol}">{"".join(cells)}</div></div>'
                   f'<div style="display:flex;gap:14px;flex-wrap:wrap;align-items:center;margin-top:10px;font-size:11.5px;color:#5A6B8C">{leg}'
                   f'<span style="margin-left:auto;color:#8A94AD">A股语义：红=强/流入，绿=弱/流出 · 悬停查看分数</span></div>')
    c_hm = card("板块热力图", f"近 {len(heads)} 个交易日", "行 = 板块方向池，列 = 交易日 · 颜色越深表示景气度越强", body_hm)

    return c_score + c_ladder + c_hm


def render(records, out_path=None, replay_src=None):
    """渲染单页看板 HTML。
    `out_path` 默认 daily/dashboard.html；`replay_src` 可指定融合源（历史快照用当日 replay.html）。"""
    out_path = out_path or OUT
    dates = [r["date"] for r in records]
    # 2) 入口链接前缀：详细看板在 daily/detail/ 下，回总看板要 ../dashboard.html
    P = ""
    # 图表窗口：新口径 8/14 起（与"新口径 8/14 起"标签对齐，舍弃旧口径 8.11-8.13 折线，让折线与红柱起点一致）
    # x_max 动态取 records 最后一日；x_min 硬编码 8/14（若新口径变化再改此处）
    # 返回主入口的相对路径（看板在 daily/ 或 daily/{date}/ 下，层级不同）
    _entry_rel = os.path.relpath(os.path.join(DAILY, "entry.html"),
                                 os.path.dirname(out_path)).replace(os.sep, "/")
    chart_x_min = "2026-08-14"
    chart_x_max = dates[-1] if dates else None
    totals = trim_series([(r["date"], r["total"]) for r in records])
    structs = trim_series([(d, r["structure"]) for d, r in zip(dates, records)])
    sents = trim_series([(d, r["sentiment"]) for d, r in zip(dates, records)])
    lims = trim_series([(d, r["limit_up"]) for d, r in zip(dates, records)])
    boards = trim_series([(d, r["max_board"]) for d, r in zip(dates, records)])
    temps = trim_series([(r["date"], r["sentiment_old"]) for r in records])

    total_vals = [v for _, v in totals if v is not None]
    avg = sum(total_vals) / len(total_vals) if total_vals else None
    last = records[-1] if records else None

    # ---- 融合最新复盘（已换肤 + 按主题切块重组） ----
    ext = extract_latest_report(replay_src)
    ext_style = ""
    blocks = {}
    if ext:
        # 注意：只取样式内容（不带 <style> 标签），模板外层统一包裹，避免双重 <style> 嵌套
        ext_style = ext["style"] + "\n" + SEMANTIC_OVERRIDE
        if ext["body"]:
            blocks = classify_blocks(split_blocks(ext["body"]))
    hero = ext["hero"] if ext else ""

    # 自选股与持仓段（单页版：直接进正文，不再拆分）
    sec_watchlist = blocks.get("watchlist", "")

    # 各主题当日块
    sec_index = blocks.get("index", "")
    sec_structure = blocks.get("structure", "")
    sec_futures = blocks.get("futures", "")      # 七、大盘结构补充 · 股指期货跟踪（2026-09-21 单列）
    sec_score = blocks.get("score", "")
    sec_ladder = blocks.get("ladder", "")
    sec_theme = blocks.get("theme", "")
    # 龙虎榜段 2026-08-31 起移除：已独立成页 daily/lhb_dashboard.html（build_lhb_dashboard.py）
    sec_review = blocks.get("review", "")
    # 段六「预案复盘」不再单独成段 → 按主题拆解并入段三/段四
    # ⚠️ 必须在【段三组装之前】调用（段三在下方，别挪到后面，否则 UnboundLocalError）
    review_emo, review_thm = split_review(strip_holdings(sec_review))
    sec_pool = blocks.get("pool", "")
    sec_plan = blocks.get("plan", "")
    if not blocks and ext and ext["body"]:
        # 降级：无锚点无法切块时，全部当日内容并入大盘结构区顶部
        sec_structure = ext["body"]

    lg = lambda x: f"{x:.0f}" if isinstance(x, (int, float)) else "-"
    gate_c = gate_color((last or {}).get("gate", "")) or "#14171A"

    avg_disp = f"{avg:.0f}" if avg is not None else "-"
    avg_val = round(avg) if avg is not None else 0
    last_total = lg(last["total"]) if last else "-"
    last_val = round(last["total"]) if last else 0
    stat_chip = "background:var(--card);border:1px solid var(--card-border);border-radius:14px;padding:10px 16px;flex:0 0 auto;display:flex;align-items:baseline;gap:10px;box-shadow:var(--card-shadow)"
    stat_lbl = "color:var(--text2);font-size:12px;white-space:nowrap"
    stats = f"""
    <div style="display:flex;gap:10px;flex-wrap:wrap;align-items:stretch">
      <div style="{stat_chip}">
        <span style="{stat_lbl}">累计交易日</span>
        <span class="num count-up" style="color:var(--text);font-size:20px;font-weight:500" data-val="{len(records)}">{len(records)}</span>
      </div>
      <div style="{stat_chip}">
        <span style="{stat_lbl}">平均总分</span>
        <span style="color:var(--blue);font-size:20px;font-weight:500"><span class="num count-up" data-val="{avg_val}">{avg_disp}</span><span style="font-size:13px;color:var(--text2)">/100</span></span>
      </div>
      <div style="{stat_chip}">
        <span style="{stat_lbl}">最近总分</span>
        <span style="color:var(--up);font-size:20px;font-weight:500"><span class="num count-up" data-val="{last_val}">{last_total}</span><span style="font-size:13px;color:var(--text2)">/100</span></span>
      </div>
      <div style="background:var(--card);border:1px solid var(--card-border);border-radius:14px;padding:10px 16px;flex:1 1 320px;min-width:240px;box-shadow:var(--card-shadow)">
        <div style="{stat_lbl};margin-bottom:2px">指数闸门</div>
        <div style="color:{gate_c};font-size:15px;font-weight:500;line-height:1.55">{(last or {}).get("gate", "-")}</div>
      </div>
    </div>"""

    sec_title = lambda no, color, name, desc: (
        f'<div class="section-title"><span class="no" style="background:{color}">{no}</span>'
        f'<span class="name">{name}</span><span class="desc">{desc}</span></div>')

    def render_ladder_stock_cards(ladder_html):
        """涨停梯队表格 → 紧凑 table 布局（板数/标的/题材·状态 三列），td 自动换行处理多标的行。
        兼容 3 列（板数/家数/标的题材）和 4 列（板数/标的/题材/状态）两种格式。"""
        if not ladder_html:
            return ''
        rows = re.findall(r'<tr>(.*?)</tr>', ladder_html, re.S)
        data_rows = [r for r in rows if '<td' in r]
        items = []
        for r in data_rows:
            tds = re.findall(r'<td[^>]*>(.*?)</td>', r, re.S)
            if len(tds) < 1:
                continue
            board_html = re.sub(r'<[^>]+>', '', tds[0]).strip() or '?'
            # 兼容 3 列（tds[1]=家数, tds[2]=标的+题材）与 4 列（tds[1]=标的, tds[2]=题材, tds[3]=状态）
            if len(tds) >= 4:
                names_text = re.sub(r'<[^>]+>', '', tds[1]).strip()
                extra_text = re.sub(r'<[^>]+>', '', tds[2]).strip()
                status_text = re.sub(r'<[^>]+>', '', tds[3]).strip() if len(tds) > 3 else ''
                meta_text = ' · '.join([t for t in [extra_text, status_text] if t])
            else:
                # 3 列：第 2 列当作家数/合并，第 3 列是标的+题材
                count_text = re.sub(r'<[^>]+>', '', tds[1]).strip() if len(tds) > 1 else ''
                inner = re.sub(r'<[^>]+>', '', tds[2]) if len(tds) > 2 else ''
                # 标的+题材合并列：用「·」分项；保留家数
                meta_text = inner.strip()
                if count_text:
                    board_html = f'{board_html} ({count_text})'
            items.append((board_html, names_text, meta_text))
        if not items:
            return ladder_html
        rows_html = ''.join(f'<tr><td class="board">{b}</td><td class="names">{n}</td><td class="meta">{m}</td></tr>' for b, n, m in items)
        style = '''<style>.ladder-table table{width:100%;border-collapse:collapse;font-size:13px}.ladder-table th{text-align:left;padding:8px 10px;background:var(--blue-soft);color:var(--blue);font-weight:600;border-bottom:1px solid var(--line);font-size:12px}.ladder-table td{padding:8px 10px;border-top:1px solid rgba(228,231,235,.5);vertical-align:top;line-height:1.5}.ladder-table .board{font-weight:600;color:var(--blue);min-width:64px;white-space:nowrap}.ladder-table .names{font-weight:500;color:var(--text);word-break:break-word}.ladder-table .meta{color:var(--sub);font-size:12px;word-break:break-word}.ladder-table tr:hover td{background:var(--card2)}</style>'''
        return f'<div class="ladder-table">{style}<table><thead><tr><th>板数</th><th>标的</th><th>题材 / 状态</th></tr></thead><tbody>{rows_html}</tbody></table></div>'

    # 主题组装（当日详情 + 跨日演变 融合；桌面端双栏布局）
    # 自选股/持仓段直接进正文（单页版）
    # 大盘结构：当日分析存在时双栏（左分析/右"闸门+中枢"），快照移出窄栏改为全宽卡片。
    # 2026-09-01 修复：snapshot 曾移入 duo 第二栏填充右栏下方空白。
    # 2026-09-07 重构（用户反馈快照越写越长）：快照 7 列在右窄栏被挤成每日一块的多行大块且随交易日无限增长，
    # 改回全宽卡片置于 duo 之下 + structure_table 紧凑化（一行一日 + 最近 10 日默认展开、更早折叠），段落高度恒定。
    gate_card = ('<div class="card"><h2>闸门时间线 <span class="tag" style="background:rgba(124,92,252,.1);color:#7C5CFC">红=可开仓 黄=仅验证 蓝=防守</span></h2>'
                 + gate_timeline(records) + '</div>')
    center_card = ('<div class="card"><h2>中枢区间演变</h2><div style="color:#6B7280;font-size:12px;margin-bottom:8px">每日 30m 中枢 ZD-ZG 区间带，观察中枢抬升/下移/扩张</div>'
                   + center_band(records) + '</div>')
    snapshot_card = ('<div class="card"><h2>大盘结构快照 <span class="tag" style="background:rgba(37,99,235,.08);color:#2563EB">最近 5 个交易日</span></h2>'
                     '<div style="color:#6B7280;font-size:12px;margin-bottom:8px">闸门配色（与下方「闸门时间线」统一）：'
                     '<span style="color:#E03131;font-weight:600">● 红=可开仓</span> · '
                     '<span style="color:#F59E0B;font-weight:600">● 黄=仅验证</span> · '
                     '<span style="color:#2563EB;font-weight:600">● 蓝=防守</span>'
                     '　·　每日 闸门/30m结构/中枢/笔/MACD/波浪；文本完整展示（2026-09-21 起取消截断，不再有省略号）</div>'
                     + structure_table(trim_records(records, "sd")) + '</div>')
    if sec_structure:
        structure = (sec_title(2, "#7C5CFC", "大盘结构", "当日缠论分析 + 跨日闸门/中枢演变 + 大盘快照")
                     + '<div class="duo"><div>' + sec_structure + '</div><div>'
                     + gate_card + center_card + '</div></div>'
                     + snapshot_card)
    else:
        structure = (sec_title(2, "#7C5CFC", "大盘结构", "跨日闸门/中枢演变（当日缠论分析并入市场总览）")
                     + gate_card + center_card + snapshot_card)
    # 期指跟踪（段七）→ 归入「大盘结构」段（语义上就是它的补充），置于快照之后
    if sec_futures:
        structure += sec_futures
    # 复盘沉淀不再放段三（用户明确：复盘应作为「明日推演」的子模块 ①）
    review_card = review_emo + review_thm
    sentiment = (sec_title(3, "#D97706", "评分与情绪", "双维评分 + 趋势 + 梯队生态")
                 + sec_score
                 + '<div class="duo-eq">'
                 + '<div class="card"><h2>评分体系总览 <span class="tag" style="background:rgba(37,99,235,.1);color:#2563EB">新口径 8/14 起</span></h2>'
                 + '<div style="color:#6B7280;font-size:12px;margin-bottom:8px">统一 0-100 刻度（结构+情绪=总分同一量纲）· 总分粗实线，情绪虚线</div>'
                 + svg_combined_chart([
                     {"points": totals,  "color": "#DC2626", "yaxis": "left", "label": "总分",   "stroke_width": 3},
                     {"points": structs, "color": "#2563EB", "yaxis": "left", "label": "结构分", "stroke_width": 1.5},
                     {"points": sents,   "color": "#16A34A", "yaxis": "left", "label": "情绪分", "stroke_width": 1.5, "dash": True},
                 ], x_min=chart_x_min, x_max=chart_x_max, single_axis=True)
                 + '</div>'
                 + '<div class="card"><h2>市场温度 · 涨停与连板高度</h2>'
                 + '<div style="color:#6B7280;font-size:12px;margin-bottom:8px">左 Y 轴 0-100+（涨停家数折线），右 Y 轴 0-6（连板柱状）· 柱状 opacity 0.45 让折线穿透可见</div>'
                 + svg_combined_chart([
                     {"points": lims, "color": "#F59E0B", "yaxis": "left",  "label": "涨停家数(家)", "kind": "line"},
                     {"points": boards, "color": "#E03131", "yaxis": "right", "label": "最高连板(板)", "kind": "bar", "opacity": 0.45},
                 ], x_min=chart_x_min, x_max=chart_x_max)
                 + '</div>'
                 + '</div>'
                 + zt_quality_table(records[-1].get("date") if records else None,
                                    ladder_html=sec_ladder))
    # ============ 净化（2026-09-21 用户原则）============
    # ① 持仓内容不上看板、不上云（持仓改由本地 md「持仓跟踪.md」轻量跟踪）
    # ② 「方向跟踪层」原属自选股，迁入题材段（用户：可融合进题材或连板生态跟踪）
    hero = strip_holdings(hero)
    sec_theme = strip_holdings(sec_theme)
    sec_watchlist, sec_dir_track = extract_dir_track(strip_holdings(sec_watchlist))
    sec_review = strip_holdings(sec_review)
    sec_pool = strip_holdings(sec_pool)
    sec_plan = strip_holdings(sec_plan)
    # 方向跟踪层：**不再占独立卡片**（用户反馈「这个模块现在空了」+「应整合进题材资金」）
    # → 作为一行紧凑小注并入段四的题材内容顶部
    if sec_dir_track:
        sec_theme = ('<div class="divider" style="margin:0 0 10px">'
                     '方向跟踪层（原属自选股）：'
                     # 抽出的原文自带「方向跟踪层：」前缀 → 去掉，避免标题重复
                     + re.sub(r'^方向跟踪层[：:]\s*', '', sec_dir_track)
                     + '　<span style="color:var(--text3)">✅ 成立 / ⚠️ 待观察 / ❌ 失败</span></div>'
                     ) + sec_theme
    dir_track_card = ""
    # 自选股表（票/身位/结果 三列）加 class：修「td:last-child 右对齐」导致的列间大空隙
    sec_watchlist = sec_watchlist.replace("<table>", '<table class="t-lft">', 1)
    theme = (sec_title(4, "#0E8A5F", "题材与资金", "题材主线 + 方向跟踪 + 板块景气资金")
             + dir_track_card
             + sec_theme
             + '<div class="card"><h2>资金趋势 · 景气板块热力图</h2>'
             + '<div style="color:#6B7280;font-size:12px;margin-bottom:8px">颜色深浅＝景气度（0-100） · 红=高景气，绿=低景气 · 悬停查看当日分与原始指标<br>数据源：OneDrive 全量A股按细分行业聚合（≈280 板块/日，非手写 TOP5） · 行＝窗口内累计成交额 TOP18 的<b>L1 方向</b>（显式分类表归口，名字即范围） · 列＝最近 10 个交易日<br><b>格内＝3 日平滑分</b>（0.5/0.3/0.2，抑制单日噪声）；<b>基准口径＝当日截面分位</b>（强度30/资金30/宽度20/量能20，中性化＋<b>固定尺度</b>后取正态分位；中位恒≈50）→ <b>横向与纵向均可比</b>；当日整体强弱另见底部「当日中位涨幅」条</div>'
             + sector_heatmap(records) + '</div>')
    # 段六定位（2026-09-21 明确）：这是 **AI 对昨日预案的复盘**（预案闭环），
    # 不是用户个人的实盘操作记录 → 标题与说明同步改写
    # 段六 已拆解并入段三/段四 → 这里不再单独成段（保留空串以兼容下游拼接）
    ops = ""          # 段六已拆解并入段三/段四，不再单独成段
    if sec_pool and sec_plan:
        decision = (sec_title(5, "#C92A2A", "明日推演", "基于多日追踪对明日盘面的推演（候选池 + 预案）· 非个人操作指令")
                    + '<div class="duo"><div>' + sec_pool + '</div><div>' + sec_plan + '</div></div>')
    else:
        decision = (sec_title(5, "#C92A2A", "明日推演", "基于多日追踪对明日盘面的推演（候选池 + 预案）· 非个人操作指令")
                    + sec_pool + sec_plan)
    history = sec_title(6, "#5F6B7A", "历史总览", "每日 总分/档位/闸门/结构/情绪/涨停/连板") + \
        '<div class="card">' + history_table(trim_records(records, "total")) + '</div>'
    # 段五「自选股」整段删除（2026-09-21 用户明确要求）
    watchlist = ""

    # 导航（单页：含自选股）
    tabs = []
    for k, v in [("mkt", "市场总览"), ("structure", "大盘结构"), ("sentiment", "评分情绪"),
                 ("theme", "题材资金"),
                 ("decision", "明日推演"), ("history", "历史总览")]:
        tabs.append(f'<a href="#{k}" data-spy="{k}">{v}</a>')
    nav_html = chr(10).join("  " + t for t in tabs)

    # ---------- 市场总览 ----------
    mkt = (sec_title(1, "#2563EB", "市场总览", "决策摘要 · 指数概况 · 核心统计")
           + hero + enrich_index_amount_mom(sec_index, records) + stats)

    html = f"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>盯盘复盘 · 趋势与决策看板</title>
<style>
{ext_style}
{BASE_CSS}
</style>
<style id="darkcss"></style>
</head>
<body><div class="wrap">
<h1>盯盘复盘 · 趋势与决策看板 <a href="{_entry_rel}" style="margin-left:auto;font-size:13px;font-weight:500;color:#2563EB;text-decoration:none;border:1px solid rgba(37,99,235,.3);background:rgba(37,99,235,.06);padding:5px 11px;border-radius:8px;white-space:nowrap">← 返回主入口</a></h1>
<div class="sub">按主题组织：每个主题 = 当日详情 + 跨日演变 ｜ 数据源：通达信 ｜ 更新：{dates[-1] if dates else "-"} ｜ A股语义色：红涨绿跌</div>
<nav class="tabs" id="topnav">
{nav_html}
</nav>

<section id="mkt">{mkt}</section>
<section id="structure">{structure}</section>
<section id="sentiment">{sentiment}</section>
<section id="theme">{theme}</section>
<section id="decision">{decision}</section>
<section id="history">{history}</section>

<div class="footer">本看板仅供复盘参考，不构成投资建议 ｜ 由 build_dashboard.py 自动生成，每日复盘自动融合</div>
</div>
<div class="chart-tip" id="chartTip"></div>
<button class="mode-btn" id="modeBtn" title="切换深浅模式">◐</button>
<button class="back-top" id="backTop" title="回到顶部">↑</button>
<script>
(function(){{
  var tip=document.getElementById('chartTip'),mBtn=document.getElementById('modeBtn'),bTop=document.getElementById('backTop');
  var html=document.documentElement;
  var darkCss=`html[data-theme=dark]{{
    --bg:#0B1220;--bg-grad:linear-gradient(180deg,#0E1626 0%,#0B1220 100%);
    --card:rgba(19,28,46,.94);--card-border:#1E293B;--card-shadow:0 2px 12px rgba(0,0,0,.3);
    --text:#E6E8EE;--text2:#8B93A5;--text3:#64748B;
    --blue:#3B82F6;--blue-soft:rgba(59,130,246,.14);--purple:#A78BFA;--purple-soft:rgba(167,139,250,.16);
    --up:#EF4444;--down:#10B981;--hold:#F59E0B;
    --th-bg:rgba(59,130,246,.08);--row-line:rgba(30,41,59,.6);
    --nav-bg:rgba(11,18,32,.9);--chip:#131C2E;
  }}
  html[data-theme=dark] .card{{border-color:#1E293B}}
  html[data-theme=dark] th{{color:#94A3B8}}
  html[data-theme=dark] .pool{{border-color:rgba(167,139,250,.35)}}
  html[data-theme=dark] tbody tr:hover{{background:rgba(59,130,246,.06)}}
  html[data-theme=dark] .chart-tip{{background:rgba(226,232,240,.95);color:#0B1220}}
  html[data-theme=dark] .footer{{color:#64748B}}
  html[data-theme=dark] .sub{{color:#94A3B8}}
  html[data-theme=dark] .note{{border-top-color:#1E293B}}
  `;
  function applyTheme(t){{
    html.setAttribute('data-theme',t);
    mBtn.textContent=t==='dark'?'☀':'◐';
  }}
  applyTheme('light');
  mBtn.addEventListener('click',function(){{
    var cur=html.getAttribute('data-theme')==='dark'?'light':'dark';
    applyTheme(cur);
    var s=document.getElementById('darkcss');
    s.textContent=cur==='dark'?darkCss:'';
  }});
  window.addEventListener('scroll',function(){{
    bTop.classList.toggle('show',window.scrollY>600);
  }},{{passive:true}});
  bTop.addEventListener('click',function(){{
    window.scrollTo({{top:0,behavior:'smooth'}});
  }});
  var spies=[].slice.call(document.querySelectorAll('nav.tabs a[data-spy]'));
  var secs=spies.map(function(a){{return document.getElementById(a.getAttribute('data-spy'));}});
  function onScroll(){{
    var pos=window.scrollY+120,cur=spies[0];
    secs.forEach(function(s,i){{if(s&&s.offsetTop<=pos)cur=spies[i];}});
    spies.forEach(function(a){{a.classList.remove('active');}});
    if(cur)cur.classList.add('active');
  }}
  window.addEventListener('scroll',onScroll,{{passive:true}});onScroll();
  spies.forEach(function(a){{
    a.addEventListener('click',function(e){{
      e.preventDefault();
      var t=document.getElementById(a.getAttribute('data-spy'));
      if(t)t.scrollIntoView({{behavior:'smooth',block:'start'}});
    }});
  }});
  document.addEventListener('mouseover',function(e){{
    var t=e.target.closest('[data-tip]');
    if(t){{tip.textContent=t.getAttribute('data-tip');tip.style.opacity=1;}}
  }});
  document.addEventListener('mousemove',function(e){{
    if(tip.style.opacity==='1'){{tip.style.left=e.pageX+'px';tip.style.top=(e.pageY-14)+'px';}}
  }});
  document.addEventListener('mouseout',function(e){{
    var t=e.target.closest('[data-tip]');
    if(t)tip.style.opacity=0;
  }});
  function countUp(el){{
    var target=parseFloat(el.getAttribute('data-val'))||0;
    var dec=(el.getAttribute('data-val').split('.')[1]||'').length;
    var dur=600,start=null;
    function step(ts){{
      if(!start)start=ts;
      var p=Math.min((ts-start)/dur,1);
      var ease=1-Math.pow(1-p,3);
      el.textContent=(target*ease).toFixed(dec);
      if(p<1)requestAnimationFrame(step);
    }}
    requestAnimationFrame(step);
  }}
  var io=new IntersectionObserver(function(entries){{
    entries.forEach(function(en){{
      if(en.isIntersecting){{countUp(en.target);io.unobserve(en.target);}}
    }});
  }},{{
    threshold:.3
  }});
  [].slice.call(document.querySelectorAll('.count-up')).forEach(function(el){{io.observe(el);}});
}})();
</script>
</div>
</body></html>"""
    # ⭐ 统一净化（2026-09-21）：sec_* 的赋值分散在多处（早段净化会被后段覆盖），
    #    改为对**最终 html** 全文净化一次 → 无论段落顺序如何都覆盖到
    #    （持仓不上看板/不上云；「操作评价」→「预案复盘」；「自选股与持仓」→「自选股」）
    # 「明日推演」段内注入两个子模块：① 当日对昨日预案的复盘（review_card）② 明日盘面推演
    html = re.sub(
        r'(<span class="name">明日推演</span><span class="desc">[^<]*</span></div>)',
        lambda m: (m.group(1)
                   + '<div class="divider" style="margin:6px 0">① 当日对昨日预案的复盘</div>'
                   + review_card
                   + '<div class="divider" style="margin:22px 0 6px">② 明日盘面推演（候选池 + 预案）</div>'),
        html, count=1)
    html = strip_holdings(html)
    # 历史总览默认折叠（用户要求：不用一直延伸展示）
    html = re.sub(r'(<section id="history">)(.*?)(</section>)',
                  r'\1<details class="fold"><summary>展开历史总览（逐日明细）</summary>\2</details>\3',
                  html, count=1, flags=re.S)
    if out_path:
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(html)
        if True:
            print(f"看板已生成: {out_path}（{len(records)} 个交易日，主题重组融合最新复盘: {ext.get('date', '无')}）")
    return html


# ---------- 单页看板（2026-09-18 回滚恢复） ----------
# daily/dashboard.html → 单页入口：市场全景 + 自选股/持仓，一页看完




def post_dense(out_path):
    """自动重排密集区：处理 daily/{date}/05_复盘与明日预案.html（不是 dashboard.html）"""
    import subprocess, sys, os, glob
    # out_path 是 dashboard.html 路径，反推 daily 目录
    daily_dir = os.path.dirname(out_path)
    files = sorted(glob.glob(os.path.join(daily_dir, '20*', '05_复盘与明日预案.html')))
    if not files:
        return
    tool = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'restyle_dense.py')
    if not os.path.exists(tool):
        return
    # 增量处理：只处理尚未包含 chip 标记的文件（避免重复）
    for f in files:
        try:
            content = open(f, encoding='utf-8').read()
            if 'class="score-chips"' in content and 'class="score-chips"' in open(tool, encoding='utf-8').read():
                # 已处理过：跳过（但也检查 stock-cards 同样）
                if 'class="stock-cards"' in content:
                    continue
            subprocess.run([sys.executable, tool, f], capture_output=True)
        except Exception:
            pass

def write_redirect_stub(path, target, date_str=None):
    """把 `daily/dashboard.html` 写成**极简跳转存根**（≈1 KB，不是看板副本）。

    背景（2026-09-21 用户调整）：详细看板**只保留每日存档** `daily/{date}/dashboard.html`，
    不再每天多生成一份同内容的「活页副本」——两份一模一样的 dashboard 会让人误以为重复产出。
    但 `daily/dashboard.html` 是云端 `/dashboard.html` 的源、且可能已被分享出去，
    直接删除会造成旧链接 404 → 降级为跳转存根，指向最新一日的存档。
    """
    tgt = target.replace(os.sep, "/")
    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>盯盘复盘 · 详细看板（跳转中）</title>
<meta http-equiv="refresh" content="0;url={tgt}">
<link rel="canonical" href="{tgt}">
<style>body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;
background:#F5F6F8;color:#14171A;margin:0;display:flex;align-items:center;justify-content:center;height:100vh}}
.b{{background:#fff;border:1px solid #E4E7EB;border-radius:12px;padding:24px 28px;text-align:center;box-shadow:0 1px 2px rgba(20,23,26,.04)}}
h1{{font-size:17px;margin:0 0 8px;font-weight:600}}p{{font-size:13.5px;color:#6B7280;margin:6px 0}}
a{{color:#2563EB;text-decoration:none;font-weight:600}}</style></head>
<body><div class="b">
<h1>详细看板已改为「每日存档」</h1>
<p>正在跳转到最新一日（{date_str or target}）的详细看板…</p>
<p><a href="{tgt}">若未自动跳转，点这里 →</a></p>
<p style="margin-top:12px;font-size:12px">入口页：<a href="entry.html">主入口</a>（含日期选择器，可回看任意交易日）</p>
</div></body></html>
"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    return path


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    # ⚠️ 2026-09-22 修复：ROOT 由模块顶部的 sys.argv 扫描设置（第 17-19 行），
    #    但 argparse 不认识 `--root` → 传 `--root` 时在 parse_args 处被拒、直接退出。
    #    **本机跑从不传 `--root`（ROOT 硬编码）所以一直没暴露；一到"换根目录"场景（云端/测试根）必炸。**
    #    这里显式声明该参数（值已在模块顶部生效），使脚本可在任意根目录下运行。
    ap.add_argument("--root", help="项目根目录（默认本机路径；由模块顶部扫描设置）")
    ap.add_argument("--snapshot", help="为指定交易日生成快照 YYYYMMDD→daily/{date}/dashboard.html"
                                       "（records 截断到该日 + 用该日 replay.html 作融合源）")
    ap.add_argument("--no-daily-snapshot", action="store_true",
                    help="不生成每日存档（退化为写 daily/dashboard.html 活页）")
    args = ap.parse_args()

    recs = collect()
    if args.snapshot:
        d8 = args.snapshot.replace("-", "")
        recs = [r for r in recs if r["date"].replace("-", "") <= d8]
        if not recs:
            print(f"✗ {d8} 之前没有可用交易日数据")
            raise SystemExit(1)
        src = os.path.join(DAILY, d8, "replay.html")
        out = os.path.join(DAILY, d8, "dashboard.html")
        render(recs, out_path=out, replay_src=(src if os.path.exists(src) else None))
        print(f"  快照融合源: {os.path.relpath(src, ROOT) if os.path.exists(src) else '（无，用最新）'}")
    else:
        # ⭐ 2026-09-21 架构调整（用户）：详细看板**只保留每日存档** `daily/{date}/dashboard.html`。
        #   不再每天额外产出一份同内容的 `daily/dashboard.html`（两份一样 → 看起来像重复产物）。
        #   `daily/dashboard.html` 降级为≈1 KB 的**跳转存根** → 指向最新一日存档，
        #   以保住已分享出去的旧链接（含云端 `/dashboard.html`）。
        last_day = recs[-1]["date"].replace("-", "") if recs else None
        if last_day and not args.no_daily_snapshot:
            out = os.path.join(DAILY, last_day, "dashboard.html")
            os.makedirs(os.path.dirname(out), exist_ok=True)
        else:
            out = OUT
        render(recs, out_path=out)
        if last_day and out != OUT:
            post_dense(out)
            write_redirect_stub(OUT, f"{last_day}/dashboard.html", f"{last_day[:4]}-{last_day[4:6]}-{last_day[6:]}")
            print(f"  每日存档: {os.path.relpath(out, ROOT)}   ← 唯一详细看板产物")
            print(f"  跳转存根: {os.path.relpath(OUT, ROOT)} → {last_day}/dashboard.html")
        else:
            post_dense(OUT)

