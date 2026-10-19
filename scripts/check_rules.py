"""规则库自检。

三条硬断言，任何一条不过就退出码非零：

1. 每条规则的 require 字段都在 docs/claim-schema.md 里定义过  —— 防字段名漂移
2. 每条规则的 source.article 能在对应的制度原文里找到           —— 防编造条款
3. 住宿费规则的阈值与 data/lodging-subset.csv 逐格一致          —— 防抄错数

另外做几项结构检查：id 唯一、decision 指向真实存在的条目、必填字段齐全。

用法：
    python scripts/check_rules.py
"""

from __future__ import annotations

import csv
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
RULES_DIR = ROOT / "rules"
CLAIM_SCHEMA = ROOT / "docs" / "claim-schema.md"
DECISIONS = ROOT / "docs" / "rule-decisions.md"
LODGING_CSV = ROOT / "data" / "lodging-subset.csv"

# 文号 → 原文文件。article 的写法在非 531 文件里是「三、」而不是「第三条」，
# 所以在 map_article() 里做一次转换。
SOURCES = {
    "财行〔2013〕531号": [ROOT / "sources" / "2013-531" / "full-text.txt"],
    "财行〔2006〕313号": [
        ROOT / "sources" / "2006-313" / "full-text.txt",
        ROOT / "sources" / "2006-313" / "backup-mof-gs.txt",
    ],
    "财行〔2015〕497号": [ROOT / "sources" / "2015-497" / "full-text.txt"],
    "财办行〔2019〕104号": [ROOT / "sources" / "2019-104" / "full-text.txt"],
}

CN_NUM = "一二三四五六七八九十"
LEVELS = ["部级", "司局级", "其他人员"]

errors: list[str] = []
warnings: list[str] = []


def err(msg: str) -> None:
    errors.append(msg)


# ---------------------------------------------------------------- 载入

def load_rules() -> list[dict]:
    rules = []
    for path in sorted(RULES_DIR.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if data is None:
            continue
        if not isinstance(data, list):
            err(f"{path.name}: 顶层应为列表")
            continue
        for r in data:
            r["_file"] = path.name
            rules.append(r)
    return rules


def documented_fields() -> set[str]:
    """从 claim-schema.md 里收集所有反引号包住的字段名。"""
    text = CLAIM_SCHEMA.read_text(encoding="utf-8")
    tokens = re.findall(r"`([A-Za-z_][A-Za-z0-9_.\[\]]*)`", text)
    names = set()
    for t in tokens:
        last = t.split(".")[-1].replace("[]", "")
        names.add(last)
    return names


def decision_ids() -> set[str]:
    text = DECISIONS.read_text(encoding="utf-8")
    return set(re.findall(r"##\s+(D-\d{3})", text))


def norm(s: str) -> str:
    s = "".join(s.split())
    for a, b in [("（", "("), ("）", ")"), ("，", ","), ("。", "."), ("；", ";"), ("％", "%")]:
        s = s.replace(a, b)
    return s


def articles_in(doc_no: str, article: str) -> bool:
    """判断「第X条」是否出现在对应原文里。非 531 文件按「X、」形式找。"""
    paths = SOURCES.get(doc_no)
    if not paths:
        err(f"未知文号 {doc_no}，无法校验条款")
        return False
    body = norm("".join(p.read_text(encoding="utf-8") for p in paths if p.exists()))

    for part in re.split(r"[、,]", article):
        part = part.strip()
        if not part:
            continue
        m = re.fullmatch(r"第([一二三四五六七八九十]+)条", part)
        if not m:
            err(f"无法解析的条款号「{part}」")
            return False
        num = m.group(1)
        # 「第N条」形式（531 / 2006）
        if norm(f"第{num}条") in body:
            continue
        # 「N、」形式（497 / 104 的条目编号）
        if doc_no.endswith("497号") or doc_no.endswith("104号"):
            if norm(f"{num}、") in body:
                continue
        err(f"{doc_no} 中找不到「第{num}条」")
        return False
    return True


# ---------------------------------------------------------------- 检查 1

def check_require_fields(rules: list[dict]) -> None:
    known = documented_fields()
    for r in rules:
        for field in r.get("require", []) or []:
            last = field.split(".")[-1].replace("[]", "")
            if last not in known:
                err(f"{r['id']}: require 字段 `{field}` 未在 claim-schema.md 中定义")


# ---------------------------------------------------------------- 检查 1b：kind 与 check 结构

KNOWN_KINDS = {"threshold", "enum", "ordinal", "aggregate", "predicate"}
VALID_OPS = {"eq", "ne", "in", "not_in", "lt", "lte", "gt", "gte", "is_null", "is_not_null"}
LOGIC_NODES = {"all", "any", "not"}
QUANT_NODES = {"for_all", "exists"}

# 不是输入字段、但引擎会算出来的只读值。必须与 claim-schema.md 的「派生值」一节一致。
DERIVED_FIELDS = {
    "travel_days",
    "daily_rate",
    "per_trip_insurance_count",
    "expected_host_transport_fee",
}

# 结构性的键：不是字段引用，是语法本身
STRUCT_KEYS = {
    "kind", "field", "op", "value", "all", "any", "not", "for_all", "exists",
    "path", "where", "test", "expr", "field_ref", "expected", "allowed",
    "allowed_by_person_level", "review_values",
    "ranks", "max_rank_by_person_level", "excluded_classes", "type_field",
    "category", "multiplier", "rate", "default", "map",
    "limits", "by_person_level", "months", "applies_to_cities",
}


def walk_fields(node, out: set[str]) -> None:
    """从 check 结构里收集所有被引用的字段名。"""
    if isinstance(node, dict):
        for key, val in node.items():
            if key in {"field", "path", "field_ref", "multiplier", "type_field"} and isinstance(val, str):
                out.add(val)
            else:
                walk_fields(val, out)
    elif isinstance(node, list):
        for item in node:
            walk_fields(item, out)


def check_kind_and_check_block(rules: list[dict]) -> None:
    known = documented_fields() | DERIVED_FIELDS
    for r in rules:
        rid = r.get("id", "<无 id>")

        # 先查字段引用：新旧格式都查一遍。旧格式用 assert，新格式用 check。
        block = r.get("check") or r.get("assert") or {}
        refs: set[str] = set()
        walk_fields(block, refs)
        for ref in sorted(refs):
            last = ref.split(".")[-1].replace("[]", "")
            if last not in known:
                err(f"{rid}: 引用了未定义字段 `{ref}`")

        kind = r.get("kind")
        if kind not in KNOWN_KINDS:
            err(f"{rid}: kind 必须是 {sorted(KNOWN_KINDS)} 之一，实际是 {kind!r}")
            continue

        if "assert" in r:
            err(f"{rid}: 仍有旧的 assert 字段，应改为 check + kind（见 SCHEMA.md）")
            continue

        check = r.get("check")
        if not isinstance(check, dict):
            err(f"{rid}: 缺 check 块")
            continue
        if check.get("kind") != kind:
            err(f"{rid}: 顶层 kind={kind!r} 与 check.kind={check.get('kind')!r} 不一致")

        if "condition" in check:
            err(f"{rid}: 仍有自由文本的 condition，必须迁移为结构化谓词")


def check_predicate_ast(rules: list[dict]) -> None:
    def validate(node, rid: str, path: str = "expr") -> None:
        if not isinstance(node, dict):
            err(f"{rid}: {path} 应为字典，实际是 {type(node).__name__}")
            return
        keys = set(node)
        if keys & LOGIC_NODES:
            node_key = (keys & LOGIC_NODES).pop()
            if len(keys) != 1:
                err(f"{rid}: {path} 的逻辑节点不应与其他键混用，实际键 {sorted(keys)}")
            val = node[node_key]
            if node_key == "not":
                validate(val, rid, f"{path}.not")
            elif not isinstance(val, list) or not val:
                err(f"{rid}: {path}.{node_key} 应为非空列表")
            else:
                for i, sub in enumerate(val):
                    validate(sub, rid, f"{path}.{node_key}[{i}]")
            return
        if keys & QUANT_NODES:
            node_key = (keys & QUANT_NODES).pop()
            body = node[node_key]
            if not isinstance(body, dict):
                err(f"{rid}: {path}.{node_key} 应为字典（含 path / test / where）")
                return
            if "path" not in body:
                err(f"{rid}: {path}.{node_key} 缺 path")
            if "test" not in body:
                err(f"{rid}: {path}.{node_key} 缺 test")
            else:
                validate(body["test"], rid, f"{path}.{node_key}.test")
            if "where" in body:
                validate(body["where"], rid, f"{path}.{node_key}.where")
            return
        if "field" not in node:
            err(f"{rid}: {path} 缺 field")
            return
        if "op" not in node:
            err(f"{rid}: {path} 缺 op")
            return
        if node["op"] not in VALID_OPS:
            err(f"{rid}: {path}.op = {node['op']!r} 不在允许集合内")
        needs_value = node["op"] not in {"is_null", "is_not_null"}
        if needs_value and "value" not in node and "field_ref" not in node:
            err(f"{rid}: {path} 的 op={node['op']} 需要 value 或 field_ref")
        if "expr" in node:
            err(f"{rid}: {path} 使用了 expr 字符串，规则里不允许自由文本公式")

    for r in rules:
        if r.get("kind") != "predicate":
            continue
        check = r.get("check") or {}
        if "expr" not in check:
            err(f"{r['id']}: predicate 规则缺 check.expr")
            continue
        validate(check["expr"], r["id"])



# ---------------------------------------------------------------- 检查 2

def check_source_articles(rules: list[dict]) -> None:
    for r in rules:
        src = r.get("source", {})
        doc_no = src.get("doc_no")
        article = src.get("article")
        if not doc_no or not article:
            err(f"{r['id']}: source 缺 doc_no 或 article")
            continue
        articles_in(doc_no, article)

        sup = r.get("supplementary_source")
        if sup:
            if not sup.get("doc_no"):
                err(f"{r['id']}: supplementary_source 缺 doc_no")
            elif sup.get("doc_no") not in SOURCES:
                err(f"{r['id']}: supplementary_source 文号 {sup.get('doc_no')} 未登记")


# ---------------------------------------------------------------- 检查 3

def load_csv() -> list[dict]:
    with LODGING_CSV.open(encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def find_row(rule: dict, rows: list[dict]) -> dict | None:
    scope = rule.get("scope", {})
    province = scope.get("province")
    candidates = [x for x in rows if x["region"] == province]
    if not candidates:
        return None

    if scope.get("whole_region"):
        for x in candidates:
            if x["sub_area"] == "全市":
                return x
        return None
    if scope.get("cities"):
        first = scope["cities"][0]
        for x in candidates:
            if first in x["sub_area"]:
                return x
        return None
    if scope.get("fallback"):
        for x in candidates:
            if x["sub_area"] == "其他地区":
                return x
        return None
    return None


def check_lodging_thresholds(rules: list[dict]) -> None:
    rows = load_csv()
    checked = 0
    for r in rules:
        limit_list = (r.get("check") or {}).get("limits")
        if r.get("topic") != "住宿费" or not limit_list:
            continue
        row = find_row(r, rows)
        if row is None:
            err(f"{r['id']}: 在 lodging-subset.csv 中找不到对应行")
            continue

        limits = {lim["kind"]: lim for lim in limit_list}
        base = limits.get("base")
        if not base:
            err(f"{r['id']}: 缺 base 限额")
            continue

        pairs = [("cap_bu", "部级"), ("cap_si", "司局级"), ("cap_other", "其他人员")]
        for col, level in pairs:
            expected = int(row[col])
            actual = base["by_person_level"].get(level)
            if actual != expected:
                err(f"{r['id']}: {level} 限额 {actual} ≠ CSV 中的 {expected}（{row['region']}/{row['sub_area']}）")

        peak = limits.get("peak")
        if peak:
            for col, level in [("peak_bu", "部级"), ("peak_si", "司局级"), ("peak_other", "其他人员")]:
                if not row.get(col):
                    err(f"{r['id']}: 规则有 peak 限额，但 CSV 对应行没有旺季数据")
                    break
                expected = int(row[col])
                actual = peak["by_person_level"].get(level)
                if actual != expected:
                    err(f"{r['id']}: 旺季 {level} 限额 {actual} ≠ CSV 中的 {expected}")
        elif row.get("peak_bu"):
            err(f"{r['id']}: CSV 有旺季数据，但规则没有 peak 限额")
        checked += 1

    if checked == 0:
        err("没有检查到任何住宿费限额规则，检查逻辑可能失效")


# ---------------------------------------------------------------- 结构检查

REQUIRED_KEYS = ["id", "topic", "source", "version", "effective_from", "scope", "when", "notes", "decision"]


def check_structure(rules: list[dict]) -> None:
    seen = set()
    decisions = decision_ids()
    for r in rules:
        rid = r.get("id", "<无 id>")
        if rid in seen:
            err(f"{rid}: id 重复")
        seen.add(rid)

        for key in REQUIRED_KEYS:
            if key not in r:
                err(f"{rid}: 缺必填字段 `{key}`")

        if r.get("decision") not in decisions:
            err(f"{rid}: decision「{r.get('decision')}」在 rule-decisions.md 中不存在")

        src = r.get("source", {})
        if src.get("tier") not in {"A", "B", "C"}:
            err(f"{rid}: source.tier 必须是 A / B / C")

        # 结果字段至少要有 on_pass / on_missing
        if "on_pass" not in r:
            err(f"{rid}: 缺 on_pass")
        if "on_missing" not in r:
            err(f"{rid}: 缺 on_missing")

        # 冲突规则必须成对
        if "conflict_with" in r:
            other = r["conflict_with"]
            if not any(x.get("id") == other for x in rules):
                err(f"{rid}: conflict_with 指向不存在的规则 {other}")
            elif not any(x.get("id") == other and x.get("conflict_with") == rid for x in rules):
                err(f"{rid}: 与 {other} 的冲突关系不是双向的")


# ---------------------------------------------------------------- 主流程

def main() -> int:
    if not RULES_DIR.exists():
        print("找不到 rules 目录")
        return 2

    rules = load_rules()
    if not rules:
        print("没有读到任何规则")
        return 2

    check_structure(rules)
    check_require_fields(rules)
    check_kind_and_check_block(rules)
    check_predicate_ast(rules)
    check_source_articles(rules)
    check_lodging_thresholds(rules)

    print(f"规则总数: {len(rules)}")
    by_topic: dict[str, int] = {}
    for r in rules:
        by_topic[r.get("topic", "?")] = by_topic.get(r.get("topic", "?"), 0) + 1
    for topic, n in sorted(by_topic.items()):
        print(f"  {topic:8} {n}")

    if warnings:
        print("\n警告:")
        for w in warnings:
            print("  [!]", w)

    if errors:
        print(f"\n失败: {len(errors)} 项")
        for e in errors:
            print("  [x]", e)
        return 1

    print("\n全部检查通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
