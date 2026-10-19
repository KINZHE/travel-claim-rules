"""检索与版本过滤。

版本过滤是这个模块最要紧的能力：检索时混进旧版本条文，后面无论如何都救不回来。
所以测试的重点不是「能不能搜到」，而是「在指定的日期下，搜到的到底是哪一版」。
"""

import pytest

from claimcheck.corpus import load_corpus
from claimcheck.retrieval import Retriever, bigrams, build_retriever, normalize


@pytest.fixture(scope="module")
def retriever() -> Retriever:
    return build_retriever(load_corpus())


def test_bigrams_of_chinese():
    # 同义词映射先把「住宿费」归一成「住宿」，二元组因此只剩一个
    assert bigrams("住宿费") == ["住宿"]


def test_bigrams_of_unmapped_text():
    assert bigrams("公杂费") == ["公杂", "杂费"]


def test_synonym_mapping():
    """口语词要映射到制度用语，否则问「住酒店」搜不到「住宿费」。"""
    assert "住宿" in normalize("住酒店")
    assert "市内交通" in normalize("打车的钱")


def test_version_filter_2013(retriever):
    """2013 年时，市内交通费的依据是 2006 版的「公杂费 30 元」。"""
    hits = retriever.search("市内交通费标准", k=5, as_of="2013-06-01")
    assert hits, "2013 年应当能检索到条文"
    assert all(h.chunk.doc_no == "财行〔2006〕313号" for h in hits), (
        "2013 年的检索结果里混进了其他版本：" + str([h.chunk.doc_no for h in hits])
    )
    joined = " ".join(h.chunk.text for h in hits)
    assert "30" in joined


def test_version_filter_current(retriever):
    """现在检索，不能出现 2006 版条文。"""
    hits = retriever.search("市内交通费标准", k=8, as_of="2026-01-01")
    assert hits
    assert all(h.chunk.doc_no != "财行〔2006〕313号" for h in hits), (
        "现行版本的检索结果里出现了已废止条文件"
    )


def test_all_for_version_full_context(retriever):
    """full 模式取全量时同样要按版本过滤。"""
    current = retriever.all_for_version("2026-01-01")
    old = retriever.all_for_version("2013-06-01")
    assert all(c.doc_no != "财行〔2006〕313号" for c in current)
    assert all(c.doc_no != "财行〔2013〕531号" for c in old)


def test_voucher_query_hits_article_24(retriever):
    hits = retriever.search("报销需要哪些凭证", k=3, as_of="2026-01-01")
    labels = [f"{h.chunk.doc_no} {h.chunk.article}" for h in hits]
    assert any("第二十四条" in label for label in labels), labels


def test_matched_terms_are_explainable(retriever):
    """命中的二元组要能取出来——检索结果要可解释，不是黑箱打分。"""
    hits = retriever.search("住宿费限额", k=3, as_of="2026-01-01")
    assert hits[0].matched
    assert all(len(g) == 2 for g in hits[0].matched)


def test_empty_query_returns_nothing(retriever):
    assert retriever.search("", k=3, as_of="2026-01-01") == []
