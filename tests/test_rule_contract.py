"""规则契约测试：每条规则至少被一个用例触发过。

为什么这个必须做成测试而不是靠自觉：
    聚合指标会掩盖单条缺失。有开源项目出现过 96 个策略里 22 个没有任何测试，
    而汇总的测试数仍然全绿。一句流传的话说得很准：
    「一个永远无法失败的检查，只会给报告增加一行绿色，别的什么也不会增加。」

同时校验期望表本身没有写错——引用不存在的规则 id 要报出来。
"""

import pytest

from claimcheck.engine import run
from claimcheck.model import Claim, load_rules

from cases import CASES

RULES = load_rules(__import__("pathlib").Path(__file__).resolve().parent.parent / "rules")
RULE_IDS = {r.id for r in RULES}


@pytest.fixture(scope="module")
def touched() -> set[str]:
    """把所有用例跑一遍，收集实际产生结果的规则 id。"""
    seen: set[str] = set()
    for name, build, as_of, _expected in CASES:
        result = run(Claim(raw=build()), RULES, as_of=as_of)
        seen.update(r.rule_id for r in result.results)
    return seen


def test_every_rule_is_triggered_by_some_case(touched: set[str]):
    missing = sorted(RULE_IDS - touched)
    assert not missing, (
        "以下规则从未被任何测试用例触发，覆盖率会出现空洞：\n  " + "\n  ".join(missing)
    )


def test_expected_tables_reference_real_rules():
    for name, _build, _as_of, expected in CASES:
        for rule_id in expected:
            assert rule_id in RULE_IDS, f"用例「{name}」期望表引用了不存在的规则 {rule_id}"


@pytest.mark.parametrize("name,build,as_of,expected", CASES, ids=[c[0] for c in CASES])
def test_case_expectations(name, build, as_of, expected):
    result = run(Claim(raw=build()), RULES, as_of=as_of)
    actual = {r.rule_id: r.status for r in result.results}
    for rule_id, want in expected.items():
        assert actual.get(rule_id) == want, (
            f"用例「{name}」的 {rule_id} 期望 {want}，实际 {actual.get(rule_id)}"
        )
