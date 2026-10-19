# 申请数据模型（claim schema）

规则库的输入。**所有规则的 `require` 字段必须出现在本文的字段表里**——这条由
`scripts/check_rules.py` 自动检查。

> 为什么要单独定义这个：规则字段名一旦漂移（`city` / `sub_area` / `destination_city` 混用），
> 引擎匹配失败时**不会报错，只会静默不命中**。这种故障最难查，所以字段名必须先钉死。

## 一、数据从哪来

本项目的申请数据**全部是构造的模拟数据**，不使用任何真实票据或个人报销记录。
`data/samples/` 下的样例文件用于演示与测试，文件中标注 `synthetic: true`。

## 二、整体结构

```json
{
  "synthetic": true,
  "claim_id": "SAMPLE-001",
  "applicant": { ... },
  "trip": { ... },
  "lodging": { ... },
  "expense_lines": [ ... ],
  "host": { ... },
  "flags": { ... }
}
```

## 三、字段表

### 3.1 申请人 `applicant`

| 字段 | 类型 | 必填 | 取值 | 缺失时的含义 |
| --- | --- | --- | --- | --- |
| `person_level` | 枚举 | 是 | `部级` / `司局级` / `其他人员` | 无法判断适用哪一档限额 → 信息不足 |
| `unit_type` | 枚举 | 是 | `中央单位` / `参照公务员法管理的事业单位` / `其他事业单位` | 无法判断是否强制适用本办法 → 信息不足 |

> **映射约定**：业务系统里的「处级」「科级」等，按 2013 版口径一律映射为 `其他人员`。
> 2013 版没有「处级以下」这个说法，级别枚举只有三档。见 `docs/rule-decisions.md` D-014。

### 3.2 行程 `trip`

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `origin_city` | 字符串 | 是 | 常驻地。用于判断是否「临时到常驻地以外地区」 |
| `destination_province` | 字符串 | 是 | 目的地省级行政区，与明细表「地区」列对齐 |
| `destination_city` | 字符串 | 是 | 目的地具体市/州，用于子区域匹配 |
| `departure_date` | 日期 `YYYY-MM-DD` | 是 | 出发日。**计入**出差天数（见下） |
| `return_date` | 日期 | 是 | 返程日。**计入**出差天数 |
| `approved` | 布尔 | 是 | 是否履行出差审批 |
| `trip_purpose_type` | 枚举 | 否 | `一般出差` / `学术会议` / `培训` / `其他`。缺省视为 `一般出差` |

**天数计算**：`travel_days = (return_date - departure_date).days + 1`，含两端。
这是**项目假定**，制度只写「自然（日历）天数」没有举例。见 `docs/ambiguity-log.md` A-005。

### 3.3 住宿 `lodging` —— 逐晚，不是总额

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `stay_occurred` | 布尔 | 是 | 是否实际发生了住宿。第二十五条的联动规则依赖它 |
| `nights` | 数组 | `stay_occurred=true` 时必填 | 每晚一条，按日期升序 |
| `nights[].date` | 日期 | 是 | 该晚的日期（入住日） |
| `nights[].amount` | 数值 | 是 | 该晚房费，元 |
| `nights[].room_type` | 枚举 | 否 | `普通套间` / `单间` / `标准间` / `其他` |
| `nights[].receipt_present` | 布尔 | 是 | 该晚是否有住宿费发票 |
| `nights[].payment_method` | 枚举 | 否 | `公务卡` / `其他` / `未结算` |

**为什么按逐晚而不是「总额 ÷ 晚数」**：

1. **旺季规则判不了**。哈尔滨 7–9 月才上浮，只知道「住了 3 晚」无法判断哪几晚落在旺季。
2. **均价会掩盖超标**。3 晚共 1200 元，均价 400；但可能是 500/500/200 —— 前两晚都超过 450 的限额，
   均价却「看起来合规」。

这条是 `docs/rule-decisions.md` D-004 的主要条目。

### 3.4 费用行 `expense_lines`

除住宿外的费用项。住宿单独建模（见 3.3），不在 `expense_lines` 里重复。

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `line_id` | 字符串 | 是 | 行标识，用于结果回溯 |
| `category` | 枚举 | 是 | `城市间交通费` / `伙食补助费` / `市内交通费` / `其他` |
| `fee_type` | 枚举 | 否 | 见下表，缺省为该类别的主费用 |
| `amount` | 数值 | 是 | 金额，元 |
| `receipt_present` | 布尔 | 是 | 是否有票据 |
| `date` | 日期 | 否 | 费用发生日 |
| `transport_type` | 枚举 | `category=城市间交通费` 时必填 | `火车` / `轮船` / `飞机` / `其他交通工具` |
| `transport_class` | 字符串 | 同上 | 席别/舱位原文，如 `高铁二等座`、`经济舱`、`硬卧` |

`fee_type` 取值：

| category | fee_type |
| --- | --- |
| `城市间交通费` | `客票` / `订票费` / `签转费` / `退票费` / `民航发展基金` / `燃油附加费` / `交通意外保险` |
| `伙食补助费` | `包干` |
| `市内交通费` | `包干` / `接待单位交费` |

> **`category` 是闭集，`其他` 是兜底。** 出现 `其他` 时，说明该笔支出落不进制度定义的四个法定类别，
> 规则无法判断它能不能报——输出「信息不足」，不是「不通过」。见 D-012。

### 3.5 接待与会议 `host`

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `meal_arranged` | 布尔 | 是 | 接待单位是否统一安排用餐 |
| `meal_place_type` | 枚举 | `meal_arranged=true` 时必填 | `单位内部食堂` / `宾馆饭店` |
| `meal_place_has_standard` | 布尔 | `meal_place_type=单位内部食堂` 时必填 | 食堂是否有对外收费标准 |
| `meal_place_standard` | 数值 | `meal_place_has_standard=true` 时必填 | 对外收费标准（元/人·天） |
| `meal_fee_paid` | 数值 | `meal_arranged=true` 时必填 | 实际向接待单位交纳的伙食费 |
| `transport_provided` | 布尔 | 是 | 接待单位是否提供交通工具 |
| `transport_has_standard` | 布尔 | `transport_provided=true` 时必填 | 是否有收费标准 |
| `transport_standard_rate` | 数值 | `transport_has_standard=true` 时必填 | 收费标准（元/半天） |
| `transport_half_days` | 数值 | `transport_provided=true` 时必填 | 提供的半天数 |
| `transport_fee_paid` | 数值 | `transport_provided=true` 时必填 | 实际向接待单位交纳的交通费 |
| `meeting_arranged_accommodation` | 布尔 | 是 | 会议/培训举办方是否统一安排食宿 |

### 3.6 其他标志 `flags`

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `unit_insurance_purchased` | 布尔 | 否 | 单位是否已统一购买交通意外保险。**缺省按「未知」处理，判信息不足** |

## 四、缺失与类型错误的统一处理

| 情况 | 输出 |
| --- | --- |
| 字段不存在 | 该规则 `on_missing` → `信息不足` |
| 字段为 `null` | 同上 |
| 类型不对（如金额是字符串） | 视为缺失，并在结果里记 `data_error` 标记 |
| 枚举值不在允许集合内 | 视为缺失，并记 `data_error` |

**没有「默认值」这回事**。缺了就判信息不足，不许猜。

## 五、样例

| 文件 | 场景 | 预期 |
| --- | --- | --- |
| `data/samples/sample-001-compliant.json` | 南京出差 3 天，司局级，各项合规 | 全部通过 |
| `data/samples/sample-002-over-limit.json` | 南京 2 晚，一晚超限额 | 住宿费不通过 |
| `data/samples/sample-003-missing-info.json` | 缺住宿发票、缺级别 | 信息不足 + 联动不通过 |
| `data/samples/sample-004-peak-season.json` | 哈尔滨 8 月出差 | 用旺季限额判断 |
