"""Produce compact disposable proof for the V11 requirement bridge.

This harness mutates only a temporary Foundry database in order to model
Catalog readiness transitions.  Requirement resolution itself is asserted to
be read-only.  It performs no live provider action and no self-expansion.
"""
from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path
from typing import Any

from jarvis_foundry.capabilities.registry import (
    record_capability_observation as record_legacy_observation,
)
from jarvis_foundry.capabilities.registry import (
    register_capability as register_legacy_capability,
)
from jarvis_foundry.capability_requirements import (
    CONTRACT_VERSION,
    FABRIC_AUTHORITY,
    FABRIC_PATH,
    refresh_capability_requirements,
    resolve_capability_requirements,
)
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.discovery_candidates import ingest_observed_candidate
from jarvis_foundry.fabric.catalog import record_outcome
from jarvis_foundry.fabric.loop import seed_builtins
from jarvis_foundry.fabric.model import VerifyVerdict
from jarvis_foundry.supervisor.authority import SUPERVISOR_CONTROLLER


LOCAL_REQUIREMENTS = (
    "cap.file.read",
    "cap.file.write",
    "cap.git.inspect",
    "cap.git.diff",
    "cap.git.commit",
    "cap.script.run",
    "cap.tests.run",
)


def _candidate(
    database: FoundryDatabase,
    *,
    token: str,
    candidate_type: str,
    objective: str,
    trusted_requirements: dict[str, Any] | None = None,
) -> dict[str, Any]:
    created = ingest_observed_candidate(
        database,
        "v11-capability-requirements-proof",
        observation_id=f"proof-observation-{token}",
        source_kind="project" if candidate_type == "tracked_work" else "web",
        source_identity=f"proof-source-{token}",
        content_hash="sha256:" + token * 64,
        objective=objective,
        candidate_type=candidate_type,
        target_project="JARVIS Foundry",
        trusted_requirements=trusted_requirements,
    )
    row = database.row("candidates", "candidate_id", str(created["candidate_id"]))
    if row is None:
        raise RuntimeError("proof candidate was not projected")
    return row


def _mark_ready(database: FoundryDatabase, *capability_ids: str) -> None:
    for capability_id in capability_ids:
        record_outcome(
            database,
            capability_id,
            success=True,
            verified=VerifyVerdict.MATCH,
            note=f"disposable V11 requirement proof fixture: {capability_id}",
            at="2026-09-02T12:00:00Z",
        )


def _resolve_read_only(
    database: FoundryDatabase,
    candidate: dict[str, Any],
    **kwargs: Any,
):
    before = database.event_count()
    result = resolve_capability_requirements(database, candidate, **kwargs)
    after = database.event_count()
    if after != before:
        raise AssertionError(
            f"requirement resolution wrote {after - before} unexpected events"
        )
    return result


def build_proof() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="jarvis-v11-capability-proof-") as raw:
        database = FoundryDatabase(Path(raw) / "foundry.db")
        database.initialize()
        seed_builtins(database)
        _mark_ready(database, *LOCAL_REQUIREMENTS)

        local = _candidate(
            database,
            token="1",
            candidate_type="tracked_work",
            objective="Implement a bounded tested JARVIS repository fix",
            trusted_requirements={
                "proposed_target": {
                    "kind": "local_repository",
                    "operation": "modify",
                    "resource_id": "resource-jarvis-foundry",
                },
                "project_profile": {
                    "kind": "local_repository",
                    "writable": True,
                    "tests_available": True,
                },
                "source": "proof-project-profile",
            },
        )
        local_result = _resolve_read_only(database, local)
        if not local_result.executable or local_result.readiness.value != "READY":
            raise AssertionError("ready local candidate did not resolve executable")

        _mark_ready(database, "cap.github.issue.read")
        github_issue = _candidate(
            database,
            token="5",
            candidate_type="github_issue",
            objective="Investigate and repair one observed repository issue",
        )
        github_issue_result = _resolve_read_only(database, github_issue)
        if not github_issue_result.executable:
            raise AssertionError("ready GitHub issue candidate did not resolve executable")

        missing = _candidate(
            database,
            token="2",
            candidate_type="test_opportunity",
            objective="Use an unavailable bounded Windows UI ability",
        )
        missing_result = _resolve_read_only(
            database,
            missing,
            action_metadata=[{"capability_id": "cap.windows.uia.execute"}],
        )
        if missing_result.missing_capabilities != ("cap.windows.uia.execute",):
            raise AssertionError("missing capability did not block")

        provider = _candidate(
            database,
            token="3",
            candidate_type="github_issue_create",
            objective="Create one provider-visible GitHub issue",
        )
        register_legacy_capability(
            database,
            capability_id="cap.github.issue.create",
            name="legacy provider claim",
            family="provider",
            surfaces=["github"],
            effect_class="web-write",
            risk="high",
            verification_hint="legacy-only",
            controller=SUPERVISOR_CONTROLLER,
            source_type="supervisor",
            source_id="v11-proof-legacy",
        )
        record_legacy_observation(
            database,
            "cap.github.issue.create",
            status="HEALTHY",
            auth_state="READY",
            availability="AVAILABLE",
            controller=SUPERVISOR_CONTROLLER,
            source_type="supervisor",
            source_id="v11-proof-legacy",
        )
        provider_result = _resolve_read_only(database, provider)
        provider_evidence = next(
            item for item in provider_result.capability_evidence
            if item.capability_id == "cap.github.issue.create"
        )
        if provider_result.executable or provider_evidence.state != "UNKNOWN":
            raise AssertionError("unproven provider write was treated as ready")

        web = _candidate(
            database,
            token="4",
            candidate_type="web_watch",
            objective="Research a bounded web source",
        )
        web_before = _resolve_read_only(database, web)
        _mark_ready(database, "cap.browser.web.read")
        before_refresh_events = database.event_count()
        web_after = refresh_capability_requirements(database, web_before)
        if database.event_count() != before_refresh_events:
            raise AssertionError("readiness refresh wrote an unexpected event")
        if not web_after.executable:
            raise AssertionError("Fabric readiness refresh did not unblock web research")
        if web_after.requirement_set_id != web_before.requirement_set_id:
            raise AssertionError("readiness refresh changed stable requirement identity")
        if web_after.snapshot_id == web_before.snapshot_id:
            raise AssertionError("readiness refresh did not change snapshot identity")

        cases = {
            "tracked_work_local": local_result.as_dict(),
            "github_issue_observation": github_issue_result.as_dict(),
            "test_opportunity_missing": missing_result.as_dict(),
            "github_issue_create_provider_write": provider_result.as_dict(),
            "web_watch_before_refresh": web_before.as_dict(),
            "web_watch_after_refresh": web_after.as_dict(),
        }
        return {
            "verdict": "V11_CAPABILITY_REQUIREMENTS_READY",
            "contract_version": CONTRACT_VERSION,
            "scope": "DISPOSABLE_CANDIDATE_DATABASE_NOT_LIVE_PROVIDER_PROOF",
            "authority": FABRIC_AUTHORITY,
            "fabric_path": FABRIC_PATH,
            "candidates_tested": list(cases),
            "ready": [
                "tracked_work_local",
                "github_issue_observation",
                "web_watch_after_refresh",
            ],
            "blocked": [
                "test_opportunity_missing",
                "github_issue_create_provider_write",
                "web_watch_before_refresh",
            ],
            "readiness_refresh": {
                "requirement_set_id_stable": True,
                "snapshot_id_changed": True,
                "before": web_before.snapshot_id,
                "after": web_after.snapshot_id,
            },
            "legacy_registry_authoritative": False,
            "resolver_writes": 0,
            "self_expansion_attempts": 0,
            "cases": cases,
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    proof = build_proof()
    rendered = json.dumps(proof, indent=2, sort_keys=True) + "\n"
    if args.output is None:
        print(rendered, end="")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
        print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
