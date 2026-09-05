"""Disposable proof for the V13 owner-context and standing-orders core."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import canonical_json
from jarvis_foundry.owner_context import (
    ContextProvenance,
    ContextScope,
    OwnerContextQuery,
    OwnerContextStore,
    StandingOrderEvaluator,
    compile_owner_context,
    compile_standing_order,
    project_owner_context,
    project_standing_orders,
    resolve_owner_reference,
)
from jarvis_foundry.owner_controls import OwnerControlService, sign_command


NOW = "2026-09-03T12:00:00Z"
SECRET = b"v13-owner-context-proof-secret-32"
BASE_SHA = "afddb8d5ca3d5c3459a4f5394e84affb92d13738"
EXPECTED_BRANCH = "candidate/v13-owner-context-20260903"
REPO_ROOT = Path(__file__).resolve().parents[1]


def _provenance(text: str, ref: str, at: str) -> ContextProvenance:
    return ContextProvenance.from_content(
        source_type="repository",
        source_id="v13-owner-context-proof",
        source_ref=ref,
        content=text,
        captured_at=at,
        evidence_refs=(f"proof:{ref}",),
        authority="REPOSITORY_AUTHORITY",
    )


def _record(
    store: OwnerContextStore,
    context_id: str,
    kind: str,
    text: str,
    *,
    project: str | None = None,
    at: str = "2026-09-03T08:00:00Z",
    state: str = "ACTIVE",
    fresh_until: str | None = None,
    priority: float = 0.0,
    attributes: dict[str, Any] | None = None,
    contradicts: tuple[str, ...] = (),
) -> str:
    store.record(
        kind,
        text,
        context_id=context_id,
        scope=(
            ContextScope(project_ids=(project,))
            if project else ContextScope(global_scope=True)
        ),
        provenance=_provenance(text, f"fixture/{context_id}", at),
        observed_at=at,
        effective_at=at,
        state=state,
        fresh_until=fresh_until,
        priority=priority,
        attributes=attributes,
        contradicts=contradicts,
    )
    return context_id


def _apply_standing_order(
    service: OwnerControlService,
    text: str,
    *,
    directive_id: str,
    nonce: str,
    project: str,
) -> str:
    draft = compile_standing_order(
        text, scope=ContextScope(project_ids=(project,)),
    )
    metadata = draft.as_metadata()
    service.apply(
        "STANDING_ORDER",
        target=project,
        exact_text=text,
        nonce=nonce,
        signature=sign_command(
            SECRET,
            "STANDING_ORDER",
            project,
            text,
            nonce,
            metadata=metadata,
        ),
        directive_id=directive_id,
        metadata=metadata,
    )
    return f"directive:{directive_id}"


def _snapshot(database_path: Path, now: str) -> dict[str, Any]:
    return project_owner_context(
        FoundryDatabase(database_path), now=now,
    ).as_dict()


def _fresh_process_snapshot(database_path: Path, now: str) -> dict[str, Any]:
    completed = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve()),
            "--snapshot-db",
            str(database_path),
            "--now",
            now,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def _git_text(*args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _source_identity() -> dict[str, Any]:
    head_sha = _git_text("rev-parse", "HEAD")
    source_sha = _git_text(
        "rev-list",
        "-1",
        "HEAD",
        "--",
        ".",
        ":(exclude)docs/CURRENT_STATE.md",
    )
    status = _git_text("status", "--porcelain")
    return {
        "worktree": str(REPO_ROOT),
        "branch": _git_text("branch", "--show-current"),
        "base_sha": BASE_SHA,
        "head_sha": head_sha,
        "source_sha": source_sha,
        "merge_base": _git_text("merge-base", "HEAD", BASE_SHA),
        "commits_ahead": int(
            _git_text("rev-list", "--count", f"{BASE_SHA}..HEAD"),
        ),
        "dirty": bool(status),
        "dirty_paths": status.splitlines(),
    }


def run_proof(
    output_root: Path,
    *,
    now: str = NOW,
    expected_branch: str = EXPECTED_BRANCH,
) -> dict[str, Any]:
    proof_root = output_root.resolve() / f"run-{uuid.uuid4().hex[:10]}"
    proof_root.mkdir(parents=True, exist_ok=False)
    database = FoundryDatabase(proof_root / "foundry.db")
    database.initialize()
    store = OwnerContextStore(database)

    _record(
        store, "goal:personal-operator", "GOAL",
        "JARVIS should understand the owner across projects and time",
    )
    _record(
        store, "program:jarvis-1.0", "PROGRAM",
        "JARVIS 1.0 personal operator program",
        project="jarvis",
    )
    jarvis_project = _record(
        store, "project:jarvis", "PROJECT",
        "JARVIS Foundry is the active personal executive project",
        project="jarvis",
    )
    _record(
        store, "project:leadgen", "PROJECT",
        "LeadGen is the contact-quality project",
        project="leadgen",
    )
    jarvis_objective = _record(
        store, "objective:jarvis-v13", "ACTIVE_OBJECTIVE",
        "Build durable owner context without a second Executive",
        project="jarvis", priority=95,
    )
    _record(
        store, "objective:leadgen-quality", "ACTIVE_OBJECTIVE",
        "Improve LeadGen contact evidence",
        project="leadgen", priority=70,
    )
    _record(
        store, "commitment:review", "COMMITMENT",
        "Review the owner-context candidate on September 4",
        project="jarvis",
        attributes={
            "fact_key": "commitment:v13-review",
            "value": "review candidate",
            "due_at": "2026-09-04T09:00:00Z",
        },
    )
    _record(
        store, "waiting:provider", "WAITING_ON_OWNER",
        "Waiting on owner to choose the final provider account",
        project="jarvis",
    )
    _record(
        store, "unresolved:provider", "UNRESOLVED_ITEM",
        "The configured provider returns HTTP 500",
        project="jarvis",
    )
    _record(
        store, "rejected:memory-platform", "REJECTED_APPROACH",
        "Do not build a second memory platform",
        project="jarvis", state="RESOLVED",
    )
    _record(
        store, "outcome:v11", "HISTORICAL_OUTCOME",
        "V11 proof was provider-blocked and produced zero effects",
        project="jarvis", state="RESOLVED",
    )
    _record(
        store, "decision:isolate-v13", "DECISION",
        "Build V13 owner context only in an isolated source worktree",
        project="jarvis",
    )
    _record(
        store, "constraint:no-live-mutation", "CONSTRAINT",
        "Do not merge, push, deploy, or mutate the live runtime",
        project="jarvis",
        attributes={
            "fact_key": "constraint:live-mutation",
            "value": "DENY",
        },
    )
    _record(
        store, "permission:isolated-source", "PERMISSION",
        "Source-only changes are permitted in the isolated V13 worktree",
        project="jarvis",
        attributes={
            "fact_key": "permission:isolated-source",
            "value": "ALLOW",
        },
    )
    old_priority = _record(
        store, "priority:old", "PRIORITY",
        "UI polish is the immediate priority",
        project="jarvis",
        at="2026-08-20T08:00:00Z",
        fresh_until="2026-09-01T00:00:00Z",
        attributes={
            "fact_key": "project:jarvis:priority",
            "value": "UI_POLISH",
        },
    )
    new_priority = _record(
        store, "priority:new", "PRIORITY",
        "Owner-context durability is the immediate priority",
        project="jarvis",
        fresh_until="2026-09-10T00:00:00Z",
        priority=100,
        attributes={
            "fact_key": "project:jarvis:priority",
            "value": "OWNER_CONTEXT",
        },
    )
    quiet = _record(
        store, "preference:quiet", "PREFERENCE",
        "Prefer quiet recovery without routine interruption",
        project="jarvis",
        attributes={
            "preference_key": "briefing_detail",
            "preference_value": "QUIET",
        },
        contradicts=("preference:detailed",),
    )
    detailed = _record(
        store, "preference:detailed", "PREFERENCE",
        "Prefer detailed progress reports during critical recovery",
        project="jarvis",
        attributes={
            "preference_key": "briefing_detail",
            "preference_value": "DETAILED",
        },
        contradicts=("preference:quiet",),
    )
    yesterday_a = _record(
        store, "unresolved:yesterday-a", "UNRESOLVED_ITEM",
        "Provider recovery item A",
        at="2026-09-02T10:00:00Z",
    )
    yesterday_b = _record(
        store, "unresolved:yesterday-b", "UNRESOLVED_ITEM",
        "Provider recovery item B",
        at="2026-09-02T10:00:00Z",
    )

    service = OwnerControlService(database, secret=SECRET)
    investigate_order = _apply_standing_order(
        service,
        "If a project becomes blocked, investigate it.",
        directive_id="standing-investigate",
        nonce="standing-investigate",
        project="jarvis",
    )
    clean_order = _apply_standing_order(
        service,
        "Keep my projects clean.",
        directive_id="standing-clean",
        nonce="standing-clean",
        project="jarvis",
    )
    revoke_text = "Owner revoked the project-cleanliness standing order"
    service.apply(
        "REVOKE",
        target="standing-clean",
        exact_text=revoke_text,
        nonce="standing-clean-revoke",
        signature=sign_command(
            SECRET,
            "REVOKE",
            "standing-clean",
            revoke_text,
            "standing-clean-revoke",
        ),
    )

    query = OwnerContextQuery(
        project_id="jarvis",
        current_situation="The JARVIS project is blocked by provider HTTP 500",
        requested_task="Continue owner-context durability work",
        recent_context_ids=(jarvis_objective,),
        situation_state="BLOCKED",
        now=now,
    )
    projection = project_owner_context(database, now=now)
    compiled = compile_owner_context(
        database, query, max_items=20, max_chars=30000,
    )
    bounded = compile_owner_context(
        database, query, max_items=5, max_chars=6000, per_kind_limit=2,
    )
    active_orders = project_standing_orders(
        database, now=now, include_history=False,
    )
    strong_reference = resolve_owner_reference(
        database, "continue this", context=query,
    )
    ambiguous_reference = resolve_owner_reference(
        database, "the thing from yesterday", now=now,
    )
    matched_order = StandingOrderEvaluator(database).evaluate(query)
    stop_path = proof_root / "EMERGENCY_STOP"
    stop_path.write_text("owner stop\n", encoding="utf-8")
    stopped_order = StandingOrderEvaluator(
        database, stop_path=stop_path,
    ).evaluate(query)
    stop_path.unlink()

    veto_text = "veto investigation while provider policy is reviewed"
    service.apply(
        "VETO_ACTION",
        target="jarvis",
        exact_text=veto_text,
        nonce="veto-investigation",
        signature=sign_command(
            SECRET,
            "VETO_ACTION",
            "jarvis",
            veto_text,
            "veto-investigation",
        ),
        directive_id="veto-investigation",
    )
    vetoed_order = StandingOrderEvaluator(database).evaluate(query)
    projection = project_owner_context(database, now=now)

    before_rebuild = _snapshot(database.path, now)
    fresh_process = _fresh_process_snapshot(database.path, now)
    database.rebuild_projections()
    after_rebuild = _fresh_process_snapshot(database.path, now)

    compiled_ids = {
        item["context_id"] for item in compiled["items"]
    }
    active_order_ids = {
        order["standing_order_id"] for order in active_orders
    }
    conflict_ids = {
        context_id
        for conflict in compiled["contradictions"]
        for context_id in conflict["context_ids"]
    }
    required_kinds = {
        "GOAL",
        "PRIORITY",
        "PROGRAM",
        "PROJECT",
        "ACTIVE_OBJECTIVE",
        "STANDING_ORDER",
        "DECISION",
        "VETO",
        "CONSTRAINT",
        "PERMISSION",
        "PREFERENCE",
        "UNRESOLVED_ITEM",
        "COMMITMENT",
        "WAITING_ON_OWNER",
        "REJECTED_APPROACH",
        "HISTORICAL_OUTCOME",
    }
    reconstructed_kinds = {
        item["kind"] for item in fresh_process["history"]
    }
    bounded_size = len(canonical_json(bounded))
    checks = {
        "01_fresh_process_reconstructs_owner_project_context": (
            any(
                item["context_id"] == jarvis_project
                for item in fresh_process["current"]
            )
            and any(
                item["context_id"] == jarvis_objective
                for item in fresh_process["current"]
            )
            and required_kinds.issubset(reconstructed_kinds)
        ),
        "02_irrelevant_history_excluded": (
            "LeadGen" not in canonical_json(compiled)
        ),
        "03_revoked_standing_order_not_active": (
            investigate_order in active_order_ids
            and clean_order not in active_order_ids
        ),
        "04_stop_veto_outranks_standing_order": (
            matched_order["status"] == "MATCHED"
            and stopped_order["status"] == "BLOCKED_BY_OWNER_CONTROL"
            and stopped_order["owner_control_reason"]
            == "emergency stop marker is present"
            and not stopped_order["matched"]
            and vetoed_order["status"] == "BLOCKED_BY_OWNER_CONTROL"
            and not vetoed_order["matched"]
        ),
        "05_contradictory_preferences_visible": (
            {quiet, detailed}.issubset(conflict_ids)
        ),
        "06_stale_context_does_not_override_newer_truth": (
            projection.get(old_priority).state == "STALE"
            and old_priority not in compiled_ids
            and new_priority in compiled_ids
        ),
        "07_continue_this_resolves_with_strong_evidence": (
            strong_reference["status"] == "RESOLVED"
            and strong_reference["selected_context_id"] == jarvis_objective
        ),
        "08_ambiguous_reference_reports_ambiguity": (
            ambiguous_reference["status"] == "AMBIGUOUS"
            and {
                yesterday_a, yesterday_b,
            }.issubset({
                item["context_id"]
                for item in ambiguous_reference["candidates"]
            })
        ),
        "09_context_remains_bounded": (
            bounded["bounds"]["used_items"] <= 5
            and bounded["bounds"]["used_chars"] == bounded_size
            and bounded_size <= 6000
            and bounded["bounds"]["truncated"] is True
        ),
        "10_restart_reconstructs_identical_durable_truth": (
            canonical_json(before_rebuild) == canonical_json(fresh_process)
            == canonical_json(after_rebuild)
        ),
    }
    source = _source_identity()
    source_attestations = {
        "branch_matches_candidate": source["branch"] == expected_branch,
        "base_is_exact_merge_base": source["merge_base"] == BASE_SHA,
        "worktree_is_clean": source["dirty"] is False,
        "source_is_reachable_from_head": subprocess.run(
            [
                "git",
                "merge-base",
                "--is-ancestor",
                source["source_sha"],
                "HEAD",
            ],
            cwd=REPO_ROOT,
            check=False,
        ).returncode == 0,
    }
    result = {
        "contract": "jarvis.v13.owner-context-proof.v1",
        "generated_at": now,
        "database": str(database.path),
        "schema": database.initialize(),
        "source": source,
        "source_attestations": source_attestations,
        "checks": checks,
        "passed": all(checks.values()) and all(source_attestations.values()),
        "counts": {
            "ledger_events": database.event_count(),
            "context_history": len(projection.records),
            "context_current": len(projection.current),
            "standing_orders_active": len(active_orders),
            "compiled_items": compiled["bounds"]["used_items"],
        },
        "artifacts": {
            "compiled_context": compiled,
            "bounded_context": bounded,
            "strong_reference": strong_reference,
            "ambiguous_reference": ambiguous_reference,
            "standing_order_match": matched_order,
            "standing_order_stopped": stopped_order,
            "standing_order_vetoed": vetoed_order,
            "reconstructed_context_kinds": sorted(reconstructed_kinds),
        },
    }
    output = proof_root / "V13_OWNER_CONTEXT_PROOF.json"
    output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    result["proof_path"] = str(output)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--snapshot-db", type=Path)
    parser.add_argument("--now", default=NOW)
    parser.add_argument("--expected-branch", default=EXPECTED_BRANCH)
    args = parser.parse_args()
    if args.snapshot_db is not None:
        print(json.dumps(
            _snapshot(args.snapshot_db, args.now),
            sort_keys=True,
        ))
        return 0
    if args.output_root is None:
        parser.error("--output-root is required")
    result = run_proof(
        args.output_root,
        now=args.now,
        expected_branch=args.expected_branch,
    )
    print(json.dumps({
        "passed": result["passed"],
        "proof_path": result["proof_path"],
        "checks": result["checks"],
        "counts": result["counts"],
        "schema": result["schema"],
        "source": result["source"],
        "source_attestations": result["source_attestations"],
    }, indent=2, sort_keys=True))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
