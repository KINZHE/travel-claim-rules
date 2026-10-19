"""适用范围匹配。

三种写法（见 docs/rule-semantics.md 第一节）：
    cities: [...]      枚举，只匹配列出的城市
    whole_region: true 全省/全市
    fallback: true     兜底：本省已列出的城市之外

优先级按特异性：枚举(3) > 全省(2) > 兜底(1)。同分且都命中 → 冲突。
不用「文件顺序靠后的生效」——那会让结果的正确性取决于 YAML 的书写顺序。
"""

from __future__ import annotations

from .model import MISSING, Claim, Rule

SPEC_CITY = 3
SPEC_REGION = 2
SPEC_FALLBACK = 1


def specificity(rule: Rule) -> int:
    """这条规则的适用范围有多具体。不适用本申请时返回 None 由调用方处理。"""
    scope = rule.scope
    if scope.get("cities"):
        return SPEC_CITY
    if scope.get("fallback"):
        return SPEC_FALLBACK
    if scope.get("whole_region"):
        return SPEC_REGION
    return SPEC_REGION  # 没写 scope 的视为全省


def scope_matches(rule: Rule, claim: Claim, province_known_cities: dict[str, set[str]]) -> bool:
    """判断规则的 scope 是否覆盖本申请的目的地。"""
    scope = rule.scope

    # 不写 province 的规则（如报销管理类）不按地区过滤
    province = scope.get("province")
    if not province:
        return True

    dest_province = claim.get("trip.destination_province")
    if dest_province is MISSING or dest_province is None:
        return False
    if dest_province != province:
        return False

    dest_city = claim.get("trip.destination_city")
    if dest_city is MISSING or dest_city is None:
        return False

    if scope.get("cities"):
        return dest_city in scope["cities"]

    if scope.get("whole_region"):
        return True

    if scope.get("fallback"):
        # 兜底：本省已由其他规则显式列出的城市不归它管
        listed = province_known_cities.get(province, set())
        return dest_city not in listed

    return True


def collect_known_cities(rules: list[Rule]) -> dict[str, set[str]]:
    """收集每个省被显式列举的城市。兜底规则要靠它判断「其他地区」的边界。"""
    out: dict[str, set[str]] = {}
    for rule in rules:
        province = rule.scope.get("province")
        cities = rule.scope.get("cities")
        if province and cities:
            out.setdefault(province, set()).update(cities)
    return out


def when_matches(rule: Rule, claim: Claim) -> bool:
    """规则的 when 条件是否与本申请相关。

    支持三种写法（可以同时出现，须全部满足）：
        category: 值或列表 —— 本申请是否涉及这些费用类别
        fee_type: 值       —— 是否有这一类费用行
        field + equals/in —— 某个申请字段的取值条件

    第三种是为了表达「本条只在接待单位确实安排了用餐时才适用」这类前置条件。
    没有它，不适用会被误判成「信息不足」——两者后果不同：
    不适用是跳过，信息不足是报了但判不了。
    """
    when = rule.when
    if not when:
        return True

    if "category" in when:
        wanted = when["category"]
        wanted = wanted if isinstance(wanted, list) else [wanted]
        present = claim.categories_present()
        if not present & set(wanted):
            return False

    if "fee_type" in when:
        present = claim.fee_types_present()
        if when["fee_type"] not in present:
            return False

    if "field" in when:
        val = claim.get(when["field"])
        if val is MISSING or val is None:
            # 字段缺失时不让规则「因为不知道而跳过」——
            # 交给求值阶段判信息不足，这样问题会暴露出来
            return True
        if "equals" in when and val != when["equals"]:
            return False
        if "in" in when and val not in when["in"]:
            return False

    return True
