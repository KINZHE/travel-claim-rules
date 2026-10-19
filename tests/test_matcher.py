"""范围匹配的单元测试。

关键是两点：
    1. 兜底规则只接管「本省没被显式列出」的城市
    2. 优先级按特异性，不按规则在文件里的顺序
"""

from claimcheck.matcher import collect_known_cities, scope_matches, specificity
from claimcheck.model import Claim, Rule


def rule(scope: dict) -> Rule:
    return Rule(raw={"id": "R-T", "kind": "predicate", "topic": "住宿费", "scope": scope, "when": {}})


def claim(province: str, city: str) -> Claim:
    return Claim(raw={"trip": {"destination_province": province, "destination_city": city}})


def test_enum_scope_matches_only_listed_cities():
    r = rule({"province": "江苏", "cities": ["南京市", "苏州市"]})
    known = {"江苏": {"南京市", "苏州市"}}
    assert scope_matches(r, claim("江苏", "南京市"), known)
    assert not scope_matches(r, claim("江苏", "徐州市"), known)


def test_whole_region_matches_any_city():
    r = rule({"province": "北京", "whole_region": True})
    assert scope_matches(r, claim("北京", "北京市"), {})


def test_fallback_does_not_take_explicitly_listed_city():
    fallback = rule({"province": "江苏", "fallback": True})
    known = {"江苏": {"南京市", "苏州市"}}
    assert not scope_matches(fallback, claim("江苏", "南京市"), known)
    assert scope_matches(fallback, claim("江苏", "徐州市"), known)


def test_collect_known_cities_ignores_fallback():
    rules = [
        rule({"province": "江苏", "cities": ["南京市"]}),
        rule({"province": "江苏", "fallback": True}),
    ]
    assert collect_known_cities(rules) == {"江苏": {"南京市"}}


def test_specificity_order():
    assert specificity(rule({"province": "江苏", "cities": ["南京市"]})) == 3
    assert specificity(rule({"province": "北京", "whole_region": True})) == 2
    assert specificity(rule({"province": "江苏", "fallback": True})) == 1


def test_wrong_province_never_matches():
    r = rule({"province": "江苏", "whole_region": True})
    assert not scope_matches(r, claim("上海", "上海市"), {})
