"""评测：跑题、算指标、算置信区间、分类失败原因。

所有指标都是确定性的：给定同一套题与同一份配置，结果完全一致，不含随机性。
这也是为什么不用 LLM 判分——模型判分本身需要单独验证，否则评测会多一层不确定。
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import yaml

from .qa import QA, Answer

ROOT = Path(__file__).resolve().parent.parent.parent
QUESTIONS = ROOT / "data" / "eval" / "questions.yaml"
FROZEN = ROOT / "data" / "eval" / "FROZEN.md"

VALID_ROUTES = {"compute", "retrieve", "refuse"}
VALID_STYLES = {"formal", "colloquial"}


# ---------------------------------------------------------------- Wilson 区间

def wilson(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson 得分区间。

    不用正态近似（p̂ ± z√(p̂(1-p̂)/n)）：样本小、比例接近 0 或 1 时它会给出
    越界的区间（如负数上界）。Wilson 不会。
    """
    if n == 0:
        return (0.0, 1.0)
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - margin), min(1.0, centre + margin))


# ---------------------------------------------------------------- 题目

@dataclass
class Question:
    id: str
    question: str
    style: str
    as_of: str
    route: str
    citations_any: list[dict]
    facts: list[str]
    must_not_contain: list[str]
    refusal: str | None
    basis: str


def load_questions(path: Path | None = None) -> list[Question]:
    raw = yaml.safe_load((path or QUESTIONS).read_text(encoding="utf-8")) or []
    out = []
    for item in raw:
        exp = item.get("expected", {})
        out.append(Question(
            id=item["id"],
            question=item["question"],
            style=item.get("style", "formal"),
            as_of=str(item.get("as_of") or "2026-10-19"),
            route=exp.get("route", "retrieve"),
            citations_any=exp.get("citations_any") or [],
            facts=exp.get("facts") or [],
            must_not_contain=exp.get("must_not_contain") or [],
            refusal=exp.get("refusal"),
            basis=item.get("basis", ""),
        ))
    return out


def validate_questions(questions: list[Question]) -> list[str]:
    """题目自身的一致性。返回问题列表，空表示通过。"""
    problems: list[str] = []
    seen = set()
    for q in questions:
        if q.id in seen:
            problems.append(f"{q.id}: id 重复")
        seen.add(q.id)
        if q.route not in VALID_ROUTES:
            problems.append(f"{q.id}: route 必须是 {sorted(VALID_ROUTES)}")
        if q.style not in VALID_STYLES:
            problems.append(f"{q.id}: style 必须是 {sorted(VALID_STYLES)}")
        if not q.basis.strip():
            problems.append(f"{q.id}: 缺 basis（标准答案必须能说出自哪一条）")
        if q.route == "refuse" and not q.refusal:
            problems.append(f"{q.id}: 拒答题必须给 expected.refusal")
        if q.route != "refuse" and not q.facts:
            problems.append(f"{q.id}: 非拒答题必须给 expected.facts")
    if questions:
        ratio = sum(1 for q in questions if q.style == "colloquial") / len(questions)
        if not 0.4 <= ratio <= 0.6:
            problems.append(f"口语题占比 {ratio:.0%}，应在 40%–60% 之间")
    return problems


def file_hash(path: Path | None = None) -> str:
    return hashlib.sha256((path or QUESTIONS).read_bytes()).hexdigest()[:16]


# ---------------------------------------------------------------- 单题评测

@dataclass
class Record:
    q: Question
    answer: Answer
    checks: dict[str, bool] = field(default_factory=dict)
    failure: str = ""
    detail: str = ""


def _cited_labels(answer: Answer) -> list[str]:
    return [f"{c.doc_no} {c.article}" for c in answer.citations]


def _matches_expected(answer: Answer, expected: list[dict]) -> bool:
    """答案的引用里是否命中至少一条预期来源。"""
    for want in expected:
        for cite in answer.citations:
            if cite.doc_no != want.get("doc_no"):
                continue
            if "article" in want and cite.article == want["article"]:
                return True
            if "article_contains" in want and want["article_contains"] in cite.article:
                return True
    return False


def _facts_ok(answer: Answer, q: Question) -> tuple[bool, str]:
    text = answer.text
    missing = [f for f in q.facts if f not in text]
    forbidden = [f for f in q.must_not_contain if f in text]
    if missing:
        return False, "缺少：" + "、".join(missing)
    if forbidden:
        return False, "出现了不该有的：" + "、".join(forbidden)
    return True, ""


def _version_ok(answer: Answer, q: Question) -> bool:
    """版本陷阱题：引用的文号必须是该基准日生效的那一版。"""
    if not q.as_of:
        return True
    old = q.as_of < "2014-01-01"
    cited_old = any("2006" in c.doc_no for c in answer.citations)
    cited_new = any("2013〕531" in c.doc_no for c in answer.citations)
    return cited_old if old else not (cited_old and not cited_new)


def evaluate_one(qa: QA, q: Question, context_mode: str = "retrieve",
                 top_k: int = 3) -> Record:
    answer = qa.ask(q.question, context_mode=context_mode, as_of=q.as_of,
                    top_k=top_k)
    rec = Record(q=q, answer=answer)

    actual_route = "refuse" if answer.refused else answer.route
    rec.checks["route"] = actual_route == q.route

    if q.route == "refuse":
        rec.checks["refusal"] = answer.refused
        rec.checks["answer"] = answer.refused        # 拒答即正确
        rec.checks["citation_recall"] = True         # 拒答不应给引用
        rec.checks["version"] = True
    else:
        ok, why = _facts_ok(answer, q)
        rec.checks["answer"] = ok
        if not ok:
            rec.detail = why
        rec.checks["refusal"] = not answer.refused
        rec.checks["citation_recall"] = _matches_expected(answer, q.citations_any)
        rec.checks["version"] = _version_ok(answer, q)

    v = answer.verification
    rec.checks["verify"] = bool(v.passed) if v is not None else True

    rec.failure = _classify_failure(rec, q)
    return rec


def _classify_failure(rec: Record, q: Question) -> str:
    a = rec.answer
    if not rec.checks["route"]:
        if q.route == "refuse" and not a.refused:
            return "UNDER_REFUSE"
        if q.route != "refuse" and a.refused:
            return "OVER_REFUSE"
        return "ROUTE"
    if not rec.checks["answer"]:
        if not rec.checks["version"]:
            return "VERSION"
        if a.route == "retrieve" and not rec.checks["citation_recall"]:
            return "RETRIEVAL"
        if a.route == "compute":
            return "WRONG_VALUE"
        return "EXTRACTION"
    if not rec.checks["verify"]:
        return "VERIFY_FALSE_POSITIVE"
    return ""


# ---------------------------------------------------------------- 汇总

METRICS = ["route", "answer", "citation_recall", "refusal", "verify", "version"]


def summarize(records: list[Record]) -> dict:
    n = len(records)
    out = {"n": n}
    for m in METRICS:
        k = sum(1 for r in records if r.checks.get(m))
        lo, hi = wilson(k, n)
        out[m] = {"k": k, "n": n, "rate": k / n if n else 0.0,
                  "ci": (round(lo, 3), round(hi, 3))}
    return out


def group_by(records: list[Record], key) -> dict[str, list[Record]]:
    groups: dict[str, list[Record]] = {}
    for r in records:
        groups.setdefault(key(r), []).append(r)
    return groups


def failure_counts(records: list[Record]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for r in records:
        if r.failure:
            counts[r.failure] = counts.get(r.failure, 0) + 1
    return counts


def run_all(qa: QA, questions: list[Question],
            context_mode: str = "retrieve") -> list[Record]:
    return [evaluate_one(qa, q, context_mode=context_mode) for q in questions]


# ---------------------------------------------------------------- 自检

def self_check(records: list[Record], expected_n: int) -> list[str]:
    """评测器自检。

    第 2 周有过教训：某个项目的评测结果文件里关键指标全是 null，
    那次评测实际跑挂了，但文件看起来像跑过了。所以这里断言：
    跑满题数、没有空白答案、没有指标全空。
    """
    problems: list[str] = []
    if len(records) != expected_n:
        problems.append(f"跑完 {len(records)} 题，应为 {expected_n} 题")
    empty = [r.q.id for r in records if not r.answer.text.strip()]
    if empty:
        problems.append(f"以下题目没有产出答案文本：{empty}")
    for m in METRICS:
        if all(not r.checks.get(m) for r in records):
            problems.append(f"指标 {m} 全为 False，评测可能没真正执行")
    return problems
