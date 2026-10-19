# 规则 schema

规则用 YAML 存。**这里是阈值的唯一来源**——所有文档只引用规则编号，不复述数字（D-016）。

配套文档：

| 文档 | 内容 |
| --- | --- |
| `docs/claim-schema.md` | 规则吃什么数据（字段名、类型、缺失语义） |
| `docs/rule-semantics.md` | 规则怎么匹配、怎么展开成检查项、怎么合并结果 |
| `docs/rule-decisions.md` | 每一处取舍的理由与面试讲法 |

> **写规则前先读这三份。** schema 只回答「一条规则长什么样」，另外三份回答「怎么用」。

## 一、规则文件组织

一个主题一个文件：

| 文件 | 主题 | 规则数 |
| --- | --- | ---: |
| `lodging.yaml` | 住宿费限额（6 地区 15 行） | 15 |
| `lodging-other.yaml` | 房型、无发票联动、公务卡 | 4 |
| `transport.yaml` | 交通等级、民航基金、保险 | 3 |
| `meals.yaml` | 伙食补助费 | 2 |
| `local-transport.yaml` | 市内交通费 | 3 |
| `reimburse.yaml` | 准入与凭证 | 4 |

## 二、字段定义

### 2.1 标识与溯源

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `id` | 是 | 规则编号，`R-<主题>-<地区或条款>-<序号>`，全大写 |
| `topic` | 是 | 主题分类，用于分组统计 |
| `source.doc` / `doc_no` / `article` | 是 | 文件标题、文号、条款号 |
| `source.table_ref` | 否 | 阈值来自附表时，指向具体表与行 |
| `source.url` | 是 | 依据链接 |
| `source.tier` | 是 | **来源层级**：`A` 官网原件 / `B` 政府转载 / `C` 商业法规库。见 `sources/SOURCES.md` |
| `supplementary_source` | 否 | 配套文件（如 104 号补充 531 号的执行口径） |
| `decision` | 是 | 指向 `docs/rule-decisions.md` 的编号，说明这条规则的关键取舍 |

**`source.tier` 是硬要求**：把 B/C 层级的数标成 A 层级，是数据来源上的虚假陈述（D-010）。

### 2.2 版本

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `version` | 是 | 规则所依据的版本标识，如 `2013-531` |
| `effective_from` / `effective_to` | 是 | **条款**的生效区间，`null` 表示现行 |
| `threshold_from` / `threshold_to` | 否 | **阈值**的生效区间，缺省时等于 `effective_from` |

条款日和阈值日经常不是同一天（D-003）。住宿费的条款 2014-01-01 生效，
但阈值来自 71 号明细表，2016-05-01 才执行。

### 2.3 适用范围

| 字段 | 说明 |
| --- | --- |
| `scope.province` | 省级行政区，与明细表「地区」列一致 |
| `scope.cities` | 显式城市列表（枚举语义） |
| `scope.fallback` | `true` 表示「本省未列出的其他市县」（兜底语义） |
| `scope.whole_region` | `true` 表示匹配全省/全市 |
| `scope.person_level` | 可选，缺省表示三档都适用 |

三者互斥与优先级见 `docs/rule-semantics.md` 第一节。**不要把「全市」写成城市名**（D-005）。

### 2.4 判断逻辑

| 字段 | 说明 |
| --- | --- |
| `when` | 什么条件下规则适用（`category`、`fee_type`） |
| `require` | 需要哪些输入字段；缺了判「信息不足」，**没有默认值** |
| `assert` | 判断本身：单值比较 / 条件表达式 / 矩阵 / 分段规则 |
| `limits` | 限额类专用：`kind: base` 与 `kind: peak` 两种 |
| `on_pass` / `on_fail` / `on_missing` | 三种基本结果 |
| `on_conflict` | 与 `conflict_with` 配对，命中冲突时输出 `需人工确认` |
| `notes` | 这条规则的说明与已知边界 |

### 2.5 五种结果

| 取值 | 含义 |
| --- | --- |
| `通过` | 判定完成且未违反 |
| `不通过` | 判定完成且违反 |
| `信息不足` | 输入缺失，或没有可用阈值 |
| `需人工确认` | 命中例外、跨版本或规则冲突 |
| `制度未覆盖` | 无规则命中，或制度确无规定 |

**`制度未覆盖` 与 `通过` 必须严格区分**——否则规则库的缺口会被伪装成合规（D-015）。

## 三、两条硬约束

**1. 数字只存一处。** 阈值只出现在 YAML 里，文档只引 `R-xxx` 编号（D-016）。

**2. 不要把「需人工」写成判定规则。** 制度里「原则上」「适当」「视情况」进不了 `assert`，
应当走 `需人工确认` 分支，并记进 `docs/ambiguity-log.md`（D-009）。

## 四、完整范例

```yaml
- id: R-LODGING-JS-CORE
  topic: 住宿费
  source:
    doc: 中央和国家机关差旅费管理办法
    doc_no: 财行〔2013〕531号
    article: 第十四条
    table_ref: 财行〔2016〕71号明细表 · 江苏省第1行（南京市、苏州市、无锡市、常州市、镇江市）
    url: https://www.gov.cn/gongbao/content/2014/content_2667621.htm
    tier: A
  version: "2013-531"
  effective_from: "2014-01-01"
  effective_to: null
  threshold_from: "2016-05-01"
  threshold_to: null
  scope:
    province: 江苏
    cities: [南京市, 苏州市, 无锡市, 常州市, 镇江市]
  when:
    category: 住宿费
  require: [lodging.nights, trip.destination_city, applicant.person_level]
  limits:
    - kind: base
      by_person_level:
        部级: 900
        司局级: 490
        其他人员: 380
  on_pass: 通过
  on_fail: 不通过
  on_missing: 信息不足
  notes: 重点城市组。与 R-LODGING-JS-OTHER 构成「枚举 + 兜底」一对。
  decision: D-005
```

## 五、写规则时的自查

每条写完问四句，答不上来说明还没写完：

1. **从哪来**——文号 + 条款号（或附表的具体行）能指出来吗？来源层级是 A 还是 B/C？
2. **何时有效**——条款生效日和阈值生效日分别是哪天？两者相同吗？
3. **适用于谁**——省份、城市、级别、费用类别都界定清楚了吗？
4. **为什么这样写**——`decision` 指向哪一条取舍？那条能回答「为什么不换个做法」吗？

写完跑一遍：

```bash
python scripts/check_rules.py
```
