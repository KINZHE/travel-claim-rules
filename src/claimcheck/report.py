"""诊断报告生成。

报告分五节，最后一节「方法与边界」是硬要求：
它把工具做不到什么也写清楚，和规则里标「建模假定」是同一个原则。
一份只说你哪里错了、不说自己边界的报告，比没有报告更容易误导人。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from .diagnose import (
    Finding, coverage, find_conflicts, find_gaps, find_inert, find_structural_gaps,
)
from .model import Rule
from .paths import ROOT
from .universe import Universe, load_universe


def _section(title: str, body: list[str]) -> list[str]:
    return [f"## {title}", ""] + body + [""]


def _findings_table(items: list[Finding]) -> list[str]:
    if not items:
        return ["无。", ""]
    lines = ["| 编号 | 问题 | 说明 | 见证 |", "| --- | --- | --- | --- |"]
    for f in items:
        detail = f.detail.replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {f.id} | {f.title} | {detail or '—'} | {f.witness or '—'} |")
    lines.append("")
    return lines


def build_report(rules: list[Rule], universe: Universe | None = None,
                 known: dict | None = None) -> str:
    universe = universe or load_universe()
    known = known or {}

    gaps = find_gaps(rules, universe, known.get("accepted_gaps", []))
    gaps += find_structural_gaps(rules, universe)
    conflicts = find_conflicts(rules, universe)
    inert = find_inert(rules, universe, known.get("intentionally_inert", []))
    cov = coverage(rules)
    summary = universe.summary()

    lines: list[str] = [
        "# 规则库诊断报告",
        "",
        f"_生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}　"
        f"规则 {len(rules)} 条　检查项 {cov['check_items_total']} 个　"
        f"测试用例 {cov['case_count']} 个_",
        "",
        "---",
        "",
        "## 结论摘要",
        "",
        f"- **确定冲突** {len(conflicts['confirmed'])} 处"
        f"（同字段、同类型的判断分歧）",
        f"- **疑似冲突** {len(conflicts['candidates'])} 处（需人工判断，见第二节）",
        f"- **无法复现的声明** {len(conflicts['unresolved'])} 处",
        f"- **缺口** {len(gaps)} 处",
        f"- **无效规则** {len(inert)} 条",
        f"- **检查项覆盖** {cov['check_items_touched']}/{cov['check_items_total']}"
        f"（{100 * cov['check_items_touched'] / max(1, cov['check_items_total']):.0f}%）",
        "",
        "---",
        "",
    ]

    lines += _section("一、缺口", _findings_table(gaps))
    lines += _section("二、冲突", [
        "### 确定的冲突",
        "",
        *_findings_table(conflicts["confirmed"]),
        "### 疑似冲突（需人工判断）",
        "",
        "同一费用类别下，一条规则判「不能报」、另一条判「能报」。"
        "是否真的构成冲突，取决于两者的适用条件是否真的重叠——工具只能指出来，判断要人来下。",
        "",
        *_findings_table(conflicts["candidates"]),
        "### 声明了但无法复现",
        "",
        "规则里写了 `conflict_with`，但构造不出两者结论分歧的见证。"
        "可能是声明过时（冲突已修复），也可能是见证库没覆盖到。",
        "",
        *_findings_table(conflicts["unresolved"]),
    ])
    lines += _section("三、无效规则", _findings_table(inert))

    kind_rows = ["| 检查类型 | 已覆盖 | 总计 | 覆盖率 |", "| --- | ---: | ---: | ---: |"]
    for kind, slot in sorted(cov["by_kind"].items()):
        pct = 100 * slot["touched"] / max(1, slot["total"])
        kind_rows.append(f"| {kind} | {slot['touched']} | {slot['total']} | {pct:.0f}% |")
    kind_rows.append("")

    uncovered = [f"- `{u}`" for u in cov["uncovered"][:30]]
    more = (f"- ……另有 {len(cov['uncovered']) - 30} 项，见命令行输出"
            if len(cov["uncovered"]) > 30 else "")
    lines += _section("四、覆盖度", [
        f"规则命中 {cov['rules_touched']}/{cov['rules_total']}，"
        f"检查项命中 {cov['check_items_touched']}/{cov['check_items_total']}。",
        "",
        "两个数字的差距本身就是信号：规则被触发了，不代表它内部的每一格都被测过。",
        "",
        *kind_rows,
        "### 从未被测试触发过的检查项",
        "",
        *(uncovered + ([more] if more else [])),
        "",
    ])

    documented = known.get("documented_gaps", [])
    doc_rows = [
        "- **{}**：{}（{}）".format(
            g["id"], " ".join(str(g["what"]).split()), " ".join(str(g["reason"]).split())
        )
        for g in documented
    ]

    lines += _section("五、方法与边界", [
        "这一节说明工具**做不到**什么。不写清楚的话，报告里的数字会被读成比实际更强。",
        "",
        "**枚举域由数据文件界定，不是全空间。** "
        f"当前域：{summary['provinces']} 个省、{summary['city_cells']} 个城市格子、"
        f"{summary['levels']} 个人员级别，合计 {summary['lodging_combos']} 个住宿判断组合。"
        "域取自 `data/lodging-subset.csv`，换成 36 个地区就只能抽样——"
        "那时这套枚举方法的成本会随地区数线性上升。",
        "",
        "**冲突检测是见证式的，不是符号推理。** "
        "一级冲突要求同类型、同判断字段，可信度高；"
        "二级冲突是启发式（「一条禁止、一条放行」），会误报，所以只列为候选，"
        "是否真冲突要人判断。",
        "",
        "**见证集是有限的。** "
        "无法构造见证的规则对会列进报告，不会静默跳过；"
        "但组合爆炸的角落覆盖不到，这是方法的固有局限。",
        "",
        "**时间维度不参与枚举。** "
        "诊断只在当前生效的规则版本上跑，跨年份的缺口（如 2014–2015 年无阈值）"
        "不在自动检测范围内，见下面的已登记缺口。",
        "",
        "**已登记、接受不处理的缺口**：",
        "",
        *(doc_rows or ["无。"]),
        "",
        "完整说明见 `docs/known-gaps.yaml`。",
        "",
    ])

    return "\n".join(lines)


def write_report(path: str | Path, rules: list[Rule], known: dict | None = None) -> Path:
    import yaml

    target = Path(path)
    universe = load_universe()
    if known is None:
        known_path = ROOT / "docs" / "known-gaps.yaml"
        known = yaml.safe_load(known_path.read_text(encoding="utf-8")) or {}
    target.write_text(build_report(rules, universe, known), encoding="utf-8")
    return target
