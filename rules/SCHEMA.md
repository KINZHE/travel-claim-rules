# 规则 schema

规则用 YAML 存。**这里是阈值的唯一来源**——所有文档只引规则编号，不复述数字。

> **状态：设计稿，等你确认后再开始写规则。** 下面「关键设计决策」一节有一个需要你拍板的问题，
> 它会影响你之后写的每一条规则，所以放在最前面。

## 一、需要你拍板：级别相关阈值怎么建模

住宿费限额是按「(子区域, 人员级别)」给出的。以江苏为例，源表一行是三个数：

| 子区域 | 部级 | 司局级 | 其他人员 |
| --- | --- | --- | --- |
| 南京市、苏州市、无锡市、常州市、镇江市 | 900 | 490 | 380 |

这就有两种建模方式：

### 做法 A：一条规则一个阈值

```yaml
- id: R-LODGING-JS-CORE-BU
  scope: {region: 江苏, sub_area: 宁苏锡常镇, person_level: 部级}
  assert: {field: amount_per_night, op: "<=", value: 900}
- id: R-LODGING-JS-CORE-SI
  scope: {region: 江苏, sub_area: 宁苏锡常镇, person_level: 司局级}
  assert: {field: amount_per_night, op: "<=", value: 490}
- id: R-LODGING-JS-CORE-OT
  scope: {region: 江苏, sub_area: 宁苏锡常镇, person_level: 其他人员}
  assert: {field: amount_per_night, op: "<=", value: 380}
```

**优点**：一条规则就是一个可判断、可测试、可统计覆盖的单位。规则命中率直接可用。
**缺点**：规则数按级别数翻三倍。光住宿费就要 45 条以上，写的时候同一个子区域的三条挤在一起，
改一个地区要同时改三处，很容易只改了两处。

### 做法 B：一条规则一张限额表

```yaml
- id: R-LODGING-JS-CORE
  scope: {region: 江苏, sub_area: 宁苏锡常镇}
  assert:
    field: amount_per_night
    op: "<="
    limit_by_person_level:
      部级: 900
      司局级: 490
      其他人员: 380
```

**优点**：规则数与源表行数一一对应（江苏 2 行 ↔ 2 条规则），核对容易，改一个地区只改一处。
**缺点**：一条规则内部包含了三个判断，如果直接用「规则命中率」统计覆盖，
**会出现「聚合掩盖单项缺失」**——规则整体命中率 100%，但「部级 900」这个阈值可能从没被任何测试用例覆盖过。
这正是要避免的那类假信号。

### ✅ 已定：做法 B + 「检查项」概念

**决定已下（2026-10-16）：采用做法 B。** 一条规则带一张按级别索引的限额表，
江苏写 1 条而不是 3 条。

为了让覆盖率统计不失真，在数据结构上把两个层次分开：

| 概念 | 定义 | 用途 |
| --- | --- | --- |
| **规则（rule）** | 一条制度规定，是**编写和溯源的单位** | 对应源表一行或一个条款 |
| **检查项（check item）** | 规则在某个输入维度上的一次具体判断，是**执行和统计覆盖的单位** | 由规则展开得到，如「江苏-宁苏锡常镇 × 部级」 |

展开后，江苏那一条规则产生 3 个检查项。**覆盖率按检查项统计，不按规则统计。**

这样既保留了 B 的编写效率，又避免了「规则命中率 100%、但某个阈值从没被测试过」这种假信号。
诊断层（第 3 周）需要实现这个展开——这本身就是诊断层要做的事之一，
也是它区别于普通规则引擎的地方。

## 二、元数据字段（每条规则必填）

这一层的存在意义：**没有它，规则库就只是一堆阈值，无法诊断、无法回溯、无法做版本对比。**

| 字段 | 含义 | 示例 |
| --- | --- | --- |
| `id` | 规则编号 | `R-LODGING-JS-CORE` |
| `source.doc` | 文件标题 | 中央和国家机关差旅费管理办法 |
| `source.doc_no` | 文号 | 财行〔2013〕531 号 |
| `source.article` | 条款号 | 第十四条 |
| `source.table_ref` | 若阈值来自附表，指向具体表与行 | 财行〔2016〕71 号明细表 · 江苏省第 1 行 |
| `source.url` | 依据链接 | 见 `sources/SOURCES.md` |
| `version` | 规则所依据的版本标识 | `2013-531` |
| `effective_from` | 生效日 | `2014-01-01` |
| `effective_to` | 失效日（现行填 `null`） | `null` |
| `scope` | 适用范围 | `{region: 江苏, sub_area: ..., person_level: [...]}` |
| `topic` | 主题分类，便于分组统计 | 住宿费 / 城市间交通费 / 报销管理 |

**编号规范**：`R-<主题>-<地区或条款>-<序号>`，全大写，用连字符。
主题用 `LODGING` / `INTERCITY` / `LOCAL` / `MEALS` / `REIMBURSE` / `BAN`。

## 三、判断逻辑字段

| 字段 | 含义 |
| --- | --- |
| `when` | 什么条件下这条规则适用（类别、是否触发） |
| `require` | 需要哪些输入字段存在；缺了就判「信息不足」，不能猜 |
| `assert` | 判断本身：字段、比较方式、阈值或限额表 |
| `on_pass` / `on_fail` / `on_missing` / `on_review` | 四种结果 |

`on_*` 的取值与输出模型的两层结构对应：

| 取值 | 含义 |
| --- | --- |
| `通过` | 条件满足 |
| `不通过` | 能确定违反，且能说清违反了哪条 |
| `信息不足` | 输入字段缺失，或制度表述本身不足以判断 |
| `需人工确认` | 命中例外条款、跨版本、或规则冲突 |

## 四、两个填写范例

### 范例 1：限额类（做法 B）

```yaml
- id: R-LODGING-JS-CORE
  topic: 住宿费
  source:
    doc: 中央和国家机关差旅费管理办法
    doc_no: 财行〔2013〕531号
    article: 第十四条
    table_ref: 财行〔2016〕71号明细表 · 江苏省第1行（南京市、苏州市、无锡市、常州市、镇江市）
    url: https://www.gov.cn/gongbao/content/2014/content_2667621.htm
  version: "2013-531"
  effective_from: "2014-01-01"
  effective_to: null
  scope:
    region: 江苏
    sub_area: 南京市、苏州市、无锡市、常州市、镇江市
    person_level: [部级, 司局级, 其他人员]
  when:
    category: 住宿费
  require: [amount_per_night, person_level, city]
  assert:
    field: amount_per_night
    op: "<="
    limit_by_person_level:
      部级: 900
      司局级: 490
      其他人员: 380
  on_pass: 通过
  on_fail: 不通过
  on_missing: 信息不足
  notes: 阈值取自 2016 年明细表；表内数值以「元/人·天」计，注意与总金额、天数区分。
```

### 范例 2：条件类（带例外分支）

```yaml
- id: R-REIMBURSE-NO-INVOICE
  topic: 报销管理
  source:
    doc: 中央和国家机关差旅费管理办法
    doc_no: 财行〔2013〕531号
    article: 第二十五条
    url: https://www.gov.cn/gongbao/content/2014/content_2667621.htm
  version: "2013-531"
  effective_from: "2014-01-01"
  effective_to: null
  scope: {}
  when:
    category: [住宿费, 城市间交通费, 伙食补助费, 市内交通费]
  require: [has_lodging_invoice, has_lodging_stay]
  assert:
    condition: "not (has_lodging_stay and not has_lodging_invoice)"
  on_pass: 通过
  on_fail: 不通过
  on_missing: 信息不足
  notes: >
    实际发生住宿而无住宿费发票的，不得报销住宿费以及城市间交通费、伙食补助费和市内交通费。
    这是一条联动规则——缺一张发票会同时影响四项费用，容易在规则库里写漏。
    2006 版对应条款只影响住宿费一项，两版的后果范围不同，属于版本差异。
```

## 五、写规则时的自查

每条写完问三句，答不上来就说明还没写完：

1. **从哪来**——能指出文号 + 条款号（或附表的具体行）吗？
2. **何时有效**——生效日期和失效日期明确吗？
3. **适用于谁**——地区、级别、费用类别都界定了吗？

另外：**不要把「需人工」的内容写成程序规则。** 制度里「原则上」「适当」「视情况」
这类表述进不了 `assert`，应当走 `需人工确认` 分支，并把理由记进 `docs/ambiguity-log.md`。
