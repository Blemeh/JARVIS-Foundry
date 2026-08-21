"""V8 hybrid memory — git-backed Tier C version history (Letta M5 adapted).

Applies to governed experiential memory ONLY. Git is never the source of
truth for Tier A operational state.

Audit repairs carried over:
- H2/C9-2: full-projection secret/poison scan BEFORE any git write
  (memories.content + procedures.body + blocks.value + serialized payloads);
  blocked commits are audited (``memory.commit_blocked``) and never land in
  history.
- H3: ``_sync.json`` markers record the DB projection hash at each commit;
  restore/revert refuse to overwrite a NON-EMPTY DB that drifted
  (``DivergenceError``) unless force=True is explicit.
- C9-4: markerless (pre-repair/external) targets fail closed unless
  force=True.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

from ..database import FoundryDatabase
from ..events import Event
from .defense import DefenseScanner

ALLOWED_COLUMNS = {
    "tier_c_memories": {
        "memory_id", "mission_id", "agent_run_id", "created_by", "mem_type", "scope",
        "content", "source_ids_json", "evidence_ids_json", "confidence", "state",
        "created_at", "last_verified", "expires_at", "supersedes", "contradicts",
        "importance", "context_cost_chars", "promotion_reason", "last_event_id", "updated_at"},
    "tier_c_procedures": {
        "procedure_id", "name", "description", "body", "trigger_terms_json", "scope",
        "state", "confidence", "source_ids_json", "evidence_ids_json", "mission_id",
        "created_by", "created_at", "last_verified", "expires_at", "supersedes",
        "last_event_id", "updated_at"},
    "tier_c_blocks": {
        "label", "value", "char_limit", "read_only", "description", "owner",
        "revision", "last_event_id", "updated_at"},
}


class DivergenceError(RuntimeError):
    """DB and git Tier C projections disagree; an explicit decision is needed."""


class TierCGitVersioning:
    """Snapshot the Tier C projection to a git repo with sync markers."""

    def __init__(self, db: FoundryDatabase, git_dir: str | Path,
                 *, scanner: DefenseScanner | None = None) -> None:
        self.db = db
        self.git_dir = Path(git_dir)
        self.git_dir.mkdir(parents=True, exist_ok=True)
        self.scanner = scanner or DefenseScanner()
        if not (self.git_dir / ".git").exists():
            self._git("init", "-q")

    # ---- git helpers -----------------------------------------------------
    def _git(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        from ..platform.windows_subprocess import headless_run_kwargs

        result = subprocess.run(
            ["git", *args], cwd=str(self.git_dir),
            capture_output=True, text=True, check=False,
            **headless_run_kwargs(),
        )
        if check and result.returncode != 0:
            raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
        return result

    def head(self) -> str:
        result = self._git("rev-parse", "HEAD", check=False)
        if result.returncode != 0:
            return ""
        return result.stdout.strip()

    def log(self, limit: int = 20) -> list[dict[str, str]]:
        if not self.head():
            return []
        result = self._git("log", f"-{limit}", "--pretty=format:%H|%an|%s|%ad", "--date=iso")
        entries = []
        for line in result.stdout.strip().splitlines():
            sha, author, subject, date = line.split("|", 3)
            entries.append({"sha": sha, "author": author, "subject": subject, "date": date})
        return entries

    # ---- projection ------------------------------------------------------
    def _projection(self) -> dict[str, list[dict[str, Any]]]:
        with self.db.connect() as conn:
            data: dict[str, list[dict[str, Any]]] = {}
            for table in ("tier_c_memories", "tier_c_procedures", "tier_c_blocks"):
                rows = conn.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
                data[table] = [dict(r) for r in rows]
        return data

    def _db_projection_hash(self) -> str:
        digest = hashlib.sha256()
        for table, rows in self._projection().items():
            for row in rows:
                digest.update(f"{table}:".encode())
                digest.update(str(row).encode())
        return digest.hexdigest()

    def _db_projection_empty(self) -> bool:
        with self.db.connect() as conn:
            for table in ("tier_c_memories", "tier_c_procedures", "tier_c_blocks"):
                count = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
                if count:
                    return False
        return True

    def _write_manifests(self) -> None:
        for table, rows in self._projection().items():
            (self.git_dir / f"{table}.json").write_text(
                json.dumps(rows, indent=1, sort_keys=True), encoding="utf-8")
        (self.git_dir / "_sync.json").write_text(
            json.dumps({"db_hash": self._db_projection_hash()}), encoding="utf-8")

    def _hash_at(self, sha: str) -> str | None:
        result = self._git("show", f"{sha}:_sync.json", check=False)
        if result.returncode != 0:
            return None
        try:
            return json.loads(result.stdout)["db_hash"]
        except Exception:
            return None

    # ---- commit ----------------------------------------------------------
    def commit_all(self, actor: str, reason: str) -> str:
        """Commit the full Tier C projection. Full-projection secret/poison
        gate runs BEFORE anything reaches git (audit repairs H2/C9-2)."""
        self._write_manifests()
        threats: dict[str, list[str]] = {}
        with self.db.connect() as conn:
            for row in conn.execute("SELECT memory_id, content FROM tier_c_memories"):
                hits = self.scanner.scan(row["content"])
                if hits:
                    threats[f"memory:{row['memory_id']}"] = hits
            for row in conn.execute("SELECT procedure_id, body FROM tier_c_procedures"):
                hits = self.scanner.scan(row["body"])
                if hits:
                    threats[f"procedure:{row['procedure_id']}"] = hits
            for row in conn.execute("SELECT label, value FROM tier_c_blocks"):
                hits = self.scanner.scan(row["value"])
                if hits:
                    threats[f"block:{row['label']}"] = hits
            # serialized payloads (metadata can carry poison too)
            for row in conn.execute(
                    "SELECT memory_id, source_ids_json, evidence_ids_json, "
                    "promotion_reason FROM tier_c_memories"):
                combined = " ".join(str(row[k] or "") for k in
                                    ("source_ids_json", "evidence_ids_json", "promotion_reason"))
                hits = self.scanner.scan(combined)
                if hits:
                    threats[f"memory_meta:{row['memory_id']}"] = hits
        if threats:
            event = Event.create(
                "memory.commit_blocked",
                {"threats": threats, "by": actor, "reason": reason},
                source_type="memory", source_id=actor,
                idempotency_key=f"memory.commit_blocked:{actor}:{reason}")
            self.db.append(event)
            raise RuntimeError(
                f"commit blocked: suspicious content in {len(threats)} record(s) "
                "(full-projection secret/poison gate); quarantine or remove before committing")
        self._git("add", "-A")
        status = self._git("status", "--porcelain")
        if not status.stdout.strip():
            return self.head()
        author = f"{actor} <{actor}@jarvis.local>"
        self._git("commit", "-q", "--author", author, "-m", f"[{actor}] {reason}")
        return self.head()

    # ---- restore ---------------------------------------------------------
    def revert_to(self, sha: str, *, actor: str, reason: str, force: bool = False) -> str:
        """Restore DB from a committed revision and record the rollback as
        its own commit. Refuses to silently discard newer DB state."""
        target_hash = self._hash_at(sha)
        current_hash = self._db_projection_hash()
        if not force and not self._db_projection_empty():
            if target_hash is None:
                raise DivergenceError(
                    f"commit {sha[:12]} carries no _sync.json marker "
                    "(pre-repair/external commit); cannot verify DB alignment — "
                    "pass force=True to override")
            if current_hash != target_hash:
                raise DivergenceError(
                    f"DB projection differs from git state at {sha[:12]} "
                    f"(db={current_hash[:12]} vs git={target_hash[:12]}); "
                    "commit the DB first, or pass force=True to discard the drift")
        self._git("checkout", sha, "--", ".")
        self._restore_from_manifests()
        self._git("add", "-A")
        author = f"{actor} <{actor}@jarvis.local>"
        self._git("commit", "-q", "--allow-empty", "--author", author, "-m", f"[{actor}] {reason}")
        return self.head()

    def restore(self, force: bool = False) -> None:
        """Restore the DB from the git checkout (rebuild path). An empty DB
        is the canonical rebuild precondition; a non-empty drifted DB is
        refused unless force=True."""
        head = self.head()
        if head:
            target_hash = self._hash_at(head)
            current_hash = self._db_projection_hash()
            if target_hash is None and not force and not self._db_projection_empty():
                raise DivergenceError(
                    f"HEAD carries no _sync.json marker; cannot verify DB alignment — "
                    "pass force=True to override")
            if (target_hash and current_hash != target_hash and not force
                    and not self._db_projection_empty()):
                raise DivergenceError(
                    f"DB projection differs from git HEAD ({current_hash[:12]} vs "
                    f"{target_hash[:12]}); commit the DB first, or force=True")
        self._restore_from_manifests()

    def _restore_from_manifests(self) -> None:
        """Rebuild tier_c tables from the checked-out manifests. Column
        keys are data, never SQL (audit repair L1: whitelist enforced)."""
        with self.db.transaction() as conn:
            # children before parents (NO ACTION FKs: history can never be
            # silently cascade-destroyed)
            for table in ("tier_c_memory_revisions", "tier_c_validations",
                          "tier_c_memories", "tier_c_block_revisions",
                          "tier_c_blocks", "tier_c_procedures"):
                conn.execute(f"DELETE FROM {table}")
            for table, allowed in ALLOWED_COLUMNS.items():
                manifest = self.git_dir / f"{table}.json"
                rows: list[dict[str, Any]] = []
                if manifest.exists():
                    rows = json.loads(manifest.read_text(encoding="utf-8"))
                    for row in rows:
                        unknown = set(row.keys()) - allowed
                        if unknown:
                            raise ValueError(
                                f"manifest {table}.json has unknown columns "
                                f"{sorted(unknown)}; restore refused")
                for row in rows:
                    columns = ", ".join(row.keys())
                    marks = ", ".join("?" for _ in row)
                    conn.execute(
                        f"INSERT INTO {table} ({columns}) VALUES ({marks})",
                        tuple(row.values()))
