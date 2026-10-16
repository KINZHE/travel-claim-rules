"""把抓下来的政府网页 HTML 转成纯文本，便于存档和逐条核对。

只用标准库，不引依赖。用法：

    python extract_text.py <输入.html> <输出.txt> [编码]

编码不传时按 utf-8 试，失败再试 gb18030（gb2312 的超集）。
"""

import html
import re
import sys
from pathlib import Path

# 这些标签里的内容不是正文，整块丢掉
DROP_BLOCKS = re.compile(
    r"<(script|style|noscript)\b[^>]*>.*?</\1>", re.I | re.S
)

# 换行类标签：先把它们变成真正的换行，再统一处理
LINE_TAGS = re.compile(r"</?(p|div|br|tr|li|h[1-6]|table)\b[^>]*>", re.I)

# 其余标签直接去掉
ANY_TAG = re.compile(r"<[^>]+>")


def read_text(path: Path, encoding: str | None) -> tuple[str, str]:
    """返回 (文本, 实际使用的编码)。"""
    raw = path.read_bytes()
    encodings = [encoding] if encoding else ["utf-8-sig", "utf-8", "gb18030"]
    for enc in encodings:
        try:
            return raw.decode(enc), enc
        except (UnicodeDecodeError, LookupError):
            continue
    # 最后兜底：忽略错误，但要把这件事说出来，不能默默降级
    return raw.decode("utf-8", errors="replace"), "utf-8(replace)"


def to_text(source: str) -> str:
    source = DROP_BLOCKS.sub("\n", source)
    source = LINE_TAGS.sub("\n", source)
    source = ANY_TAG.sub("", source)
    source = html.unescape(source)

    lines = []
    for line in source.splitlines():
        # 政府网页里全角空格和普通空格都常见，统一去掉首尾
        cleaned = line.replace("　", " ").strip()
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines)


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2

    src = Path(sys.argv[1])
    dst = Path(sys.argv[2])
    encoding = sys.argv[3] if len(sys.argv) > 3 else None

    if not src.is_file():
        print(f"输入文件不存在: {src}")
        return 1

    raw, used = read_text(src, encoding)
    text = to_text(raw)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(text + "\n", encoding="utf-8")

    print(f"{src.name} -> {dst.name} | 编码={used} | {len(text.splitlines())} 行 / {len(text)} 字")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
