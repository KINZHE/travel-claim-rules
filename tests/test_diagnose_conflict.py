"""诊断层必须能自己发现冲突，而不是复述人工声明。

这几个测试的核心手法：**把规则复制到临时目录，删掉所有 `conflict_with` 再跑诊断**。
如果还能发现那两处冲突，说明检测是真的在做判断，不是在读声明。

这是本模块最重要的验收标准——诊断层的意义就是发现人工标注漏掉的东西。
"""

from pathlib import Path

import pytest
import yaml

from claimcheck.diagnose import find_conflicts
from claimcheck.model import load_rules
from claimcheck.universe import load_universe

ROOT = Path(__file__).resolve().parent.parent


def _rules_without_declarations(tmp_path: Path):
    """复制规则目录，删掉 conflict_with / on_conflict，返回新的规则列表。"""
    dest = tmp_path / "rules"
    dest.mkdir()
    for src in sorted((ROOT / "rules").glob("*.yaml")):
        data = yaml.safe_load(src.read_text(encoding="utf-8")) or []
        cleaned = []
        for rule in data:
            rule = {k: v for k, v in rule.items() if k not in {"conflict_with", "on_conflict"}}
            cleaned.append(rule)
        (dest / src.name).write_text(
            yaml.safe_dump(cleaned, allow_unicode=True, sort_keys=False), encoding="utf-8"
        )
    return load_rules(dest)


def test_room_type_conflict_found_without_declaration(tmp_path):
    """531 号第十三条 vs 497 号第三条：同字段、同类型、结论不同。"""
    rules = _rules_without_declarations(tmp_path)
    assert not any(r.raw.get("conflict_with") for r in rules), "副本里不应残留冲突声明"

    result = find_conflicts(rules, load_universe())
    pairs = {tuple(sorted(f.rule_ids)) for f in result["confirmed"]}
    assert (
        "R-LODGING-ROOM-TYPE", "R-LODGING-ROOM-TYPE-SUPERSEDED",
    ) in pairs


def test_meeting_conflict_found_as_candidate_without_declaration(tmp_path):
    """第二十条 vs 第三十条：同文件内部的冲突，靠「一禁一放」形状识别。"""
    rules = _rules_without_declarations(tmp_path)
    result = find_conflicts(rules, load_universe())

    joined = [tuple(sorted(f.rule_ids)) for f in result["candidates"]]
    assert ("R-LOCAL-ALLOWANCE", "R-LOCAL-MEETING-SCOPE") in joined


def test_no_invoice_vs_limit_is_not_a_conflict(tmp_path):
    """缺发票判不通过、限额判通过，两条同时成立很正常，不能报成冲突。

    它们回答的是两个问题：一个查凭证，一个查金额。
    把这条防住，二级冲突的启发式才不至于变成噪声源。
    """
    rules = _rules_without_declarations(tmp_path)
    result = find_conflicts(rules, load_universe())

    forbidden = {tuple(sorted(["R-LODGING-NO-INVOICE", "R-LODGING-JS-CORE"]))}
    for group in ("confirmed", "candidates", "unresolved"):
        for finding in result[group]:
            assert tuple(sorted(finding.rule_ids)) not in forbidden, (
                f"误报：{finding.title} 出现在 {group} 里"
            )


def test_declared_conflicts_are_reproducible():
    """规则里声明的每一对，都必须能构造出结论分歧的见证。"""
    rules = load_rules(ROOT / "rules")
    result = find_conflicts(rules, load_universe())
    assert result["unresolved"] == [], (
        "有声明了但无法复现的冲突：" + ", ".join(f.id for f in result["unresolved"])
    )
