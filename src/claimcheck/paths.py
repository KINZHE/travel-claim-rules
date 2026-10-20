"""项目根目录定位。

规则、语料、评测题都在仓库里，不在包内。所以「根目录在哪」必须能被找出来，
否则 `pip install .`（非可编辑安装）之后，包在 site-packages 里，
相对路径会指到一个没有 rules/ 的地方。

查找顺序：

    1. 环境变量 CLAIMCHECK_ROOT
    2. 从当前目录向上找，直到看见同时含有 rules/ 与 data/ 的目录
    3. 从包所在位置向上找（可编辑安装时就是仓库根）
    4. 兜底：当前目录

这样三种用法都能工作：仓库内直接跑、`pip install -e .`、从别的目录调用。
"""

from __future__ import annotations

import os
from pathlib import Path

_MARKERS = ("rules", "data")


def _looks_like_root(path: Path) -> bool:
    return all((path / marker).is_dir() for marker in _MARKERS)


def _search_up(start: Path) -> Path | None:
    for candidate in [start, *start.parents]:
        if _looks_like_root(candidate):
            return candidate
    return None


def find_root() -> Path:
    env = os.environ.get("CLAIMCHECK_ROOT")
    if env:
        return Path(env).expanduser().resolve()

    found = _search_up(Path.cwd())
    if found:
        return found.resolve()

    # 包位于 <root>/src/claimcheck/paths.py
    found = _search_up(Path(__file__).resolve().parent)
    if found:
        return found

    return Path.cwd().resolve()


ROOT = find_root()
