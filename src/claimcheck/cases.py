"""测试用例登记表。

集中写在一处的原因：
    1. 「每条规则至少被一个用例触发」这个契约需要一个全局视图
    2. 第 3 周的覆盖率统计可以直接复用这张表，不用重跑引擎

每个用例是 (名称, 申请字典, 基准日 or None, 期望状态字典)。
期望只列非「通过」的项——「通过」是默认值，全列出来反而看不清重点。
"""

from __future__ import annotations

import copy
from typing import Any, Callable

from .universe import LEVELS


def base_claim(**over: Any) -> dict:
    """构造一份默认合规的申请，用关键字参数覆盖任意字段。

    默认值取南京、司局级、3 天、住宿 2 晚各 400 元——
    400 元低于本子集里所有司局级限额，所以地区覆盖用例只要换省份和城市就行。
    """
    claim = {
        "synthetic": True,
        "claim_id": over.pop("claim_id", "TEST"),
        "applicant": {
            "person_level": over.pop("person_level", "司局级"),
            "unit_type": over.pop("unit_type", "中央单位"),
        },
        "trip": {
            "origin_city": "北京",
            "destination_province": over.pop("province", "江苏"),
            "destination_city": over.pop("city", "南京市"),
            "departure_date": over.pop("depart", "2026-03-02"),
            "return_date": over.pop("ret", "2026-03-04"),
            "approved": True,
            "trip_purpose_type": "一般出差",
        },
        "lodging": {
            "stay_occurred": True,
            "nights": [
                {"date": over.pop("night1", "2026-03-02"), "amount": 400,
                 "room_type": "标准间", "receipt_present": True, "payment_method": "公务卡"},
                {"date": over.pop("night2", "2026-03-03"), "amount": 400,
                 "room_type": "标准间", "receipt_present": True, "payment_method": "公务卡"},
            ],
        },
        "expense_lines": over.pop("lines", [
            {"line_id": "L1", "category": "城市间交通费", "fee_type": "客票", "amount": 443.5,
             "date": "2026-03-02", "receipt_present": True,
             "transport_type": "火车", "transport_class": "二等座"},
            {"line_id": "L2", "category": "市内交通费", "fee_type": "包干", "amount": 240,
             "receipt_present": True},
            {"line_id": "L3", "category": "伙食补助费", "fee_type": "包干", "amount": 300,
             "receipt_present": True},
        ]),
        "host": {
            "meal_arranged": False,
            "meal_place_type": None,
            "meal_place_has_standard": None,
            "meal_place_standard": None,
            "meal_fee_paid": None,
            "transport_provided": False,
            "transport_has_standard": None,
            "transport_standard_rate": None,
            "transport_half_days": None,
            "transport_fee_paid": None,
            "meeting_arranged_accommodation": False,
        },
        "flags": {"unit_insurance_purchased": False},
    }
    return claim


# ---------------------------------------------------------------- 修改器

def over_limit(c: dict) -> dict:
    c["lodging"]["nights"][0]["amount"] = 520          # 江苏司局级限额 490
    return c


def missing_invoice(c: dict) -> dict:
    c["lodging"]["nights"][1]["receipt_present"] = False
    return c


def no_level(c: dict) -> dict:
    c["applicant"]["person_level"] = None
    return c


def host_meal_internal(c: dict) -> dict:
    """接待单位用内部食堂安排用餐，无对外标准，按日标准的 100% 交纳。"""
    c["host"].update({
        "meal_arranged": True,
        "meal_place_type": "单位内部食堂",
        "meal_place_has_standard": False,
        "meal_fee_paid": 100,
    })
    return c


def host_transport_free(c: dict) -> dict:
    """接待单位提供交通工具，无收费标准，按每半天 40 元交费。"""
    c["host"].update({
        "transport_provided": True,
        "transport_has_standard": False,
        "transport_half_days": 2,
        "transport_fee_paid": 80,
    })
    c["expense_lines"].append({
        "line_id": "L4", "category": "市内交通费", "fee_type": "接待单位交费",
        "amount": 80, "receipt_present": True,
    })
    return c


def with_insurance(c: dict) -> dict:
    c["expense_lines"].append({
        "line_id": "L5", "category": "城市间交通费", "fee_type": "交通意外保险",
        "amount": 30, "receipt_present": True,
    })
    return c


def meeting_arranged(c: dict) -> dict:
    c["trip"]["trip_purpose_type"] = "学术会议"
    c["host"]["meeting_arranged_accommodation"] = True
    return c


# ---------------------------------------------------------------- 用例

Case = tuple[str, Callable[[], dict], str | None, dict[str, str]]


def _c(**over) -> Callable[[], dict]:
    return lambda: base_claim(**copy.deepcopy(over))


def _m(fn, **over) -> Callable[[], dict]:
    return lambda: fn(base_claim(**copy.deepcopy(over)))


REGION_CASES = [
    ("北京", "北京市"), ("上海", "上海市"),
    ("江苏", "南京市"), ("江苏", "徐州市"),
    ("广东", "广州市"), ("广东", "汕头市"),
    ("四川", "成都市"), ("四川", "阿坝州"), ("四川", "绵阳市"),
    ("四川", "宜宾市"), ("四川", "凉山州"), ("四川", "德阳市"), ("四川", "自贡市"),
    ("黑龙江", "哈尔滨市"), ("黑龙江", "齐齐哈尔市"),
]

CASES: list[Case] = []

# 住宿费限额是按 (地区, 级别) 给的，只测司局级等于三条档位里只验了一条。
# 三档全测，每条住宿规则才会真正被覆盖到。
for _province, _city in REGION_CASES:
    for _level in LEVELS:
        CASES.append((
            f"{_province}-{_city}-{_level}",
            _c(province=_province, city=_city, person_level=_level,
               claim_id=f"T-{_province}-{_city}-{_level}"),
            None,
            {},
        ))

CASES += [
    ("合规", _c(claim_id="T-OK"), None, {}),
    ("首晚超限", _m(over_limit, claim_id="T-OVER"), None, {"R-LODGING-JS-CORE": "不通过"}),
    ("缺一晚发票", _m(missing_invoice, claim_id="T-NOINV"), None, {
        "R-LODGING-NO-INVOICE": "不通过",
        "R-REIMBURSE-VOUCHER": "不通过",
    }),
    ("级别未填", _m(no_level, claim_id="T-NOLEVEL"), None, {
        "R-LODGING-JS-CORE": "信息不足",
        "R-LODGING-ROOM-TYPE": "信息不足",
        "R-TRANSPORT-CLASS": "信息不足",
    }),
    ("内部食堂用餐", _m(host_meal_internal, claim_id="T-MEAL"), None, {}),
    ("接待方提供交通", _m(host_transport_free, claim_id="T-TRANS"), None, {}),
    ("购买交通意外险", _m(with_insurance, claim_id="T-INS"), None, {}),
    ("会议统一安排食宿", _m(meeting_arranged, claim_id="T-MEET"), None, {}),
    # 2006 版规则只在 2013 年及以前生效。用同一份申请、换个判断基准日即可覆盖。
    ("2006版基准", _c(claim_id="T-2006", depart="2013-06-03", ret="2013-06-05",
                    night1="2013-06-03", night2="2013-06-04"), "2013-06-01", {}),
]

# 旺季限额按级别各有一档，只测司局级等于旺季那一组格子只验了三分之一。
# 两个有旺季的地区 × 三档级别。
_PEAK_CASES = [
    ("哈尔滨旺季", "哈尔滨市", "2026-08-10"),
    ("黑龙江其他地区旺季", "牡丹江市", "2026-07-10"),
]
for _label, _city, _day in _PEAK_CASES:
    for _level in LEVELS:
        _ret = _day[:8] + f"{int(_day[8:]) + 2:02d}"
        CASES.append((
            f"{_label}-{_level}",
            _c(claim_id=f"T-PEAK-{_city}-{_level}", province="黑龙江", city=_city,
               person_level=_level, depart=_day, ret=_ret,
               night1=_day, night2=_day[:8] + f"{int(_day[8:]) + 1:02d}"),
            None,
            {},
        ))

# 交通等级表按 (级别, 交通工具) 展开成 12 格，只测火车等于只验了四分之一。
# 每种交通工具按各自的最低合规席别各测一遍。
_TRANSPORT_CASES = [
    ("火车", "硬座"),
    ("轮船", "三等舱"),
    ("飞机", "经济舱"),
    ("其他交通工具", "凭据报销"),
]
for _level in LEVELS:
    for _ttype, _tclass in _TRANSPORT_CASES:
        CASES.append((
            f"交通-{_level}-{_ttype}",
            lambda lv=_level, tt=_ttype, tc=_tclass: base_claim(
                claim_id=f"T-TR-{lv}-{tt}",
                person_level=lv,
                lines=[
                    {"line_id": "L1", "category": "城市间交通费", "fee_type": "客票",
                     "amount": 300, "date": "2026-03-02", "receipt_present": True,
                     "transport_type": tt, "transport_class": tc},
                    {"line_id": "L2", "category": "市内交通费", "fee_type": "包干",
                     "amount": 240, "receipt_present": True},
                    {"line_id": "L3", "category": "伙食补助费", "fee_type": "包干",
                     "amount": 300, "receipt_present": True},
                ],
            ),
            None,
            {},
        ))


def all_rule_ids_touched() -> set[str]:
    """由测试填充：每次运行后把实际出现的 rule_id 记进来。"""
    return set()
