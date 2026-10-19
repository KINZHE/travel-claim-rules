"""从规则 YAML 生成来源映射表 docs/source-mapping.md。

为什么要有这个脚本：
    规则一多，「这条规则依据的是哪一条」就没人能凭记忆回答。
    手写映射表一定会和规则本身脱节，所以让它由规则生成——**改规则，表自动更新**。

用法：
    python scripts/gen_source_mapping.py
"""

from pathlib import Path

import yaml

# 项目根目录：本文件在 scripts/ 下，所以 parent.parent
ROOT = Path(__file__).resolve().parent.parent
RULES_DIR = ROOT / "rules"
OUTPUT = ROOT / "docs" / "source-mapping.md"


def load_rules() -> list[dict]:
    """读 rules/ 下所有 yaml，拼成一个列表。每个文件顶层是规则数组。"""
    rules = []
    for path in sorted(RULES_DIR.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not data:
            continue
        for rule in data:
            rule["_file"] = path.name
            rules.append(rule)
    return rules


def cell(value) -> str:
    """表格单元格：空值显示为 —，竖线转义，避免破坏表格。"""
    if value is None or value == "":
        return "—"
    return str(value).replace("|", "\\|")


def build_table(rules: list[dict]) -> list[str]:
    lines = [
        "| 规则 ID | 主题 | 文号 | 条款 | 附表行 | 版本 | 生效 | 层级 | 取舍 | 文件 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in rules:
        src = r.get("source", {})
        window = r.get("effective_from", "")
        if r.get("effective_to"):
            window = f"{window} ~ {r['effective_to']}"
        if r.get("threshold_from"):
            window += f"（阈值 {r['threshold_from']} 起）"
        lines.append(
            "| {id} | {topic} | {doc_no} | {article} | {table_ref} | {version} | {window} | {tier} | {decision} | {file} |".format(
                id=f"`{r.get('id', '?')}`",
                topic=cell(r.get("topic")),
                doc_no=cell(src.get("doc_no")),
                article=cell(src.get("article")),
                table_ref=cell(src.get("table_ref")),
                version=cell(r.get("version")),
                window=cell(window),
                tier=cell(src.get("tier")),
                decision=cell(r.get("decision")),
                file=cell(r.get("_file")),
            )
        )
    return lines


def main() -> None:
    rules = load_rules()
    lines = [
        "# 规则与制度来源的映射",
        "",
        "**本文件由 `scripts/gen_source_mapping.py` 自动生成，不要手改。**",
        "改规则后重新运行脚本即可。",
        "",
        f"共 {len(rules)} 条规则。层级 A = 官网原件，B = 政府转载，C = 商业法规库。",
        "",
    ]
    lines += build_table(rules)
    lines.append("")
    OUTPUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"已生成 {OUTPUT.relative_to(ROOT)}（{len(rules)} 条规则）")


if __name__ == "__main__":
    main()
