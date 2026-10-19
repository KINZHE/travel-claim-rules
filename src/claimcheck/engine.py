"""引擎：把规则跑在申请上，产出逐条结果与整体结论。

执行顺序（每一步都对应 docs/rule-semantics.md 的一条约定）：
    1. 版本门       申请日期落在条款区间内？不在就跳过这条规则
    2. 阈值门       条款在，但阈值不覆盖这个时间 → 信息不足（不是跳过）
    3. 范围匹配     地区、城市、兜底
    4. when 匹配    本申请是否涉及这条规则管的费用类别
    5. 求值         交给对应的检查类型
    6. 冲突处理     两条规则都命中且结论分歧 → 需人工确认
    7. 合并         先按费用项，再汇总整单
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .checks import Outcome, expand_check_items, run_check, worst
from .matcher import collect_known_cities, scope_matches, when_matches
from .model import Claim, Rule, in_window


@dataclass
class CheckResult:
    rule_id: str
    check_item: str
    topic: str
    scope_category: str | None      # 归属费用项；None 表示整单级
    status: str
    observed: dict = field(default_factory=dict)
    message: str = ""
    conflict: list[str] = field(default_factory=list)
    skipped_reason: str = ""        # 被版本门/范围门挡掉时填，便于排查


@dataclass
class EngineResult:
    claim_id: str
    trip_date: str | None
    results: list[CheckResult]
    by_category: dict[str, str]
    overall: str

    def to_dict(self) -> dict:
        return {
            "claim_id": self.claim_id,
            "trip_date": self.trip_date,
            "overall": self.overall,
            "by_category": self.by_category,
            "results": [
                {
                    "rule_id": r.rule_id,
                    "check_item": r.check_item,
                    "topic": r.topic,
                    "scope_category": r.scope_category,
                    "status": r.status,
                    "observed": r.observed,
                    "message": r.message,
                    "conflict": r.conflict,
                }
                for r in self.results
            ],
        }


def _category_of(rule: Rule) -> str | None:
    """规则归属哪个费用项。报销管理类是整单级（None）。"""
    if rule.topic == "报销管理":
        return None
    return rule.topic or None


def _split_label(rule: Rule, suffix: str, claim: Claim) -> str:
    """分项检查项的命名，必须与 checks.expand_check_items 的展开名一致，
    否则覆盖率分子永远对不上分母。"""
    level = claim.get("applicant.person_level")
    level = level if isinstance(level, str) else "main"
    if rule.kind == "threshold":
        return f"{suffix}::{level}"      # base::司局级 / peak::司局级
    if rule.kind == "ordinal":
        return f"{level}::{suffix}"      # 司局级::飞机
    return suffix


def _check_item_id(rule: Rule, claim: Claim) -> str:
    """运行期实际产生的检查项 ID。要和 expand_check_items 的命名对齐。"""
    levels = claim.get("applicant.person_level")
    level = levels if isinstance(levels, str) else "main"
    kind = rule.kind

    if kind == "threshold":
        # 用到的限额档由求值过程决定，这里按 base 档给一个稳定 ID；
        # 旺季命中时由求值结果补充到 observed 里。
        return f"{rule.id}::base::{level}"
    if kind == "enum":
        return f"{rule.id}::{level}" if rule.check.get("allowed_by_person_level") else f"{rule.id}::main"
    if kind == "ordinal":
        return f"{rule.id}::{level}::all"
    if kind == "aggregate":
        return f"{rule.id}::default"
    return f"{rule.id}::main"


def run(claim: Claim, rules: list[Rule], as_of: str | None = None) -> EngineResult:
    """对一份申请跑全部规则。

    as_of：用指定日期代替出差日做版本判断。用于历史复核与版本对比演示——
    一份 2013 年的单据，既可以按当年制度判，也可以在制度更替后按新制度复核。
    只影响「适用哪一版规则」，不影响天数计算。
    """
    from datetime import date

    known_cities = collect_known_cities(rules)
    basis = date.fromisoformat(as_of) if as_of else claim.trip_date

    results: list[CheckResult] = []
    skipped: list[str] = []

    for rule in rules:
        # --- 1. 版本门 ---
        if not in_window(basis, rule.effective_from, rule.effective_to):
            skipped.append(f"{rule.id}: 条款生效区间不含判断基准日")
            continue

        # --- 3. 范围门 ---
        if not scope_matches(rule, claim, known_cities):
            skipped.append(f"{rule.id}: 目的地不在适用范围")
            continue

        # --- 4. when 门 ---
        if not when_matches(rule, claim):
            skipped.append(f"{rule.id}: 本申请不涉及该费用类别")
            continue

        # --- 2. 阈值门（放在范围之后，避免对不相关的申请报「阈值未覆盖」）---
        thr_from = rule.raw.get("threshold_from")
        if thr_from and not in_window(basis, thr_from, rule.raw.get("threshold_to")):
            results.append(CheckResult(
                rule_id=rule.id,
                check_item=_check_item_id(rule, claim),
                topic=rule.topic,
                scope_category=_category_of(rule),
                status="信息不足",
                message=f"该规则有规定，但本项目没有 {thr_from} 之前的标准数据，无法判断",
            ))
            continue

        # --- 5. 求值 ---
        outcome: Outcome = run_check(rule, claim)
        if outcome.splits:
            # 一条规则实际产生了多个检查项（等级类逐交通工具、阈值类区分淡旺季）。
            # 不再额外记一条「汇总项」——各分项本身带正确的状态，
            # 按费用项合并时自然会取到最坏的那个，多记一条只会让结果表变长。
            for suffix, sub in outcome.splits.items():
                results.append(CheckResult(
                    rule_id=rule.id,
                    check_item=f"{rule.id}::{_split_label(rule, suffix, claim)}",
                    topic=rule.topic,
                    scope_category=_category_of(rule),
                    status=sub.status,
                    observed=sub.observed,
                    message=sub.message,
                ))
            continue

        results.append(CheckResult(
            rule_id=rule.id,
            check_item=_check_item_id(rule, claim),
            topic=rule.topic,
            scope_category=_category_of(rule),
            status=outcome.status,
            observed=outcome.observed,
            message=outcome.message,
        ))

    results = _resolve_conflicts(results)
    results += _uncovered_categories(claim, rules)

    by_category = _merge_by_category(results)
    overall = worst([r.status for r in results]) if results else "制度未覆盖"

    return EngineResult(
        claim_id=claim.claim_id,
        trip_date=basis.isoformat() if basis else None,
        results=results,
        by_category=by_category,
        overall=overall,
    )


def _uncovered_categories(claim: Claim, rules: list[Rule]) -> list[CheckResult]:
    """找出本申请里「规则库里根本没有对应规则」的费用类别。

    为什么必须单独报：如果一整类费用没有规则管，它就不会产生任何结果，
    整体结论会因此变成「通过」——等于把规则库的缺口伪装成合规（D-015）。
    这里判的是「规则库里有没有」，不是「本时段适不适用」：
    后者由版本门处理，输出信息不足，两者含义不同。
    """
    library_cats: set[str] = set()
    for rule in rules:
        cats = rule.when.get("category")
        if cats is None:
            continue
        library_cats.update(cats if isinstance(cats, list) else [cats])

    uncovered = claim.categories_present() - library_cats
    return [
        CheckResult(
            rule_id="<未覆盖>",
            check_item=f"<未覆盖>::{cat}",
            topic=cat,
            scope_category=cat,
            status="制度未覆盖",
            message=f"规则库中没有适用于「{cat}」的规定，需人工确认该类别是否属于差旅费范围",
        )
        for cat in sorted(uncovered)
    ]


def _resolve_conflicts(results: list[CheckResult]) -> list[CheckResult]:
    """两条规则都命中且结论不同 → 双方改判需人工确认；结论相同则保留原结论。

    为什么不是「一命中就转人工」：那会让两条规则互相抵消，正常情形也判不了
    （见 docs/rule-semantics.md 第二节）。
    """
    by_rule = {r.rule_id: r for r in results}
    for r in results:
        if not r.conflict:
            continue
        for other_id in r.conflict:
            other = by_rule.get(other_id)
            if other is None:
                continue
            if other.status == r.status:
                continue
            # 分歧：双方都标冲突，并降到需人工确认
            for target in (r, other):
                target.conflict = sorted({r.rule_id, other_id})
                target.status = "需人工确认"
                target.message = (
                    f"与 {other_id if target is r else r.rule_id} 在同一情形下结论不同，"
                    f"制度未规定如何取舍，需人工确认"
                )
    return results


def _merge_by_category(results: list[CheckResult]) -> dict[str, str]:
    grouped: dict[str, list[str]] = {}
    for r in results:
        key = r.scope_category or "整单"
        grouped.setdefault(key, []).append(r.status)
    return {k: worst(v) for k, v in grouped.items()}


def check_item_universe(rules: list[Rule]) -> list[str]:
    """全部检查项（覆盖率分母）。与运行结果无关，只由规则本身决定。"""
    out: list[str] = []
    for rule in rules:
        out.extend(expand_check_items(rule))
    return out
