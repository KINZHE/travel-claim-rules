"""结构化谓词求值。三值逻辑：真 / 假 / 未知。

为什么是三值而不是布尔：
    「实际发生住宿」为真，但某一晚的 receipt_present 没填——这一晚到底算不算缺发票？
    二值逻辑只能猜（默认 false 就变成误报，默认 true 就变成漏报）。
    三值逻辑如实返回「未知」，上层就能输出「信息不足」而不是给一个没依据的结论。

    三个基本规则：
        any  : 只要有一个为真就是真；否则有任何未知就是未知；否则为假
        all  : 只要有一个为假就是假；否则有任何未知就是未知；否则为真
        not  : 真↔假，未知仍是未知

支持的操作（与 SCHEMA.md 一致）：
    eq / ne / in / not_in / lt / lte / gt / gte / is_null / is_not_null
支持的结构：
    {all: [...]} / {any: [...]} / {not: {...}}
    {for_all: {path, where?, test}} / {exists: {path, where?, test}}
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from .model import MISSING, Claim, get_path


class Tri(Enum):
    TRUE = "true"
    FALSE = "false"
    UNKNOWN = "unknown"

    def __bool__(self) -> bool:  # 防止有人误当布尔用
        raise TypeError("Tri 不能用 bool() 转换，请显式比较 Tri.TRUE")


VALID_OPS = {"eq", "ne", "in", "not_in", "lt", "lte", "gt", "gte", "is_null", "is_not_null"}


class UndefinedField(Exception):
    """谓词引用了申请里没有的字段。上层据此判「信息不足」。"""

    def __init__(self, path: str):
        self.path = path
        super().__init__(f"字段未定义或缺失: {path}")


class PredicateError(Exception):
    """谓词结构本身有问题（写错了，不是数据缺失）。"""


def _resolve(node: dict, ctx: dict, get) -> Any:
    """取节点的操作数：value 直接给，field_ref 去申请里取。"""
    if "value" in node:
        return node["value"]
    if "field_ref" in node:
        return get(node["field_ref"])
    raise PredicateError(f"节点既没有 value 也没有 field_ref: {node}")


def _compare(op: str, left: Any, right: Any) -> Tri:
    """两侧都拿到值之后做比较。值缺失由调用方提前处理。"""
    if op == "eq":
        return Tri.TRUE if left == right else Tri.FALSE
    if op == "ne":
        return Tri.TRUE if left != right else Tri.FALSE
    if op == "in":
        if not isinstance(right, (list, tuple, set)):
            raise PredicateError(f"in 的右值应为列表，实际是 {type(right).__name__}")
        return Tri.TRUE if left in right else Tri.FALSE
    if op == "not_in":
        if not isinstance(right, (list, tuple, set)):
            raise PredicateError(f"not_in 的右值应为列表，实际是 {type(right).__name__}")
        return Tri.TRUE if left not in right else Tri.FALSE
    if op == "is_null":
        return Tri.TRUE if left is None else Tri.FALSE
    if op == "is_not_null":
        return Tri.TRUE if left is not None else Tri.FALSE

    # 数值比较：类型不对就视为无法判断
    if not isinstance(left, (int, float)) or isinstance(left, bool):
        return Tri.UNKNOWN
    if not isinstance(right, (int, float)) or isinstance(right, bool):
        return Tri.UNKNOWN
    table = {
        "lt": left < right,
        "lte": left <= right,
        "gt": left > right,
        "gte": left >= right,
    }
    return Tri.TRUE if table[op] else Tri.FALSE


def _any(values: list[Tri]) -> Tri:
    if any(v is Tri.TRUE for v in values):
        return Tri.TRUE
    if any(v is Tri.UNKNOWN for v in values):
        return Tri.UNKNOWN
    return Tri.FALSE


def _all(values: list[Tri]) -> Tri:
    if any(v is Tri.FALSE for v in values):
        return Tri.FALSE
    if any(v is Tri.UNKNOWN for v in values):
        return Tri.UNKNOWN
    return Tri.TRUE


def evaluate(expr: dict, claim: Claim, relative: dict | None = None) -> Tri:
    """对表达式求值。

    relative 不为 None 时，field 路径相对于该字典（用于量词内部的 test / where）。
    """
    if not isinstance(expr, dict):
        raise PredicateError(f"谓词节点应为字典，实际是 {type(expr).__name__}")

    def get(path: str) -> Any:
        if relative is not None and "." not in path:
            val = relative.get(path, MISSING)
        else:
            val = claim.get(path)
        if val is MISSING:
            raise UndefinedField(path)
        return val

    # --- 逻辑节点 ---
    if "all" in expr:
        return _all([evaluate(sub, claim, relative) for sub in expr["all"]])
    if "any" in expr:
        return _any([evaluate(sub, claim, relative) for sub in expr["any"]])
    if "not" in expr:
        inner = evaluate(expr["not"], claim, relative)
        if inner is Tri.UNKNOWN:
            return Tri.UNKNOWN
        return Tri.FALSE if inner is Tri.TRUE else Tri.TRUE

    # --- 量词节点 ---
    for qkey, want_all in (("for_all", True), ("exists", False)):
        if qkey in expr:
            body = expr[qkey]
            # 量词作用于数组：直接取数组元素，不能用 iter_path
            # （iter_path 对不含 [] 的路径会返回整个数组作为一个值）
            array = get_path(claim.raw, body["path"])
            if array is MISSING or not isinstance(array, list):
                return Tri.UNKNOWN
            items = list(array)
            # where 先把元素筛一遍，筛完不剩：for_all 为真（没有要检查的），exists 为假
            if "where" in body:
                kept = []
                for item in items:
                    if evaluate(body["where"], claim, relative=item) is Tri.TRUE:
                        kept.append(item)
                items = kept
            results = [evaluate(body["test"], claim, relative=item) for item in items]
            return _all(results) if want_all else _any(results)

    # --- 比较节点 ---
    op = expr.get("op")
    if op not in VALID_OPS:
        raise PredicateError(f"未知操作 {op!r}")
    if "field" not in expr:
        raise PredicateError(f"比较节点缺 field: {expr}")

    try:
        left = get(expr["field"])
    except UndefinedField:
        # 字段不存在 → 未知，不是假
        return Tri.UNKNOWN

    # is_null / is_not_null 只有一个操作数
    if op in {"is_null", "is_not_null"}:
        return _compare(op, left, None)

    try:
        right = _resolve(expr, None, get)
    except UndefinedField:
        return Tri.UNKNOWN

    # 字段存在但值为 null：对「是否为 null」之外的操作都无法比较，同样是未知。
    # 这一步不能省——否则 person_level: null 会被判成「不在允许集合里」，
    # 结果从「信息不足」变成「不通过」，这是误报。
    if left is None or right is None:
        return Tri.UNKNOWN

    return _compare(op, left, right)
