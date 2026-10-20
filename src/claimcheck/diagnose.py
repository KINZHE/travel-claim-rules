"""规则库质量诊断。

四个检查，各自回答一个问题：

    缺口   —— 有没有输入格子没有任何规则管？
    冲突   —— 有没有两个规则对同一件事给出不同答案？
    无效   —— 有没有规则永远不起作用？
    覆盖   —— 有没有检查项从来没被测试触发过？

设计前提（写进报告「方法与边界」）：
    - 枚举域由数据文件界定，不是全空间
    - 冲突分两级，二级是启发式，会误报，所以只列为「候选」
    - 无法自动判定的规则对要报出来，不能静默跳过
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import yaml

from .paths import find_root
from .checks import expand_check_items
from .engine import run
from .matcher import collect_known_cities, scope_matches
from .model import Claim, Rule, in_window
from .universe import (
    LEVELS, UNKNOWN_CITY, Universe, iter_lodging_cells, load_universe,
)

ROOT = find_root()

# 只有这四个主题对应「一笔具体费用能不能报」。
# 报销管理、准入校验不在此列——它们是整单级检查，彼此独立、可同时成立。
CATEGORY_TOPICS = {"住宿费", "城市间交通费", "伙食补助费", "市内交通费"}


@dataclass
class Finding:
    kind: str                 # gap / conflict / candidate / inert / unreproducible
    id: str
    title: str
    detail: str = ""
    witness: str = ""         # 见证的描述，便于人工复核
    rule_ids: list[str] = field(default_factory=list)


# ---------------------------------------------------------------- 见证构造

def _base_claim(**over) -> dict:
    """一份默认合规的申请。诊断用它做见证的骨架。"""
    claim = {
        "synthetic": True,
        "claim_id": over.pop("claim_id", "DIAG"),
        "applicant": {
            "person_level": over.pop("person_level", "司局级"),
            "unit_type": over.pop("unit_type", "中央单位"),
        },
        "trip": {
            "origin_city": "北京",
            "destination_province": over.pop("province", "江苏"),
            "destination_city": over.pop("city", "南京市"),
            "departure_date": over.pop("depart", "2026-03-02"),
            "return_date": over.pop("ret", "2026-03-04"),
            "approved": True,
            "trip_purpose_type": over.pop("purpose", "一般出差"),
        },
        "lodging": {
            "stay_occurred": over.pop("stay", True),
            "nights": over.pop("nights", [
                {"date": "2026-03-02", "amount": 400, "room_type": "标准间",
                 "receipt_present": True, "payment_method": "公务卡"},
                {"date": "2026-03-03", "amount": 400, "room_type": "标准间",
                 "receipt_present": True, "payment_method": "公务卡"},
            ]),
        },
        "expense_lines": over.pop("lines", [
            {"line_id": "L1", "category": "城市间交通费", "fee_type": "客票", "amount": 443.5,
             "date": "2026-03-02", "receipt_present": True,
             "transport_type": "火车", "transport_class": "二等座"},
            {"line_id": "L2", "category": "市内交通费", "fee_type": "包干", "amount": 240,
             "receipt_present": True},
            {"line_id": "L3", "category": "伙食补助费", "fee_type": "包干", "amount": 300,
             "receipt_present": True},
        ]),
        "host": {
            "meal_arranged": over.pop("meal_arranged", False),
            "meal_place_type": over.pop("meal_place_type", None),
            "meal_place_has_standard": over.pop("meal_has_std", None),
            "meal_place_standard": over.pop("meal_std", None),
            "meal_fee_paid": over.pop("meal_paid", None),
            "transport_provided": over.pop("transport_provided", False),
            "transport_has_standard": over.pop("transport_has_std", None),
            "transport_standard_rate": over.pop("transport_rate", None),
            "transport_half_days": over.pop("transport_half_days", None),
            "transport_fee_paid": over.pop("transport_paid", None),
            "meeting_arranged_accommodation": over.pop("meeting_accom", False),
        },
        "flags": {"unit_insurance_purchased": over.pop("unit_insurance", False)},
    }
    return claim


def _witnesses(universe: Universe) -> list[tuple[str, dict]]:
    """见证集合。每条是一个可能触发特定规则组合的代表性申请。

    为什么用「代表性申请」而不是穷举字段组合：穷举会爆炸，而且大部分组合没有意义。
    这里挑的是**规则库实际用到的判别条件**各取一个取值，覆盖它们之间的交叉。
    做不到的组合会在报告里列为「无法自动判定」，不假装覆盖了。
    """
    w: list[tuple[str, dict]] = [("合规基准", _base_claim())]

    # 房型：司局级住普通套间（531 号不允许，497 号不限制）
    w.append(("司局级住普通套间", _base_claim(nights=[
        {"date": "2026-03-02", "amount": 400, "room_type": "普通套间",
         "receipt_present": True, "payment_method": "公务卡"},
    ])))
    # 部级住标准间（531 号要求普通套间）
    w.append(("部级住标准间", _base_claim(person_level="部级", nights=[
        {"date": "2026-03-02", "amount": 900, "room_type": "标准间",
         "receipt_present": True, "payment_method": "公务卡"},
    ])))

    # 会议统一安排食宿 + 报销市内交通费（第二十条 vs 第三十条）
    w.append(("学术会议且举办方安排食宿", _base_claim(
        purpose="学术会议", meeting_accom=True)))
    w.append(("学术会议但自行安排食宿", _base_claim(
        purpose="学术会议", meeting_accom=False)))

    # 缺住宿发票（第二十五条联动）
    w.append(("住宿但缺发票", _base_claim(nights=[
        {"date": "2026-03-02", "amount": 400, "room_type": "标准间",
         "receipt_present": False, "payment_method": "公务卡"},
    ])))
    w.append(("未住宿且无发票", _base_claim(stay=False, nights=[])))

    # 住宿超标
    w.append(("司局级住宿超限", _base_claim(nights=[
        {"date": "2026-03-02", "amount": 520, "room_type": "标准间",
         "receipt_present": True, "payment_method": "公务卡"},
    ])))

    # 接待单位安排用餐 / 提供交通工具
    w.append(("内部食堂无标准用餐", _base_claim(
        meal_arranged=True, meal_place_type="单位内部食堂",
        meal_has_std=False, meal_paid=100)))
    w.append(("接待方提供交通无标准", _base_claim(
        transport_provided=True, transport_has_std=False,
        transport_half_days=2, transport_paid=80)))

    # 交通席别超标
    w.append(("司局级坐商务座", _base_claim(lines=[
        {"line_id": "L1", "category": "城市间交通费", "fee_type": "客票", "amount": 1800,
         "date": "2026-03-02", "receipt_present": True,
         "transport_type": "火车", "transport_class": "商务座"},
    ])))

    # 未审批 / 同城 / 级别缺失
    w.append(("未审批", _base_claim()))
    w[-1][1]["trip"]["approved"] = False
    w.append(("同城出差", _base_claim(city="北京市", province="北京")))
    w[-1][1]["trip"]["origin_city"] = "北京市"
    w.append(("级别未填", _base_claim(person_level=None)))

    # 各地区的代表格子：覆盖 15 条住宿规则的范围
    for province, city in [
        ("北京", "北京市"), ("上海", "上海市"),
        ("江苏", "苏州市"), ("江苏", UNKNOWN_CITY),
        ("广东", "广州市"), ("广东", UNKNOWN_CITY),
        ("四川", "成都市"), ("四川", "阿坝州"), ("四川", "凉山州"), ("四川", UNKNOWN_CITY),
        ("黑龙江", "哈尔滨市"), ("黑龙江", UNKNOWN_CITY),
    ]:
        w.append((f"{province}-{city}", _base_claim(province=province, city=city)))

    # 旺季：哈尔滨 8 月
    w.append(("哈尔滨 8 月", _base_claim(
        province="黑龙江", city="哈尔滨市", depart="2026-08-10", ret="2026-08-12",
        nights=[{"date": "2026-08-10", "amount": 520, "room_type": "标准间",
                 "receipt_present": True, "payment_method": "公务卡"},
                {"date": "2026-08-11", "amount": 520, "room_type": "标准间",
                 "receipt_present": True, "payment_method": "公务卡"}])))
    return w


# ---------------------------------------------------------------- 缺口

def _rule_applies_to_cell(rule: Rule, claim: Claim, known: dict[str, set[str]]) -> bool:
    if not scope_matches(rule, claim, known):
        return False
    if not in_window(claim.trip_date, rule.effective_from, rule.effective_to):
        return False
    thr = rule.raw.get("threshold_from")
    if thr and not in_window(claim.trip_date, thr, rule.raw.get("threshold_to")):
        return False
    return True


def find_gaps(rules: list[Rule], universe: Universe, accepted: list[dict]) -> list[Finding]:
    """枚举住宿费用的 (省, 市, 级别) 格子，找出没有规则覆盖的。"""
    known = collect_known_cities(rules)
    thresholds = [r for r in rules if r.kind == "threshold" and r.topic == "住宿费"]
    findings: list[Finding] = []

    accepted_keys = {
        (g.get("province"), g.get("level")) for g in accepted
        if g.get("kind") == "lodging_cell"
    }

    missing_by_key: dict[tuple[str, str], list[str]] = {}
    for cell, level, _season in iter_lodging_cells(universe):
        probe = Claim(raw=_base_claim(province=cell.province, city=cell.city,
                                      person_level=level))
        matched = False
        for rule in thresholds:
            if not _rule_applies_to_cell(rule, probe, known):
                continue
            levels = set()
            for lim in rule.check["limits"]:
                levels.update(lim["by_person_level"].keys())
            if level in levels:
                matched = True
                break
        if not matched:
            if (cell.province, level) in accepted_keys:
                continue
            missing_by_key.setdefault((cell.province, level), []).append(cell.city)

    for (province, level), cities in sorted(missing_by_key.items()):
        findings.append(Finding(
            kind="gap",
            id=f"GAP-{province}-{level}",
            title=f"{province} 的 {level} 档住宿费无规则覆盖",
            detail=f"涉及城市：{'、'.join(cities)}",
            rule_ids=[],
        ))
    return findings


def find_structural_gaps(rules: list[Rule], universe: Universe) -> list[Finding]:
    """规则自身的形状缺口：枚举缺档、等级表缺格、合计规则缺默认值。"""
    findings: list[Finding] = []
    provinces = set(universe.provinces)

    for rule in rules:
        if rule.kind == "enum" and "allowed_by_person_level" in rule.check:
            have = set(rule.check["allowed_by_person_level"])
            missing = set(LEVELS) - have
            if missing:
                findings.append(Finding(
                    kind="gap", id=f"GAP-STRUCT-{rule.id}",
                    title=f"{rule.id} 缺少 {'/'.join(sorted(missing))} 档的允许取值",
                    rule_ids=[rule.id],
                ))

        if rule.kind == "ordinal":
            ranks = rule.check.get("ranks", {})
            ceilings = rule.check.get("max_rank_by_person_level", {})
            missing = []
            for ttype in ranks:
                for level in LEVELS:
                    if level not in ceilings.get(ttype, {}):
                        missing.append(f"{ttype}/{level}")
            if missing:
                findings.append(Finding(
                    kind="gap", id=f"GAP-STRUCT-{rule.id}",
                    title=f"{rule.id} 的等级上限表缺 {len(missing)} 格",
                    detail="缺：" + "、".join(missing),
                    rule_ids=[rule.id],
                ))

        if rule.kind == "aggregate":
            rate = rule.check.get("rate", {})
            if "default" not in rate:
                findings.append(Finding(
                    kind="gap", id=f"GAP-STRUCT-{rule.id}",
                    title=f"{rule.id} 未给出默认标准",
                    rule_ids=[rule.id],
                ))
            scope = rule.scope.get("province")
            if scope and scope not in provinces:
                findings.append(Finding(
                    kind="gap", id=f"GAP-SCOPE-{rule.id}",
                    title=f"{rule.id} 的省份「{scope}」不在枚举域内",
                    rule_ids=[rule.id],
                ))
    return findings


# ---------------------------------------------------------------- 冲突

def _evaluate_witnesses(rules: list[Rule], universe: Universe) -> dict[str, dict[str, list[str]]]:
    """对每个见证跑一遍**全部规则**，结果按 (见证, 规则) 归档。

    不能拿单条规则单独跑：引擎会把「规则库里没有任何规则管的类别」报成制度未覆盖，
    单跑一条规则时其余三个类别都会被误报成未覆盖，冲突判定会读到假状态。
    """
    evaluated: dict[str, dict[str, list[str]]] = {}
    for name, raw in _witnesses(universe):
        result = run(Claim(raw=raw), rules)
        per_rule: dict[str, list[str]] = {}
        for r in result.results:
            if r.rule_id == "<未覆盖>":
                continue
            per_rule.setdefault(r.rule_id, []).append(r.status)
        evaluated[name] = per_rule
    return evaluated


def find_conflicts(rules: list[Rule], universe: Universe) -> dict[str, list[Finding]]:
    """见证式冲突检测。

    一级：同 kind + 同 check.field，见证上结论不同 → 确定冲突。
    二级：同 topic 下 predicate 判不通过、非 predicate 判通过，
          且 predicate 未声明 affects（联动规则天然与多条规则共存）→ 候选。
    声明：conflict_with 的对，若构造不出分歧见证 → 陈旧声明。
    """
    confirmed: list[Finding] = []
    candidates: list[Finding] = []
    unresolved: list[Finding] = []

    witnesses = _witnesses(universe)
    evaluated = _evaluate_witnesses(rules, universe)

    # --- 一级 ---
    by_topic: dict[str, list[Rule]] = {}
    for rule in rules:
        by_topic.setdefault(rule.topic, []).append(rule)

    for topic, group in by_topic.items():
        for i, a in enumerate(group):
            for b in group[i + 1:]:
                if a.kind != b.kind:
                    continue
                field_a = (a.check or {}).get("field")
                field_b = (b.check or {}).get("field")
                if not field_a or field_a != field_b:
                    continue
                for name, raw in witnesses:
                    sa = set(evaluated[name].get(a.id, []))
                    sb = set(evaluated[name].get(b.id, []))
                    if not sa or not sb or sa == sb:
                        continue
                    if sa & {"不通过"} and sb & {"通过"}:
                        confirmed.append(Finding(
                            kind="conflict",
                            id=f"CF-{a.id}-{b.id}",
                            title=f"{a.id} 与 {b.id} 对同一判断给出不同结论",
                            detail=f"两者判断的都是 `{field_a}`，见证「{name}」下结论分别为 "
                                   f"{sorted(sa)} 与 {sorted(sb)}",
                            witness=name, rule_ids=[a.id, b.id],
                        ))
                        break

    confirmed_ids = {tuple(sorted(f.rule_ids)) for f in confirmed}

    # --- 二级 ---
    # 只在「费用类别」主题下比较。报销管理类的四条是**整单级准入检查**
    # （单位类型、是否审批、凭证是否齐、是否在常驻地外），彼此独立、可以同时成立，
    # 不构成「同一笔费用能不能报」的分歧。拿它们做 permit/forbid 比较会全是误报。
    for topic, group in by_topic.items():
        if topic not in CATEGORY_TOPICS:
            continue
        preds = [r for r in group if r.kind == "predicate" and not r.raw.get("affects")]
        others = [r for r in group if r.kind != "predicate"]
        for a in preds:
            for b in others:
                if tuple(sorted([a.id, b.id])) in confirmed_ids:
                    continue
                for name, raw in witnesses:
                    sa = set(evaluated[name].get(a.id, []))
                    sb = set(evaluated[name].get(b.id, []))
                    if "不通过" in sa and "通过" in sb:
                        candidates.append(Finding(
                            kind="candidate",
                            id=f"CAND-{a.id}-{b.id}",
                            title=f"{a.id} 禁止、{b.id} 放行（疑似冲突）",
                            detail=f"见证「{name}」下 {a.id} 判不通过而 {b.id} 判通过。"
                                   f"是否构成真冲突取决于两者的适用条件是否真的重叠，需人工判断",
                            witness=name, rule_ids=[a.id, b.id],
                        ))
                        break

    # --- 已声明但无法复现 ---
    declared_pairs: set[tuple[str, str]] = set()
    for rule in rules:
        other = rule.raw.get("conflict_with")
        if other:
            declared_pairs.add(tuple(sorted([rule.id, other])))

    for pair in sorted(declared_pairs):
        a, b = (next((r for r in rules if r.id == x), None) for x in pair)
        if a is None or b is None:
            continue
        reproduced = False
        for name, _raw in witnesses:
            sa = set(evaluated[name].get(a.id, []))
            sb = set(evaluated[name].get(b.id, []))
            if sa and sb and sa != sb:
                reproduced = True
                break
        if not reproduced:
            unresolved.append(Finding(
                kind="unreproducible",
                id=f"DECL-{a.id}-{b.id}",
                title=f"{a.id} 与 {b.id} 声明了冲突，但构造不出分歧见证",
                detail="可能是声明已过时（冲突已被修复），也可能是见证库没覆盖到这种情况",
                rule_ids=[a.id, b.id],
            ))

    return {"confirmed": confirmed, "candidates": candidates, "unresolved": unresolved}


# ---------------------------------------------------------------- 无效规则

def find_inert(rules: list[Rule], universe: Universe, whitelist: list[dict]) -> list[Finding]:
    """找出在任何输入下都无法影响结论的规则。

    本引擎没有优先级——所有匹配的规则都执行、结果再合并，
    所以经典的「被高优先级规则遮蔽」在这里不存在（D-021）。
    这里检测的是两种真实形态：范围为空、永远为通过。
    """
    findings: list[Finding] = []
    known = collect_known_cities(rules)
    excused = {w["rule_id"]: w.get("reason", "") for w in whitelist}

    nested = {
        "R-LODGING-JS-CORE": ("江苏", "南京"),
    }

    for rule in rules:
        if rule.id in excused:
            continue

        # 形态一：永远为通过（`allowed: any` 这类）
        if rule.kind == "enum" and (rule.check or {}).get("allowed") == "any":
            findings.append(Finding(
                kind="inert", id=f"INERT-{rule.id}",
                title=f"{rule.id} 永远判通过，不可能改变任何结论",
                detail="`allowed: any` 表示不限制取值。若它是为了与别的规则构成冲突对，"
                       "应登记进 known-gaps.yaml 的 intentionally_inert 并写明理由",
                rule_ids=[rule.id],
            ))
            continue

        # 形态二：范围为空——在枚举域里匹配不到任何格子
        province = rule.scope.get("province")
        if not province:
            continue
        matched_any = False
        for cell in universe.all_cells():
            probe = Claim(raw=_base_claim(province=cell.province, city=cell.city))
            if _rule_applies_to_cell(rule, probe, known):
                matched_any = True
                break
        if not matched_any:
            findings.append(Finding(
                kind="inert", id=f"INERT-{rule.id}",
                title=f"{rule.id} 的适用范围在枚举域内没有任何格子",
                detail=f"scope={rule.scope}",
                rule_ids=[rule.id],
            ))
    return findings


# ---------------------------------------------------------------- 覆盖度

def coverage(rules: list[Rule]) -> dict:
    """检查项覆盖率。

    分子来自**测试用例**（claimcheck.cases），不是诊断自己的见证集——
    否则就成了「用见证集给见证集打分」，数字再高也没有意义。
    诊断报告说「这一格从来没被测过」时，依据必须就是真正跑的那套用例。
    """
    from .cases import CASES

    universe_items: dict[str, str] = {}   # check_item -> kind
    for rule in rules:
        for item in expand_check_items(rule):
            universe_items[item] = rule.kind

    touched: set[str] = set()
    rules_touched: set[str] = set()
    for _name, build, as_of, _expected in CASES:
        result = run(Claim(raw=build()), rules, as_of=as_of)
        for r in result.results:
            touched.add(r.check_item)
            rules_touched.add(r.rule_id)

    by_kind: dict[str, dict[str, int]] = {}
    for item, kind in universe_items.items():
        slot = by_kind.setdefault(kind, {"total": 0, "touched": 0})
        slot["total"] += 1
        if item in touched:
            slot["touched"] += 1

    uncovered = sorted(item for item in universe_items if item not in touched)
    return {
        "check_items_total": len(universe_items),
        "check_items_touched": sum(1 for i in universe_items if i in touched),
        "rules_total": len(rules),
        "rules_touched": len(rules_touched),
        "by_kind": by_kind,
        "uncovered": uncovered,
        "case_count": len(CASES),
    }
