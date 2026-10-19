"""谓词求值的单元测试。

重点不是覆盖所有操作符，而是**验证三值逻辑**——
「字段缺失」必须是「未知」而不是「假」，否则所有缺字段的申请都会被误判成违规。
"""

import pytest

from claimcheck.model import Claim
from claimcheck.predicate import Tri, evaluate


def C(**raw) -> Claim:
    return Claim(raw=raw)


def test_eq_true_and_false():
    c = C(trip={"approved": True})
    assert evaluate({"field": "trip.approved", "op": "eq", "value": True}, c) is Tri.TRUE
    assert evaluate({"field": "trip.approved", "op": "eq", "value": False}, c) is Tri.FALSE


def test_missing_field_is_unknown_not_false():
    c = C(trip={})
    assert evaluate({"field": "trip.approved", "op": "eq", "value": True}, c) is Tri.UNKNOWN


def test_null_field_is_unknown_for_comparison():
    c = C(applicant={"person_level": None})
    assert evaluate({"field": "applicant.person_level", "op": "eq", "value": "部级"}, c) is Tri.UNKNOWN
    assert evaluate({"field": "applicant.person_level", "op": "in", "value": ["部级"]}, c) is Tri.UNKNOWN


def test_is_null_still_works_on_null():
    c = C(applicant={"person_level": None})
    assert evaluate({"field": "applicant.person_level", "op": "is_null"}, c) is Tri.TRUE
    assert evaluate({"field": "applicant.person_level", "op": "is_not_null"}, c) is Tri.FALSE


def test_all_short_circuits_on_false():
    """all 里只要有一个确定为假，整体就是假——哪怕另一项未知。"""
    c = C(a={"x": 1}, b={})
    expr = {"all": [
        {"field": "a.x", "op": "eq", "value": 2},      # 假
        {"field": "b.y", "op": "eq", "value": 1},      # 未知（字段不存在）
    ]}
    assert evaluate(expr, c) is Tri.FALSE


def test_any_short_circuits_on_true():
    c = C(a={"x": 1}, b={})
    expr = {"any": [
        {"field": "a.x", "op": "eq", "value": 1},      # 真
        {"field": "b.y", "op": "eq", "value": 1},      # 未知
    ]}
    assert evaluate(expr, c) is Tri.TRUE


def test_any_all_unknown_is_unknown():
    c = C(b={})
    expr = {"any": [{"field": "b.y", "op": "eq", "value": 1}]}
    assert evaluate(expr, c) is Tri.UNKNOWN


def test_not_unknown_stays_unknown():
    c = C(b={})
    assert evaluate({"not": {"field": "b.y", "op": "eq", "value": 1}}, c) is Tri.UNKNOWN


def test_field_ref_compares_two_fields():
    c = C(trip={"a": "X", "b": "Y"})
    expr = {"field": "trip.a", "op": "ne", "field_ref": "trip.b"}
    assert evaluate(expr, c) is Tri.TRUE


def test_for_all_vacuous_truth():
    """没有元素时 for_all 为真——没有要检查的，就不算违规。"""
    c = C(lodging={"nights": []})
    expr = {"for_all": {"path": "lodging.nights",
                        "test": {"field": "receipt_present", "op": "eq", "value": True}}}
    assert evaluate(expr, c) is Tri.TRUE


def test_exists_finds_offending_item():
    c = C(lodging={"nights": [
        {"receipt_present": True},
        {"receipt_present": False},
    ]})
    expr = {"exists": {"path": "lodging.nights",
                       "test": {"field": "receipt_present", "op": "eq", "value": False}}}
    assert evaluate(expr, c) is Tri.TRUE


def test_where_narrows_the_item_set():
    c = C(lines=[
        {"category": "火车", "ok": False},
        {"category": "飞机", "ok": True},
    ])
    expr = {"for_all": {
        "path": "lines",
        "where": {"field": "category", "op": "eq", "value": "飞机"},
        "test": {"field": "ok", "op": "eq", "value": True},
    }}
    assert evaluate(expr, c) is Tri.TRUE


def test_numeric_comparison_rejects_bool():
    """布尔值参与数值比较应视为未知，不能因为 True==1 就判通过。"""
    c = C(x={"v": True})
    assert evaluate({"field": "x.v", "op": "lte", "value": 5}, c) is Tri.UNKNOWN
