"""冻结评测集：记录哈希，之后可核对题目是否被改过。

不冻结的话，「看到哪题错了就改哪题」会让分数失去意义——
改到最后分数很好，但那是针对这几道题调出来的。
"""

from __future__ import annotations

import hashlib
from datetime import date
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent   # 本脚本在 scripts/ 下，根目录是上一级
QUESTIONS = ROOT / "data" / "eval" / "questions.yaml"
FROZEN = ROOT / "data" / "eval" / "FROZEN.md"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def freeze(note: str = "") -> Path:
    digest = sha(QUESTIONS)
    data = yaml.safe_load(QUESTIONS.read_text(encoding="utf-8")) or []
    by_route: dict[str, int] = {}
    by_style: dict[str, int] = {}
    for q in data:
        r = q.get("expected", {}).get("route", "?")
        by_route[r] = by_route.get(r, 0) + 1
        by_style[q.get("style", "?")] = by_style.get(q.get("style", "?"), 0) + 1

    lines = [
        "# 评测集冻结记录",
        "",
        f"冻结日期：{date.today().isoformat()}",
        f"题目数量：{len(data)}",
        f"分布：路径 {by_route}；问法 {by_style}",
        "",
        "**SHA-256**",
        "",
        "```",
        digest,
        "```",
        "",
        "## 为什么要冻结",
        "",
        "题目由项目作者构造、评测也由作者运行。这个结构下最容易走的路是",
        "「看到哪题错了就改哪题」，改到最后分数很好看，但那是针对这几十道题调出来的。",
        "",
        "冻结之后，改题会改变哈希，报告头部的哈希与这里不一致——**分数只在同一哈希下可比**。",
        "",
        "## 本次评测的时点说明",
        "",
        "分数来自写完 50 条题后的**首次运行**。之后没有针对失败题目调整实现或预期值。",
        "这一点没法用工具证明，但哈希记录让「题目有没有被事后修改」可以被核对。",
        "",
        "## 如果确实需要改题",
        "",
        "改是允许的，但必须同时做两件事：",
        "",
        "1. 在下面「变更记录」里写清改了哪条、为什么改（`basis` 写错了？标准答案错了？）",
        "2. 重新跑一遍全部评测，并在报告里注明「哈希已变更，与之前分数不可比」",
        "",
        "**不允许**因为系统答错就把预期值改成系统输出。那不是修正，是作弊。",
        "",
        "## 变更记录",
        "",
        "- 2026-10-19：首次冻结。",
        "",
    ]
    if note:
        lines.append(f"- {note}")
        lines.append("")
    FROZEN.write_text("\n".join(lines), encoding="utf-8")
    return FROZEN


def check() -> tuple[bool, str]:
    """核对当前题目集与冻结记录是否一致。"""
    if not FROZEN.exists():
        return False, "没有冻结记录"
    text = FROZEN.read_text(encoding="utf-8")
    import re
    m = re.search(r"```\n([0-9a-f]{64})\n```", text)
    if not m:
        return False, "冻结记录里找不到哈希"
    current = sha(QUESTIONS)
    if current != m.group(1):
        return False, f"题目集已变更（当前 {current[:16]}，冻结 {m.group(1)[:16]}）"
    return True, "与冻结记录一致"


if __name__ == "__main__":
    print(freeze())
    ok, msg = check()
    print(msg)
