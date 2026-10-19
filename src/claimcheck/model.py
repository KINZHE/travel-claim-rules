"""规则与申请的数据结构、载入、以及路径取值。

设计要点：
    - 申请数据保持为嵌套 dict，不强行转成对象——规则里的路径就是照着这个结构写的
    - 派生值在载入时算好，与原始数据放在一起，规则可以像普通字段一样引用
    - 「缺失」与「值为空」是同一件事：都返回 MISSING，交给上层判「信息不足」
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import yaml

# 表示「这个路径取不到值」。用哨兵而不是 None，因为 None 可能是合法的业务值。
MISSING = object()


# ---------------------------------------------------------------- 路径取值

def get_path(obj: Any, path: str) -> Any:
    """按 a.b.c 取值，取不到返回 MISSING。字典和列表都支持。"""
    cur = obj
    for part in path.split("."):
        if cur is MISSING:
            return MISSING
        if isinstance(cur, dict):
            cur = cur.get(part, MISSING)
        elif isinstance(cur, list):
            # 列表上直接取字段名 = 对每个元素取样，返回列表
            cur = [get_path(item, part) for item in cur]
        else:
            return MISSING
    return cur


def iter_path(obj: Any, path: str) -> list[tuple[Any, Any]]:
    """处理 `a.b[].c` 这种「对数组每项取字段」的路径。

    返回 [(元素, 值), ...]。路径不含 [] 时返回 [(obj, 值)]，长度 1。
    """
    if "[]" not in path:
        val = get_path(obj, path)
        return [] if val is MISSING else [(obj, val)]

    head, tail = path.split("[]", 1)
    head = head.rstrip(".")
    tail = tail.lstrip(".")
    items = get_path(obj, head) if head else obj
    if items is MISSING or not isinstance(items, list):
        return []
    out = []
    for item in items:
        val = get_path(item, tail) if tail else item
        if val is not MISSING:
            out.append((item, val))
    return out


# ---------------------------------------------------------------- 申请

@dataclass
class Claim:
    raw: dict
    path: Path | None = None
    derived: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.derived = compute_derived(self.raw)

    def get(self, path: str) -> Any:
        """先查派生值（派生值都是一级名称），再查原始数据。"""
        if path in self.derived:
            return self.derived[path]
        return get_path(self.raw, path)

    @property
    def trip_date(self) -> date | None:
        d = get_path(self.raw, "trip.departure_date")
        if d is MISSING or not d:
            return None
        return date.fromisoformat(str(d))

    @property
    def claim_id(self) -> str:
        return str(self.raw.get("claim_id", "<无 id>"))

    def expense_lines(self) -> list[dict]:
        lines = get_path(self.raw, "expense_lines")
        return lines if isinstance(lines, list) else []

    def lodging_nights(self) -> list[dict]:
        nights = get_path(self.raw, "lodging.nights")
        return nights if isinstance(nights, list) else []

    def categories_present(self) -> set[str]:
        """本申请实际涉及哪些费用类别。住宿单独判断，其余看费用行。"""
        cats = {line.get("category") for line in self.expense_lines() if line.get("category")}
        if get_path(self.raw, "lodging.stay_occurred") is True:
            cats.add("住宿费")
        return cats

    def fee_types_present(self) -> set[str]:
        return {line.get("fee_type") for line in self.expense_lines() if line.get("fee_type")}


def compute_derived(raw: dict) -> dict:
    """派生值。必须与 docs/claim-schema.md 第四节一致。"""
    derived: dict[str, Any] = {}

    dep = get_path(raw, "trip.departure_date")
    ret = get_path(raw, "trip.return_date")
    if dep is not MISSING and ret is not MISSING and dep and ret:
        try:
            derived["travel_days"] = (date.fromisoformat(str(ret)) - date.fromisoformat(str(dep))).days + 1
        except ValueError:
            pass

    # 本次出差购买的交通意外保险份数
    lines = get_path(raw, "expense_lines")
    if not isinstance(lines, list):
        lines = []
    derived["per_trip_insurance_count"] = sum(
        1 for line in lines
        if isinstance(line, dict) and line.get("fee_type") == "交通意外保险"
    )

    # 无收费标准时，接待单位提供交通工具应缴费用 = 日标准的一半 × 半天数
    half_days = get_path(raw, "host.transport_half_days")
    if half_days is not MISSING and isinstance(half_days, (int, float)):
        derived["expected_host_transport_fee"] = 40 * half_days

    return derived


def load_claim(path: str | Path) -> Claim:
    p = Path(path)
    raw = json.loads(p.read_text(encoding="utf-8"))
    return Claim(raw=raw, path=p)


# ---------------------------------------------------------------- 规则

@dataclass
class Rule:
    raw: dict
    file: str = ""

    @property
    def id(self) -> str:
        return self.raw["id"]

    @property
    def kind(self) -> str:
        return self.raw["kind"]

    @property
    def check(self) -> dict:
        return self.raw["check"]

    @property
    def topic(self) -> str:
        return self.raw.get("topic", "")

    @property
    def scope(self) -> dict:
        return self.raw.get("scope", {}) or {}

    @property
    def when(self) -> dict:
        return self.raw.get("when", {}) or {}

    def source_of(self, doc_key: str = "doc_no") -> str:
        return self.raw.get("source", {}).get(doc_key, "")

    def __getattr__(self, item: str):
        # 允许 r.effective_from 这类直取；取不到返回 None 而不是 AttributeError
        if item.startswith("_"):
            raise AttributeError(item)
        return self.raw.get(item)


def load_rules(rules_dir: str | Path) -> list[Rule]:
    """读目录下所有 yaml。规则文件顶层是数组。"""
    out: list[Rule] = []
    for path in sorted(Path(rules_dir).glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not data:
            continue
        for raw in data:
            out.append(Rule(raw=raw, file=path.name))
    return out


# ---------------------------------------------------------------- 日期区间

def in_window(d: date | None, start, end) -> bool:
    """判断日期是否落在 [start, end] 内。start/end 为 None 表示不限。

    日期缺失时返回 False —— 上层会把它当作「无法判断」而不是「适用」。
    """
    if d is None:
        return False
    if start:
        if d < date.fromisoformat(str(start)):
            return False
    if end:
        if d > date.fromisoformat(str(end)):
            return False
    return True
