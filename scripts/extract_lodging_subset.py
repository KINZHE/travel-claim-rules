"""从财政部明细表里抽出本项目选定的地区子集，输出干净的表。

原始文件：sources/2016-71/lodging-standards.xls
**只读 Sheet1**（正式发布的明细表）。Sheet3 是征求意见稿，不得用于取数，
原因见 sources/SOURCES.md 与 docs/ambiguity-log.md 的 A-001。

表格结构（Sheet1）：
    第 5-7 行是表头（多层合并单元格），第 8 行起是数据
    列：序号 | 地区 | 子区域 | 部级 | 司局级 | 其他人员 | 旺季地区 | 旺季期间 | 旺季部级 | 旺季司局级 | 旺季其他
    合并单元格导致「序号」和「地区」只出现在每个省的第一行，需要向下填充

用法：
    python scripts/extract_lodging_subset.py <输入.xls> <输出目录>
"""

import sys
from pathlib import Path

import pandas as pd

# 选定的地区子集。明细表覆盖 36 个地区，本项目只取这 6 个，
# 理由是控制规则规模，同时保留三类不同结构：
#   单行地区（北京、上海）——最简单
#   多子区域地区（江苏、广东）——含「其他地区」兜底行
#   细分子区域地区（四川 7 行）——兜底行是完整性的关键
#   含旺季上浮地区（黑龙江）——唯一的例外场景来源
SUBSET_REGIONS = ["北京", "上海", "江苏", "广东", "四川", "黑龙江"]

DATA_START_ROW = 8  # 第 8 行（0 基）起是数据

COLUMNS = {
    "region": 1,        # 地区
    "sub_area": 2,      # 子区域
    "cap_bu": 3,        # 部级
    "cap_si": 4,        # 司局级
    "cap_other": 5,     # 其他人员
    "peak_area": 6,     # 旺季地区
    "peak_period": 7,   # 旺季期间
    "peak_bu": 8,       # 旺季上浮价 部级
    "peak_si": 9,       # 旺季上浮价 司局级
    "peak_other": 10,   # 旺季上浮价 其他人员
}


def cell(v) -> str:
    """单元格转字符串，NaN 转空串，并清掉换行和多余空白。"""
    if pd.isna(v):
        return ""
    return " ".join(str(v).split())


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2

    src = Path(sys.argv[1])
    out_dir = Path(sys.argv[2])
    out_dir.mkdir(parents=True, exist_ok=True)

    # 只读 Sheet1 —— 这一行是有意的，别改成读全部工作表
    df = pd.read_excel(src, sheet_name="Sheet1", header=None)

    # 校验：确认没有误读调查表。官方附件里混有「修改建议调查表」（见 ambiguity-log A-001），
    # 它的标题和正式表很像，肉眼容易看错，所以让脚本自己拦住。
    check = pd.read_excel(src, sheet_name="Sheet1", header=None, nrows=3)
    title = " ".join(str(v) for v in check.values.ravel() if not pd.isna(v))
    if "建议" in title or "调查表" in title:
        print(f"错误：读到的是调查表而非正式标准表，标题为「{title[:60]}」")
        print("      正式表标题应含「住宿费标准明细表」或「住宿费标准调整表」")
        return 1

    rows = []
    current_region = ""
    for i in range(DATA_START_ROW, len(df)):
        raw_region = cell(df.iat[i, COLUMNS["region"]])
        if raw_region:
            current_region = raw_region
        if not current_region:
            continue

        sub_area = cell(df.iat[i, COLUMNS["sub_area"]])
        cap_bu = cell(df.iat[i, COLUMNS["cap_bu"]])
        # 子区域和三个限额都空的行是合并单元格的空白行，跳过
        if not sub_area and not cap_bu:
            continue

        rows.append(
            {
                "region": current_region,
                "sub_area": sub_area,
                "cap_bu": cap_bu,
                "cap_si": cell(df.iat[i, COLUMNS["cap_si"]]),
                "cap_other": cell(df.iat[i, COLUMNS["cap_other"]]),
                "peak_area": cell(df.iat[i, COLUMNS["peak_area"]]),
                "peak_period": cell(df.iat[i, COLUMNS["peak_period"]]),
                "peak_bu": cell(df.iat[i, COLUMNS["peak_bu"]]),
                "peak_si": cell(df.iat[i, COLUMNS["peak_si"]]),
                "peak_other": cell(df.iat[i, COLUMNS["peak_other"]]),
            }
        )

    full = pd.DataFrame(rows)
    subset = full[full["region"].isin(SUBSET_REGIONS)].reset_index(drop=True)

    # 校验：选中的地区是不是都在表里找到了
    found = set(subset["region"])
    missing = [r for r in SUBSET_REGIONS if r not in found]
    if missing:
        print(f"警告：以下地区未在表中找到 -> {missing}")

    subset.to_csv(out_dir / "lodging-subset.csv", index=False, encoding="utf-8-sig")

    # 同时输出一份 Markdown，方便直接看和写规则时核对
    lines = [
        "# 住宿费标准子集（取自财行〔2016〕71 号明细表 Sheet1）",
        "",
        f"子集地区：{'、'.join(SUBSET_REGIONS)}（共 {len(subset)} 行）",
        f"原表共 {len(full)} 行数据、覆盖 36 个地区。**本子集是截取，不是全量。**",
        "单位：元/人·天。",
        "",
        "| 地区 | 子区域 | 部级 | 司局级 | 其他人员 | 旺季地区 | 旺季期间 | 旺季部级 | 旺季司局级 | 旺季其他 |",
        "| --- | --- | ---: | ---: | ---: | --- | --- | ---: | ---: | ---: |",
    ]
    for _, r in subset.iterrows():
        lines.append(
            "| {region} | {sub_area} | {cap_bu} | {cap_si} | {cap_other} | "
            "{peak_area} | {peak_period} | {peak_bu} | {peak_si} | {peak_other} |".format(**r)
        )
    lines += [
        "",
        "> 空白表示该地区无旺季上浮标准。",
        "> 取数来源与排除说明见 `sources/SOURCES.md`。",
        "",
    ]
    (out_dir / "lodging-subset.md").write_text("\n".join(lines), encoding="utf-8")

    print(f"全表 {len(full)} 行 -> 子集 {len(subset)} 行")
    print(f"输出：{out_dir / 'lodging-subset.csv'}、{out_dir / 'lodging-subset.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
