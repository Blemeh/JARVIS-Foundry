"""WP11 slice B (V2-OPERATIONAL): whole-vault retrieval.

Deterministic + bounded:
- one envelope for SQLite knowledge claims AND real vault notes (read
  via the vault index allowlist — never raw user paths);
- claims are token-filtered (ALL query tokens must match the statement),
  mission/entity/type/confidence/source/time filters, confidence-ordered,
  capped at limit;
- notes match on the same AND-token rule over the note text, scored by
  matched-token count, capped; every note carries provenance (path +
  content sha256) and a stale flag (file changed since indexing);
- every claim carries support (V4-S1: SOURCE-KIND-AWARE — vault claims
  are SUPPORTED when their source file is ACTIVE in the index (and,
  with the vault root available, the file is byte-identical to the
  index and the referenced line hash still exists); handoff/research
  claims when the file exists under the directory and the referenced
  line hash is present; repo claims when the commit exists in the
  bounded git log; system claims when the event exists on the ledger)
  and its contradiction rows — the envelope never hides stale knowledge
  or contradictions (gate criteria 3-5);
- evaluate_retrieval measures precision/recall/FP/FN/cross-mission
  leakage over labeled probes.
"""
from __future__ import annotations

import hashlib
import re
import sqlite3
import subprocess
from pathlib import Path
from typing import Any, Iterable

from .knowledge import canonicalize_statement
from .knowledge_entities import claims_for_entity, resolve_entity
from .knowledge_extract import extract_statements

_TOKEN_SPLIT = re.compile(r"[^a-z0-9]+")

_CLAIM_COLUMNS = ("claim_id, mission_id, statement, claim_type, confidence, "
                  "fresh_until, source_run_id, source_event_id, status, "
                  "updated_at")

#: kinds whose claims reference a commit in a bounded git log
_REPO_LOG_DEPTH = 2000


def _tokens(text: str) -> set[str]:
    return {t for t in _TOKEN_SPLIT.split(canonicalize_statement(text)) if t}


class _SupportCache:
    """Per-call cache for the kind-aware support checks: each file's
    extracted statement hashes and each repo's commit set are computed
    once per call, never once per claim."""

    def __init__(self) -> None:
        self._file_hashes: dict[Path, frozenset[str]] = {}
        self._repo_commits: dict[Path, frozenset[str]] = {}

    def line_hashes(self, path: Path) -> frozenset[str] | None:
        """sha256[:16] of every extracted statement in the file, or None
        when the file cannot be read."""
        if path in self._file_hashes:
            return self._file_hashes[path]
        try:
            statements = extract_statements(path.read_text(encoding="utf-8"))
        except OSError:
            self._file_hashes[path] = frozenset()
            return None
        hashes = frozenset(
            hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]
            for s in statements)
        self._file_hashes[path] = hashes
        return hashes

    def repo_commits(self, repo: Path) -> frozenset[str]:
        """The bounded git log (HEAD-first, _REPO_LOG_DEPTH commits)."""
        if repo in self._repo_commits:
            return self._repo_commits[repo]
        try:
            from .platform.windows_subprocess import headless_run_kwargs

            output = subprocess.run(
                ["git", "-C", str(repo), "log", "--format=%H",
                 f"-n{_REPO_LOG_DEPTH}"],
                capture_output=True, text=True, timeout=30, check=False,
                **headless_run_kwargs(),
            )
            commits = frozenset(output.stdout.splitlines())
        except (OSError, subprocess.SubprocessError):
            commits = frozenset()
        self._repo_commits[repo] = commits
        return commits


def note_support(connection: sqlite3.Connection, claim: dict[str, Any], *,
                 vault_root: str | Path | None = None,
                 handoff_dir: str | Path | None = None,
                 research_dirs: Iterable[str | Path] = (),
                 repo_paths: Iterable[str | Path] = ()) -> str:
    """Source-kind-aware SUPPORTED/UNSUPPORTED for one claim.

    Vault claims (the source event's first segment matches a vault_files
    rel_path): SUPPORTED when the file is ACTIVE in the index; with
    ``vault_root`` given, additionally the on-disk file must be
    byte-identical to the index hash and the referenced line hash (when
    carried) must exist in the file. Handoff/research claims: file
    exists under the directory (+ line hash match). Repo claims: commit
    exists in the bounded git log. System claims: event exists on the
    ledger. Everything else: UNSUPPORTED — a claim whose source cannot
    be verified is never fabricate-supported.
    """
    context = _SupportContext(
        vault_root=vault_root, handoff_dir=handoff_dir,
        research_dirs=research_dirs, repo_paths=repo_paths)
    return _support_one(connection, claim, context, _SupportCache())


def batch_support(connection: sqlite3.Connection,
                  claims: Iterable[dict[str, Any]], *,
                  vault_root: str | Path | None = None,
                  handoff_dir: str | Path | None = None,
                  research_dirs: Iterable[str | Path] = (),
                  repo_paths: Iterable[str | Path] = (),
                  ) -> dict[str, str]:
    """Kind-aware support for many claims with a shared per-call cache
    (each file is read and extracted once, each repo log fetched once)."""
    context = _SupportContext(
        vault_root=vault_root, handoff_dir=handoff_dir,
        research_dirs=research_dirs, repo_paths=repo_paths)
    cache = _SupportCache()
    return {claim["claim_id"]: _support_one(connection, claim, context, cache)
            for claim in claims}


class _SupportContext:
    def __init__(self, *, vault_root, handoff_dir, research_dirs, repo_paths):
        self.vault_root = Path(vault_root).resolve() if vault_root else None
        self.handoff_dir = Path(handoff_dir).resolve() if handoff_dir else None
        self.research_dirs = tuple(Path(d).resolve() for d in research_dirs)
        self.repo_paths = tuple(Path(r).resolve() for r in repo_paths)

    def repo_for(self, name: str) -> Path | None:
        for repo in self.repo_paths:
            if repo.name == name:
                return repo
        return None


def _support_one(connection: sqlite3.Connection, claim: dict[str, Any],
                 context: _SupportContext, cache: _SupportCache) -> str:
    source_event = claim.get("source_event_id")
    if not source_event:
        return "UNSUPPORTED"
    # event id shapes: vault "rel_path:<hash16>", handoff/research
    # "handoff:NAME.md:<hash16>", repo "repo:NAME:<sha40>",
    # system "system:<digits>"
    parts = source_event.split(":")
    rel = parts[0]
    line_hash: str | None = None
    last = parts[-1]
    if len(last) == 16 and all(c in "0123456789abcdef" for c in last):
        line_hash = last
    row = connection.execute(
        "SELECT status, content_sha256 FROM vault_files WHERE rel_path = ?",
        (rel,)).fetchone()
    if row is not None:
        # VAULT semantics: registry first, then the strongest checks the
        # provided context allows
        if row[0] != "ACTIVE":
            return "UNSUPPORTED"
        if context.vault_root is None:
            return "SUPPORTED"  # registry-only mode (legacy behavior)
        path = (context.vault_root / rel).resolve()
        try:
            path.relative_to(context.vault_root)
        except ValueError:
            return "UNSUPPORTED"  # path escape guard
        if not path.is_file():
            return "UNSUPPORTED"
        try:
            disk_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            return "UNSUPPORTED"
        if disk_hash != str(row[1]):
            return "UNSUPPORTED"  # file changed since indexing
        if line_hash is not None:
            hashes = cache.line_hashes(path)
            if hashes is None or line_hash not in hashes:
                return "UNSUPPORTED"  # the referenced line is gone
        return "SUPPORTED"
    # non-vault kinds
    if rel == "handoff" or rel == "research":
        if rel == "handoff":
            directory = context.handoff_dir
        else:
            directory = context.research_dirs[0] if context.research_dirs else None
        if directory is None:
            return "UNSUPPORTED"
        name = parts[1] if len(parts) >= 2 else ""
        path = (directory / name).resolve()
        try:
            path.relative_to(directory)
        except ValueError:
            return "UNSUPPORTED"
        if not path.is_file():
            return "UNSUPPORTED"
        if line_hash is not None:
            hashes = cache.line_hashes(path)
            if hashes is None or line_hash not in hashes:
                return "UNSUPPORTED"
        return "SUPPORTED"
    if rel == "repo":
        # repo:<name>:<sha>
        if len(parts) != 3:
            return "UNSUPPORTED"
        name, sha = parts[1], parts[2]
        repo = context.repo_for(name)
        if repo is None:
            return "UNSUPPORTED"
        return "SUPPORTED" if sha in cache.repo_commits(repo) else "UNSUPPORTED"
    if rel == "system":
        if len(parts) != 2 or not parts[1].isdigit():
            return "UNSUPPORTED"
        row = connection.execute(
            "SELECT 1 FROM events WHERE sequence = ?",
            (int(parts[1]),)).fetchone()
        return "SUPPORTED" if row else "UNSUPPORTED"
    return "UNSUPPORTED"


def _claim_contradictions(connection: sqlite3.Connection,
                          claim_id: str) -> list[dict[str, Any]]:
    rows = connection.execute(
        "SELECT contradiction_id, claim_a_id, claim_b_id, claim_type, reason "
        "FROM knowledge_contradictions WHERE claim_a_id = ? OR claim_b_id = ? "
        "ORDER BY rowid", (claim_id, claim_id)).fetchall()
    return [dict(r) for r in rows]


def _claim_matches(claim: dict[str, Any], wanted: set[str], *,
                   mission_id: str | None, entity: str | None,
                   claim_type: str | None, min_confidence: float | None,
                   source: str | None, time_from: str | None,
                   time_to: str | None, connection: sqlite3.Connection) -> bool:
    if mission_id and claim["mission_id"] != mission_id:
        return False
    if claim_type and claim["claim_type"] != claim_type:
        return False
    if min_confidence is not None and (claim["confidence"] or 0.0) < min_confidence:
        return False
    if source and not (claim["source_run_id"] or "").startswith(source):
        return False
    if time_from or time_to:
        anchor = claim.get("updated_at") or ""
        if time_from and anchor < time_from:
            return False
        if time_to and anchor > time_to:
            return False
    if entity:
        entity_id = resolve_entity(connection, entity)
        if entity_id is None or claim["claim_id"] not in claims_for_entity(
                connection, entity_id):
            return False
    if wanted and not wanted <= _tokens(claim["statement"]):
        return False
    return True


def retrieve_vault(connection: sqlite3.Connection, vault_root: str | Path,
                   query: str, *, mission_id: str | None = None,
                   entity: str | None = None, claim_type: str | None = None,
                   min_confidence: float | None = None,
                   source: str | None = None, time_from: str | None = None,
                   time_to: str | None = None,
                   handoff_dir: str | Path | None = None,
                   research_dirs: Iterable[str | Path] = (),
                   repo_paths: Iterable[str | Path] = (),
                   limit: int = 50) -> dict[str, Any]:
    """Bounded retrieval envelope: claims + matching vault notes.

    Support marking is source-kind-aware (V4-S1): handoff_dir and
    repo_paths let handoff/repo claims resolve their sources; without
    them those kinds report UNSUPPORTED (no source = no support).
    """
    limit = max(1, min(int(limit), 500))
    root = Path(vault_root).resolve()
    wanted = _tokens(query)

    raw = connection.execute(
        f"SELECT {_CLAIM_COLUMNS} FROM knowledge_claims").fetchall()
    claims = []
    for row in raw:
        claim = dict(row)
        if _claim_matches(claim, wanted, mission_id=mission_id, entity=entity,
                          claim_type=claim_type, min_confidence=min_confidence,
                          source=source, time_from=time_from, time_to=time_to,
                          connection=connection):
            claims.append(claim)
    claims.sort(key=lambda c: (-(c["confidence"] or 0.0), c["claim_id"]))
    claims = claims[:limit]

    support = batch_support(connection, claims, vault_root=vault_root,
                            handoff_dir=handoff_dir,
                            research_dirs=research_dirs,
                            repo_paths=repo_paths)
    enriched = [{
        **claim,
        "support": support.get(claim["claim_id"], "UNSUPPORTED"),
        "contradictions": _claim_contradictions(connection, claim["claim_id"]),
    } for claim in claims]

    notes = []
    note_rows = connection.execute(
        "SELECT rel_path, content_sha256, mtime FROM vault_files "
        "WHERE status = 'ACTIVE' ORDER BY rel_path LIMIT 2000").fetchall()
    for rel_path, digest, indexed_mtime in note_rows:
        path = (root / rel_path).resolve()
        # the index allowlist is the ONLY source of paths — a row pointing
        # outside the vault root is excluded, never read
        if not path.is_relative_to(root) or not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        matched = wanted & _tokens(text)
        if len(matched) != len(wanted):
            continue
        try:
            current_mtime = str(path.stat().st_mtime_ns)
        except OSError:
            current_mtime = indexed_mtime
        notes.append({
            "rel_path": rel_path, "content_sha256": digest,
            "matched_tokens": sorted(matched), "score": len(matched),
            "stale": current_mtime != indexed_mtime,
        })
    notes.sort(key=lambda n: (-n["score"], n["rel_path"]))
    notes = notes[:limit]

    return {"query": query, "claims": enriched, "notes": notes,
            "claim_count": len(enriched), "note_count": len(notes),
            "truncated": len(enriched) + len(notes) >= limit}


def evaluate_retrieval(connection: sqlite3.Connection, vault_root: str | Path,
                       probes: list[dict[str, Any]]) -> dict[str, Any]:
    """Precision/recall/FP/FN/cross-mission-leakage over labeled probes.

    probe = {"query", "mission_id", "relevant_claims": [ids],
             "relevant_notes": [rel_paths]}
    """
    true_positives = false_positives = false_negatives = leakage = 0
    for probe in probes:
        result = retrieve_vault(connection, vault_root, probe["query"],
                                mission_id=probe.get("mission_id"), limit=100)
        returned_ids = {c["claim_id"] for c in result["claims"]}
        returned_paths = {n["rel_path"] for n in result["notes"]}
        relevant = set(probe.get("relevant_claims", []))
        relevant_notes = set(probe.get("relevant_notes", []))
        true_positives += (len(returned_ids & relevant)
                           + len(returned_paths & relevant_notes))
        false_positives += (len(returned_ids - relevant)
                            + len(returned_paths - relevant_notes))
        false_negatives += (len(relevant - returned_ids)
                            + len(relevant_notes - returned_paths))
        for claim in result["claims"]:
            if probe.get("mission_id") and claim["mission_id"] != probe["mission_id"]:
                leakage += 1
    total = true_positives + false_positives
    precision = true_positives / total if total else 0.0
    total = true_positives + false_negatives
    recall = true_positives / total if total else 0.0
    return {"precision": round(precision, 4), "recall": round(recall, 4),
            "true_positives": true_positives,
            "false_positives": false_positives,
            "false_negatives": false_negatives,
            "cross_mission_leakage": leakage}
