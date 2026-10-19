"""阶梯消融：逐级加部件，每级跑同一套题。

为什么要分级而不是只报最终成绩：一个总分说不清「哪个部件在起作用」。
如果去掉某级指标没变，那一级就是白加的——这种结论只有分级才能得到。

从 L0 到 L4 每一级只加一个部件，加的顺序按依赖关系：
同义词 → 版本过滤 → 问题分类 → 引用校验。
"""

from __future__ import annotations

from dataclasses import dataclass

from .evaluate import Question, Record, evaluate_one, summarize
from .qa import QA


@dataclass
class Level:
    key: str
    title: str
    hypothesis: str
    kwargs: dict


LEVELS = [
    Level(
        key="L0",
        title="基线：全部走检索，无同义词，不过滤版本",
        hypothesis="对照组。所有改进都相对它来衡量。",
        kwargs=dict(route_mode="always_retrieve", version_filter=False,
                    use_synonyms=False),
    ),
    Level(
        key="L1",
        title="+ 同义词映射",
        hypothesis="口语题（「住酒店」「打车的钱」）的召回应当提升。",
        kwargs=dict(route_mode="always_retrieve", version_filter=False,
                    use_synonyms=True),
    ),
    Level(
        key="L2",
        title="+ 版本过滤",
        hypothesis="版本陷阱题的引用应当切到正确的版本，VERSION 类失败归零。",
        kwargs=dict(route_mode="always_retrieve", version_filter=True,
                    use_synonyms=True),
    ),
    Level(
        key="L3",
        title="+ 三类问题分类（可计算走规则引擎）",
        hypothesis="核心主张：数值题的答案准确率显著上升。",
        kwargs=dict(route_mode="auto", version_filter=True, use_synonyms=True),
    ),
]


def run_level(questions: list[Question], level: Level,
              top_k: int = 3) -> list[Record]:
    """每一级都用一个全新的 QA，避免状态串味。"""
    qa = QA(**level.kwargs)
    return [evaluate_one(qa, q, context_mode="retrieve", top_k=top_k)
            for q in questions]


def verification_effect(records: list[Record]) -> dict:
    """L4 不提高正确率，提高的是**可知性**：系统敢不敢说这个答案可信。

    两个数：
      capture      —— 答错的题里，有多大比例被校验标出来了
      false_alarm  —— 答对的题里，有多大比例被校验误标
    capture 高、false_alarm 低，校验才有用。
    """
    wrong = [r for r in records if not r.q.route == "refuse"
             and (not r.checks.get("answer") or not r.checks.get("route"))]
    right = [r for r in records if r.q.route != "refuse"
             and r.checks.get("answer") and r.checks.get("route")]

    flagged = [r for r in wrong if r.answer.verification is not None
               and not r.answer.verification.passed]
    false_alarms = [r for r in right if r.answer.verification is not None
                    and not r.answer.verification.passed]

    return {
        "wrong": len(wrong),
        "captured": len(flagged),
        "capture_rate": len(flagged) / len(wrong) if wrong else 0.0,
        "right": len(right),
        "false_alarms": len(false_alarms),
        "false_alarm_rate": len(false_alarms) / len(right) if right else 0.0,
    }


def run_ablation(questions: list[Question], top_k: int = 3) -> list[dict]:
    rows = []
    for level in LEVELS:
        records = run_level(questions, level, top_k=top_k)
        stats = summarize(records)
        rows.append({
            "level": level.key,
            "title": level.title,
            "hypothesis": level.hypothesis,
            "stats": stats,
            "records": records,
            "verification": verification_effect(records),
        })
    return rows


def compare_full_vs_retrieve(questions: list[Question]) -> dict:
    """两种上下文方式的对比。

    这个规模（不到一万字）本来可以整段塞进上下文，所以检索是否值得必须用数据回答。
    但要注意：**抽取式生成器只能用一个片段**，给它全部条文它也只会挑其中一段。
    所以这个对比在当期配置下测不出「长上下文有没有用」——它测的是同一件事的两种取法。
    要真正比较，需要生成式模型。这一点写在报告里，不能把「一样」说成「结论」。
    """
    qa = QA()
    retrieve = [evaluate_one(qa, q, context_mode="retrieve") for q in questions]
    full = [evaluate_one(qa, q, context_mode="full") for q in questions]

    identical = sum(
        1 for a, b in zip(retrieve, full)
        if a.answer.text == b.answer.text and a.answer.refusal == b.answer.refusal
    )
    return {
        "retrieve": summarize(retrieve),
        "full": summarize(full),
        "identical": identical,
        "n": len(questions),
    }
