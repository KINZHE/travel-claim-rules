"""缺口、无效规则与覆盖度的检测。

每个测试都**故意制造一个问题**，确认工具能报出来；
再确认没制造问题时它不报（否则就是狼来了）。
"""

from pathlib import Path

import yaml

from claimcheck.diagnose import coverage, find_gaps, find_inert, find_structural_gaps
from claimcheck.model import load_rules
from claimcheck.universe import UNKNOWN_CITY, load_universe

ROOT = Path(__file__).resolve().parent.parent


def _copy_rules(tmp_path: Path, drop: str = "", add: list | None = None):
    dest = tmp_path / "rules"
    dest.mkdir()
    for src in sorted((ROOT / "rules").glob("*.yaml")):
        data = yaml.safe_load(src.read_text(encoding="utf-8")) or []
        kept = [r for r in data if r.get("id") != drop]
        if add and src.name == "lodging.yaml":
            kept = kept + add
        (dest / src.name).write_text(
            yaml.safe_dump(kept, allow_unicode=True, sort_keys=False), encoding="utf-8"
        )
    return load_rules(dest)


def test_removing_fallback_creates_a_gap(tmp_path):
    """删掉江苏的兜底规则，徐州这类非重点城市就没人管了。"""
    rules = _copy_rules(tmp_path, drop="R-LODGING-JS-OTHER")
    gaps = find_gaps(rules, load_universe(), [])
    assert gaps, "删掉兜底规则后应当报出缺口"
    joined = " ".join(g.title + g.detail for g in gaps)
    assert "江苏" in joined
    assert UNKNOWN_CITY in joined or "徐州" in joined


def test_no_gap_on_intact_rules():
    """规则库完整时不报缺口。这条防的是「永远在报警」的假阳性。"""
    rules = load_rules(ROOT / "rules")
    gaps = find_gaps(rules, load_universe(), [])
    gaps += find_structural_gaps(rules, load_universe())
    assert gaps == []


def test_accepted_gap_is_suppressed(tmp_path):
    """申报过的缺口不报。"""
    rules = _copy_rules(tmp_path, drop="R-LODGING-JS-OTHER")
    universe = load_universe()
    accepted = [
        {"kind": "lodging_cell", "province": "江苏", "level": lv}
        for lv in ("部级", "司局级", "其他人员")
    ]
    assert find_gaps(rules, universe, accepted) == []


def test_always_passing_rule_is_inert(tmp_path):
    """加一条 allowed: any 的规则，且不在白名单里 → 必须报出来。"""
    extra = [{
        "id": "R-TEST-ALWAYS-PASS",
        "topic": "住宿费",
        "kind": "enum",
        "source": {"doc": "测试", "doc_no": "财行〔2013〕531号", "article": "第十三条",
                   "url": "https://example.invalid", "tier": "A"},
        "version": "2013-531",
        "effective_from": "2014-01-01",
        "effective_to": None,
        "scope": {},
        "when": {"category": "住宿费"},
        "require": ["lodging.nights"],
        "check": {"kind": "enum", "field": "lodging.nights[].room_type", "allowed": "any"},
        "on_pass": "通过",
        "on_missing": "信息不足",
        "notes": "测试用",
        "decision": "D-009",
    }]
    rules = _copy_rules(tmp_path, add=extra)
    inert = find_inert(rules, load_universe(), [])
    ids = [f.rule_ids[0] for f in inert]
    assert "R-TEST-ALWAYS-PASS" in ids


def test_declared_inert_rule_is_suppressed():
    """白名单里的规则不报。白名单条目必须带理由，这条由 check_rules.py 保证。"""
    rules = load_rules(ROOT / "rules")
    known = yaml.safe_load((ROOT / "docs" / "known-gaps.yaml").read_text(encoding="utf-8"))
    inert = find_inert(rules, load_universe(), known["intentionally_inert"])
    assert inert == []


def test_scope_outside_domain_is_inert(tmp_path):
    """写一条只适用于西藏的规则——西藏不在枚举域内，永远匹配不到。"""
    extra = [{
        "id": "R-TEST-OUTSIDE",
        "topic": "住宿费",
        "kind": "threshold",
        "source": {"doc": "测试", "doc_no": "财行〔2013〕531号", "article": "第十四条",
                   "url": "https://example.invalid", "tier": "A"},
        "version": "2013-531",
        "effective_from": "2014-01-01",
        "effective_to": None,
        "threshold_from": "2016-05-01",
        "scope": {"province": "西藏", "whole_region": True},
        "when": {"category": "住宿费"},
        "require": ["lodging.nights"],
        "check": {"kind": "threshold", "field": "lodging.nights[].amount",
                  "limits": [{"kind": "base",
                              "by_person_level": {"部级": 800, "司局级": 500, "其他人员": 350}}]},
        "on_pass": "通过",
        "on_fail": "不通过",
        "on_missing": "信息不足",
        "notes": "测试用",
        "decision": "D-009",
    }]
    rules = _copy_rules(tmp_path, add=extra)
    inert = find_inert(rules, load_universe(), [])
    assert any("R-TEST-OUTSIDE" in f.rule_ids for f in inert)


def test_coverage_denominator_matches_expansion():
    rules = load_rules(ROOT / "rules")
    cov = coverage(rules)
    assert cov["check_items_total"] == 86
    assert sum(v["total"] for v in cov["by_kind"].values()) == 86


def test_uncovered_items_are_real():
    """未覆盖清单里的每一项都确实不在任何用例的运行结果里。"""
    rules = load_rules(ROOT / "rules")
    cov = coverage(rules)
    # 目前已知的 3 个空洞：伙食补助费里三个不在子集内的省份
    assert set(cov["uncovered"]) == {
        "R-MEALS-ALLOWANCE::西藏",
        "R-MEALS-ALLOWANCE::青海",
        "R-MEALS-ALLOWANCE::新疆",
    }
