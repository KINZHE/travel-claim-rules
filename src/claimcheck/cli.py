"""命令行入口。

    python -m claimcheck check data/samples/sample-002-over-limit.json
    python -m claimcheck check <claim.json> --db runs.db --save
    python -m claimcheck coverage --db runs.db
    python -m claimcheck items            # 列出全部检查项（覆盖率分母）
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from .paths import find_root
from .engine import check_item_universe, run
from .model import load_claim, load_rules

ROOT = find_root()


def _digest(rules_dir: Path) -> str:
    """规则文件的指纹。写进 runs 表，便于回答「这次跑的是哪一版规则」。"""
    h = hashlib.sha256()
    for path in sorted(rules_dir.glob("*.yaml")):
        h.update(path.read_bytes())
    return h.hexdigest()[:16]


def cmd_check(args: argparse.Namespace) -> int:
    result = run(load_claim(args.claim), load_rules(args.rules), as_of=args.as_of)

    print(f"申请 {result.claim_id}  出差日 {result.trip_date}")
    print("=" * 68)
    for r in result.results:
        flag = "  " if r.status == "通过" else ">>"
        print(f"{flag} [{r.status}] {r.rule_id}")
        if r.message:
            print(f"        {r.message}")
        if r.conflict:
            print(f"        冲突对: {', '.join(r.conflict)}")
    print("=" * 68)
    print("按费用项:")
    for cat, status in sorted(result.by_category.items()):
        print(f"  {cat:8} {status}")
    print(f"整体结论: {result.overall}")

    if args.db:
        from . import store
        conn = store.connect(args.db)
        rules = load_rules(args.rules)
        store.register_rules(conn, rules)
        if args.save:
            run_id = store.save(conn, result, _digest(Path(args.rules)))
            print(f"已写入 {args.db}，run_id={run_id}")
        else:
            print(f"（加 --save 才会写入 {args.db}）")
        conn.close()

    if args.json:
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))

    return 0


def cmd_items(args: argparse.Namespace) -> int:
    rules = load_rules(args.rules)
    items = check_item_universe(rules)
    print(f"检查项总数: {len(items)}（规则 {len(rules)} 条）")
    for item in items:
        print("  " + item)
    return 0


def cmd_diagnose(args: argparse.Namespace) -> int:
    import yaml

    from . import diagnose as D
    from .report import build_report
    from .universe import load_universe

    rules = load_rules(args.rules)
    universe = load_universe()
    known_path = Path(args.known_gaps)
    known = yaml.safe_load(known_path.read_text(encoding="utf-8")) if known_path.exists() else {}

    report = build_report(rules, universe, known)

    if args.out:
        Path(args.out).write_text(report, encoding="utf-8")
        print(f"报告已写入 {args.out}")
    else:
        print(report)

    # 退出码：确定冲突、缺口、或无法复现的声明 → 非零，便于当 CI 门禁用。
    # 疑似冲突和无效规则不阻断——它们本来就需要人来判断。
    conflicts = D.find_conflicts(rules, universe)
    gaps = D.find_gaps(rules, universe, known.get("accepted_gaps", []))
    gaps += D.find_structural_gaps(rules, universe)
    blocking = len(conflicts["confirmed"]) + len(gaps) + len(conflicts["unresolved"])
    if blocking and args.strict:
        print(f"\n严格模式：{blocking} 项需要处理")
        return 1
    return 0


def cmd_ask(args: argparse.Namespace) -> int:
    from .qa import QA

    qa = QA()
    answer = qa.ask(args.question, context_mode=args.context, as_of=args.as_of,
                    top_k=args.top_k)

    if args.json:
        payload = {
            "question": answer.question,
            "route": answer.route,
            "refusal": answer.refusal,
            "answer": answer.text,
            "citations": [
                {"label": c.label, "kind": c.kind, "text": c.text}
                for c in answer.citations
            ],
            "verification": answer.verification.to_dict() if answer.verification else None,
            "trace": answer.trace,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    print(f"问：{answer.question}")
    print("=" * 68)
    print(answer.text or "（无内容）")
    print()

    if answer.citations:
        print("依据：")
        for cite in answer.citations:
            mark = {"clause": "条款", "table": "表格", "secondary": "二手"}.get(cite.kind, "")
            print(f"  [{mark}] {cite.label}")
            print(f"        {cite.text[:100]}{'…' if len(cite.text) > 100 else ''}")
        print()

    v = answer.verification
    if v is not None:
        print(f"校验：{'通过' if v.passed else '未通过'}")
        for problem in v.problems:
            print(f"  × {problem}")
        for warning in v.warnings:
            print(f"  ! {warning}")
    elif answer.refused:
        print(f"拒答：{answer.refusal}")

    print()
    print("路径：" + answer.trace.get("route", "?"), end="")
    if answer.trace.get("classification"):
        print(f" | {answer.trace['classification']}", end="")
    if answer.trace.get("rule_id"):
        print(f" | 规则 {answer.trace['rule_id']}", end="")
    if answer.trace.get("context_mode"):
        print(f" | 上下文 {answer.trace['context_mode']}", end="")
    print()
    return 0


def cmd_eval(args: argparse.Namespace) -> int:
    from .evaluate import load_questions, run_all, summarize, validate_questions, self_check
    from .qa import QA

    questions = load_questions()
    problems = validate_questions(questions)
    if problems:
        print("题目集有问题：")
        for p in problems:
            print("  -", p)
        return 1

    if args.ablation:
        from .ablation import run_ablation
        rows = run_ablation(questions)
        print(f"题目 {len(questions)} 条\n")
        print(f"{'级别':4} {'路径':>6} {'答案':>6} {'引用召回':>8}  变更")
        for row in rows:
            s = row["stats"]
            print(f"{row['level']:4} {s['route']['rate']:>6.0%} {s['answer']['rate']:>6.0%} "
                  f"{s['citation_recall']['rate']:>8.0%}  {row['title']}")
        return 0

    if args.out:
        from .evalreport import build_report
        Path(args.out).write_text(build_report(questions), encoding="utf-8")
        print(f"报告已写入 {args.out}")
        return 0

    qa = QA()
    records = run_all(qa, questions)
    issues = self_check(records, len(questions))
    s = summarize(records)
    print(f"题目 {len(questions)} 条")
    for m in ("route", "answer", "citation_recall", "refusal", "verify", "version"):
        d = s[m]
        lo, hi = d["ci"]
        print(f"  {m:16} {d['k']:2}/{d['n']} = {d['rate']:.0%}  95%CI {lo:.2f}–{hi:.2f}")
    if issues:
        print("\n自检未通过：")
        for i in issues:
            print("  -", i)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(prog="claimcheck", description="差旅报销规则引擎")
    parser.add_argument("--rules", default=str(ROOT / "rules"), help="规则目录")
    sub = parser.add_subparsers(dest="command", required=True)

    p_check = sub.add_parser("check", help="对一份申请跑全部规则")
    p_check.add_argument("claim", help="申请 JSON 路径")
    p_check.add_argument("--as-of", help="用指定日期代替出差日做版本判断（YYYY-MM-DD，用于历史复核与版本对比）")
    p_check.add_argument("--db", help="SQLite 文件路径")
    p_check.add_argument("--save", action="store_true", help="结果写入 SQLite")
    p_check.add_argument("--json", action="store_true", help="同时输出 JSON")
    p_check.set_defaults(func=cmd_check)

    p_items = sub.add_parser("items", help="列出全部检查项")
    p_items.set_defaults(func=cmd_items)

    p_diag = sub.add_parser("diagnose", help="对规则库做质量诊断")
    p_diag.add_argument("--out", help="写入 Markdown 报告（不给则打印到终端）")
    p_diag.add_argument("--known-gaps", default=str(ROOT / "docs" / "known-gaps.yaml"),
                        help="已申报缺口与豁免的文件")
    p_diag.add_argument("--strict", action="store_true",
                        help="确定冲突、缺口、无法复现的声明存在时返回非零（用于 CI 门禁）")
    p_diag.set_defaults(func=cmd_diagnose)

    p_ask = sub.add_parser("ask", help="就制度提问")
    p_ask.add_argument("question", help="问题（用引号括起来）")
    p_ask.add_argument("--context", choices=["retrieve", "full"], default="retrieve",
                       help="上下文方式：检索片段 / 全量条文（第 5 周要对比两者）")
    p_ask.add_argument("--as-of", help="按指定日期判断适用哪一版制度（YYYY-MM-DD）")
    p_ask.add_argument("--top-k", type=int, default=3, help="检索返回条数")
    p_ask.add_argument("--json", action="store_true", help="输出 JSON")
    p_ask.set_defaults(func=cmd_ask)

    p_eval = sub.add_parser("eval", help="跑评测集")
    p_eval.add_argument("--out", help="写入 Markdown 报告")
    p_eval.add_argument("--ablation", action="store_true", help="只跑阶梯消融")
    p_eval.set_defaults(func=cmd_eval)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
