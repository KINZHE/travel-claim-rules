"""把财政部原始明细表（.xls）导出成可读的文本和 CSV，便于核对与取数。

原始文件：sources/2016-71/lodging-standards.xls
（财行〔2016〕71 号附件，2016-04-13 生成，代码页 936）

用法：
    python scripts/dump_lodging_table.py <输入.xls> <输出目录>
"""

import sys
from pathlib import Path

import pandas as pd


def dump(xls_path: Path, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    book = pd.ExcelFile(xls_path)

    lines = [f"来源文件：{xls_path.name}", f"工作表：{', '.join(book.sheet_names)}", ""]

    for sheet in book.sheet_names:
        df = book.parse(sheet, header=None)
        if df.empty:
            continue

        # 每个工作表原样导出 CSV，别做任何清洗——
        # 清洗放在取数阶段做，存档阶段要保持原貌
        csv_path = out_dir / f"{sheet}.csv"
        df.to_csv(csv_path, index=False, header=False, encoding="utf-8-sig")

        lines.append("=" * 70)
        lines.append(f"工作表 {sheet}：{df.shape[0]} 行 × {df.shape[1]} 列")
        lines.append(f"已导出：{csv_path.name}")
        lines.append("=" * 70)
        for i, row in df.iterrows():
            cells = ["" if pd.isna(v) else str(v).strip() for v in row]
            # 去掉整行为空的
            if not any(cells):
                continue
            lines.append(f"[{i:>3}] " + " | ".join(cells))
        lines.append("")

    (out_dir / "_dump.txt").write_text("\n".join(lines), encoding="utf-8")
    print(f"导出完成：{out_dir}")


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    src = Path(sys.argv[1])
    if not src.is_file():
        print(f"文件不存在: {src}")
        return 1
    dump(src, Path(sys.argv[2]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
