"""问题分类：可计算 / 需检索 / 拒答。

**分类用规则，不用模型。** 分类错了会直接影响答案来源，而模型分类不可复现、
改一次提示词结果就变。用模式匹配虽然覆盖有限，但每次结果一样、可测、可解释。

分类失败的默认行为是**走检索**，不是拒答——宁可多检索一次，
也不要把能答的问题挡在门外。拒答留给「检索也找不到依据」的情形。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .universe import Universe, load_universe

# 级别词 → 标准枚举值
LEVEL_WORDS = {
    "部级": "部级", "部长": "部级", "副部长": "部级", "副部级": "部级",
    "司局级": "司局级", "司长": "司局级", "局长": "司局级", "厅级": "司局级",
    "处级": "其他人员", "科级": "其他人员", "一般人员": "其他人员",
    "其他人员": "其他人员", "普通人员": "其他人员",
}

# 费用类别词 → 规则里的类别
CATEGORY_WORDS = {
    "住宿": "住宿费", "酒店": "住宿费", "宾馆": "住宿费", "房费": "住宿费",
    "伙食": "伙食补助费", "餐费": "伙食补助费", "吃饭": "伙食补助费",
    "市内交通": "市内交通费", "打车": "市内交通费", "出租车": "市内交通费",
    "城市间交通": "城市间交通费", "火车": "城市间交通费", "飞机": "城市间交通费",
    "交通费": "城市间交通费",
}

# 金额类问句的特征词。出现这些词才说明问的是「多少钱」，而非「要什么材料」
AMOUNT_HINTS = [
    "多少", "多少钱", "上限", "限额", "标准", "能报", "最多", "额度", "包干",
    "怎么算", "按什么标准", "一天", "每天",
]

# 明确问「材料 / 凭证」的问句 → 直接走检索
VOUCHER_HINTS = ["凭证", "材料", "发票", "票据", "审批单", "需要什么", "要哪些"]

# 制度没规定、应当明确拒答的话题
OUT_OF_SCOPE_HINTS = [
    "生病", "医疗", "看病", "住院", "药费",
    "工伤", "保险理赔", "退休", "工资", "加班",
]


@dataclass
class Classification:
    route: str                       # compute / retrieve
    reason: str
    province: str | None = None
    city: str | None = None
    person_level: str | None = None
    category: str | None = None
    as_of: str | None = None
    month: int | None = None
    out_of_scope: bool = False
    notes: list[str] = field(default_factory=list)


def _find_date(text: str) -> str | None:
    """从问句里抽年份，转成该年的一个代表日期。

    只认年份不认具体日：问句通常只说「2013 年出差」，具体日期要靠申请人提供。
    用年中做代表日，避免跨年边界的情形被误判。
    """
    m = re.search(r"(20\d{2})\s*年", text)
    if not m:
        return None
    year = int(m.group(1))
    if not (2000 <= year <= 2100):
        return None
    return f"{year}-07-01"


def _find_city(text: str, universe: Universe) -> tuple[str | None, str | None]:
    """从问句里找城市，返回 (省, 市)。同时认省份名本身。"""
    for province in universe.provinces:
        if province in text:
            return province, None
    for province, cells in universe.cities_by_province.items():
        for cell in cells:
            if cell.is_synthetic:
                continue
            # 「南京市」与「南京」都要认
            short = cell.city.rstrip("市州")
            if cell.city in text or (len(short) >= 2 and short in text):
                return province, cell.city
    return None, None


def _find_level(text: str) -> str | None:
    # 长词优先，避免「副部长级」被「部长」先匹配
    for word in sorted(LEVEL_WORDS, key=len, reverse=True):
        if word in text:
            return LEVEL_WORDS[word]
    return None


def _find_category(text: str) -> str | None:
    for word in sorted(CATEGORY_WORDS, key=len, reverse=True):
        if word in text:
            return CATEGORY_WORDS[word]
    return None


def _find_month(text: str) -> int | None:
    """从问句里抽月份。只认「8 月」「八月」这类明确表述。

    不做「夏天」这类模糊推断——旺季判定要精确到月，推错了比不推更糟。
    """
    m = re.search(r"(1[0-2]|[1-9])\s*月", text)
    if m:
        return int(m.group(1))
    cn = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6,
          "七": 7, "八": 8, "九": 9, "十": 10, "十一": 11, "十二": 12}
    for word in sorted(cn, key=len, reverse=True):
        if f"{word}月" in text:
            return cn[word]
    return None


def classify(question: str, universe: Universe | None = None,
             today: str = "2026-10-19") -> Classification:
    """判定问题走哪条路径。"""
    universe = universe or load_universe()
    text = question.strip()

    # 1. 制度明确没覆盖的话题，直接标出（仍走检索，由检索结果决定是否拒答）
    if any(h in text for h in OUT_OF_SCOPE_HINTS):
        return Classification(
            route="retrieve", reason="话题疑似超出制度范围，交给检索判定是否拒答",
            out_of_scope=True, notes=["命中超范围提示词，但仍先检索，避免误拒"],
        )

    as_of = _find_date(text)
    month = _find_month(text)
    province, city = _find_city(text, universe)
    level = _find_level(text)
    category = _find_category(text)

    # 2. 明确问凭证 / 材料的，走检索
    if any(h in text for h in VOUCHER_HINTS):
        return Classification(
            route="retrieve", reason="问的是凭证或材料，属于条文表述，不是可算的数值",
            province=province, city=city, person_level=level, category=category,
            as_of=as_of, month=month,
        )

    # 3. 可计算：有金额特征词 + 能定位到费用类别
    #    住宿费还要求定位到地区或级别（限额按地区×级别给，缺了就报不出具体数）；
    #    伙食与市内交通是全国统一定额（少数省份例外），不需要地区也能答。
    has_amount_hint = any(h in text for h in AMOUNT_HINTS)
    needs_scope = category == "住宿费"
    scope_ready = bool(province or level) or not needs_scope

    if has_amount_hint and category and scope_ready:
        return Classification(
            route="compute", reason="金额类问题，且能定位到费用类别与适用条件，交给规则引擎计算",
            province=province, city=city, person_level=level, category=category,
            as_of=as_of, month=month,
        )

    # 4. 其余全部走检索
    missing = []
    if not has_amount_hint:
        missing.append("没有金额特征词")
    if not category:
        missing.append("没有识别出费用类别")
    if not (province or level):
        missing.append("没有识别出地区或级别")
    return Classification(
        route="retrieve",
        reason="未满足可计算条件（" + "、".join(missing) + "），走条文检索",
        province=province, city=city, person_level=level, category=category,
        as_of=as_of, month=month,
    )
