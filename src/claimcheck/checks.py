"""五类检查的求值器。

每条规则有一个 kind，决定用哪个求值器。这不是为了炫技——五类判断的对象不同：

    threshold  比较某个数值与上限，上限可能按人员级别取
    enum       判断取值是否在允许集合内，集合可能按人员级别取
    ordinal    判断等级序数是否超上限（坐更低等级是允许的）
    aggregate  判断某类费用行合计是否等于「天数 × 标准」
    predicate  通用布尔组合，用于确实无法归类的情形

前四类都能暴露「枚举维度」，第 3 周做缺口分析要用（见 docs/rule-semantics.md）。
predicate 只有 1 个检查项，不参与条件空间枚举——这个边界要写清楚，不能假装全能。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .model import MISSING, Claim, Rule, get_path, iter_path
from .predicate import Tri, UndefinedField, evaluate

# 结果状态。顺序即严重度，合并时取最高的。
STATUS_ORDER = ["通过", "制度未覆盖", "信息不足", "需人工确认", "超支自理", "不通过"]
SEVERITY = {s: i for i, s in enumerate(STATUS_ORDER)}


def worst(statuses: list[str]) -> str:
    """取严重度最高的状态。空列表视为「通过」。"""
    if not statuses:
        return "通过"
    return max(statuses, key=lambda s: SEVERITY.get(s, 0))


@dataclass
class Outcome:
    """一条检查项的求值结果。

    splits 用于「一条规则实际产生了多个检查项」的情形：
    等级类规则逐交通工具类型判断、阈值类规则可能落到旺季限额，
    这些在覆盖率统计里应当是各自独立的格子，不能合并成一个。
    """
    status: str
    observed: dict = field(default_factory=dict)
    message: str = ""
    splits: dict[str, "Outcome"] | None = None

    def grade(self) -> str:
        """把结果换成规则里 on_pass / on_fail 的取值。"""
        return self.status


def _missing(message: str) -> Outcome:
    return Outcome("信息不足", {}, message)


# ---------------------------------------------------------------- 阈值类

def check_threshold(rule: Rule, claim: Claim) -> Outcome:
    """逐项比较数值与限额。

    住宿是逐晚判断的：旺季规则要判断「哪几晚落在旺季」，均价判断会掩盖单晚超标（D-004）。
    """
    check = rule.check
    field_path = check["field"]
    level = claim.get("applicant.person_level")
    if level is MISSING or level is None:
        return _missing("人员级别未填写，无法确定适用哪一档限额")

    pairs = iter_path(claim.raw, field_path)
    if not pairs:
        return _missing(f"没有可检查的 {field_path} 数据")

    limits = {lim["kind"]: lim for lim in check["limits"]}
    base = limits.get("base")
    if not base:
        return _missing("规则缺少 base 限额")

    peak = limits.get("peak")
    city = claim.get("trip.destination_city")
    night_city = city if city is not MISSING else None

    offenders = []
    details = []

    for item, value in pairs:
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return _missing(f"{field_path} 含非数值: {value!r}")

        # 选限额：先看是否落在旺季
        limit = base["by_person_level"].get(level)
        used = "base"
        if peak and _in_peak(item, peak, night_city):
            limit = peak["by_person_level"].get(level)
            used = "peak"

        if limit is None:
            return _missing(f"规则未定义 {level} 档的限额")

        date_tag = item.get("date") if isinstance(item, dict) else None
        details.append({"date": date_tag, "amount": value, "limit": limit, "limit_kind": used})
        if value > limit:
            offenders.append((date_tag, value, limit, used))

    if offenders:
        parts = [
            f"{d or '未标日期'} 晚 {a} 元超过 {'旺季' if k == 'peak' else ''}限额 {l} 元"
            for d, a, l, k in offenders
        ]
        return Outcome(
            rule.raw.get("on_fail", "不通过"),
            {"nights": details, "offenders": offenders},
            "；".join(parts),
            splits=_split_by_limit(details, rule.raw.get("on_fail", "不通过")),
        )

    return Outcome(
        "通过", {"nights": details}, f"{len(details)} 晚均未超过限额",
        splits=_split_by_limit(details, "通过"),
    )


def _split_by_limit(details: list[dict], fail_status: str) -> dict[str, Outcome]:
    """按 base / peak 拆成检查项。每个分项要带上自己的违规晚次，
    否则报告里只剩「有 2 笔超标」，看不出是哪一晚、按哪档限额超的。"""
    out: dict[str, Outcome] = {}
    for kind in {d["limit_kind"] for d in details}:
        same = [d for d in details if d["limit_kind"] == kind]
        bad = [d for d in same if d["amount"] > d["limit"]]
        if bad:
            msg = "；".join(
                f"{d['date'] or '未标日期'} 晚 {d['amount']} 元超过 "
                f"{'旺季' if kind == 'peak' else ''}限额 {d['limit']} 元"
                for d in bad
            )
            out[kind] = Outcome(fail_status, {"nights": same, "offenders": bad}, msg)
        else:
            out[kind] = Outcome("通过", {"nights": same}, f"{len(same)} 晚未超过限额")
    return out


def _in_peak(item: Any, peak: dict, city: str | None) -> bool:
    """判断这一晚是否适用旺季限额。月份或城市不满足则为 False。"""
    months = peak.get("months") or []
    cities = peak.get("applies_to_cities") or []

    if cities and city not in cities:
        return False
    if not months:
        return True
    if not isinstance(item, dict) or "date" not in item:
        return False
    try:
        month = int(str(item["date"]).split("-")[1])
    except (ValueError, IndexError):
        return False
    return month in months


# ---------------------------------------------------------------- 枚举类

def check_enum(rule: Rule, claim: Claim) -> Outcome:
    """判断取值是否在允许集合内。集合可按人员级别取，也可声明为 any。"""
    check = rule.check
    field_path = check["field"]

    if check.get("allowed") == "any":
        return Outcome("通过", {}, "本条不限制取值")

    allowed = check.get("allowed")
    if allowed is None and "allowed_by_person_level" in check:
        level = claim.get("applicant.person_level")
        if level is MISSING or level is None:
            return _missing("人员级别未填写，无法确定允许取值")
        allowed = check["allowed_by_person_level"].get(level)
        if allowed is None:
            return _missing(f"规则未定义 {level} 档的允许取值")

    pairs = iter_path(claim.raw, field_path)
    if not pairs:
        return _missing(f"没有可检查的 {field_path} 数据")

    review_values = check.get("review_values") or []
    bad = []
    for item, value in pairs:
        if value is None:
            return _missing(f"{field_path} 存在空值")
        if value not in allowed:
            bad.append(value)

    if bad:
        return Outcome(
            rule.raw.get("on_fail", "不通过"),
            {"violations": bad, "allowed": allowed},
            f"取值 {bad} 不在允许范围 {allowed} 内",
        )

    hits_review = [v for _, v in pairs if v in review_values]
    if hits_review:
        return Outcome(
            rule.raw.get("on_review", "需人工确认"),
            {"values": hits_review},
            f"取值 {hits_review} 虽在允许范围内，但需按本单位细则确认",
        )

    return Outcome("通过", {"allowed": allowed}, "取值均在允许范围内")


# ---------------------------------------------------------------- 等级类

def check_ordinal(rule: Rule, claim: Claim) -> Outcome:
    """按等级序数判断。坐低于标准的席别是允许的，只有超标准才违规（D-017）。"""
    check = rule.check
    level = claim.get("applicant.person_level")
    if level is MISSING or level is None:
        return _missing("人员级别未填写，无法确定可乘等级")

    ranks = check["ranks"]
    max_rank = check["max_rank_by_person_level"]
    excluded = set(check.get("excluded_classes") or [])

    violations: list[dict] = []
    checked = 0
    by_type: dict[str, list[dict]] = {}
    missing_reason = ""

    for line in claim.expense_lines():
        if line.get("category") != "城市间交通费":
            continue
        ttype = line.get("transport_type")
        tclass = line.get("transport_class")
        if not ttype or not tclass:
            return _missing("费用行缺少交通工具类型或席别")

        if tclass in excluded:
            violations.append({"line": line.get("line_id"), "reason": f"{tclass} 属排除项"})
            by_type.setdefault(ttype, []).append({"reason": f"{tclass} 属排除项"})
            continue

        table = ranks.get(ttype)
        if not table:
            return _missing(f"规则未定义「{ttype}」的等级序列")
        if tclass not in table:
            return _missing(f"规则未定义「{ttype}」的「{tclass}」等级")

        ceiling = max_rank.get(ttype, {}).get(level)
        if ceiling is None:
            return _missing(f"规则未定义 {level} 档在「{ttype}」上的上限")

        checked += 1
        if table[tclass] > ceiling:
            v = {"line": line.get("line_id"), "class": tclass,
                 "rank": table[tclass], "ceiling": ceiling}
            violations.append(v)
            by_type.setdefault(ttype, []).append(v)
        else:
            by_type.setdefault(ttype, [])

    if violations:
        splits = {
            ttype: Outcome(
                rule.raw.get("on_fail", "超支自理"),
                {"violations": vs},
                "；".join(f"{v.get('class', v.get('reason'))} 超过 {level} 档上限" for v in vs),
            )
            for ttype, vs in by_type.items() if vs
        }
        for ttype, vs in by_type.items():
            if not vs:
                splits[ttype] = Outcome("通过", {}, f"「{ttype}」席别符合 {level} 档标准")
        return Outcome(
            rule.raw.get("on_fail", "超支自理"),
            {"violations": violations},
            f"{len(violations)} 笔席别超标，超支部分由个人自理",
            splits=splits,
        )
    if checked == 0:
        return _missing("本单没有城市间交通费，无法判断")
    return Outcome(
        "通过", {"checked": checked}, f"{checked} 笔交通费席别符合级别标准",
        splits={ttype: Outcome("通过", {}, f"「{ttype}」席别符合 {level} 档标准") for ttype in by_type},
    )


# ---------------------------------------------------------------- 合计类

def check_aggregate(rule: Rule, claim: Claim) -> Outcome:
    """某类费用行的合计是否等于 天数 × 日标准。"""
    check = rule.check
    category = check["category"]

    days = claim.get(check.get("multiplier", "travel_days"))
    if days is MISSING or not isinstance(days, int):
        return _missing("无法计算出差天数")

    rate_conf = check["rate"]
    rate = rate_conf.get("default")
    field_name = rate_conf.get("field")
    if field_name:
        key = claim.get(field_name)
        if key is MISSING or key is None:
            return _missing(f"无法确定 {field_name}，不知道适用哪一档标准")
        rate = rate_conf.get("map", {}).get(key, rate_conf.get("default"))
    if rate is None:
        return _missing("规则未给出可用标准")

    lines = [l for l in claim.expense_lines() if l.get("category") == category]
    if not lines:
        return _missing(f"本单没有 {category} 费用行")

    total = 0
    for line in lines:
        amt = line.get("amount")
        if not isinstance(amt, (int, float)) or isinstance(amt, bool):
            return _missing(f"{category} 费用行金额不是数值: {amt!r}")
        total += amt

    expected = days * rate
    if total != expected:
        return Outcome(
            rule.raw.get("on_fail", "不通过"),
            {"total": total, "expected": expected, "days": days, "rate": rate},
            f"{category}合计 {total} 元，应为 {days} 天 × {rate} 元 = {expected} 元",
        )
    return Outcome(
        "通过",
        {"total": total, "expected": expected, "days": days, "rate": rate},
        f"{category}合计 {total} 元，与 {days} 天 × {rate} 元一致",
    )


# ---------------------------------------------------------------- 谓词类

def check_predicate(rule: Rule, claim: Claim) -> Outcome:
    """通用布尔组合。三值求值，未知一律判「信息不足」。"""
    try:
        result = evaluate(rule.check["expr"], claim)
    except UndefinedField as e:
        return _missing(str(e))

    if result is Tri.TRUE:
        return Outcome("通过", {}, "条件满足")
    if result is Tri.FALSE:
        return Outcome(rule.raw.get("on_fail", "不通过"), {}, "条件不满足")
    return Outcome("需人工确认" if rule.raw.get("on_review") else "信息不足", {},
                   "条件中有无法判断的项（字段缺失或值不足以比较）")


EVALUATORS = {
    "threshold": check_threshold,
    "enum": check_enum,
    "ordinal": check_ordinal,
    "aggregate": check_aggregate,
    "predicate": check_predicate,
}


def run_check(rule: Rule, claim: Claim) -> Outcome:
    evaluator = EVALUATORS.get(rule.kind)
    if evaluator is None:
        raise ValueError(f"{rule.id}: 未知的 kind {rule.kind!r}，无法求值")
    return evaluator(rule, claim)


# ---------------------------------------------------------------- 检查项展开

def expand_check_items(rule: Rule) -> list[str]:
    """静态展开出一条规则包含哪些检查项。用于覆盖率分母，与运行结果无关。

    只展开已知维度的四类；predicate 恒为 1 个（见模块文档）。
    """
    rid = rule.id
    kind = rule.kind

    def levels(values) -> list[str]:
        return [str(v) for v in values]

    if kind == "threshold":
        out = []
        for lim in rule.check["limits"]:
            for level in lim["by_person_level"]:
                out.append(f"{rid}::{lim['kind']}::{level}")
        return out

    if kind == "enum":
        tbl = rule.check.get("allowed_by_person_level")
        if tbl:
            return [f"{rid}::{level}" for level in tbl]
        return [f"{rid}::main"]

    if kind == "ordinal":
        ranks = rule.check["ranks"]
        ceilings = rule.check["max_rank_by_person_level"]
        out = []
        for ttype in ranks:
            for level in ceilings.get(ttype, {}):
                out.append(f"{rid}::{level}::{ttype}")
        return out

    if kind == "aggregate":
        rate = rule.check["rate"]
        keys = list(rate.get("map", {}).keys())
        if keys:
            return [f"{rid}::{k}" for k in keys] + [f"{rid}::default"]
        return [f"{rid}::default"]

    return [f"{rid}::main"]
