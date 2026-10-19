# SQLite 结果库

引擎的每次运行都可以写入 SQLite，供后续统计与对比使用。

**为什么第 2 周就落库**：第 3 周的覆盖率统计、第 5 周的评测都要查历史结果。
每次重跑引擎再算，既慢又不可比——两次跑之间规则可能已经改过了。
落库之后，「同一份申请在新旧规则下哪里不同」就是一条 JOIN。

## 表结构

```sql
CREATE TABLE runs (
    run_id       TEXT PRIMARY KEY,   -- 12 位随机串
    claim_id     TEXT NOT NULL,
    trip_date    TEXT,               -- 判断基准日（可能是 --as-of 指定的）
    rules_digest TEXT,               -- 规则文件指纹，回答「这次跑的是哪一版规则」
    created_at   TEXT NOT NULL       -- UTC ISO 时间
);

CREATE TABLE results (
    run_id       TEXT NOT NULL,
    check_item   TEXT NOT NULL,      -- 检查项 ID，如 R-LODGING-JS-CORE::base::司局级
    rule_id      TEXT NOT NULL,      -- 见下方说明：可能是「未覆盖」哨兵值
    topic        TEXT,
    category     TEXT,               -- 归属费用项；NULL 表示整单级
    status       TEXT NOT NULL,      -- 通过 / 不通过 / 信息不足 / 需人工确认 / 制度未覆盖 / 超支自理
    observed     TEXT,               -- JSON：实际值、限额、违规明细
    message      TEXT,
    PRIMARY KEY (run_id, check_item)
);

-- 规则清单单独一张表。有它才能问「哪些规则从未被触发过」——
-- 只用 results 表回答不了这个问题，因为没触发过的规则在里面根本没有行。
CREATE TABLE rules (
    rule_id TEXT PRIMARY KEY,
    topic   TEXT,
    kind    TEXT,
    file    TEXT
);
```

### `rule_id` 的哨兵值

当一份申请里出现规则库**完全没有对应规则**的费用类别时，引擎会补一条结果，
`rule_id` 记为 `<未覆盖>`。它不是一条规则，只是「这里没有规则」这个事实的记录。

做规则级统计时要排除它，例如查「每条规则触发次数」时加 `AND rule_id <> '<未覆盖>'`；
查「从未被触发的规则」不受影响——那条查询以 `rules` 表为左表，哨兵不会出现在结果里。

两张索引：`results(rule_id)`、`runs(claim_id)`。

## 设计取舍

**没做 ORM、没做迁移框架。** 这个项目的数据量是几百行，标准库 `sqlite3` 足够。
加一层 ORM 会让人以为这个项目很复杂，实际上只是多了一层要维护的东西。

**`observed` 存 JSON 文本而不是拆成列。** 不同检查类型的观测内容不一样
（阈值类是金额，等级类是 rank，合计类是总和与期望值），拆成固定列会有大量空值。

**`check_item` 而不是 `rule_id` 做联合主键的一半。** 一条规则可能展开成多个检查项，
按规则存汇总数会掩盖单项缺失（D-006）。

## 用法

```bash
# 跑一份申请并写入
python -m claimcheck check data/samples/sample-002-over-limit.json --db runs.db --save

# 批量写入（脚本里循环调用 store.save 即可）
```

## 已知限制

- 同一份申请在不同基准日（`--as-of`）跑出来的 run 会分别记录，`runs.trip_date` 即当时的基准日。
  对比新旧版本时要按 run 区分，不能按 claim 合并。
- 没有记录「规则文件的具体版本」，只有一个 16 位摘要。要做到可复现，应当同时在 git 里
  保留规则文件的提交；摘要只是用来发现「两次跑的规则不一样」。
