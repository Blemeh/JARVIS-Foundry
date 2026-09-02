"""Prove V11 Research Intelligence with real read-only source receipts.

The source finalist comes from the frozen A2 proof database opened with
``mode=ro``.  Web and GitHub connectors perform GET/read operations, local
project evidence is read directly, and all durable writes are confined to a
new disposable schema-v39 evidence database under ``--evidence-root``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from jarvis_foundry.command_center import build_snapshot
from jarvis_foundry.connectors.github_research import GitHubResearchConnector
from jarvis_foundry.connectors.web_research import PlaywrightBrowserConnector
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.discovery_candidates import ingest_observed_candidate
from jarvis_foundry.reconcile import reconcile
from jarvis_foundry.research_dossier import (
    DOSSIER_CONTRACT,
    ResearchDossierPolicy,
    build_research_dossier,
    candidate_evidence_revision,
    dossier_cache_key,
    research_candidate_dossier,
)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git_state(repository: Path) -> dict[str, Any]:
    head = subprocess.run(
        ["git", "-C", str(repository), "rev-parse", "HEAD"],
        capture_output=True,
        check=True,
        text=True,
        timeout=20,
    ).stdout.strip()
    status = subprocess.run(
        ["git", "-C", str(repository), "status", "--porcelain=v1"],
        capture_output=True,
        check=True,
        text=True,
        timeout=20,
    ).stdout
    worktrees = subprocess.run(
        ["git", "-C", str(repository), "worktree", "list", "--porcelain"],
        capture_output=True,
        check=True,
        text=True,
        timeout=20,
    ).stdout
    return {
        "head": head,
        "status": status.splitlines(),
        "worktree_count": sum(
            line.startswith("worktree ") for line in worktrees.splitlines()
        ),
        "worktree_list_sha256": hashlib.sha256(worktrees.encode("utf-8")).hexdigest(),
    }


def _source_finalist(source_database: Path) -> dict[str, Any]:
    uri = f"file:{source_database.resolve().as_posix()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    try:
        row = connection.execute(
            "SELECT c.candidate_id AS source_candidate_id, c.objective, "
            "c.expected_outcome, c.candidate_type, c.status AS candidate_status, "
            "cs.source_kind, cs.source_identity, cs.source_ref, cs.content_hash, "
            "cs.observed_at, cs.fresh_until "
            "FROM candidates c JOIN candidate_sources cs "
            "ON cs.candidate_id = c.candidate_id "
            "WHERE lower(c.objective) LIKE '%python 3.14%' "
            "ORDER BY c.rowid, cs.rowid LIMIT 1"
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        raise AssertionError("source A2 database has no Python 3.14 finalist")
    return dict(row)


def _count_sql(database: FoundryDatabase, table: str) -> int:
    allowed = {"missions", "directives"}
    if table not in allowed:
        raise ValueError(f"unsupported proof table {table}")
    with database.connect(readonly=True) as connection:
        return int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def run(args: argparse.Namespace) -> dict[str, Any]:
    repository = args.repository.resolve()
    source_database = args.source_database.resolve()
    evidence_root = args.evidence_root.resolve()
    if evidence_root.exists():
        raise FileExistsError(f"refusing to reuse proof root: {evidence_root}")
    if not source_database.is_file():
        raise FileNotFoundError(source_database)

    evidence_root.mkdir(parents=True)
    repository_before = _git_state(repository)
    source_db_before = {
        "bytes": source_database.stat().st_size,
        "mtime_ns": source_database.stat().st_mtime_ns,
        "sha256": _sha256(source_database),
    }
    source_candidate = _source_finalist(source_database)

    database = FoundryDatabase(evidence_root / "v11-research-intelligence.db")
    schema = database.initialize()
    if schema != 39:
        raise AssertionError(f"expected schema 39, got {schema}")
    started = datetime.now(timezone.utc)
    # Keep deterministic cache transitions while leaving enough wall-clock
    # room for real browser/API reads to precede dossier evaluation time.
    proof_clock = started + timedelta(minutes=5)
    created = ingest_observed_candidate(
        database,
        "v11-research-intelligence-proof",
        observation_id="real-a2-finalist-source",
        source_kind=str(source_candidate["source_kind"]),
        source_identity=str(source_candidate["source_identity"]),
        source_ref=str(source_candidate["source_ref"]),
        content_hash=str(source_candidate["content_hash"]),
        objective=str(source_candidate["objective"]),
        target_project="JARVIS Foundry",
        alias_key=f"real-a2:{source_candidate['source_candidate_id']}",
        evidence=[{
            "target_project": "JARVIS Foundry",
            "source_candidate_id": source_candidate["source_candidate_id"],
            "source_database_sha256": source_db_before["sha256"],
            "authority": "DATA_ONLY",
        }],
        candidate_type=str(source_candidate.get("candidate_type") or "research"),
        expected_outcome=str(
            source_candidate.get("expected_outcome")
            or "Evidence-backed Python runtime compatibility decision"
        ),
        confidence=0.8,
        observed_at=str(source_candidate.get("observed_at") or _iso(started)),
        fresh_until=str(
            source_candidate.get("fresh_until") or _iso(started + timedelta(days=7))
        ),
        expires_at=_iso(started + timedelta(days=30)),
    )
    if not created.get("created"):
        raise AssertionError("failed to create disposable finalist identity")
    candidate_id = str(created["candidate_id"])

    initial_calls = 0
    follow_up_calls: list[str] = []
    source_attempts: list[dict[str, Any]] = []
    latest_initial_evidence: list[dict[str, Any]] = []
    github_response_hash = ""

    def initial_reader(
        _identity: dict[str, Any],
        _question: str,
        _policy: ResearchDossierPolicy,
    ) -> dict[str, Any]:
        nonlocal initial_calls, github_response_hash, latest_initial_evidence
        initial_calls += 1
        expiry = (
            proof_clock - timedelta(seconds=1)
            if initial_calls == 1 else proof_clock + timedelta(hours=72)
        )

        browser = PlaywrightBrowserConnector(allowed_hosts=("docs.python.org",))
        web = browser.read(
            "https://docs.python.org/3/whatsnew/3.14.html",
            allowed_hosts=("docs.python.org",),
        )
        if web.attempt.outcome != "COMPLETED" or web.page is None:
            raise AssertionError(
                f"real web read failed: {web.attempt.outcome}/{web.attempt.error_code}"
            )
        source_attempts.append({
            "source": "web",
            "action_kind": web.attempt.action_kind,
            "outcome": web.attempt.outcome,
            "provider_ref": web.attempt.provider_ref,
            "content_hash": web.page.source_sha256,
            "status": web.page.status,
        })

        github_attempt, tags = GitHubResearchConnector().read_api(
            "github.release.read", "/repos/python/cpython/tags?per_page=100",
        )
        if github_attempt.outcome != "COMPLETED" or not isinstance(tags, list):
            raise AssertionError(
                "real GitHub read failed: "
                f"{github_attempt.outcome}/{github_attempt.error_code}"
            )
        matching_tags = [
            str(item.get("name")) for item in tags
            if isinstance(item, dict) and str(item.get("name") or "").startswith("v3.14")
        ]
        if not matching_tags:
            raise AssertionError("CPython tags response contained no v3.14 tag")
        github_response_hash = str(
            (github_attempt.after_state or {}).get("response_hash") or ""
        )
        source_attempts.append({
            "source": "github",
            "action_kind": github_attempt.action_kind,
            "outcome": github_attempt.outcome,
            "provider_ref": github_attempt.provider_ref,
            "content_hash": github_response_hash,
            "matching_tags": matching_tags[:10],
        })

        latest_initial_evidence = [
            {
                "source_kind": "web",
                "source_ref": web.page.final_url,
                "source_title": web.page.title,
                "statement": (
                    "The official Python documentation served the Python 3.14 "
                    "release-notes page successfully and identifies it as current "
                    "Python 3.14 documentation."
                ),
                "stance": "SUPPORTING",
                "claim_key": "python_314_candidate_viability",
                "confidence": 0.90,
                "content_hash": web.page.source_sha256,
                "retrieved_at": web.page.fetched_at,
                "fresh_until": expiry,
                "evidence_types": ["EXPECTED_BENEFIT", "FRESHNESS"],
                "outcome": web.attempt.outcome,
                "independent_source": "web:docs.python.org",
            },
            {
                "source_kind": "github",
                "source_ref": str(github_attempt.provider_ref),
                "source_title": "CPython repository tags",
                "statement": (
                    "The live CPython tags API includes released Python 3.14 "
                    f"tags, including {matching_tags[0]}."
                ),
                "stance": "SUPPORTING",
                "claim_key": "python_314_candidate_viability",
                "confidence": 0.88,
                "content_hash": github_response_hash,
                "retrieved_at": started,
                "fresh_until": expiry,
                "evidence_types": [
                    "EXPECTED_BENEFIT", "PROJECT_CONTEXT", "FRESHNESS",
                ],
                "outcome": github_attempt.outcome,
                "independent_source": "github:python/cpython",
            },
        ]
        return {
            "status": "COMPLETED",
            "source_evidence": latest_initial_evidence,
            "unresolved_unknowns": [
                {
                    "unknown_id": "runtime-effort-dependency",
                    "question": (
                        "What launcher, dependency, and regression work would "
                        "Python 3.14 adoption require in JARVIS?"
                    ),
                    "why_it_matters": "Migration cost may reverse candidate viability.",
                    "expected_information_value": 0.99,
                },
                {
                    "unknown_id": "production-provider-proof",
                    "question": "Do all production providers operate correctly on Python 3.14?",
                    "why_it_matters": "Public release evidence cannot prove provider compatibility.",
                    "expected_information_value": 0.92,
                },
            ],
            "source_ref": web.page.final_url,
            "budget_used": 0.5,
        }

    def follow_up_reader(
        unknown: dict[str, Any],
        _identity: dict[str, Any],
        _question: str,
        _policy: ResearchDossierPolicy,
    ) -> dict[str, Any]:
        follow_up_calls.append(str(unknown["unknown_id"]))
        manifest = repository / "pyproject.toml"
        launcher = repository / "src" / "jarvis_foundry" / "deployment" / "release.py"
        manifest_bytes = manifest.read_bytes()
        launcher_bytes = launcher.read_bytes()
        digest = hashlib.sha256(manifest_bytes + b"\x00" + launcher_bytes).hexdigest()
        manifest_text = manifest_bytes.decode("utf-8", errors="replace")
        launcher_text = launcher_bytes.decode("utf-8", errors="replace")
        pinned = "py -3.13" in launcher_text
        declared = 'requires-python = ">=3.13"' in manifest_text
        if not pinned or not declared:
            return {
                "status": "UNKNOWN",
                "resolved": False,
                "reason": "expected local runtime anchors were not found",
                "evidence": [],
            }
        return {
            "status": "COMPLETED",
            "resolved": True,
            "reason": "bounded local dependency and release-launcher read",
            "evidence": [{
                "source_kind": "local",
                "source_ref": (
                    f"{repository}#pyproject.toml+"
                    "src/jarvis_foundry/deployment/release.py"
                ),
                "source_title": "JARVIS Python runtime and release launcher contract",
                "statement": (
                    "JARVIS accepts Python 3.13 or newer in package metadata, "
                    "but its Windows release launcher explicitly invokes Python "
                    "3.13; adopting 3.14 therefore requires launcher and regression proof."
                ),
                "stance": "CONTRADICTING",
                "claim_key": "python_314_candidate_viability",
                "confidence": 0.96,
                "content_hash": digest,
                "retrieved_at": started,
                "fresh_until": proof_clock + timedelta(hours=24),
                "evidence_types": ["EFFORT", "DEPENDENCY", "PROJECT_CONTEXT"],
                "outcome": "COMPLETED",
                "independent_source": f"local:{repository}",
            }],
        }

    policy = ResearchDossierPolicy(max_follow_up_source_reads=1)
    first_revision = candidate_evidence_revision(database, candidate_id)
    first = research_candidate_dossier(
        database,
        candidate_id=candidate_id,
        cycle_id="real-initial",
        initial_reader=initial_reader,
        follow_up_reader=follow_up_reader,
        policy=policy,
        reserved_budget=0.5,
        per_candidate_budget=0.5,
        cycle_budget=2.0,
        now=proof_clock,
    )
    refreshed = research_candidate_dossier(
        database,
        candidate_id=candidate_id,
        cycle_id="real-stale-refresh",
        initial_reader=initial_reader,
        follow_up_reader=follow_up_reader,
        policy=policy,
        reserved_budget=0.5,
        per_candidate_budget=0.5,
        cycle_budget=2.0,
        now=proof_clock + timedelta(seconds=1),
    )
    before_reuse_events = database.event_count()
    calls_before_reuse = initial_calls
    reused = research_candidate_dossier(
        database,
        candidate_id=candidate_id,
        cycle_id="real-unchanged-reuse",
        initial_reader=initial_reader,
        follow_up_reader=follow_up_reader,
        policy=policy,
        reserved_budget=0.5,
        per_candidate_budget=0.5,
        cycle_budget=2.0,
        now=proof_clock + timedelta(seconds=2),
    )
    calls_after_reuse = initial_calls
    reuse_event_delta = database.event_count() - before_reuse_events

    if len(github_response_hash) != 64:
        raise AssertionError("GitHub response hash is not a SHA-256 identity")
    linked = ingest_observed_candidate(
        database,
        "v11-research-intelligence-proof-revision",
        observation_id="real-github-tags-source",
        source_kind="GITHUB",
        source_identity="github:python/cpython:tags",
        source_ref="https://api.github.com/repos/python/cpython/tags?per_page=100",
        content_hash=f"sha256:{github_response_hash}",
        objective=str(source_candidate["objective"]),
        target_project="JARVIS Foundry",
        alias_key=f"real-a2:{source_candidate['source_candidate_id']}",
        observed_at=_iso(proof_clock + timedelta(seconds=3)),
        fresh_until=_iso(proof_clock + timedelta(hours=72)),
    )
    changed_revision = candidate_evidence_revision(database, candidate_id)
    changed = research_candidate_dossier(
        database,
        candidate_id=candidate_id,
        cycle_id="real-revision-changed",
        initial_reader=initial_reader,
        follow_up_reader=follow_up_reader,
        policy=policy,
        reserved_budget=0.5,
        per_candidate_budget=0.5,
        cycle_budget=2.0,
        now=proof_clock + timedelta(seconds=3),
    )

    dossier = dict(changed["dossier"])
    support_only = build_research_dossier(
        candidate=dossier["candidate_identity"],
        research_question=dossier["research_question"],
        evidence_revision_identity=dossier["evidence_revision_identity"],
        evidence=dossier["supporting_evidence"],
        unresolved_unknowns=dossier["unresolved_unknowns"],
        policy=policy,
        generated_at=proof_clock + timedelta(seconds=3),
    )
    before_projection_events = database.event_count()
    snapshot = build_snapshot(database, now=_iso(proof_clock + timedelta(seconds=3)))
    command_center = snapshot["discovery"]["research_intelligence"]
    projection_event_delta = database.event_count() - before_projection_events

    forbidden_event_types = sorted({
        event.event_type for event in database.events()
        if event.event_type in {
            "executive.candidate_admitted",
            "executive.objective_created",
            "mission.created",
        } or event.event_type.startswith("system.action_")
    })
    external_write_actions = sorted({
        item["action_kind"] for item in source_attempts
        if item["action_kind"] not in {
            "browser.web.read", "github.release.read",
        }
    })
    source_db_after = {
        "bytes": source_database.stat().st_size,
        "mtime_ns": source_database.stat().st_mtime_ns,
        "sha256": _sha256(source_database),
    }
    repository_after = _git_state(repository)
    reconciliation = reconcile(database)

    report: dict[str, Any] = {
        "contract": DOSSIER_CONTRACT,
        "schema": schema,
        "database": str(database.path),
        "source_finalist": source_candidate,
        "candidate_id": candidate_id,
        "dossier": dossier,
        "multi_source": {
            "passed": len(dossier["source_provenance"]) == 3,
            "source_kinds": sorted({
                item["source_kind"] for item in dossier["source_provenance"]
            }),
            "attempts": source_attempts,
        },
        "contradictions": {
            "passed": bool(dossier["contradictions"]),
            "count": len(dossier["contradictions"]),
            "confidence_with_contradiction": dossier["confidence"]["score"],
            "confidence_without_contradiction": support_only["confidence"]["score"],
            "confidence_fell": (
                dossier["confidence"]["score"] < support_only["confidence"]["score"]
            ),
        },
        "freshness": {
            "passed": (
                first["dossier"]["freshness"]["status"] == "STALE"
                and refreshed["cache_status"] == "REFRESHED"
                and refreshed["dossier"]["freshness"]["status"] == "FRESH"
            ),
            "initial": first["dossier"]["freshness"],
            "refreshed": refreshed["dossier"]["freshness"],
        },
        "cache": {
            "passed": (
                reused["status"] == "REUSED"
                and reuse_event_delta == 0
                and calls_after_reuse == calls_before_reuse
                and initial_calls == calls_after_reuse + 1
                and first_revision != changed_revision
                and changed["cache_status"] == "REVISION_CHANGED"
            ),
            "initial_cache_status": first["cache_status"],
            "refresh_cache_status": refreshed["cache_status"],
            "reuse_cache_status": reused["cache_status"],
            "reuse_event_delta": reuse_event_delta,
            "reader_calls_before_reuse": calls_before_reuse,
            "reader_calls_after_reuse": calls_after_reuse,
            "revision_change_reader_calls": initial_calls - calls_after_reuse,
            "first_evidence_revision": first_revision,
            "changed_evidence_revision": changed_revision,
            "cache_key": dossier["cache_key"],
            "cache_key_matches_revision": dossier["cache_key"] == dossier_cache_key(
                candidate_id, changed_revision,
            ),
            "source_linked": bool(linked.get("source_linked")),
        },
        "follow_up": {
            "passed": (
                set(follow_up_calls) == {"runtime-effort-dependency"}
                and dossier["budget"]["source_reads_used"]
                <= dossier["budget"]["source_read_limit"]
            ),
            "unknowns_targeted": follow_up_calls,
            "attempts": dossier["budget"]["follow_up_attempts"],
            "source_reads_used": dossier["budget"]["source_reads_used"],
            "source_read_limit": dossier["budget"]["source_read_limit"],
        },
        "unknowns": {
            "passed": any(
                item["unknown_id"] == "production-provider-proof"
                and item["status"] == "UNRESOLVED"
                for item in dossier["unresolved_unknowns"]
            ),
            "items": dossier["unresolved_unknowns"],
        },
        "executive_machine_readable": {
            "passed": (
                isinstance(dossier.get("confidence", {}).get("score"), float)
                and dossier["final_research_conclusion"]["scoring_authority"] is False
                and dossier["final_research_conclusion"]["admission_authority"] is False
            ),
            "conclusion": dossier["final_research_conclusion"],
        },
        "command_center": {
            "passed": (
                command_center["count"] == 1
                and bool(command_center["dossiers"][0]["summary"])
                and projection_event_delta == 0
            ),
            "projection": command_center,
            "event_delta": projection_event_delta,
        },
        "boundaries": {
            "passed": (
                _count_sql(database, "missions") == 0
                and database.event_count(event_type="executive.objective_created") == 0
                and not forbidden_event_types
                and not external_write_actions
                and source_db_after == source_db_before
                and repository_after == repository_before
            ),
            "objectives_created": database.event_count(
                event_type="executive.objective_created"
            ),
            "missions_created": _count_sql(database, "missions"),
            "external_writes": len(external_write_actions),
            "external_write_actions": external_write_actions,
            "forbidden_event_types": forbidden_event_types,
            "source_database_unchanged": source_db_after == source_db_before,
            "repository_unchanged": repository_after == repository_before,
            "connector_actions": sorted({
                item["action_kind"] for item in source_attempts
            }),
        },
        "source_database_before": source_db_before,
        "source_database_after": source_db_after,
        "repository_before": repository_before,
        "repository_after": repository_after,
        "quick_check": database.quick_check(),
        "reconciliation": {
            "consistent": reconciliation.consistent,
            "event_count": reconciliation.event_count,
            "mismatches": len(reconciliation.mismatches),
            "missing_rows": len(reconciliation.missing_rows),
            "unexpected_rows": len(reconciliation.unexpected_rows),
            "integrity_failures": len(reconciliation.integrity_failures),
        },
    }
    report["passed"] = (
        all(report[key]["passed"] for key in (
            "multi_source", "contradictions", "freshness", "cache",
            "follow_up", "unknowns", "executive_machine_readable",
            "command_center", "boundaries",
        ))
        and report["cache"]["cache_key_matches_revision"]
        and report["quick_check"] == "ok"
        and report["reconciliation"]["consistent"]
    )
    (evidence_root / "V11_RESEARCH_INTELLIGENCE_PROOF.json").write_text(
        json.dumps(report, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", required=True, type=Path)
    parser.add_argument("--source-database", required=True, type=Path)
    parser.add_argument("--evidence-root", required=True, type=Path)
    report = run(parser.parse_args())
    print(json.dumps(report, indent=2, sort_keys=True, default=str))
    return 0 if report["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
