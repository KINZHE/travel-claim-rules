"""问答编排：分类 → 取材料 → 生成 → 校验。

编排层的责任是**把决策路径记下来**。同一个问题走了引擎还是检索、用的是哪一版制度、
校验过没过，都要能在输出里看到——否则用户没法判断这个答案该信几分。
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path

from .classify import Classification, classify
from .corpus import Chunk, load_corpus
from .generate import Draft, ExtractiveGenerator, Generator
from .model import Rule, load_rules
from .retrieval import Retriever, build_retriever
from .universe import Universe, load_universe
from .verify import Citation

ROOT = Path(__file__).resolve().parent.parent.parent
LODGING_CSV = ROOT / "data" / "lodging-subset.csv"

REFUSAL_NO_RETRIEVAL = "NO_RETRIEVAL"
REFUSAL_INSUFFICIENT = "INSUFFICIENT"
REFUSAL_NOT_COVERED = "NOT_COVERED"

REFUSAL_TEXT = {
    REFUSAL_NO_RETRIEVAL: "没有检索到与问题相关的制度条文，无法回答。",
    REFUSAL_INSUFFICIENT: "检索到了相关条文，但现有条文不足以回答这个问题，需要人工确认。",
    REFUSAL_NOT_COVERED: "现行制度没有就该问题作出规定。",
}


@dataclass
class Answer:
    question: str
    route: str                      # compute / retrieve / refuse
    text: str = ""
    citations: list = field(default_factory=list)
    refusal: str | None = None
    verification: object | None = None
    trace: dict = field(default_factory=dict)

    @property
    def refused(self) -> bool:
        return self.refusal is not None


class QA:
    def __init__(self, rules: list[Rule] | None = None,
                 corpus: list[Chunk] | None = None,
                 universe: Universe | None = None,
                 generator: Generator | None = None,
                 lodging_rows: list[dict] | None = None,
                 route_mode: str = "auto",
                 version_filter: bool = True,
                 use_synonyms: bool = True,
                 verify_answers: bool = True):
        """`route_mode` / `version_filter` / `use_synonyms` 是消融实验的开关，
        默认值等于当前行为——**加这些参数不能改变任何现有测试的结果**。

        route_mode: "auto" 走三类分类；"always_retrieve" 全部走检索（消融基线）
        version_filter: False 时检索不过滤版本，会同时召回新旧条文
        use_synonyms: False 时关闭同义词映射
        verify_answers: False 时跳过引用校验（消融里用来单独观察校验的作用）
        """
        self.rules = rules if rules is not None else load_rules(ROOT / "rules")
        self.corpus = corpus if corpus is not None else load_corpus()
        self.universe = universe if universe is not None else load_universe()
        self.retriever: Retriever = build_retriever(self.corpus, use_synonyms=use_synonyms)
        self.generator = generator or ExtractiveGenerator()
        self.lodging_rows = lodging_rows if lodging_rows is not None else _load_lodging_rows()
        self.route_mode = route_mode
        self.version_filter = version_filter
        self.verify_answers = verify_answers

    # ------------------------------------------------------------ 入口

    def ask(self, question: str, context_mode: str = "retrieve",
            as_of: str | None = None, top_k: int = 3) -> Answer:
        cls = classify(question, self.universe)
        if as_of:
            cls.as_of = as_of
        cls.as_of = cls.as_of or "2026-10-19"

        if self.route_mode == "always_retrieve":
            # 消融基线：不看问题类型，一律检索。分类带来的增益靠它来量。
            cls.notes.append("route_mode=always_retrieve，强制走检索")
            cls.route = "retrieve"
            return self._retrieve(question, cls, context_mode, top_k)

        if cls.route == "compute":
            answer = self._compute(question, cls)
            if answer is not None:
                return answer
            # 计算失败（比如该类别没有数值标准），退回检索，不算错误
            cls.notes.append("可计算路径未取到数值，退回条文检索")
            cls.route = "retrieve"

        return self._retrieve(question, cls, context_mode, top_k)

    # ------------------------------------------------------------ 可计算路径

    def _compute(self, question: str, cls: Classification) -> Answer | None:
        category = cls.category
        if category == "住宿费":
            return self._compute_lodging(question, cls)
        if category in ("伙食补助费", "市内交通费"):
            return self._compute_daily(question, cls, category)
        # 城市间交通费是等级限制，不是金额上限——交给检索
        return None

    def _compute_lodging(self, question: str, cls: Classification) -> Answer | None:
        level = cls.person_level
        province = cls.province
        city = cls.city

        rule = _match_lodging_rule(self.rules, province, city)
        if rule is None:
            return None

        base = None
        peak = None
        for lim in rule.check.get("limits", []):
            if lim["kind"] == "base":
                base = lim["by_person_level"]
            elif lim["kind"] == "peak":
                peak = lim

        if not base:
            return None

        # 问句点了旺季月份时，用旺季限额作主答案
        in_peak = False
        if peak and cls.month:
            months = peak.get("months") or []
            cities = peak.get("applies_to_cities") or []
            if cls.month in months and (not cities or cls.city in cities):
                in_peak = True

        limits = peak["by_person_level"] if in_peak else base
        place = "、".join(filter(None, [province, city]))

        if level:
            if level not in limits:
                return None
            season = "旺季" if in_peak else ""
            body = f"在{place}出差，{level}人员的{season}住宿费限额为 {limits[level]} 元/人·天。"
        else:
            parts = "，".join(f"{lv} {val} 元" for lv, val in limits.items())
            season = "（旺季期间）" if in_peak else ""
            body = f"在{place}出差，住宿费限额{season}为：{parts}，均按人·天计。"

        row = _find_lodging_row(self.lodging_rows, province, city)
        table_cite, _ = _table_citation(rule, row)
        body += "\n\n限额的单位是「元/人·天」，与住宿总额、天数要区分开。"

        peak_line = _peak_note(rule, cls)
        if peak_line:
            body += "\n\n" + peak_line

        draft = self.generator.answer(
            question,
            _chunks_for_rule(rule, self.corpus),
            extra_lines=[body],
            extra_citations=[table_cite] if table_cite else [],
        )

        return self._finalize(question, draft, {
            "route": "compute",
            "category": category_label(cls.category),
            "rule_id": rule.id,
            "rule_source": f"{rule.source_of('doc_no')} {rule.source_of('article')}",
            "table_ref": rule.source_of("table_ref"),
            "season": "peak" if in_peak else "base",
            "classification": cls.reason,
        })

    def _compute_daily(self, question: str, cls: Classification,
                       category: str) -> Answer | None:
        rule = _match_aggregate_rule(self.rules, category, as_of=cls.as_of)
        if rule is None:
            return None
        rate_conf = rule.check.get("rate", {})
        base = rate_conf.get("default")
        if base is None:
            return None

        # 规则可以声明 display_name：2006 版叫「公杂费」，2013 版叫「市内交通费」。
        # 名字变了但规则 topic 相同（为了能直接对比），显示时用回当时的叫法。
        display = rule.raw.get("display_name") or category
        lines = [f"{display}按每人每天 {base} 元包干，按出差自然（日历）天数计算。"]
        if display != category:
            lines[0] += f"（该制度版本中称「{display}」，现行名称为「{category}」）"
        mapping = rate_conf.get("map") or {}
        if mapping:
            pairs = "，".join(f"{k} {v} 元" for k, v in mapping.items())
            lines.append(f"其中 {pairs}。")

        chunks = _chunks_for_rule(rule, self.corpus)

        extra_citations = []
        # 531 号的附表在 gov.cn 文本里标注为「略」，100 / 120 这两个数值
        # 来自地方政府与高校转载（B/C 层级），不是财政部原件。
        # 这一点必须写在答案里，也必须在引用里标出来——
        # 不然校验会把数字判成「无法追溯」，而这次判定是对的：它确实不在被引条文里。
        source = rule.raw.get("source", {})
        if source.get("tier") in {"B", "C"}:
            numbers = [str(base)] + [str(v) for v in mapping.values()]
            tier_note = f"标准数值（{'、'.join(sorted(set(numbers)))} 元）来源层级 {source['tier']}：{source.get('table_ref', '')}"
            lines.append(
                f"该标准来自地方政府与高校转载，未取得财政部原始附表，来源层级 {source['tier']}。"
            )
            extra_citations.append(Citation(
                doc_no=source.get("doc_no", ""),
                article="附表（未取得原件）",
                text=tier_note,
                kind="secondary",
            ))

        draft = self.generator.answer(question, chunks,
                                      extra_lines=lines,
                                      extra_citations=extra_citations)

        return self._finalize(question, draft, {
            "route": "compute",
            "category": category,
            "rule_id": rule.id,
            "rule_source": f"{rule.source_of('doc_no')} {rule.source_of('article')}",
            "classification": cls.reason,
        })

    # ------------------------------------------------------------ 检索路径

    def _retrieve(self, question: str, cls: Classification,
                  context_mode: str, top_k: int) -> Answer:
        # 消融开关：关掉版本过滤时，检索会同时召回新旧条文——
        # 这正是要量化的故障模式，不是「实现得糙」。
        as_of = cls.as_of if self.version_filter else None

        if context_mode == "full":
            chunks = self.retriever.all_for_version(as_of)
            hits = []
        else:
            hits = self.retriever.search(question, k=top_k, as_of=as_of)
            chunks = [h.chunk for h in hits]

        if not hits and context_mode == "retrieve":
            return self._refuse(question, REFUSAL_NO_RETRIEVAL, {
                "route": "refuse",
                "classification": cls.reason,
                "as_of": as_of,
                "context_mode": context_mode,
            })

        if cls.out_of_scope:
            # 超范围话题：即便检索到了什么，也不该硬答
            return self._refuse(question, REFUSAL_NOT_COVERED, {
                "route": "refuse",
                "classification": cls.reason,
                "as_of": as_of,
                "context_mode": context_mode,
                "note": "命中超范围话题提示词，且制度未就该事项作出规定",
            })

        # full 模式下没有排序，用检索分数挑出最相关的一条作为主引用
        ranked = self.retriever.search(question, k=max(top_k, 3), as_of=as_of)
        lead = [h.chunk for h in ranked] or chunks

        draft = self.generator.answer(question, lead)

        if draft.insufficient:
            return self._refuse(question, REFUSAL_INSUFFICIENT, {
                "route": "refuse",
                "classification": cls.reason,
                "as_of": as_of,
                "context_mode": context_mode,
                "detail": draft.reason,
            })

        return self._finalize(question, draft, {
            "route": "retrieve",
            "classification": cls.reason,
            "as_of": as_of,
            "context_mode": context_mode,
            "retrieved": [f"{h.chunk.label}（{h.score:.1f}）" for h in hits] if hits else ["全量条文"],
            "top_matched_terms": hits[0].matched[:6] if hits else [],
        })

    # ------------------------------------------------------------ 收尾

    def _finalize(self, question: str, draft: Draft, trace: dict) -> Answer:
        from .verify import verify

        verification = None
        if self.verify_answers:
            verification = verify(draft.text, draft.citations, self.corpus)
        return Answer(
            question=question,
            route=trace["route"],
            text=draft.text,
            citations=draft.citations,
            verification=verification,
            trace=trace,
        )

    def _refuse(self, question: str, code: str, trace: dict) -> Answer:
        return Answer(
            question=question,
            route="refuse",
            text=REFUSAL_TEXT[code],
            refusal=code,
            trace=trace,
        )


# ---------------------------------------------------------------- 辅助

def category_label(category: str | None) -> str:
    return category or "未知"


def _load_lodging_rows() -> list[dict]:
    if not LODGING_CSV.exists():
        return []
    with LODGING_CSV.open(encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _match_lodging_rule(rules: list[Rule], province: str | None,
                        city: str | None) -> Rule | None:
    """按「具体城市 > 全省 > 兜底」找住宿费限额规则。"""
    if not province:
        return None
    candidates = [r for r in rules
                  if r.kind == "threshold" and r.topic == "住宿费"
                  and r.scope.get("province") == province]
    if not candidates:
        return None

    if city:
        for rule in candidates:
            if city in (rule.scope.get("cities") or []):
                return rule
    for rule in candidates:
        if rule.scope.get("whole_region"):
            return rule
    for rule in candidates:
        if rule.scope.get("fallback"):
            return rule
    return candidates[0]


def _match_aggregate_rule(rules: list[Rule], category: str,
                          as_of: str | None = None) -> Rule | None:
    """找该费用类别下、在 as_of 当天生效的合计规则。

    必须按日期挑，不能默认取「没有失效日期」的那条：
    问 2013 年的市内交通费，答案应当来自 2006 版第十五条（公杂费 30 元），
    而不是现行第二十条（80 元）。这正是版本混用最典型的表现。
    """
    matched: list[Rule] = []
    for rule in rules:
        if rule.kind != "aggregate" or rule.topic != category:
            continue
        if as_of:
            start = rule.raw.get("effective_from")
            end = rule.raw.get("effective_to")
            if start and as_of < start:
                continue
            if end and as_of > end:
                continue
        matched.append(rule)

    if not matched:
        return None
    # 多个同时生效时优先取生效日期更晚的（后发文件覆盖）
    matched.sort(key=lambda r: r.raw.get("effective_from", ""), reverse=True)
    return matched[0]


def _find_lodging_row(rows: list[dict], province: str | None,
                      city: str | None) -> dict | None:
    if not province:
        return None
    match = [r for r in rows if r["region"] == province]
    if not match:
        return None
    if city:
        for row in match:
            if city in row["sub_area"]:
                return row
    for row in match:
        if row["sub_area"] in ("全市", "全省"):
            return row
    for row in match:
        if row["sub_area"] == "其他地区":
            return row
    return match[0]


def _chunks_for_rule(rule: Rule, corpus: list[Chunk]) -> list[Chunk]:
    """取出规则依据的条文片段。

    规则的 `source.article` 可能是复合引用（「第十六条、第十七条」），
    也可能指向表（「附表」）。这里按「、」拆开逐个找，找不到就跳过——
    全部找不到时返回空列表，由生成器判为「材料不足」，而不是抛异常。
    """
    import re

    refs = [a.strip() for a in re.split(r"[、,，]", rule.source_of("article")) if a.strip()]
    out: list[Chunk] = []
    for ref in refs:
        for chunk in corpus:
            if chunk.doc_no == rule.source_of("doc_no") and chunk.article == ref:
                out.append(chunk)
                break
    return out


def _table_citation(rule: Rule, row: dict | None):
    """明细表行的引用。表格不在语料里，单独构造一个引用对象。

    引用的 doc_no 要指向**真正的来源文件**（71 号），不是规则归属的条款文件（531 号）。
    两者混用会让人以为数字出自 531 号正文，而正文里只有「见附表」。
    """
    import re

    from .verify import Citation

    ref = rule.source_of("table_ref")
    if not ref:
        return None, ""
    m = re.match(r"(财行〔\d+〕\d+号|财办行〔\d+〕\d+号)", ref)
    doc_no = m.group(1) if m else rule.source_of("doc_no")

    if row:
        text = (
            f"{row['region']}·{row['sub_area']}：部级 {row['cap_bu']}，"
            f"司局级 {row['cap_si']}，其他人员 {row['cap_other']}（元/人·天）"
        )
        # 有旺季标准时必须一并带上：答案里会提到旺季数值，
        # 引用里没有的话，数字校验会（正确地）判为无法追溯。
        if row.get("peak_bu"):
            text += (
                f"；旺季（{row.get('peak_period', '')}）上浮为："
                f"部级 {row['peak_bu']}，司局级 {row['peak_si']}，其他人员 {row['peak_other']}"
            )
    else:
        text = ref
    return Citation(doc_no=doc_no, article=ref, text=text, kind="table"), text


def _peak_note(rule: Rule, cls) -> str:
    """问句提到月份时，说明该月是否适用旺季标准。

    旺季是住宿费里最容易漏的一档：标准不是全年通用的，
    并且上浮只对特定城市生效。答的时候要说清楚，不然用户会按淡季限额去安排。
    """
    month = getattr(cls, "month", None)
    peak = next((lim for lim in rule.check.get("limits", []) if lim["kind"] == "peak"), None)
    if peak is None:
        return ""
    months = peak.get("months") or []
    cities = peak.get("applies_to_cities") or []

    if month:
        # 主答案已经按旺季给过了，这里不再重复
        if month in months and (not cities or cls.city in cities):
            return f"旺季期间为 {'、'.join(map(str, months))} 月，仅对 {'、'.join(cities)} 生效；其他月份按常规限额。"
        reason = "该月不在旺季期间" if month not in months else "该城市不在旺季上浮范围内"
        return f"注意：{reason}（旺季为 {'、'.join(map(str, months))} 月，适用 {'、'.join(cities)}），因此按常规限额。"

    vals = "，".join(f"{lv} {v} 元" for lv, v in peak["by_person_level"].items())
    scope = "、".join(cities) if cities else "该地区"
    return (
        f"另需注意：{scope}在 {'、'.join(map(str, months))} 月适用旺季上浮标准，"
        f"届时限额为 {vals}。"
    )
