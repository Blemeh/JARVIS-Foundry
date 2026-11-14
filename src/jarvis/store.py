"""SQLite queue: plain tables, numbered migrations, one connection per thread."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path

QUEUED, PLANNING, AWAITING_APPROVAL, RUNNING = "QUEUED", "PLANNING", "AWAITING_APPROVAL", "RUNNING"
DONE, FAILED, REJECTED, STOPPED = "DONE", "FAILED", "REJECTED", "STOPPED"
CANCELLED = "CANCELLED"
ACTIVE = (PLANNING, RUNNING)
FINAL = (DONE, FAILED, REJECTED, CANCELLED)

MIGRATIONS = [
    """
    CREATE TABLE repos (name TEXT PRIMARY KEY, last_poll TEXT);
    CREATE TABLE tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        repo TEXT NOT NULL,
        title TEXT NOT NULL,
        body TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL,
        scope TEXT NOT NULL DEFAULT '[]',
        needs_plan INTEGER NOT NULL DEFAULT 0,
        plan TEXT,
        plan_approved INTEGER NOT NULL DEFAULT 0,
        issue INTEGER,
        branch TEXT,
        base_sha TEXT,
        worktree TEXT,
        pr_url TEXT,
        reason TEXT,
        repairs INTEGER NOT NULL DEFAULT 0,
        owner TEXT,
        created_at TEXT NOT NULL,
        started_at TEXT,
        finished_at TEXT
    );
    CREATE INDEX tasks_status ON tasks(status);
    CREATE TABLE runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        task_id INTEGER NOT NULL REFERENCES tasks(id),
        kind TEXT NOT NULL,
        attempt INTEGER NOT NULL,
        engine TEXT NOT NULL,
        model TEXT,
        outcome TEXT,
        exit_code INTEGER,
        cost_usd REAL,
        duration_s REAL,
        transcript TEXT,
        started_at TEXT NOT NULL,
        finished_at TEXT
    );
    CREATE TABLE log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        task_id INTEGER,
        ts TEXT NOT NULL,
        message TEXT NOT NULL
    );
    CREATE TABLE proposals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        repo TEXT NOT NULL,
        task_id INTEGER,
        text TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'proposed',
        created_at TEXT NOT NULL
    );
    """,
    "ALTER TABLE runs ADD COLUMN pid INTEGER;",
    "ALTER TABLE tasks ADD COLUMN pr_state TEXT;",  # open | merged | closed (GitHub mode)
]


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._local = threading.local()
        self.migrate()

    @property
    def db(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=30000")
            self._local.conn = conn
        return conn

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

    def migrate(self) -> None:
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        for number, script in enumerate(MIGRATIONS[version:], start=version + 1):
            self.db.executescript(f"BEGIN;{script};PRAGMA user_version={number};COMMIT;")

    # tasks -------------------------------------------------------------
    def add_task(self, repo: str, title: str, body: str = "", *, scope=(), needs_plan=False,
                 issue: int | None = None) -> int:
        cur = self.db.execute(
            "INSERT INTO tasks (repo, title, body, status, scope, needs_plan, issue, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (repo, title, body, QUEUED, json.dumps(list(scope)), int(needs_plan), issue, now()),
        )
        return int(cur.lastrowid)

    def task(self, task_id: int) -> sqlite3.Row | None:
        return self.db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()

    def tasks(self, statuses=None, limit: int | None = None) -> list[sqlite3.Row]:
        sql, params = "SELECT * FROM tasks", []
        if statuses:
            sql += f" WHERE status IN ({','.join('?' * len(statuses))})"
            params += list(statuses)
        sql += " ORDER BY id DESC"
        if limit:
            sql += f" LIMIT {int(limit)}"
        return self.db.execute(sql, params).fetchall()

    def open_prs(self) -> list[sqlite3.Row]:
        """DONE tasks whose GitHub pull request is not known to be merged or closed."""
        return self.db.execute(
            "SELECT * FROM tasks WHERE status = ? AND pr_url LIKE 'http%'"
            " AND (pr_state IS NULL OR pr_state = 'open') ORDER BY id", (DONE,)).fetchall()

    def pr_counts(self) -> dict[str, int]:
        rows = self.db.execute(
            "SELECT COALESCE(pr_state, 'open'), COUNT(*) FROM tasks WHERE status = ?"
            " AND pr_url LIKE 'http%' GROUP BY 1", (DONE,)).fetchall()
        return {state: count for state, count in rows}

    def task_for_issue(self, repo: str, issue: int) -> sqlite3.Row | None:
        return self.db.execute(
            "SELECT * FROM tasks WHERE repo = ? AND issue = ?", (repo, issue)).fetchone()

    def update(self, task_id: int, **values) -> None:
        cols = ", ".join(f"{key} = ?" for key in values)
        self.db.execute(f"UPDATE tasks SET {cols} WHERE id = ?", (*values.values(), task_id))

    def claim(self, owner: str) -> sqlite3.Row | None:
        """Atomically move the oldest QUEUED task to RUNNING for this owner."""
        db = self.db
        db.execute("BEGIN IMMEDIATE")
        try:
            row = db.execute(
                "SELECT id FROM tasks WHERE status = ? ORDER BY id LIMIT 1", (QUEUED,)).fetchone()
            if row is not None:
                db.execute(
                    "UPDATE tasks SET status = ?, owner = ?, started_at = ?, finished_at = NULL,"
                    " reason = NULL WHERE id = ?", (RUNNING, owner, now(), row["id"]))
            db.execute("COMMIT")
        except BaseException:
            db.execute("ROLLBACK")
            raise
        return self.task(row["id"]) if row is not None else None

    def requeue(self, statuses) -> int:
        cur = self.db.execute(
            f"UPDATE tasks SET status = ?, owner = NULL WHERE status IN"
            f" ({','.join('?' * len(statuses))})", (QUEUED, *statuses))
        return cur.rowcount

    # runs and cost -----------------------------------------------------
    def add_run(self, task_id: int, kind: str, attempt: int, engine: str) -> int:
        cur = self.db.execute(
            "INSERT INTO runs (task_id, kind, attempt, engine, started_at) VALUES (?, ?, ?, ?, ?)",
            (task_id, kind, attempt, engine, now()))
        return int(cur.lastrowid)

    def finish_run(self, run_id: int, **values) -> None:
        values["finished_at"] = now()
        cols = ", ".join(f"{key} = ?" for key in values)
        self.db.execute(f"UPDATE runs SET {cols} WHERE id = ?", (*values.values(), run_id))

    def set_run_pid(self, run_id: int, pid: int) -> None:
        self.db.execute("UPDATE runs SET pid = ? WHERE id = ?", (pid, run_id))

    def open_runs(self, task_id: int) -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT * FROM runs WHERE task_id = ? AND finished_at IS NULL", (task_id,)).fetchall()

    def runs(self, task_id: int) -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT * FROM runs WHERE task_id = ? ORDER BY id", (task_id,)).fetchall()

    def cost(self, task_id: int) -> tuple[float, bool]:
        """(sum of reported cost, whether every finished run reported its cost)."""
        row = self.db.execute(
            "SELECT COALESCE(SUM(cost_usd), 0), COUNT(*), COUNT(cost_usd) FROM runs"
            " WHERE task_id = ? AND finished_at IS NOT NULL", (task_id,)).fetchone()
        return float(row[0]), row[1] == row[2]

    # log ---------------------------------------------------------------
    def log(self, task_id: int | None, message: str) -> None:
        self.db.execute("INSERT INTO log (task_id, ts, message) VALUES (?, ?, ?)",
                        (task_id, now(), message))

    def logs(self, task_id: int) -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT * FROM log WHERE task_id = ? ORDER BY id", (task_id,)).fetchall()

    # proposals (repo notes) --------------------------------------------
    def add_proposal(self, repo: str, task_id: int | None, text: str) -> int:
        cur = self.db.execute(
            "INSERT INTO proposals (repo, task_id, text, created_at) VALUES (?, ?, ?, ?)",
            (repo, task_id, text, now()))
        return int(cur.lastrowid)

    def proposals(self, repo: str, status: str | None = "proposed") -> list[sqlite3.Row]:
        sql, params = "SELECT * FROM proposals WHERE repo = ?", [repo]
        if status:
            sql += " AND status = ?"
            params.append(status)
        return self.db.execute(sql + " ORDER BY id", params).fetchall()

    def set_proposal(self, proposal_id: int, status: str) -> sqlite3.Row | None:
        self.db.execute("UPDATE proposals SET status = ? WHERE id = ?", (status, proposal_id))
        return self.db.execute("SELECT * FROM proposals WHERE id = ?", (proposal_id,)).fetchone()

    # repos (runtime state only; settings live in config.toml) ----------
    def last_poll(self, repo: str) -> str | None:
        row = self.db.execute("SELECT last_poll FROM repos WHERE name = ?", (repo,)).fetchone()
        return row["last_poll"] if row else None

    def set_last_poll(self, repo: str) -> None:
        self.db.execute(
            "INSERT INTO repos (name, last_poll) VALUES (?, ?)"
            " ON CONFLICT(name) DO UPDATE SET last_poll = excluded.last_poll", (repo, now()))
