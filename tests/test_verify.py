"""引用校验。

校验的价值全在「该失败时真的失败」。所以三个失败测试比一个通过测试更重要——
一个永远通过的校验器，只会给报告增加一行绿色。
"""

from claimcheck.corpus import load_corpus
from claimcheck.verify import Citation, extract_numbers, verify


def _corpus():
    return load_corpus()


def _article24():
    return next(c for c in _corpus()
                if c.doc_no == "财行〔2013〕531号" and c.article == "第二十四条")


def test_good_answer_passes():
    chunk = _article24()
    cite = Citation(doc_no=chunk.doc_no, article=chunk.article, text=chunk.text)
    text = f"{chunk.doc_no} {chunk.article}规定：{chunk.text}"
    v = verify(text, [cite], _corpus())
    assert v.passed, v.problems


def test_missing_citation_fails():
    v = verify("报销需要提供发票。", [], _corpus())
    assert not v.passed
    assert not v.citation_exists


def test_nonexistent_article_fails():
    cite = Citation(doc_no="财行〔2013〕531号", article="第九十九条", text="编造的内容")
    v = verify("按第九十九条执行。", [cite], _corpus())
    assert not v.passed
    assert any("不存在的条款" in p for p in v.problems)


def test_paraphrase_masquerading_as_quote_fails():
    """引文开头与原文对不上，说明是把转述当成原文引用。"""
    chunk = _article24()
    cite = Citation(doc_no=chunk.doc_no, article=chunk.article,
                    text="出差人员必须提供所有原始票据，否则不予报销。")
    v = verify("出差人员必须提供所有原始票据。", [cite], _corpus())
    assert not v.passed
    assert any("引文与原文不一致" in p for p in v.problems)


def test_number_not_in_citation_fails():
    """答案里出现了被引片段中没有的数字。"""
    chunk = _article24()
    cite = Citation(doc_no=chunk.doc_no, article=chunk.article, text=chunk.text)
    v = verify("报销时限为 15 个工作日。", [cite], _corpus())
    assert not v.passed
    assert "15" in v.untraceable_numbers


def test_negative_claim_not_in_citation_fails():
    """答案宣称制度禁止某事，但被引片段里没有这个说法。"""
    chunk = _article24()
    cite = Citation(doc_no=chunk.doc_no, article=chunk.article, text=chunk.text)
    v = verify("出差人员不得乘坐飞机。", [cite], _corpus())
    assert not v.passed
    assert "不得" in v.unsupported_claims


def test_number_check_ignores_article_numbers():
    """「第二十三条」里的数字是条款编号，不该被当成需要溯源的事实数值。"""
    assert "二十三" not in extract_numbers("参见第二十三条")
    assert extract_numbers("限额 490 元") == ["490"]


def test_soft_claim_only_warns():
    """「不超过」这类转述只提示，不判失败——判失败会因为措辞差异大量误报。"""
    chunk = _article24()
    cite = Citation(doc_no=chunk.doc_no, article=chunk.article, text=chunk.text)
    v = verify("报销金额不超过标准。", [cite], _corpus())
    assert v.passed
    assert any("不超过" in w for w in v.warnings)


def test_secondary_source_warns_but_passes():
    """二手来源标注了层级，可以过，但必须给出警告。"""
    chunk = _article24()
    clause = Citation(doc_no=chunk.doc_no, article=chunk.article, text=chunk.text)
    secondary = Citation(doc_no="财行〔2013〕531号", article="附表（未取得原件）",
                         text="标准数值 100 元，来源层级 B", kind="secondary")
    v = verify("伙食补助 100 元每天。", [secondary, clause], _corpus())
    assert v.passed
    assert any("二手来源" in w for w in v.warnings)


def test_empty_table_citation_fails():
    cite = Citation(doc_no="财行〔2016〕71号", article="明细表", text="", kind="table")
    v = verify("限额为 490 元。", [cite], _corpus())
    assert not v.passed
