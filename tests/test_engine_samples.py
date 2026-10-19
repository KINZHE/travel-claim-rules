"""与手工推导的预期结果对账。

data/samples/expected.md 是按规则和制度原文**手工算出来**的，不是跑代码生成的。
测试失败时先怀疑引擎，不要为了让测试变绿去改预期值。
若确实发现手工推导有误，改之前要在 expected.md 里写明为什么。
"""

from pathlib import Path

import pytest

from claimcheck.engine import run
from claimcheck.model import load_claim, load_rules

ROOT = Path(__file__).resolve().parent.parent
RULES = load_rules(ROOT / "rules")


def check(name: str, **kw):
    return run(load_claim(ROOT / "data" / "samples" / f"{name}.json"), RULES, **kw)


def statuses(result) -> dict[str, str]:
    return {r.rule_id: r.status for r in result.results}


def test_sample_001_all_pass():
    result = check("sample-001-compliant")
    assert result.overall == "通过"
    assert [r.rule_id for r in result.results if r.status != "通过"] == []


def test_sample_002_first_night_over_limit():
    result = check("sample-002-over-limit")
    assert result.overall == "不通过"
    bad = [r for r in result.results if r.rule_id == "R-LODGING-JS-CORE"]
    assert len(bad) == 1
    assert bad[0].status == "不通过"
    # 必须能指出是哪一晚、限额多少——只报「不通过」没有可核查性
    assert "2026-04-10" in bad[0].message
    assert "490" in bad[0].message


def test_sample_003_missing_info_and_linkage():
    result = check("sample-003-missing-info")
    st = statuses(result)
    assert result.overall == "不通过"
    assert st["R-LODGING-SH-001"] == "信息不足"      # 级别未填
    assert st["R-LODGING-ROOM-TYPE"] == "信息不足"
    assert st["R-TRANSPORT-CLASS"] == "信息不足"
    assert st["R-LODGING-PAYMENT-CARD"] == "不通过"  # 第二晚未用公务卡
    assert st["R-LODGING-NO-INVOICE"] == "不通过"    # 联动规则
    assert st["R-REIMBURSE-VOUCHER"] == "不通过"


def test_sample_004_peak_season_uses_peak_limit():
    """520 元低于旺季限额 540，但高于淡季 450——用错限额就会误判。"""
    result = check("sample-004-peak-season")
    assert result.overall == "通过"
    assert statuses(result)["R-LODGING-HLJ-HARBIN"] == "通过"


def test_sample_005_2006_version():
    """按 2006 版判断：补贴金额符合当年标准，缺发票只影响住宿费。"""
    result = check("sample-005-cross-version", as_of="2013-06-01")
    st = statuses(result)
    assert st["R-LOCAL-ALLOWANCE-2006"] == "通过"
    assert st["R-MEALS-ALLOWANCE-2006"] == "通过"
    assert st["R-LODGING-NO-INVOICE-2006"] == "不通过"
    # 2013 版规则不该在 2013 年生效
    assert "R-LOCAL-ALLOWANCE" not in st


def test_sample_005_2013_version_differs():
    """同一份单据按 2013 版复核：补贴标准不符，且缺发票牵连四项。"""
    result = check("sample-005-cross-version", as_of="2014-06-01")
    st = statuses(result)
    assert st["R-LOCAL-ALLOWANCE"] == "不通过"       # 90 元 ≠ 3×80
    assert st["R-MEALS-ALLOWANCE"] == "不通过"       # 150 元 ≠ 3×100
    assert st["R-LODGING-NO-INVOICE"] == "不通过"
    assert "R-LOCAL-ALLOWANCE-2006" not in st


def test_2016_threshold_gap_is_reported_not_skipped():
    """2014–2015 年住宿费阈值我们没拿到，应判信息不足而不是静默跳过。"""
    result = check("sample-005-cross-version", as_of="2014-06-01")
    st = statuses(result)
    assert st["R-LODGING-JS-CORE"] == "信息不足"
