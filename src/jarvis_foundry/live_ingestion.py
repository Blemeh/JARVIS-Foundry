"""WP11 slice C (V2-OPERATIONAL): continuous ingestion.

run_ingestion_cycle connects bounded sources into knowledge claims:

- vault_source: ACTIVE vault_files rows whose file hash changed since the
  last ingestion of that file (tracked via source_run_id); per-file
  SAVEPOINT isolation — a poisoned file counts as an error, the cycle
  continues; content-derived claim ids make re-runs and restarts
  idempotent;
- handoff_source / research_source: bounded *.md dirs, same line-based
  extraction + per-file savepoints;
- repo_source: bounded git log subjects per repo (read-only, no push);
- event_source: live ledger events of meaningful types since the stored
  cursor (ingestion_cursors — bounds the scan; a restart never re-scans
  the whole ledger).

Every cycle measures items/bytes/elapsed per source (gate criterion 14)
and records ingestion_queued/completed events. Note content is DATA ONLY.
"""
from __future__ import annotations

import hashlib
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Iterable

from .database import FoundryDatabase
from .events import Event
from .knowledge_extract import extract_statements

_MISSION_DEFAULT = "mission-wfm-optimizer"
_EVENT_TYPES = ("verification.passed", "verification.failed",
                "evidence.created", "mission.completed", "step.completed",
                "fault_injection.completed")
_MAX_STATEMENT = 300


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _claim_id_for_line(rel_path: str, line: str, *,
                       mission_id: str = _MISSION_DEFAULT) -> str:
    return f"claim-{_sha(f'{mission_id}|{rel_path}|{line}')[:16]}"


def _source_run_id(kind: str, rel_path: str, file_hash: str) -> str:
    return f"{kind}:{rel_path}:{file_hash}"


def _last_file_hash(db: FoundryDatabase, kind: str,
                    rel_path: str) -> str | None:
    """file hash captured at the last ingestion of this file (if any)."""
    with db.connect() as connection:
        row = connection.execute(
            "SELECT source_run_id FROM knowledge_claims "
            "WHERE source_event_id LIKE ? ORDER BY rowid DESC LIMIT 1",
            (f"{rel_path}:%",)).fetchone()
    if row is None:
        return None
    run_id = row[0]
    return run_id.split(":", 2)[2] if run_id.startswith(f"{kind}:") else None


def _ingest_lines(db: FoundryDatabase, mission_id: str, kind: str,
                  rel_path: str, text: str, file_hash: str,
                  *, source_id: str, limit: int,
                  ) -> tuple[int, int, int, list[str]]:
    """Append one claim per extracted statement (savepoint-isolated batch).

    Returns (statements, created, errors, error_messages). Idempotent:
    unchanged lines dedup by content-derived claim ids; changed lines
    create NEW claims (history preserved).
    """
    statements = extract_statements(text)
    statements = statements[:limit] if limit else statements
    created = 0
    errors: list[str] = []
    with db.connect() as connection:
        connection.execute("SAVEPOINT file_batch")
        try:
            for line in statements:
                claim_id = _claim_id_for_line(rel_path, line,
                                              mission_id=mission_id)
                event = Event.create(
                    "knowledge.claim_recorded",
                    {"claim_id": claim_id, "mission_id": mission_id,
                     "statement": line, "claim_type": "fact",
                     "confidence": 0.5,
                     "source": {"run_id": _source_run_id(
                                    kind, rel_path, file_hash),
                                "event_id": f"{rel_path}:{_sha(line)[:16]}"}},
                    source_type="supervisor", source_id=source_id,
                    idempotency_key=f"knowledge.claim_recorded:{claim_id}")
                try:
                    applied = db.append(event)
                    if applied[1]:
                        created += 1
                except Exception as exc:  # noqa: BLE001 - per-line isolation
                    errors.append(f"{rel_path}: {exc}")
            connection.execute("RELEASE SAVEPOINT file_batch")
        except BaseException:
            connection.execute("ROLLBACK TO SAVEPOINT file_batch")
            connection.execute("RELEASE SAVEPOINT file_batch")
            raise
    return len(statements), created, len(errors), errors


def _vault_source(db: FoundryDatabase, *, root: Path, mission_id: str,
                  source_id: str, max_items: int, max_bytes: int,
                  budget: dict[str, int]) -> dict[str, Any]:
    items_seen = created = errors = bytes_read = 0
    capped = False
    error_msgs: list[str] = []
    rows = db.all("vault_files")
    rows = [r for r in rows if r["status"] == "ACTIVE"]
    rows.sort(key=lambda r: r["rel_path"])
    for row in rows:
        if budget["items"] <= 0:
            capped = True
            break
        rel = row["rel_path"]
        path = (root / rel).resolve()
        if not path.is_relative_to(root.resolve()) or not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            errors += 1
            error_msgs.append(f"{rel}: {exc}")
            continue
        bytes_read += len(text.encode("utf-8"))
        if bytes_read > max_bytes:
            capped = True
            break
        file_hash = _sha(text)
        if _last_file_hash(db, "vault", rel) == file_hash:
            continue  # unchanged since last ingestion
        n, c, e, msgs = _ingest_lines(
            db, mission_id, "vault", rel, text, file_hash,
            source_id=source_id, limit=budget["items"])
        items_seen += n
        created += c
        errors += e
        error_msgs.extend(msgs)
        budget["items"] -= c
        if budget["items"] <= 0 and n > c:
            capped = True
    return {"items_seen": items_seen, "claims_created": created,
            "errors": errors, "error_messages": error_msgs[:5],
            "capped": capped,
            "metrics": {"items_seen": items_seen, "bytes_read": bytes_read}}


def _md_files(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    return sorted(p for p in directory.glob("*.md") if p.is_file())


def _dir_source(db: FoundryDatabase, *, kind: str, directory: Path,
                mission_id: str, source_id: str, max_items: int,
                max_bytes: int, budget: dict[str, int]) -> dict[str, Any]:
    items_seen = created = errors = bytes_read = 0
    capped = False
    error_msgs: list[str] = []
    for path in _md_files(directory):
        if budget["items"] <= 0:
            capped = True
            break
        rel = f"{kind}:{path.name}"
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            errors += 1
            error_msgs.append(f"{path.name}: {exc}")
            continue
        bytes_read += len(text.encode("utf-8"))
        if bytes_read > max_bytes:
            capped = True
            break
        file_hash = _sha(text)
        if _last_file_hash(db, kind, rel) == file_hash:
            continue
        n, c, e, msgs = _ingest_lines(
            db, mission_id, kind, rel, text, file_hash,
            source_id=source_id, limit=budget["items"])
        items_seen += n
        created += c
        errors += e
        error_msgs.extend(msgs)
        budget["items"] -= c
        if budget["items"] <= 0 and n > c:
            capped = True
    return {"items_seen": items_seen, "claims_created": created,
            "errors": errors, "error_messages": error_msgs[:5],
            "capped": capped,
            "metrics": {"items_seen": items_seen, "bytes_read": bytes_read}}


def _repo_source(db: FoundryDatabase, *, repo_paths: list[Path],
                 mission_id: str, source_id: str, max_items: int,
                 budget: dict[str, int]) -> dict[str, Any]:
    created = errors = 0
    error_msgs: list[str] = []
    capped = False
    for repo in repo_paths:
        if budget["items"] <= 0:
            capped = True
            break
        repo = Path(repo).resolve()
        name = repo.name
        try:
            output = subprocess.run(
                ["git", "-C", str(repo), "log", "--format=%H|%s",
                 f"-n{budget['items'] + 1}"],
                capture_output=True, text=True, timeout=30, check=False)
        except (OSError, subprocess.SubprocessError) as exc:
            errors += 1
            error_msgs.append(f"{name}: {exc}")
            continue
        lines = [ln for ln in output.stdout.splitlines() if "|" in ln]
        if len(lines) > budget["items"]:
            capped = True
            lines = lines[:budget["items"]]
        for line in lines:
            sha, subject = line.split("|", 1)
            claim_id = _claim_id_for_line(f"repo:{name}", subject,
                                          mission_id=mission_id)
            event = Event.create(
                "knowledge.claim_recorded",
                {"claim_id": claim_id, "mission_id": mission_id,
                 "statement": subject[: _MAX_STATEMENT], "claim_type": "fact",
                 "confidence": 0.5,
                 "source": {"run_id": f"repo:{name}:{sha}",
                            "event_id": f"repo:{name}:{sha}"}},
                source_type="supervisor", source_id=source_id,
                idempotency_key=f"knowledge.claim_recorded:{claim_id}")
            try:
                applied = db.append(event)
                if applied[1]:
                    created += 1
                    budget["items"] -= 1
            except Exception as exc:  # noqa: BLE001
                errors += 1
                error_msgs.append(f"{name}:{sha}: {exc}")
    return {"items_seen": created + errors, "claims_created": created,
            "errors": errors, "error_messages": error_msgs[:5],
            "capped": capped,
            "metrics": {"items_seen": created + errors, "bytes_read": 0}}


def _event_source(db: FoundryDatabase, *, mission_id: str, source_id: str,
                  max_items: int, budget: dict[str, int]) -> dict[str, Any]:
    with db.connect() as connection:
        cursor_row = connection.execute(
            "SELECT cursor FROM ingestion_cursors "
            "WHERE source_kind = 'system'").fetchone()
        start = int(cursor_row[0]) if cursor_row else 0
        rows = connection.execute(
            f"SELECT sequence, event_type, idempotency_key FROM events "
            f"WHERE sequence > ? AND event_type IN "
            f"({','.join('?' * len(_EVENT_TYPES))}) "
            f"ORDER BY sequence LIMIT ?",
            (start, *_EVENT_TYPES, max_items + 1)).fetchall()
    capped = len(rows) > max_items
    rows = rows[:max_items]
    created = errors = 0
    error_msgs: list[str] = []
    for sequence, event_type, key in rows:
        statement = f"{event_type} {key}"[: _MAX_STATEMENT]
        claim_id = _claim_id_for_line(f"system:{sequence}", statement,
                                      mission_id=mission_id)
        event = Event.create(
            "knowledge.claim_recorded",
            {"claim_id": claim_id, "mission_id": mission_id,
             "statement": statement, "claim_type": "fact",
             "confidence": 0.5,
             "source": {"run_id": f"system:{sequence}",
                        "event_id": f"system:{sequence}"}},
            source_type="supervisor", source_id=source_id,
            idempotency_key=f"knowledge.claim_recorded:{claim_id}")
        try:
            applied = db.append(event)
            if applied[1]:
                created += 1
        except Exception as exc:  # noqa: BLE001
            errors += 1
            error_msgs.append(f"{sequence}: {exc}")
        db.append(Event.create(
            "ingestion.cursor_updated",
            {"source_kind": "system", "cursor": str(sequence)},
            source_type="supervisor", source_id=source_id,
            idempotency_key=f"ingestion.cursor_updated:system:{sequence}"))
    return {"items_seen": len(rows), "claims_created": created,
            "errors": errors, "error_messages": error_msgs[:5],
            "capped": capped,
            "metrics": {"items_seen": len(rows), "bytes_read": 0}}


_ADAPTERS: dict[str, Callable[..., dict[str, Any]]] = {
    "vault": _vault_source,
    "handoffs": _dir_source,
    "research": _dir_source,
    "repo": _repo_source,
    "system": _event_source,
}


def run_ingestion_cycle(db: FoundryDatabase, *,
                        sources: Iterable[str] = ("vault", "handoffs",
                                                  "repo", "research",
                                                  "system"),
                        vault_root: str | Path | None = None,
                        handoff_dir: str | Path | None = None,
                        research_dirs: Iterable[str | Path] | None = None,
                        repo_paths: Iterable[str | Path] | None = None,
                        mission_id: str = _MISSION_DEFAULT,
                        max_items: int = 50, max_bytes: int = 200_000,
                        source_id: str = "live-ingestion",
                        ) -> dict[str, Any]:
    """Run one bounded ingestion cycle over the requested sources.

    Returns per-source reports with claims_created/errors/metrics.
    Restart-safe: unchanged content dedups; cursors bound event scans.
    """
    budget = {"items": max(1, int(max_items))}
    cycle_id = f"ingest-{uuid.uuid4().hex[:12]}"
    started = time.monotonic()
    reports: dict[str, Any] = {}
    for kind in sources:
        if kind == "vault":
            if vault_root is None:
                continue
            report = _vault_source(
                db, root=Path(vault_root), mission_id=mission_id,
                source_id=source_id, max_items=max_items, max_bytes=max_bytes,
                budget=budget)
        elif kind == "handoffs":
            if handoff_dir is None:
                continue
            report = _dir_source(
                db, kind="handoff", directory=Path(handoff_dir),
                mission_id=mission_id, source_id=source_id,
                max_items=max_items, max_bytes=max_bytes, budget=budget)
        elif kind == "research":
            if not research_dirs:
                continue
            report = _dir_source(
                db, kind="research", directory=Path(list(research_dirs)[0]),
                mission_id=mission_id, source_id=source_id,
                max_items=max_items, max_bytes=max_bytes, budget=budget)
        elif kind == "repo":
            if not repo_paths:
                continue
            report = _repo_source(
                db, repo_paths=list(repo_paths), mission_id=mission_id,
                source_id=source_id, max_items=max_items, budget=budget)
        elif kind == "system":
            report = _event_source(
                db, mission_id=mission_id, source_id=source_id,
                max_items=max_items, budget=budget)
        else:
            continue
        report["metrics"]["elapsed_ms"] = round(
            (time.monotonic() - started) * 1000, 1)
        reports[kind] = report
        db.append(Event.create(
            "knowledge.ingestion_queued",
            {"ingestion_id": cycle_id, "mission_id": mission_id,
             "source_type": kind, "source_ref": f"cycle-{cycle_id}",
             "source_hash": ""},
            source_type="supervisor", source_id=source_id,
            idempotency_key=f"knowledge.ingestion_queued:{cycle_id}:{kind}"))
        db.append(Event.create(
            "knowledge.ingestion_completed",
            {"ingestion_id": cycle_id, "claims_count":
             report["claims_created"], "errors": report["errors"],
             "metrics": report["metrics"]},
            source_type="supervisor", source_id=source_id,
            idempotency_key=f"knowledge.ingestion_completed:{cycle_id}:{kind}"))
    return reports
