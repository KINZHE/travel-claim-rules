"""检索：字符二元组 + BM25，带版本过滤。

**为什么用字符二元组而不是分词**：中文没有空格，引 `jieba` 会带进几 MB 依赖，
而且对法律文本的分词效果未必比二元组好。二元组的另一个好处是完全可解释——
能说清某个片段为什么被召回（命中了哪几个二元组）。

**版本过滤在检索阶段就做，不在生成阶段做。** 检索时混进旧版本条文，
后面无论如何都救不回来——模型看到旧条文就会照着答。
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field

from .corpus import Chunk

# BM25 参数。小语料上用经验值，不调参——调了也没有验证集可调。
K1 = 1.5
B = 0.75

# 问句里的口语词 → 制度里的说法。这一步比换检索引擎便宜得多，
# 调研里有项目发现它是最大的单点增益。
SYNONYMS = {
    "住酒店": "住宿",
    "酒店": "住宿",
    "住宿费": "住宿",
    "差旅费": "差旅",
    "报销标准": "标准",
    "上限": "限额",
    "最多": "限额",
    "能报多少": "限额",
    "怎么算": "计算",
    "打车的钱": "市内交通",
    "出租车": "市内交通",
    "吃饭": "伙食",
    "餐费": "伙食",
    "火车票": "城市间交通",
    "机票": "城市间交通",
}

# 问句里常见但没有检索价值的词
STOP_PHRASES = [
    "请问", "我想知道", "我想问", "是什么", "有哪些", "怎么样", "可以吗",
    "能不能", "是否", "需要", "规定", "应该", "怎么",
]


def normalize(text: str) -> str:
    """同义词替换 + 去停用短语 + 去标点空白。"""
    t = text
    for phrase in STOP_PHRASES:
        t = t.replace(phrase, "")
    # 长词优先替换，避免「住宿费」先被「住宿」吃掉后剩个「费」
    for src in sorted(SYNONYMS, key=len, reverse=True):
        t = t.replace(src, SYNONYMS[src])
    t = re.sub(r"[\s，。、？！：；（）()《》\[\]“”\"'.,?!:;-]+", "", t)
    return t


def bigrams(text: str) -> list[str]:
    """字符二元组。单字文本返回自身。"""
    t = normalize(text)
    if len(t) <= 1:
        return [t] if t else []
    return [t[i:i + 2] for i in range(len(t) - 1)]


@dataclass
class Hit:
    chunk: Chunk
    score: float
    matched: list[str] = field(default_factory=list)


class Retriever:
    """倒排索引 + BM25。语料只有几十条，构建成本可以忽略。"""

    def __init__(self, chunks: list[Chunk]):
        self.chunks = chunks
        self._docs: list[Counter] = []
        self._lens: list[int] = []
        self._df: Counter = Counter()

        for chunk in chunks:
            grams = bigrams(chunk.text)
            counts = Counter(grams)
            self._docs.append(counts)
            self._lens.append(len(grams))
            for gram in counts:
                self._df[gram] += 1

        self._n = len(chunks)
        self._avg_len = sum(self._lens) / max(1, self._n)

    def _idf(self, gram: str) -> float:
        df = self._df.get(gram, 0)
        # 加 0.5 的平滑，避免除零；语料小，不做更复杂的处理
        return math.log(1 + (self._n - df + 0.5) / (df + 0.5))

    def search(self, query: str, k: int = 5, as_of: str | None = None,
               version: str | None = None) -> list[Hit]:
        """检索。

        as_of：按这个日期过滤条文版本（默认不过滤，但调用方应当总是传）。
        version：直接指定版本号，优先于 as_of。
        """
        q_grams = bigrams(query)
        if not q_grams:
            return []

        scored: list[Hit] = []
        for idx, chunk in enumerate(self.chunks):
            if version and chunk.version != version:
                continue
            if not version and as_of and not _covers(chunk, as_of):
                continue

            counts = self._docs[idx]
            dl = self._lens[idx] or 1
            score = 0.0
            matched = []
            for gram in set(q_grams):
                tf = counts.get(gram, 0)
                if tf == 0:
                    continue
                matched.append(gram)
                denom = tf + K1 * (1 - B + B * dl / self._avg_len)
                score += self._idf(gram) * tf * (K1 + 1) / denom
            if score > 0:
                scored.append(Hit(chunk=chunk, score=score, matched=sorted(matched)))

        scored.sort(key=lambda h: (-h.score, h.chunk.chunk_id))
        return scored[:k]

    def all_for_version(self, as_of: str | None = None) -> list[Chunk]:
        """当前生效版本的**全部**条文。给 `--context full` 用。"""
        if as_of is None:
            return list(self.chunks)
        return [c for c in self.chunks if _covers(c, as_of)]


def _covers(chunk: Chunk, as_of: str) -> bool:
    if as_of < chunk.effective_from:
        return False
    if chunk.effective_to and as_of > chunk.effective_to:
        return False
    return True


def build_retriever(chunks: list[Chunk]) -> Retriever:
    return Retriever(chunks)
