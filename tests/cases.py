"""测试用例登记表（实际定义在 src/claimcheck/cases.py）。

放在包里而不是 tests/ 下，是因为**覆盖率统计要用同一份用例**：
诊断层声称「检查项 X 从未被任何测试触发」时，依据的必须就是真正跑的这套用例，
否则两边口径不一，数字没有意义。
"""

from claimcheck.cases import CASES, REGION_CASES, base_claim, over_limit  # noqa: F401

__all__ = ["CASES", "REGION_CASES", "base_claim", "over_limit"]
