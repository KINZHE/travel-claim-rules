"""把运行结果写进 SQLite。

为什么第 2 周就落库：第 3 周的覆盖率统计、第 5 周的评测都要查历史结果。
每次都重跑引擎再算，既慢又不可比——两次跑之间规则可能已经改过了。
落库之后，「同一份申请在新旧规则下哪里不同」就是一条 JOIN。

刻意不做：ORM、迁移框架、连接池。这个项目的数据量是几百行，标准库 sqlite3 足够。
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .engine import EngineResult

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id       TEXT PRIMARY KEY,
    claim_id     TEXT NOT NULL,
    trip_date    TEXT,
    rules_digest TEXT,
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS results (
    run_id       TEXT NOT NULL,
    check_item   TEXT NOT NULL,
    rule_id      TEXT NOT NULL,
    topic        TEXT,
    category     TEXT,
    status       TEXT NOT NULL,
    observed     TEXT,
    message      TEXT,
    PRIMARY KEY (run_id, check_item)
);

-- 规则清单单独一张表。有它才能问「哪些规则从未被触发过」——
-- 只用 results 表回答不了这个问题，因为没触发过的规则在里面根本没有行。
CREATE TABLE IF NOT EXISTS rules (
    rule_id TEXT PRIMARY KEY,
    topic   TEXT,
    kind    TEXT,
    file    TEXT
);

CREATE INDEX IF NOT EXISTS idx_results_rule ON results (rule_id);
CREATE INDEX IF NOT EXISTS idx_runs_claim   ON runs (claim_id);
"""


def connect(db_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def register_rules(conn: sqlite3.Connection, rules) -> None:
    """登记规则清单（幂等）。每次保存前调用一次即可。"""
    with conn:
        conn.executemany(
            "INSERT INTO rules (rule_id, topic, kind, file) VALUES (?,?,?,?)"
            " ON CONFLICT(rule_id) DO UPDATE SET topic=excluded.topic, kind=excluded.kind, file=excluded.file",
            [(r.id, r.topic, r.kind, r.file) for r in rules],
        )


def save(conn: sqlite3.Connection, result: EngineResult, rules_digest: str = "") -> str:
    """写入一次运行，返回 run_id。"""
    run_id = uuid.uuid4().hex[:12]
    created = datetime.now(timezone.utc).isoformat(timespec="seconds")

    with conn:
        conn.execute(
            "INSERT INTO runs (run_id, claim_id, trip_date, rules_digest, created_at) VALUES (?,?,?,?,?)",
            (run_id, result.claim_id, result.trip_date, rules_digest, created),
        )
        conn.executemany(
            "INSERT INTO results (run_id, check_item, rule_id, topic, category, status, observed, message)"
            " VALUES (?,?,?,?,?,?,?,?)",
            [
                (
                    run_id,
                    r.check_item,
                    r.rule_id,
                    r.topic,
                    r.scope_category,
                    r.status,
                    json.dumps(r.observed, ensure_ascii=False) if r.observed else None,
                    r.message,
                )
                for r in result.results
            ],
        )
    return run_id


def summary(conn: sqlite3.Connection) -> dict:
    """几行概览，给命令行用。"""
    runs = conn.execute("SELECT COUNT(*) AS n FROM runs").fetchone()["n"]
    rows = conn.execute(
        "SELECT status, COUNT(*) AS n FROM results GROUP BY status ORDER BY n DESC"
    ).fetchall()
    return {"runs": runs, "status": {r["status"]: r["n"] for r in rows}}
