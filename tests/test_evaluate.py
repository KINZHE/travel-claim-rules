"""评测器自身的测试。

评测器最容易出的问题不是算错，而是**没真正跑**：
题目没跑完、指标全空、静默跳过——文件看起来像跑过了。
所以这里的测试重点在「自检能不能发现这些情况」。
"""

import math
from pathlib import Path

import pytest

from claimcheck import ablation, evaluate
from claimcheck.evaluate import (
    Question, file_hash, load_questions, validate_questions, wilson,
)

ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------- Wilson 区间

def test_wilson_midpoint():
    lo, hi = wilson(50, 100)
    assert lo < 0.5 < hi
    assert 0.39 < lo < 0.41 and 0.59 < hi < 0.61


def test_wilson_stays_in_bounds_at_extremes():
    """全对或全错时，正态近似会给出越界区间（负数上界 / 大于 1 的上界）；Wilson 不会。"""
    lo, hi = wilson(50, 50)
    assert 0.0 <= lo <= 1.0 and hi == 1.0
    assert lo > 0.9                      # 50/50 的下界仍然很高，不是 1.0

    lo, hi = wilson(0, 50)
    assert lo == 0.0 and 0.0 < hi < 0.15  # 0/50 的上界仍然很低，不是 0.0


def test_wilson_empty_sample():
    assert wilson(0, 0) == (0.0, 1.0)


def test_wilson_narrows_with_more_samples():
    """样本越多区间越窄——这是报告里「50 条样本区间很宽」的依据。"""
    lo1, hi1 = wilson(30, 50)
    lo2, hi2 = wilson(300, 500)
    assert (hi2 - lo2) < (hi1 - lo1)


# ---------------------------------------------------------------- 题目集

@pytest.fixture(scope="module")
def questions() -> list[Question]:
    return load_questions()


def test_question_set_is_valid(questions):
    assert validate_questions(questions) == []


def test_question_count_and_mix(questions):
    assert len(questions) == 50
    colloquial = sum(1 for q in questions if q.style == "colloquial")
    assert 0.4 <= colloquial / len(questions) <= 0.6


def test_every_question_has_basis(questions):
    """没有 basis 的题考的是印象，不是制度。"""
    missing = [q.id for q in questions if not q.basis.strip()]
    assert missing == []


def test_refusal_questions_have_code(questions):
    for q in questions:
        if q.route == "refuse":
            assert q.refusal, f"{q.id} 是拒答题但没给 refusal 代码"


def test_non_refusal_questions_have_facts(questions):
    for q in questions:
        if q.route != "refuse":
            assert q.facts, f"{q.id} 没给期望事实，无法判断对错"


def test_validate_catches_bad_question():
    bad = [Question(id="X-1", question="?", style="formal", as_of="2026-01-01",
                    route="compute", citations_any=[], facts=[], must_not_contain=[],
                    refusal=None, basis="")]
    problems = validate_questions(bad)
    assert any("basis" in p for p in problems)
    assert any("facts" in p for p in problems)


def test_validate_catches_duplicate_ids(questions):
    dup = questions + [questions[0]]
    assert any("重复" in p for p in validate_questions(dup))


def test_validate_catches_style_imbalance():
    """全是正式问法时应当报警——那说明没有防住「照系统措辞出题」。"""
    formal = [Question(id=f"F-{i}", question="?", style="formal", as_of="2026-01-01",
                       route="retrieve", citations_any=[], facts=["x"],
                       must_not_contain=[], refusal=None, basis="x")
              for i in range(10)]
    assert any("口语" in p for p in validate_questions(formal))


# ---------------------------------------------------------------- 自检

def test_self_check_catches_short_run(questions):
    """跑完的题数不对，必须报出来。"""
    fake = [evaluate.Record(q=questions[0], answer=_fake_answer("答案"))]
    problems = evaluate.self_check(fake, 50)
    assert any("应为 50" in p for p in problems)


def test_self_check_catches_empty_answers(questions):
    fake = [evaluate.Record(q=q, answer=_fake_answer("")) for q in questions]
    problems = evaluate.self_check(fake, len(questions))
    assert any("没有产出答案" in p for p in problems)


def test_self_check_catches_all_false_metric(questions):
    """指标全 False 说明评测没真正执行，不能当成「成绩差」报出去。"""
    fake = [evaluate.Record(q=q, answer=_fake_answer("答案")) for q in questions]
    for rec in fake:
        for m in evaluate.METRICS:
            rec.checks[m] = False
    problems = evaluate.self_check(fake, len(questions))
    assert any("全为 False" in p for p in problems)


def _fake_answer(text: str):
    from claimcheck.qa import Answer
    return Answer(question="?", route="retrieve", text=text)


# ---------------------------------------------------------------- 冻结

def test_questions_match_frozen_hash():
    """题目集与冻结记录一致。改了题就必须更新 FROZEN.md 并说明原因。"""
    import sys
    sys.path.insert(0, str(ROOT / "scripts"))
    import freeze_eval
    ok, msg = freeze_eval.check()
    assert ok, msg


# ---------------------------------------------------------------- 消融

def test_ablation_levels_are_ordered():
    """每一级只比上一级多一个部件，否则说不清是谁的贡献。"""
    keys = [lv.key for lv in ablation.LEVELS]
    assert keys == ["L0", "L1", "L2", "L3"]
    for prev, cur in zip(ablation.LEVELS, ablation.LEVELS[1:]):
        diff = [k for k in cur.kwargs if cur.kwargs[k] != prev.kwargs[k]]
        assert len(diff) == 1, f"{prev.key}→{cur.key} 改了 {diff}，应只改一个"


def test_ablation_is_deterministic(questions):
    """消融结果必须可复现——不含随机性。"""
    subset = questions[:6]
    a = [r.answer.text for r in ablation.run_level(subset, ablation.LEVELS[0])]
    b = [r.answer.text for r in ablation.run_level(subset, ablation.LEVELS[0])]
    assert a == b
