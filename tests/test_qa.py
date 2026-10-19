"""问题分类与端到端问答。

分类用规则不用模型，所以每条判断都能写死成断言；
端到端测试则要确认三条路径（可计算 / 检索 / 拒答）都真的走得通。
"""

import pytest

from claimcheck.classify import classify
from claimcheck.qa import QA


@pytest.fixture(scope="module")
def qa() -> QA:
    return QA()


# ---------------------------------------------------------------- 分类

def test_compute_route_for_lodging_amount():
    c = classify("南京出差，司局级，住宿最多能报多少？")
    assert c.route == "compute"
    assert c.category == "住宿费"
    assert c.province == "江苏"
    assert c.city == "南京市"
    assert c.person_level == "司局级"


def test_voucher_question_goes_to_retrieval():
    """问材料/凭证的，不是数值问题，不能走可计算路径。"""
    c = classify("报销需要哪些凭证？")
    assert c.route == "retrieve"


def test_unknown_topic_flagged_out_of_scope():
    c = classify("出差期间生病的医疗费怎么报？")
    assert c.out_of_scope


def test_date_extraction():
    c = classify("2013 年出差，市内交通费怎么算？")
    assert c.as_of == "2013-07-01"


def test_month_extraction():
    c = classify("8 月去哈尔滨，司局级住宿上限多少")
    assert c.month == 8


def test_no_category_falls_back_to_retrieval():
    """定位不到费用类别就不硬算，交给检索。"""
    c = classify("出差要注意什么？")
    assert c.route == "retrieve"


# ---------------------------------------------------------------- 三条路径

def test_compute_answer_has_limit_and_rule(qa):
    a = qa.ask("南京出差，司局级，住宿最多能报多少？")
    assert a.route == "compute"
    assert "490" in a.text
    assert a.trace["rule_id"] == "R-LODGING-JS-CORE"
    assert a.verification.passed


def test_compute_answer_cites_both_clause_and_table(qa):
    """数字来自表格、权限来自条款，两个引用都要给。"""
    a = qa.ask("南京出差，司局级，住宿最多能报多少？")
    kinds = {c.kind for c in a.citations}
    assert "table" in kinds
    assert "clause" in kinds


def test_peak_season_uses_peak_limit(qa):
    """8 月的哈尔滨适用旺季限额，不是常规限额。"""
    a = qa.ask("8 月去哈尔滨，司局级住宿上限多少")
    assert "540" in a.text
    assert a.trace.get("season") == "peak"
    assert a.verification.passed


def test_off_season_uses_base_limit(qa):
    a = qa.ask("12 月去哈尔滨，司局级住宿上限多少")
    assert "450" in a.text
    assert a.trace.get("season") == "base"


def test_retrieval_answer_quotes_article_24(qa):
    a = qa.ask("报销需要哪些凭证？")
    assert a.route == "retrieve"
    assert any("第二十四条" in c.label for c in a.citations)
    assert a.verification.passed


def test_out_of_scope_is_refused_not_answered(qa):
    a = qa.ask("出差期间生病的医疗费怎么报？")
    assert a.refused
    assert a.refusal == "NOT_COVERED"
    assert a.citations == []


def test_meals_answer_flags_secondary_source(qa):
    """伙食标准来自 B/C 层级，答案必须自己说出来。"""
    a = qa.ask("伙食补助费标准是多少")
    assert "100" in a.text
    assert any(c.kind == "secondary" for c in a.citations)
    assert any("二手来源" in w for w in a.verification.warnings)


# ---------------------------------------------------------------- 两种上下文

def test_full_context_mode_works(qa):
    a = qa.ask("报销需要哪些凭证？", context_mode="full")
    assert a.route == "retrieve"
    assert a.trace["context_mode"] == "full"
    assert a.text


def test_retrieve_and_full_both_answer(qa):
    q = "报销需要哪些凭证？"
    a1 = qa.ask(q, context_mode="retrieve")
    a2 = qa.ask(q, context_mode="full")
    assert a1.text and a2.text
    assert a1.trace["context_mode"] != a2.trace["context_mode"]


# ---------------------------------------------------------------- 版本

def test_as_of_switches_version(qa):
    """按 2013 年判断时，市内交通费应走 2006 版的 30 元。"""
    a = qa.ask("市内交通费怎么算", as_of="2013-06-01")
    assert "30" in a.text or "公杂费" in a.text
