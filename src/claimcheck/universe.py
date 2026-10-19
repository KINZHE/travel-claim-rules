"""枚举域：诊断层能枚举的输入空间。

设计原则：**域由数据文件界定，不凭空造。**

推理不了无限空间，但也不需要——规则库只覆盖明细表的 6 个地区子集，
枚举这 6 个省的城市与级别就足以回答「有没有格子没人管」。
把域放大到 36 个地区只是让报告更长，不增加信息。

已知上限（写进诊断报告）：6 省 × 最多 8 城市 × 3 级别 ≈ 150 格。
换成全量就得抽样，那时这里的方法要换（见 docs/rule-decisions.md D-021 的边界说明）。
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
LODGING_CSV = ROOT / "data" / "lodging-subset.csv"

LEVELS = ["部级", "司局级", "其他人员"]
CATEGORIES = ["住宿费", "城市间交通费", "伙食补助费", "市内交通费"]
# 合成类别：不属四个法定类别，用来探测「规则库根本没管」的情形
UNKNOWN_CATEGORY = "其他费用"
# 合成城市：不属该省任何一个具名子区域，用来探测兜底规则是否到位
UNKNOWN_CITY = "未列出的市"

# 旺季月份从明细表读，这里只是解析时的默认值兜底
PEAK_MONTHS_HINT = {"哈尔滨市": [7, 8, 9]}


@dataclass(frozen=True)
class CityCell:
    """一个 (省, 市) 组合。"""
    province: str
    city: str
    is_synthetic: bool = False   # True 表示是探针城市（「未列出的市」）


@dataclass
class Universe:
    provinces: list[str]
    cities_by_province: dict[str, list[CityCell]]
    peak_months: dict[tuple[str, str], list[int]]

    def all_cells(self) -> list[CityCell]:
        out: list[CityCell] = []
        for province in self.provinces:
            out.extend(self.cities_by_province[province])
        return out

    def summary(self) -> dict:
        cells = self.all_cells()
        return {
            "provinces": len(self.provinces),
            "city_cells": len(cells),
            "levels": len(LEVELS),
            "lodging_combos": len(cells) * len(LEVELS),
            "categories": len(CATEGORIES) + 1,
        }


def _split_sub_area(text: str, province: str = "") -> list[str]:
    """「南京市、苏州市、无锡市」→ 城市列表。

    「全市」在直辖市里就是本省本身（北京 → 北京市），不能当成空——
    否则北京、上海在枚举域里一个城市都没有，缺口分析会把它们全报成未覆盖。
    「其他地区」是兜底行，返回空（由探针城市负责探测兜底是否到位）。
    """
    text = (text or "").strip()
    if not text:
        return []
    if text == "其他地区":
        return []
    if text == "全市":
        return [f"{province}市"] if province else []
    if text == "全省":
        return [f"{province}市"] if province else []
    parts = [p.strip() for p in text.replace("，", "、").split("、")]
    return [p for p in parts if p]


def _parse_months(text: str) -> list[int]:
    """「7-9月」→ [7,8,9]；「11-3月」→ [11,12,1,2,3]；解析不了返回 []。"""
    text = (text or "").strip()
    if not text:
        return []
    import re
    months: list[int] = []
    for start, end in re.findall(r"(\d{1,2})\s*[-－]\s*(\d{1,2})", text):
        a, b = int(start), int(end)
        if 1 <= a <= 12 and 1 <= b <= 12:
            m = a
            while True:
                months.append(m)
                if m == b:
                    break
                m = m % 12 + 1
    for single in re.findall(r"(?<![-－\d])(\d{1,2})(?![-－\d])", text):
        v = int(single)
        if 1 <= v <= 12 and v not in months:
            months.append(v)
    return sorted(set(months))


def load_universe(csv_path: Path | None = None) -> Universe:
    path = csv_path or LODGING_CSV
    provinces: list[str] = []
    cities_by_province: dict[str, list[CityCell]] = {}
    peak_months: dict[tuple[str, str], list[int]] = {}

    with path.open(encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            province = row["region"].strip()
            if province not in cities_by_province:
                provinces.append(province)
                cities_by_province[province] = []

            for city in _split_sub_area(row["sub_area"], province):
                cell = CityCell(province, city)
                if cell not in cities_by_province[province]:
                    cities_by_province[province].append(cell)

            # 旺季城市可能是子区域里的具体市，也可能就是整个子区域
            peak_area = (row.get("peak_area") or "").strip()
            if peak_area:
                for city in _split_sub_area(peak_area, province) or [row["sub_area"].strip()]:
                    peak_months.setdefault((province, city), [])
                    peak_months[(province, city)] = _parse_months(row.get("peak_period", ""))

    # 兜底：CSV 里没写旺季月份时用提示值
    for key, months in list(peak_months.items()):
        if not months and key[1] in PEAK_MONTHS_HINT:
            peak_months[key] = PEAK_MONTHS_HINT[key[1]]

    for province in provinces:
        cities_by_province[province].append(CityCell(province, UNKNOWN_CITY, is_synthetic=True))

    return Universe(provinces=provinces, cities_by_province=cities_by_province, peak_months=peak_months)


def peak_months_for(universe: Universe, province: str, city: str) -> list[int]:
    """某个城市适用旺季的月份列表。没有旺季标准时返回空。"""
    if (province, city) in universe.peak_months:
        return universe.peak_months[(province, city)]
    # 「其他地区」的旺季由表里该省的兜底行决定
    return []


def iter_lodging_cells(universe: Universe):
    """产出 (cell, person_level, peak_month or None) 的枚举。

    peak_month 为 None 表示「非旺季」。对没有旺季标准的城市只产出一格。
    """
    for cell in universe.all_cells():
        months = peak_months_for(universe, cell.province, cell.city)
        seasons = [None] + (months[:1] if months else [])
        for level in LEVELS:
            for season in seasons:
                yield cell, level, season
