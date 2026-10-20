"""语料切分：把制度原文切成带版本元数据的片段。

**按条切，不按字数切。** 按固定字数切会把「行为描述」和「罚则/限额」拆散，
检索到半句反而更糟——这在调研里是几条独立实践都印证过的做法。

每个片段必须带版本信息（`doc_no` / `version` / `effective_from` / `effective_to`），
因为**新旧版本混用是这类问答最大的错误来源**（调研中有项目统计约 50% 的错答源于此）。
版本不是元数据装饰，是检索时必须用的过滤器。
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from .paths import find_root

ROOT = find_root()
SOURCES = ROOT / "sources"

CN_NUM = "一二三四五六七八九十"


@dataclass
class Chunk:
    chunk_id: str
    doc_no: str
    article: str            # 「第十四条」或「一、」
    version: str
    effective_from: str
    effective_to: str | None
    text: str
    chapter: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def label(self) -> str:
        """人读的引用标签。"""
        return f"{self.doc_no} {self.article}"


# ---------------------------------------------------------------- 各来源的解析

# 网页页脚的起点。政府网页在正文之后会跟一大段导航、备案号、版权声明，
# 最后一条条文会把这些全部吞进去——不截掉的话，它会在检索里被当成正文命中。
FOOTER_MARKERS = [
    "相关稿件", "网站地图", "联系我们", "主办单位", "版权所有",
    "京ICP备", "京公网安备", "网站标识码", "上一条：", "下一条：",
    "关闭", "字号：", "打印",
]


def _strip_footer(text: str) -> str:
    cut = len(text)
    for marker in FOOTER_MARKERS:
        idx = text.find(marker)
        if idx != -1:
            cut = min(cut, idx)
    return text[:cut].strip()


def _clean(text: str) -> str:
    """去掉换行、全角空格，截掉网页页脚，保留标点。"""
    text = _strip_footer(text)
    text = text.replace("　", " ").replace("\n", "")
    return re.sub(r"\s+", " ", text).strip()


def parse_by_article(path: Path, *, doc_no: str, version: str,
                     effective_from: str, effective_to: str | None) -> list[Chunk]:
    """按「第X条」切分。章节标题会作为 chapter 挂在后续条文上。"""
    raw = path.read_text(encoding="utf-8")
    # 只从第一条开始，之前的标题、页眉、通知正文都丢掉
    start = raw.find("\n第一条")
    if start < 0:
        start = raw.find("第一条")
    if start < 0:
        raise ValueError(f"{path.name}: 找不到「第一条」")
    body = raw[start:]

    # 在「第X章」处也切一刀，记录章名
    parts = re.split(r"\n(?=(?:第[一二三四五六七八九十]+章|第[一二三四五六七八九十]+条))", body)

    chunks: list[Chunk] = []
    chapter = ""
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if re.match(r"^第[一二三四五六七八九十]+章", part):
            chapter = part.split("\n")[0].strip()
            continue
        m = re.match(r"^(第[一二三四五六七八九十]+条)\s*(.*)$", part, re.S)
        if not m:
            continue
        article, content = m.group(1), _clean(m.group(2))
        if not content:
            continue
        chunks.append(Chunk(
            chunk_id=f"{doc_no}::{article}",
            doc_no=doc_no, article=article, version=version,
            effective_from=effective_from, effective_to=effective_to,
            text=content, chapter=chapter,
        ))
    return chunks


def parse_by_item(path: Path, *, doc_no: str, version: str,
                  effective_from: str, effective_to: str | None) -> list[Chunk]:
    """按「一、二、三、」切分（497 号、104 号这种通知类文件）。"""
    raw = path.read_text(encoding="utf-8")
    parts = re.split(r"\n(?=[一二三四五六七八九十]+、)", raw)

    chunks: list[Chunk] = []
    for part in parts:
        part = part.strip()
        m = re.match(r"^([一二三四五六七八九十]+、)\s*(.*)$", part, re.S)
        if not m:
            continue
        article, content = m.group(1), _clean(m.group(2))
        if not content:
            continue
        chunks.append(Chunk(
            chunk_id=f"{doc_no}::{article}",
            doc_no=doc_no, article=article, version=version,
            effective_from=effective_from, effective_to=effective_to,
            text=content,
        ))
    return chunks


# ---------------------------------------------------------------- 汇总

SPECS = [
    dict(kind="article", path=SOURCES / "2013-531" / "full-text.txt",
         doc_no="财行〔2013〕531号", version="2013-531",
         effective_from="2014-01-01", effective_to=None),
    dict(kind="article", path=SOURCES / "2006-313" / "full-text.txt",
         doc_no="财行〔2006〕313号", version="2006-313",
         effective_from="2007-01-01", effective_to="2013-12-31"),
    dict(kind="item", path=SOURCES / "2015-497" / "full-text.txt",
         doc_no="财行〔2015〕497号", version="2015-497",
         effective_from="2016-01-01", effective_to=None),
    dict(kind="item", path=SOURCES / "2019-104" / "full-text.txt",
         doc_no="财办行〔2019〕104号", version="2019-104",
         effective_from="2019-08-01", effective_to=None),
]


def load_corpus() -> list[Chunk]:
    chunks: list[Chunk] = []
    for spec in SPECS:
        path = spec.pop("path")
        kind = spec.pop("kind")
        try:
            if kind == "article":
                chunks.extend(parse_by_article(path, **spec))
            else:
                chunks.extend(parse_by_item(path, **spec))
        except FileNotFoundError:
            continue
        finally:
            spec["path"] = path
            spec["kind"] = kind
    return chunks


def build_corpus_file(out: Path | None = None) -> Path:
    out = out or ROOT / "data" / "corpus.jsonl"
    chunks = load_corpus()
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        for chunk in chunks:
            f.write(json.dumps(chunk.to_dict(), ensure_ascii=False) + "\n")
    return out


def load_corpus_file(path: Path | None = None) -> list[Chunk]:
    path = path or ROOT / "data" / "corpus.jsonl"
    out = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                out.append(Chunk(**json.loads(line)))
    return out
