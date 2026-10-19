"""空输入与残缺输入：系统绝不能凭空判「通过」。

这是整个项目最重要的一条不变量：
    缺数据时输出「信息不足」或「制度未覆盖」，都不是「通过」。
    一旦缺数据能变成通过，规则库的所有缺口都会被伪装成合规。
"""

from claimcheck.engine import run
from claimcheck.model import Claim, load_rules

from pathlib import Path

RULES = load_rules(Path(__file__).resolve().parent.parent / "rules")


def test_empty_claim_never_passes():
    result = run(Claim(raw={"claim_id": "EMPTY"}), RULES)
    passed = [r.rule_id for r in result.results if r.status == "通过"]
    assert not passed, f"空申请不应有任何规则判通过，实际有: {passed}"
    assert result.overall != "通过"


def test_claim_without_trip_date_produces_no_pass():
    result = run(Claim(raw={
        "claim_id": "NO-DATE",
        "applicant": {"person_level": "司局级", "unit_type": "中央单位"},
        "lodging": {"stay_occurred": True, "nights": [
            {"date": "2026-03-02", "amount": 100, "room_type": "标准间",
             "receipt_present": True, "payment_method": "公务卡"}]},
        "expense_lines": [],
        "host": {},
        "flags": {},
    }), RULES)
    assert result.overall != "通过"


def test_unknown_category_is_not_a_pass():
    """制度未覆盖的类别不能因为「没规则管」就判通过。"""
    result = run(Claim(raw={
        "claim_id": "UNKNOWN-CAT",
        "applicant": {"person_level": "司局级", "unit_type": "中央单位"},
        "trip": {"origin_city": "北京", "destination_province": "江苏",
                 "destination_city": "南京市", "departure_date": "2026-03-02",
                 "return_date": "2026-03-04", "approved": True},
        "lodging": {"stay_occurred": False, "nights": []},
        "expense_lines": [
            {"line_id": "L1", "category": "通讯费", "amount": 50, "receipt_present": True},
        ],
        "host": {"meal_arranged": False, "transport_provided": False,
                 "meeting_arranged_accommodation": False},
        "flags": {"unit_insurance_purchased": False},
    }), RULES)
    # 通讯费不属四个法定类别，没有任何规则去判它——整体不能是「通过」
    assert result.overall != "通过"
