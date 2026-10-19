"""生成器：把召回材料变成答案。

本期只实现抽取式。LLM 版**只在文档里留接口，不写实现**——
没有 API key 就测不了，测不了就无法证明它工作；
塞一批没跑过的代码进去，等于在仓库里放了个装饰品。

抽取式的定位不是「凑合」，而是基线：它的每个数字都能追溯，每种失败都看得见。
接上 LLM 之后，它仍然是对照组——判断 LLM 有没有变好，靠的就是和它比。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from .corpus import Chunk
from .verify import Citation


@dataclass
class Draft:
    """生成器的输出。校验与展示都以这个结构为准。"""
    text: str
    citations: list[Citation] = field(default_factory=list)
    insufficient: bool = False          # 材料不足以回答
    reason: str = ""                    # 不足以回答时的说明


class Generator(Protocol):
    """生成器接口。

    LLM 版要实现同一个接口，才能在不改编排代码的前提下替换。
    """

    def answer(self, question: str, context: list[Chunk], **kw) -> Draft: ...


class ExtractiveGenerator:
    """抽取式：挑最相关的一段条文，原样引用；能结构化取数的取数。

    它不会「总结」，也不改写条文措辞——改写就意味着可能说出原文没有的话，
    而校验层（verify.py）正是靠「逐字可查」来判断的。
    """

    def answer(self, question: str, context: list[Chunk], **kw) -> Draft:
        if not context:
            return Draft(text="", insufficient=True, reason="没有检索到相关条文")

        top = context[0]
        citation = Citation(doc_no=top.doc_no, article=top.article, text=top.text)

        # 有附加的结构化事实时（比如从明细表取到的限额），一并给出
        extra_lines: list[str] = kw.get("extra_lines") or []
        extra_citations: list[Citation] = kw.get("extra_citations") or []

        body = "\n".join(extra_lines) if extra_lines else ""

        quoted = f"{top.doc_no} {top.article}规定：{top.text}"
        text = "\n\n".join(part for part in (body, quoted) if part)

        return Draft(
            text=text,
            citations=extra_citations + [citation],
        )


class LLMGenerator:
    """预留接口。本期不实现。

    实现时需要满足同样的约束，否则校验层会用不上：

    1. **只能用 context 里的内容作答**，不能引入外部知识
    2. **每个事实性陈述都要能指到某条引用**
    3. **context 不足时返回 insufficient=True**，不要猜

    提示词模板（未验证，仅作接口说明）：

        system: 你是差旅费制度问答助手。只能依据下面提供的条文回答；
                条文不足以回答时，明确说「制度未规定」或「现有条文不足以回答」。
                回答中的每个数字必须来自所引条文，不得计算或改写。
        user: 问题：{question}
              条文：
              {context}

    接上之后要做的第一件事，不是调提示词，而是把 `ExtractiveGenerator` 作为对照组
    跑同一套评测集——否则无法判断模型到底带来了什么。
    """

    def __init__(self, *args, **kwargs):
        raise NotImplementedError(
            "本期未实现：没有可用的 API key，无法验证。"
            "接口与约束见本类的文档字符串。"
        )

    def answer(self, question: str, context: list[Chunk], **kw) -> Draft:  # pragma: no cover
        raise NotImplementedError
