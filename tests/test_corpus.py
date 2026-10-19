"""语料切分：按条切、带版本元数据。

切分是最容易无声出错的地方——少一条条文不会报错，只会让某些问题永远答不出来。
所以这里的测试以「数量」和「内容完整性」为准，不看格式。
"""

from pathlib import Path

import pytest

from claimcheck.corpus import Chunk, load_corpus, parse_by_article, parse_by_item, SOURCES


@pytest.fixture(scope="module")
def corpus() -> list[Chunk]:
    return load_corpus()


def test_article_counts(corpus):
    """条数必须和制度原文一致。"""
    from collections import Counter
    counts = Counter(c.doc_no for c in corpus)
    assert counts["财行〔2013〕531号"] == 33
    assert counts["财行〔2006〕313号"] == 28
    assert counts["财办行〔2019〕104号"] == 7


def test_every_chunk_has_version_metadata(corpus):
    """版本字段不是装饰——检索时靠它过滤，缺了就会新旧混用。"""
    for chunk in corpus:
        assert chunk.doc_no
        assert chunk.article
        assert chunk.version
        assert chunk.effective_from, f"{chunk.chunk_id} 缺生效日期"
        assert chunk.text.strip(), f"{chunk.chunk_id} 正文为空"


def test_effective_windows_do_not_overlap(corpus):
    """两版 531 类文件的生效区间不应重叠，否则同一日期会同时命中两份制度。"""
    a = [c for c in corpus if c.doc_no == "财行〔2013〕531号"][0]
    b = [c for c in corpus if c.doc_no == "财行〔2006〕313号"][0]
    assert b.effective_to < a.effective_from


def test_long_article_not_truncated(corpus):
    """第二十三条是 531 号里条款最多的一条，抽查它没被切断。"""
    chunk = next(c for c in corpus
                 if c.doc_no == "财行〔2013〕531号" and c.article == "第二十三条")
    assert "城市间交通费" in chunk.text
    assert "超支部分由个人自理" in chunk.text
    assert len(chunk.text) > 100


def test_article_with_table_is_intact(corpus):
    """第七条含等级表，切分时表格内容不能丢。"""
    chunk = next(c for c in corpus
                 if c.doc_no == "财行〔2013〕531号" and c.article == "第七条")
    assert "商务座" in chunk.text
    assert "头等舱" in chunk.text


def test_chapter_is_attached(corpus):
    """章名要挂到条文上，便于按章聚合或展示。"""
    chunk = next(c for c in corpus
                 if c.doc_no == "财行〔2013〕531号" and c.article == "第十四条")
    assert "住宿费" in chunk.chapter


def test_no_heading_or_notice_leaked_into_corpus(corpus):
    """切分起点在「第一条」，之前的通知正文、页眉不能混进来。"""
    for chunk in corpus:
        assert "国务院公报" not in chunk.text
        assert "网站标识码" not in chunk.text


def test_chunk_ids_are_unique(corpus):
    ids = [c.chunk_id for c in corpus]
    assert len(ids) == len(set(ids))
