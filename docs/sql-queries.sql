-- 结果库的常用查询
--
-- 每条查询都是实际会用到的，不是为练习造出来的。
-- 对应的表结构见 docs/sqlite-schema.md。
-- 用法：sqlite3 runs.db < docs/sql-queries.sql
-- 或者在 Python 里 conn.execute(语句)。


-- 1. 哪些规则从未被触发过
--    这是覆盖率空心化的直接证据。需要 rules 表配合，因为没触发过的规则
--    在 results 里根本没有行——只用 results 永远查不出来。
SELECT r.rule_id, r.topic, r.kind, r.file
FROM rules AS r
LEFT JOIN results AS x ON x.rule_id = r.rule_id
WHERE x.rule_id IS NULL
ORDER BY r.rule_id;


-- 2. 每条规则的触发次数与状态分布
--    次数少但一直在报错的规则，往往是最需要先人工确认的。
SELECT rule_id,
       COUNT(*) AS hits,
       SUM(status = '通过')     AS passed,
       SUM(status = '不通过')   AS failed,
       SUM(status = '信息不足') AS insufficient
FROM results
GROUP BY rule_id
ORDER BY hits DESC, rule_id;


-- 3. 同一份申请在两次运行（不同版本基准日）下的结论分布
--
--    注意：这里**不能**按 check_item 对比。两个版本的规则集不同，
--    2013 版有 R-LOCAL-ALLOWANCE、2006 版有 R-LOCAL-ALLOWANCE-2006，
--    同一个 check_item 不会同时出现在两次运行里——按 check_item JOIN 会得到 0 行。
--    正确的比法是按 run 聚合，看结论分布怎么变。
SELECT ra.claim_id,
       ra.trip_date AS basis,
       ra.run_id,
       r.status,
       COUNT(*) AS n
FROM runs AS ra
JOIN results AS r ON r.run_id = ra.run_id
WHERE ra.claim_id = 'SAMPLE-005'
GROUP BY ra.run_id, r.status
ORDER BY ra.trip_date, n DESC;


-- 4. 各状态占比
--    看整体健康度。注意「通过」占比高不代表没问题——
--    规则库可能只是覆盖得少（见查询 1）。
SELECT status,
       COUNT(*) AS n,
       ROUND(100.0 * COUNT(*) / (SELECT COUNT(*) FROM results), 1) AS pct
FROM results
GROUP BY status
ORDER BY n DESC;


-- 5. 按费用类别看结论
--    「哪个环节最容易出问题」比「总共多少不通过」更有用。
SELECT category,
       status,
       COUNT(*) AS n
FROM results
WHERE category IS NOT NULL
GROUP BY category, status
ORDER BY category, n DESC;


-- 6. 哪些规则产生了「信息不足」
--    信息不足不是噪声：它的每一条都指向一个具体的字段缺口或数据来源缺口。
SELECT rule_id, COUNT(*) AS n
FROM results
WHERE status = '信息不足'
GROUP BY rule_id
ORDER BY n DESC;


-- 7. 一次运行里，哪些规则判了不通过、原因是什么
--    排查具体单据时用。
SELECT r.rule_id, r.category, r.message, r.observed
FROM results AS r
JOIN runs AS u ON u.run_id = r.run_id
WHERE u.claim_id = 'SAMPLE-002'
  AND r.status = '不通过'
ORDER BY r.rule_id;


-- 8. 规则在最近几次运行中的稳定性
--    如果某条规则的结果在不同单据间剧烈变化，可能说明它的适用条件写得不够精确。
SELECT rule_id,
       COUNT(DISTINCT status) AS distinct_statuses,
       COUNT(*)               AS total_hits
FROM results
GROUP BY rule_id
HAVING COUNT(*) >= 2
ORDER BY distinct_statuses DESC, total_hits DESC;
