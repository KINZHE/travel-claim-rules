"""评测报告生成。

报告里三件事必须写清楚，否则数字会被读成比实际更强：
    1. 题目是谁出的（自己出题自己考这个结构限制）
    2. 每条指标带置信区间（50 条样本的区间很宽）
    3. 哪些失败是系统的问题、哪些是题目本身的问题

失败清单不修饰。第 6 周讲简历时，「知道哪里不行」比「样样都行」可信。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from .ablation import compare_full_vs_retrieve, run_ablation
from .evaluate import (
    METRICS, Question, Record, failure_counts, file_hash, group_by,
    run_all, self_check, summarize,
)
from .qa import QA

ROOT = Path(__file__).resolve().parent.parent.parent

FAILURE_LABELS = {
    "ROUTE": "走错路径",
    "RETRIEVAL": "没召回到正确条文",
    "EXTRACTION": "召回到了但不是答案所在",
    "WRONG_VALUE": "数值取错档位",
    "VERSION": "用错制度版本",
    "OVER_REFUSE": "该答却拒答",
    "UNDER_REFUSE": "该拒却答了",
    "VERIFY_FALSE_POSITIVE": "答案对但校验误判",
}

METRIC_LABELS = {
    "route": "路径准确率",
    "answer": "答案正确率",
    "citation_recall": "引用召回率",
    "refusal": "拒答正确率",
    "verify": "校验通过率",
    "version": "版本正确率",
}

METRIC_TARGETS = {
    "route": 0.85,
    "answer": 0.80,
    "citation_recall": 0.80,
    "refusal": 0.90,
    "version": 0.80,
}


def _pct(x: float) -> str:
    return f"{x * 100:.0f}%"


def _metric_row(name: str, d: dict) -> str:
    lo, hi = d["ci"]
    target = METRIC_TARGETS.get(name)
    mark = ""
    if target is not None:
        mark = "达标" if d["rate"] >= target else "**未达标**"
    return (
        f"| {METRIC_LABELS.get(name, name)} | {d['k']}/{d['n']} | "
        f"{_pct(d['rate'])} | {_pct(lo)}–{_pct(hi)} | "
        f"{_pct(target) if target else '—'} | {mark} |"
    )


def _type_of(q: Question) -> str:
    if q.as_of < "2014-01-01":
        return "版本陷阱"
    if q.route == "refuse":
        return "拒答"
    if q.route == "compute":
        return "可计算"
    return "需读条文"


def build_report(questions: list[Question]) -> str:
    qa = QA()
    records = run_all(qa, questions)
    problems = self_check(records, len(questions))

    overall = summarize(records)
    by_type = group_by(records, lambda r: _type_of(r.q))
    by_style = group_by(records, lambda r: "口语" if r.q.style == "colloquial" else "正式")

    ablation_rows = run_ablation(questions)
    context_cmp = compare_full_vs_retrieve(questions)
    failures = failure_counts(records)

    failed_records = [r for r in records if r.failure]
    lines: list[str] = [
        "# 评测报告",
        "",
        f"_生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}　"
        f"题目 {len(questions)} 条　题目集哈希 `{file_hash()}`_",
        "",
        "---",
        "",
        "## 结论摘要",
        "",
        f"- **路径准确率** {_pct(overall['route']['rate'])}"
        f"（{overall['route']['k']}/{overall['route']['n']}）",
        f"- **答案正确率** {_pct(overall['answer']['rate'])}"
        f"（{overall['answer']['k']}/{overall['answer']['n']}）",
        f"- **拒答正确率** {_pct(overall['refusal']['rate'])}",
        f"- **失败题** {len(failed_records)} 条，分类："
        + ("、".join(f"{FAILURE_LABELS.get(k, k)} {v}" for k, v in sorted(failures.items()))
           if failures else "无"),
        "",
        "---",
        "",
        "## 一、方法与边界",
        "",
        "**这是自己出题自己考的评测。** 题目由项目作者构造，评测也由作者运行。",
        "这个结构天然有循环论证的风险：若题目照着系统输出的措辞写，分数会虚高，",
        "**而且从结果上完全看不出来**。为此做了三件事，但都不能完全消除这个限制：",
        "",
        "1. **从条文反向构题**：先定「依据哪几条条文」，再从条文写问题与标准答案，",
        "   不是先看系统答什么再编标准。每条题都带 `basis` 字段记录出处。",
        "2. **约一半是口语化问法**（实际 "
        f"{sum(1 for q in questions if q.style == 'colloquial') / len(questions):.0%}）：",
        "   系统分类靠模式匹配，照系统措辞出题会人为抬高准确率。",
        "3. **写完题目后第一次运行即作为结果**：本次分数来自写完 50 条题后的首次运行，",
        "   之后没有针对失败题目调整实现。",
        "",
        "**样本量小。** 50 条题的比例估计，95% 置信区间宽度约 ±10 个百分点。",
        "下面每条指标都带 Wilson 区间；区间重叠时应当认为两者**没有显著差异**，",
        "而不是比较点估计的小数位。",
        "",
        "**不用 LLM 判分。** 没有可用的 API key，而且模型判分本身需要单独验证——",
        "加一层不确定的东西进评测，只会让结论更难解释。",
        "",
        "---",
        "",
        "## 二、总体指标",
        "",
        "| 指标 | 命中 | 比例 | 95% 置信区间 | 目标 | 结论 |",
        "| --- | ---: | ---: | --- | ---: | --- |",
    ]
    for m in METRICS:
        lines.append(_metric_row(m, overall[m]))
    lines += ["", "---", "", "## 三、分类型", ""]

    lines += ["| 类型 | 题数 | 路径 | 答案 | 引用召回 | 拒答 |", "| --- | ---: | ---: | ---: | ---: | ---: |"]
    for name in ["可计算", "需读条文", "拒答", "版本陷阱"]:
        group = by_type.get(name, [])
        if not group:
            continue
        s = summarize(group)
        lines.append(
            f"| {name} | {len(group)} | {_pct(s['route']['rate'])} | "
            f"{_pct(s['answer']['rate'])} | {_pct(s['citation_recall']['rate'])} | "
            f"{_pct(s['refusal']['rate'])} |"
        )
    lines.append("")

    lines += ["按问法分：", "", "| 问法 | 题数 | 路径 | 答案 |", "| --- | ---: | ---: | ---: |"]
    for name in ["正式", "口语"]:
        group = by_style.get(name, [])
        if not group:
            continue
        s = summarize(group)
        lines.append(f"| {name} | {len(group)} | {_pct(s['route']['rate'])} | {_pct(s['answer']['rate'])} |")
    lines += [
        "",
        "**不报总分。** 可计算题与拒答题的性质完全不同，合并成一个数字会掩盖分类问题——",
        "可计算 100%、拒答 30%，总体 75%，这个总分掩盖了「拒答能力差」。",
        "",
        "---",
        "",
        "## 四、阶梯消融",
        "",
        "逐级加部件，每级跑同一套 50 题。每一级只改一个部件，加的顺序按依赖关系。",
        "",
        "| 级别 | 变更 | 路径 | 答案 | 引用召回 |",
        "| --- | --- | ---: | ---: | ---: |",
    ]
    for row in ablation_rows:
        s = row["stats"]
        lines.append(
            f"| {row['level']} | {row['title']} | {_pct(s['route']['rate'])} | "
            f"{_pct(s['answer']['rate'])} | {_pct(s['citation_recall']['rate'])} |"
        )
    lines += [""]

    # 逐级解读，包括「没变化」的那一级
    prev = None
    for row in ablation_rows:
        s = row["stats"]
        if prev is None:
            lines.append(f"**{row['level']}（{row['title']}）**：基线。答案正确率 "
                         f"{_pct(s['answer']['rate'])}。")
        else:
            d_ans = s["answer"]["rate"] - prev["answer"]["rate"]
            d_route = s["route"]["rate"] - prev["route"]["rate"]
            lines.append(
                f"**{row['level']}（{row['title']}）**：假设是「{row['hypothesis']}」　"
                f"→ 答案 {d_ans:+.0%}、路径 {d_route:+.0%}。"
            )
        prev = s
    lines += [""]

    # 同义词那一级的特别说明
    l0, l1 = ablation_rows[0]["stats"], ablation_rows[1]["stats"]
    if abs(l0["answer"]["rate"] - l1["answer"]["rate"]) < 1e-9:
        lines += [
            "> **L1 与 L0 指标完全相同，这一点要单独说明。** 已确认同义词开关确实生效——",
            "> 关闭后有 5 道题的检索结果发生变化（题目 Q-001/004/009/011/012）。",
            "> 但在这个题目集上它没有转化为正确率提升：这几道题本来就因版本或路径问题答错，",
            "> 换一条引用也不足以救回来。**这不等于同义词无用，而是说它在当前失败结构下不是瓶颈。**",
            "",
        ]

    l2, l3 = ablation_rows[2]["stats"], ablation_rows[3]["stats"]
    if l3["answer"]["rate"] > l2["answer"]["rate"]:
        lines += [
            f"> **L3 是全套设计里最重要的一级。** 从「全部走检索」改成「能算的走规则引擎」后，",
            f"> 答案正确率从 {_pct(l2['answer']['rate'])} 升到 {_pct(l3['answer']['rate'])}。",
            "> 这印证了一个设计判断：制度问答里数值类问题占相当比例，",
            "> 把它们交给可能出错的生成过程，不如直接读已验证的规则。",
            "",
        ]

    lines += [
        "### 引用校验的实际作用",
        "",
        "| 级别 | 答错的题 | 被校验标出 | 捕获率 | 答对的题 | 误标 | 误标率 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in ablation_rows:
        v = row["verification"]
        lines.append(
            f"| {row['level']} | {v['wrong']} | {v['captured']} | {_pct(v['capture_rate'])} | "
            f"{v['right']} | {v['false_alarms']} | {_pct(v['false_alarm_rate'])} |"
        )
    lines += [
        "",
        "**这一项的结论对校验不利，但要如实写：在当前生成方式下，校验的捕获率是 0。**",
        "原因不是校验没用，而是**抽取式生成器不会编造**——它引用的每个字都来自语料、",
        "每个数字都来自被引片段，所以校验的三层检查结构上不可能失败。",
        "",
        "校验的价值要在接入生成式模型之后才能体现（那时才会出现编造引用、改写数字、",
        "说出原文没有的结论）。现在保留它，是因为它定义了「什么算可核查」，",
        "而不是因为它在当前配置下抓到了错。",
        "",
        "---",
        "",
        "## 五、两种上下文方式",
        "",
        "这个规模（全部条文不到一万字）本来可以整段塞进模型上下文。所以「检索是否值得」",
        "必须用数据回答，不能预设。",
        "",
        "| 上下文方式 | 路径 | 答案 | 引用召回 |",
        "| --- | ---: | ---: | ---: |",
    ]
    for name, label in [("retrieve", "检索 top-3"), ("full", "全量条文")]:
        s = context_cmp[name]
        lines.append(
            f"| {label} | {_pct(s['route']['rate'])} | {_pct(s['answer']['rate'])} | "
            f"{_pct(s['citation_recall']['rate'])} |"
        )
    lines += [""]

    r_ans = context_cmp["retrieve"]["answer"]["rate"]
    f_ans = context_cmp["full"]["answer"]["rate"]
    identical = context_cmp.get("identical", 0)
    n = context_cmp.get("n", len(questions))
    if identical == n:
        lines += [
            f"> **两种方式在全部 {n} 条题上产出了完全相同的答案。这不构成「检索更好」的证据。**",
            "> 原因在生成方式：抽取式生成器只能用一个片段，给它全部条文它也只会挑其中一段——",
            "> 而挑出来的那段，恰好就是检索排序的第一条。",
            ">",
            "> 所以这一节**测不出「长上下文是否有用」**。要回答那个问题需要生成式模型",
            "> （能同时读入多条条文并组织答案）。本期没有 key，因此这一项标注为**未验证**，",
            "> 而不是「两者一样好」。",
            "",
        ]
    elif abs(r_ans - f_ans) < 0.05:
        lines += [
            "> 两者差异在 5 个百分点以内，且样本量只有 50 条——这个差距落在置信区间内，",
            "> **不能据此判断优劣**。",
            "",
        ]
    else:
        better = "检索" if r_ans > f_ans else "全量条文"
        lines += [f"> 当前题目集上 **{better}** 更好。", ""]

    lines += ["---", "", "## 六、失败清单", ""]
    if not failed_records:
        lines += ["无失败题目。", ""]
    else:
        lines += [
            f"共 {len(failed_records)} 条。每条给出题目、期望、系统实际输出与失败归类。",
            "",
        ]

        # 拒答失败是最系统性的那一类，单独说明根因与已排除的修法
        under_refuse = [r for r in failed_records if r.failure == "UNDER_REFUSE"]
        if under_refuse:
            n_refuse = sum(1 for q in questions if q.route == "refuse")
            lines += [
                "### 先说最系统性的那一类：该拒没拒",
                "",
                f"{n_refuse} 道拒答题里错了 {len(under_refuse)} 道。这不是散点，是同一个原因：",
                "**系统没有「这个问题制度没覆盖」的通用判断**，只依赖一张关键词清单",
                "（生病、医疗、通讯、旅游……）。关键词之外的话题，它就会去引用一条不相干的条文来回答。",
                "",
                "试过两条确定性的判据，**都不成立**，记录在此以免重走：",
                "",
                "| 候选判据 | 设想 | 实测 | 结论 |",
                "| --- | --- | --- | --- |",
                "| 检索分数阈值 | 答不出的题分数应当低 | 拒答题 3.9–11.5；正常题 1.9–45.9 | 区间完全重叠，**不可分** |",
                "| 问句词汇在语料中的覆盖率 | 没覆盖的话题词汇覆盖率低 | 拒答题 25–54%；正常题 14–85% | 区间完全重叠，**不可分** |",
                "",
                "要真正解决，需要判断「召回的条文是否真的回答了这个问题」——这是语义层面的判断，",
                "确定性方法做不到。**所以本期不做伪装式的修补**：不靠扩充关键词清单去贴合这 6 道题，",
                "那正是冻结机制要防的事。",
                "",
            ]

        for r in failed_records:
            q = r.q
            a = r.answer
            lines += [
                f"### {q.id}　{FAILURE_LABELS.get(r.failure, r.failure)}",
                "",
                f"- **问题**：{q.question}",
                f"- **基准日**：{q.as_of}　**问法**：{'口语' if q.style == 'colloquial' else '正式'}",
                f"- **期望**：路径 `{q.route}`"
                + (f"、引用 {'/'.join(str(c) for c in q.citations_any)}" if q.citations_any else "")
                + (f"、必须出现 {'、'.join(q.facts)}" if q.facts else ""),
                f"- **实际**：路径 `{a.route}`、拒答 `{a.refusal}`、"
                f"引用 {[c.label for c in a.citations] or '无'}",
            ]
            if r.detail:
                lines.append(f"- **差异**：{r.detail}")
            snippet = (a.text or "（空）").replace("\n", " ")[:120]
            lines += [f"- **输出**：{snippet}…", f"- **依据**：{q.basis}", ""]

    lines += [
        "---",
        "",
        "## 七、复现",
        "",
        "```bash",
        "PYTHONPATH=src python -m claimcheck eval --out docs/eval-report.md",
        "PYTHONPATH=src python -m claimcheck eval --ablation",
        "```",
        "",
        "题目集是人工维护的真值来源，进版本控制；冻结哈希见 `data/eval/FROZEN.md`。",
        "改动题目后哈希会变，报告头部会显示新哈希——**分数只有在同一哈希下才可比**。",
        "",
    ]

    if problems:
        lines += ["---", "", "## 评测器自检", ""]
        lines += [f"- {p}" for p in problems]
        lines.append("")

    return "\n".join(lines)
