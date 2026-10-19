"""引用校验：全部是确定性检查，不需要模型。

三层：

    1. 引用存在   每个引用都能在语料/明细表里找到
    2. 数字可溯   答案里出现的每个数字，必须出现在被引用的片段里
    3. 断言有据   答案里的否定性断言（不得/不予/禁止），被引片段里必须有对应表述

为什么只查否定性断言而不查所有判定词：像「不超过」「按…计算」这类说法，
条款原文往往用别的措辞表达（「在限额内」「按规定标准」），查了全是误报。
**误报多了，校验就会被忽略**，比不校验更糟。所以只查最要命的那一类：
宣称制度禁止某事，而制度其实没这么说。

校验失败不隐藏——直接展示失败原因。这是「可核查」的落地形式。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .corpus import Chunk

# 否定性断言。答案里出现这些词，被引片段里必须能找到对应表述。
NEGATIVE_CLAIMS = ["不得", "不予", "禁止", "严禁", "不予报销", "一律不"]

# 弱判定词：只做提示，不算失败。它们的措辞与原文差异大，硬查会误报。
SOFT_CLAIMS = ["不超过", "应当", "必须", "可以"]


@dataclass
class Citation:
    doc_no: str
    article: str
    text: str
    kind: str = "clause"        # clause（条文）| table（明细表行）

    @property
    def label(self) -> str:
        return f"{self.doc_no} {self.article}"


@dataclass
class Verification:
    passed: bool
    citation_exists: bool
    numbers_traceable: bool
    claims_supported: bool
    problems: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    untraceable_numbers: list[str] = field(default_factory=list)
    unsupported_claims: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "citation_exists": self.citation_exists,
            "numbers_traceable": self.numbers_traceable,
            "claims_supported": self.claims_supported,
            "problems": self.problems,
            "warnings": self.warnings,
            "untraceable_numbers": self.untraceable_numbers,
            "unsupported_claims": self.unsupported_claims,
        }


def extract_numbers(text: str) -> list[str]:
    """抽出文本里的数字（含百分数）。

    排除纯序数性的「第X条」里的数字——那是条款编号，不是事实数值。
    """
    cleaned = re.sub(r"第[一二三四五六七八九十百\d]+条", "", text)
    cleaned = re.sub(r"财行〔\d+〕\d+号|财办行〔\d+〕\d+号", "", cleaned)
    return re.findall(r"\d+(?:\.\d+)?", cleaned)


def _normalize_for_match(text: str) -> str:
    return re.sub(r"[\s，。、；：（）()《》“”\"'.,;:]+", "", text)


def _find_in_corpus(doc_no: str, article: str, corpus: list[Chunk]) -> Chunk | None:
    for chunk in corpus:
        if chunk.doc_no == doc_no and chunk.article == article:
            return chunk
    return None


def verify(answer_text: str, citations: list[Citation],
           corpus: list[Chunk]) -> Verification:
    problems: list[str] = []
    warnings: list[str] = []

    # ---- 1. 引用存在 ----
    citation_ok = True
    if not citations:
        citation_ok = False
        problems.append("答案没有任何引用")

    for cite in citations:
        if cite.kind == "table":
            # 明细表行不在语料里，只能检查它是否自带内容
            if not cite.text.strip():
                citation_ok = False
                problems.append(f"表格引用缺少内容：{cite.label}")
            continue
        if cite.kind == "secondary":
            # 二手来源：不在本项目的语料里，但必须标明层级，且不能冒充为条文
            if not cite.text.strip():
                citation_ok = False
                problems.append(f"二手来源引用缺少说明：{cite.label}")
            else:
                warnings.append(
                    f"{cite.label} 是二手来源（{cite.text[:40]}…），不是条文原件，可信度低于 A 级来源"
                )
            continue
        found = _find_in_corpus(cite.doc_no, cite.article, corpus)
        if found is None:
            citation_ok = False
            problems.append(f"引用了不存在的条款：{cite.label}")
        elif _normalize_for_match(found.text[:40]) != _normalize_for_match(cite.text[:40]):
            # 引文与原文开头对不上，可能是转述冒充原文
            citation_ok = False
            problems.append(f"引文与原文不一致：{cite.label}")

    # ---- 2. 数字可溯 ----
    cite_blob = _normalize_for_match("".join(c.text for c in citations))
    answer_numbers = extract_numbers(answer_text)
    untraceable = [n for n in answer_numbers if n not in cite_blob]
    numbers_ok = not untraceable
    if untraceable:
        problems.append(
            "答案中的数字未能追溯到被引片段：" + "、".join(sorted(set(untraceable)))
        )

    # ---- 3. 断言有据 ----
    answer_norm = _normalize_for_match(answer_text)
    unsupported = []
    for claim in NEGATIVE_CLAIMS:
        if claim in answer_norm and claim not in cite_blob:
            unsupported.append(claim)
    claims_ok = not unsupported
    if unsupported:
        problems.append(
            "答案包含被引片段没有的否定性断言：" + "、".join(unsupported)
        )

    # ---- 弱提示（不影响通过与否）----
    for claim in SOFT_CLAIMS:
        if claim in answer_norm and claim not in cite_blob:
            warnings.append(f"「{claim}」未在被引片段中逐字出现（可能是转述，需人工看）")

    return Verification(
        passed=citation_ok and numbers_ok and claims_ok,
        citation_exists=citation_ok,
        numbers_traceable=numbers_ok,
        claims_supported=claims_ok,
        problems=problems,
        warnings=warnings,
        untraceable_numbers=sorted(set(untraceable)),
        unsupported_claims=unsupported,
    )
