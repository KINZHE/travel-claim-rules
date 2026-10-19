# travel-claim-rules

从公开制度提取差旅报销规则，并对规则库自身做质量诊断。

> **状态：在建。** 目前处于第 1 周（制度来源落地与规则提取），规则引擎、诊断、问答尚未开始。
> 本 README 里凡属计划中的内容都标注为「计划」，不要把计划读成已有功能。

## 这个项目做什么

三件事，共用一套元数据（文号、条款号、版本、生效日期）：

1. **跑规则**：输入一条差旅报销申请，输出逐项检查结果与整体结论，每项都能指回具体条款与版本
2. **诊断规则库自己**：找出缺口、冲突、死规则，以及哪些规则从未被测试触发
3. **可核查的问答**（计划）：就制度提问，回答必须带引用；引用须支持结论；无依据时拒答

第 2 件是重点。规则库的覆盖度分析在开源里基本是空白——业务规则引擎侧（Drools/KIE）和
访问控制侧（XACML policy coverage）各有成熟做法，但业务规则 + Python + 开源这个组合上没有
现成实现，商业侧的 Pega、Corticon 有但不开源。所以这部分自己写。

## 制度来源

规则依据以下公开文件，原文与附件存档在 `sources/`：

| 文号 | 名称 | 执行日期 | 在项目里的角色 |
| --- | --- | --- | --- |
| 财行〔2013〕531 号 | 中央和国家机关差旅费管理办法 | 2014-01-01 | **规则主体**（八章三十三条） |
| 财行〔2006〕313 号 | 中央国家机关和事业单位差旅费管理办法 | 2007-01-01（2014-01-01 废止） | 版本对比与冲突演示 |
| 财行〔2015〕497 号 | 关于调整中央和国家机关差旅住宿费标准等有关问题的通知 | 2016-01-01 | 住宿费标准的一次调整；含旺季上浮比例 |
| 财行〔2016〕71 号 | 赴地方差旅住宿费标准明细表 | 2016-05-01 | **住宿费阈值的依据**（含原始 xls 附件） |
| 财办行〔2019〕104 号 | 关于规范差旅伙食费和市内交通费收交管理有关事项的通知 | 2019-08-01 | 把「向接待单位交费」变成可计算的百分比 |

来源 URL、抓取日期、**来源层级**与校验值见 [`sources/SOURCES.md`](sources/SOURCES.md)。

### 两处已知的边界

1. **531 号在 gov.cn 的公报文本里，附表标注为「略，详情请登录财政部网站」**——
   住宿费与伙食补助费的阈值不在正文中。住宿费已由 2016 年原始附件解决；
   **伙食补助费标准表尚未取得 A 层级原件**，目前只有 B/C 层级转载的数值
   （一般地区 100 元/人·天，西藏、青海、新疆 120 元）。抄录时须标注来源层级。
2. **规则覆盖的是明细表的 6 个地区子集，不是全部 36 个地区**，见 `data/lodging-subset.md`。

## 目录

```
travel-claim-rules/
├── sources/          制度原文存档 + 来源登记（SOURCES.md）
├── docs/             条款清单、版本沿革、来源映射、歧义记录、规则语义、取舍记录、SQL 查询
├── rules/            规则库（YAML），数字的唯一来源
│   └── SCHEMA.md     字段定义、五种检查类型、填写范例
├── src/claimcheck/   引擎：模型、谓词、检查、匹配、编排、落库、命令行
├── data/             干净数据集 + 模拟申请样例 + 手工推导的预期结果
├── tests/            谓词、匹配、样例对账、规则契约、空输入
├── scripts/          来源处理与规则自检
└── environment.yml   环境定义
```

## 脚本

```bash
conda activate travel-claim-rules

# --- 来源处理 ---
python scripts/extract_text.py <输入.html> <输出.txt>          # 网页转纯文本
python scripts/dump_lodging_table.py sources/2016-71/lodging-standards.xls sources/2016-71/extracted/
python scripts/extract_lodging_subset.py sources/2016-71/lodging-standards.xls data/

# --- 规则库维护 ---
python scripts/check_rules.py            # 自检：字段、条款、阈值三条硬断言
python scripts/gen_source_mapping.py     # 生成 docs/source-mapping.md（生成物，不要手改）

# --- 引擎 ---
PYTHONPATH=src python -m claimcheck check data/samples/sample-002-over-limit.json
PYTHONPATH=src python -m claimcheck check <claim.json> --as-of 2013-06-01   # 按历史版本判断
PYTHONPATH=src python -m claimcheck check <claim.json> --db runs.db --save  # 结果写库
PYTHONPATH=src python -m claimcheck items                                   # 列出全部检查项

# --- 规则库诊断 ---
PYTHONPATH=src python -m claimcheck diagnose                               # 打印报告
PYTHONPATH=src python -m claimcheck diagnose --out docs/diagnosis-report.md
PYTHONPATH=src python -m claimcheck diagnose --strict                      # 有问题则退出码非零，可作 CI 门禁

# --- 制度问答 ---
PYTHONPATH=src python -m claimcheck ask "南京出差，司局级，住宿最多能报多少？"
PYTHONPATH=src python -m claimcheck ask "报销需要哪些凭证？"
PYTHONPATH=src python -m claimcheck ask "市内交通费怎么算" --as-of 2013-06-01   # 按历史版本回答
PYTHONPATH=src python -m claimcheck ask "..." --context full                    # 全量条文上下文
PYTHONPATH=src python -m claimcheck ask "..." --json                            # 机器可读输出

# --- 测试 ---
PYTHONPATH=src python -m pytest -q
```

> Windows 下如果直接 `python -m claimcheck` 找不到包，用 `PYTHONPATH=src` 前缀，
> 或者在项目根目录执行 `pip install -e .`。

## 环境

```bash
conda env create -f environment.yml     # 实例建在 C:\App\Miniconda\envs\travel-claim-rules
conda activate travel-claim-rules
```

## 明确不引入

这个清单和功能清单一样重要——它说明的是取舍，不是能力不足。

| 不引入 | 理由 |
| --- | --- |
| OPA / Rego | 需要工具链、bundle 签名、SBOM 整套供应链，对这个规模的项目是过度工程 |
| LangGraph / MCP | 本项目的流程是确定的几条线，不需要通用 agent 编排 |
| Langfuse / Phoenix 等观测平台 | 需要时用托管，不自建 |
| 前端框架 | 命令行足够，前端会吃掉时间且不产生区分度 |
| 现成的规则引擎库 | 它们解决「表达和执行规则」，本项目要解决「诊断规则库自身」，需要完全控制规则结构；引 DSL 只增加间接层 |
| 向量数据库 / GraphRAG | 先用关键词检索做基线，是否引入由实验数据决定，不由热度决定 |

## 边界说明

- **规则覆盖的是明细表的一个子集**，不是全部 36 个地区。范围声明见规则文件头。
- **住宿费规则只覆盖 2016-05-01 之后的申请**。2014–2015 年的标准附表未取得（原文标注「略」），
  该时段的申请判「信息不足」，不套用现行标准（D-013）。
- **伙食补助费的标准来自 B/C 层级来源**（地方政府网站、高校文件转载），不是财政部原件，
  规则元数据的 `source.tier` 已标明（D-010）。
- 诊断算法采用**有界字段域上的枚举**，字段一多就组合爆炸。工业界用 OBDD 或超矩形空间求差。
  适用边界写在 `docs/` 对应文档里。
- 申请数据全部为模拟数据，不使用任何真实票据或个人报销记录。

## 进展

- [x] 制度来源落地（2013 版、2006 版、2015 版附件、2016 明细表原件、2019 年 104 号）
- [x] 版本沿革梳理（6 个节点的链条 + **10 处版本差异**，逐条指到两版条款号）
- [x] 规则 schema 设计（已定：一条规则带按级别索引的限额表）
- [x] 歧义记录起步（12 条，含一处**真实的规则冲突**：531 号第十三条 vs 497 号第三条的房型规定）
- [x] 条款分类（33 条全部完成：可判断 20 / 需人工 5 / 无法结构化 8）
- [x] 输入数据模型（`docs/claim-schema.md` + 4 个模拟样例）
- [x] 规则匹配与展开语义（`docs/rule-semantics.md`）
- [x] **规则库第一版：31 条规则，约 60 个检查项**（住宿 19 / 交通 3 / 伙食 2 / 市内交通 3 / 报销准入 4）
- [x] 规则自检脚本（三条断言，已用探针验证确实会失败）
- [x] **第 2 周：规则语言改造 + 引擎 + 版本对比 + 结果落库**
  - 规则从自由文本条件改为结构化谓词，五类检查按 `kind` 分派（D-018、D-019）
  - 引擎 6 个模块 + 命令行；55 个测试全绿；4 个样例与手工推导逐条对上
  - 2006 版 3 条对比规则，用 `--as-of` 做跨版本复核
  - SQLite 结果库 + 8 条实际会用的查询
- [x] **第 3 周：规则库质量诊断**
  - 缺口、冲突（分两级）、无效规则、覆盖度四项检查
  - **两处真实冲突全部自动发现**：房型（确定）、会议市内交通（疑似）
  - 验证方式是把 `conflict_with` 全删掉再跑，仍能发现——不依赖人工标注
  - 覆盖率从 40% 提到 97%，剩余 3 个空洞有明确出处（G-005）
- [x] **第 4 周：制度问答与引用校验**
  - 三类问题三条路径：可计算（走规则引擎）/ 需读条文（检索 + 逐字引用）/ 制度未覆盖（拒答）
  - 语料按条切分，带版本元数据；检索用字符二元组 + BM25，版本过滤在检索阶段完成
  - 三层确定性校验：引用存在、数字可溯、断言有据
  - 抽取式生成器做基线；LLM 层只留接口与约束，未实现（没有 key，测不了就不写）
  - `--context retrieve|full` 两种上下文方式，供第 5 周对比
- [ ] 评测集与消融实验（第 5 周）

## 已知的真实冲突（诊断层的验收用例）

531 号第十三条规定「部级住普通套间，司局级及以下住单间或标准间」；
而 497 号第三条明确写「自行选择宾馆住宿（**不分房型**）」。两条都现行有效。

**诊断层已能自动报出这处冲突。** 验证方式是：把规则里所有 `conflict_with` 删掉再跑，
它仍然能发现——说明检测靠的是判断，不是复述人工标注。
完整结果见 [`docs/diagnosis-report.md`](docs/diagnosis-report.md)。
详见 [`docs/ambiguity-log.md`](docs/ambiguity-log.md) A-012。
