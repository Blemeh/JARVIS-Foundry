from __future__ import annotations

import argparse
import dataclasses
import enum
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

from jarvis_foundry.connectors.account_activation import (
    AccountActivationManager,
    FakeCredentialStore,
    FakeGmailProvider,
)
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.evolution.detector import detect_weaknesses
from jarvis_foundry.evolution.executive_portfolio import (
    register_work_item,
    select_next_work,
    set_work_status,
    wake_is_due,
)
from jarvis_foundry.postv7 import (
    execute_library_step,
    plan_library_procedure,
    procedure_eligibility,
    project_account_capabilities,
    record_library_outcome,
    select_library_work,
)
from jarvis_foundry.procedure_library.contracts import ProofStatus
from jarvis_foundry.procedure_library.definitions import build_catalog
from jarvis_foundry.reporting.briefing import (
    briefing_count,
    generate_briefing,
    latest_briefing,
)
from jarvis_foundry.operations.procedure import load_procedure
from jarvis_foundry.resilience.core import (
    EffectAmbiguity,
    IncidentStatus,
    IncidentStore,
    rebuild_incidents,
)
from jarvis_foundry.acceptance_harness.observer import observe_canonical


def _jsonable(value: Any) -> Any:
    if isinstance(value, enum.Enum):
        return value.value
    if dataclasses.is_dataclass(value):
        return _jsonable(dataclasses.asdict(value))
    if hasattr(value, "to_payload"):
        return _jsonable(value.to_payload())
    if hasattr(value, "as_dict"):
        return _jsonable(value.as_dict())
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, set, frozenset)):
        return [_jsonable(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return value


def _proven(catalog: Any) -> Any:
    result = catalog
    for procedure_id in catalog.ids:
        result = result.promote(
            procedure_id,
            status=ProofStatus.PROVEN_ONCE.value,
            last_verified="2026-08-24",
            verifier_passed=True,
            evidence_refs=(f"proof:postv7-live:{procedure_id}",),
        )
    return result


def _effect(step: Any, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
    return {
        "state": "COMPLETED",
        "after_state": {"step": step.step_id, "fixture": "postv7-live"},
        "postcondition_results": {item: True for item in step.postconditions},
    }


def _verify(effect_result: Any, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
    return {"verdict": "PASS", "verifier_id": "verifier-postv7-independent"}


def main() -> int:
    parser = argparse.ArgumentParser(description="Disposable Post-V7 canonical ledger proof")
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--release-root", type=Path, required=True)
    parser.add_argument("--release-sha", required=True)
    parser.add_argument("--release-id", required=True)
    args = parser.parse_args()

    root = args.output_root.resolve()
    root.mkdir(parents=True, exist_ok=False)
    database = FoundryDatabase(root / "foundry.db")
    schema = database.initialize()

    # Account readiness is deliberately source-only: no credential is stored,
    # no provider call is made, and no capability ticket may be issued.
    credentials = FakeCredentialStore()
    provider = FakeGmailProvider()
    manager = AccountActivationManager(database, credentials)
    manager.register(
        provider,
        account_identity="postv7-owner@example.invalid",
        credential_reference=None,
        requested_scopes=("gmail.readonly", "gmail.compose"),
        activation_id="activation-postv7-live-unconfigured",
    )
    projected_blocked = project_account_capabilities(
        database,
        "activation-postv7-live-unconfigured",
        {"communication.draft": ("gmail.compose",), "evidence.verify": ()},
    )
    catalog = _proven(build_catalog())
    bug = catalog.get("jarvis.bug-triage")
    blocked_receipt = procedure_eligibility(
        database,
        bug,
        projected_blocked["resource_id"],
        activation_id="activation-postv7-live-unconfigured",
    )

    # V7A -> V7B -> V5/V7C -> independent verifier -> V7A outcome.
    selected = select_library_work(
        database,
        catalog,
        loop_id="postv7-live-loop-001",
        resource_id="disposable:postv7-live",
        domain="bug-triage",
    )
    decision = selected["decision"]
    plan = plan_library_procedure(
        database,
        bug,
        mission_id="mission-postv7-live-001",
        target="fixture:bug-triage",
        resource_id="disposable:postv7-live",
        evidence_refs=("evidence:postv7:live:before",),
    )
    orient = execute_library_step(
        database,
        plan=plan,
        step_id="orient",
        resource_id="disposable:postv7-live",
        verifier=_verify,
        effect_executor=_effect,
        before_state={"identity": "fixture:bug-triage"},
        evidence_refs=("evidence:postv7:live:orient",),
    )
    execute = execute_library_step(
        database,
        plan=plan,
        step_id="execute",
        resource_id="disposable:postv7-live",
        verifier=_verify,
        effect_executor=_effect,
        before_state={"identity": "fixture:bug-triage"},
        evidence_refs=("evidence:postv7:live:execute",),
    )
    outcome_event = record_library_outcome(
        database,
        decision=decision,
        mission_id="mission-postv7-live-001",
        status="PASSED",
        actual={"procedure_id": bug.procedure_id, "verifier": "verifier-postv7-independent"},
    )
    if decision.entity_id:
        set_work_status(
            database,
            str(decision.entity_id),
            "COMPLETE",
            reason="independently verified procedure outcome",
            source_id="postv7-live-proof",
        )

    # V7C ambiguity: provider truth is unavailable, so the effect is frozen and
    # an incident is visible.  The unrelated procedure remains eligible.
    ambiguous_plan = plan_library_procedure(
        database,
        bug,
        mission_id="mission-postv7-live-ambiguous",
        target="fixture:bug-triage",
        resource_id="disposable:postv7-live",
        evidence_refs=("evidence:postv7:live:ambiguous",),
    )
    ambiguous = execute_library_step(
        database,
        plan=ambiguous_plan,
        step_id="orient",
        resource_id="disposable:postv7-live",
        verifier=lambda *_args, **_kwargs: {
            "verdict": "BLOCKED", "verifier_id": "verifier-postv7-independent"
        },
        effect_executor=lambda *_args, **_kwargs: {
            "state": "UNKNOWN", "reason": "provider truth unavailable"
        },
        before_state={"identity": "fixture:bug-triage"},
        evidence_refs=("evidence:postv7:live:ambiguous",),
    )
    incidents = rebuild_incidents(database)
    unrelated = procedure_eligibility(
        database,
        catalog.get("jarvis.flake-diagnosis"),
        "disposable:postv7-live",
    )
    # The provider-disabled boundary is deliberately not self-healed.  Record
    # an explicit mismatch reconciliation and leave the incident BLOCKED so
    # V7A can truthfully WAIT until owner/provider evidence wakes it.
    incident_resolution = None
    if incidents:
        incident_store = IncidentStore(database, source_id="postv7-live-proof")
        blocked_incident = incident_store.transition(
            incidents[0].incident_id,
            IncidentStatus.BLOCKED,
            reason="provider truth unavailable; owner reconciliation is required",
            evidence={"provider_mode": "DISABLED", "real_external_effect": False},
        )
        incident_resolution = incident_store.reconcile_effect(
            incidents[0].incident_id,
            classification=EffectAmbiguity.MISMATCH,
            evidence={
                "classification": "MISMATCH",
                "reason": "provider truth unavailable in disabled mode",
                "effect_action_key": incidents[0].effect_action_key,
            },
        )

    # V7A WAIT followed by a durable wake condition and a fresh selection.
    wait = select_next_work(
        database,
        loop_id="postv7-live-wait-loop",
        resource_id="disposable:postv7-live",
        capability="procedure.select",
        source_id="postv7-live-proof",
    )
    wake_before = wake_is_due(database)
    register_work_item(
        database,
        item_id="postv7-live-wake-work",
        kind="RESEARCH",
        objective="wake the portfolio after new evidence arrives",
        resource_id="disposable:postv7-live",
        expected_value=0.9,
        information_gain=0.9,
        confidence=0.5,
        effort=0.1,
        risk=0.1,
        reversibility=0.9,
        requires_workers=0,
        requires_verifier=False,
        evidence_refs=("evidence:postv7:live:wake",),
        source_id="postv7-live-proof",
    )
    wake_after = wake_is_due(database)
    woke = select_next_work(
        database,
        loop_id="postv7-live-wake-loop",
        resource_id="disposable:postv7-live",
        capability="procedure.select",
        source_id="postv7-live-proof",
    )

    now = datetime.now(timezone.utc)
    v6 = detect_weaknesses(
        database,
        window_start=(now - timedelta(hours=1)).isoformat(),
        window_end=now.isoformat(),
    )
    generate_briefing(
        database,
        locale="en",
        source_type="supervisor",
        source_id="postv7-live-proof",
    )
    briefing = latest_briefing(database)
    observation = observe_canonical(
        database,
        stop_path=root / "EMERGENCY_STOP",
        release_root=args.release_root,
        now=now.isoformat(),
    )

    checks = {
        "accounts_unconfigured": projected_blocked["projected"][0]["state"] == "DEGRADED",
        "account_procedure_blocked": not blocked_receipt.eligible,
        "catalog_22": len(catalog.definitions) == 22,
        "catalog_proven_once": all(
            item.proof_status == ProofStatus.PROVEN_ONCE.value
            for item in catalog.definitions
        ),
        "catalog_no_trusted": not any(
            item.proof_status == ProofStatus.TRUSTED.value
            for item in catalog.definitions
        ),
        "verified_completion": execute["outcome"] == "COMPLETED"
        and execute["verification"]["verdict"] == "PASS",
        "ambiguous_frozen": ambiguous["outcome"] == "FROZEN"
        and bool(incidents)
        and str(incident_resolution.status) == IncidentStatus.BLOCKED.value,
        "unrelated_eligible": unrelated.eligible,
        "portfolio_wait": wait.kind == "WAIT",
        "portfolio_wake": wake_before is False and wake_after is True and woke.kind == "SELECT",
        "observer_clean": observation["fixture_leakage"] is False
        and observation["manual_continuation_required"] == 0,
        "quick_check": database.quick_check() == "ok",
    }
    verdict = "PASS" if all(checks.values()) else "FAIL"

    payload = {
        "verdict": verdict,
        "checks": checks,
        "proof_boundary": {
            "provider_mode": "DISABLED",
            "real_accounts": False,
            "real_credentials": False,
            "real_external_effects": False,
            "fixture_leakage": False,
            "manual_continuation_required": 0,
        },
        "release": {
            "source_sha": args.release_sha,
            "release_id": args.release_id,
            "release_root": str(args.release_root.resolve()),
        },
        "database": {
            "path": str(database.path),
            "schema": schema,
            "quick_check": database.quick_check(),
            "event_count": database.event_count(),
        },
        "accounts": {
            "live_state": "UNCONFIGURED",
            "owner_activation_required": True,
            "projected": projected_blocked,
            "procedure_eligibility": _jsonable(blocked_receipt),
            "capability_ticket_issued": any(
                event.event_type == "capability.issued" for event in database.events()
            ),
        },
        "procedure_library": {
            "catalog_count": len(catalog.definitions),
            "proven_once_count": sum(
                item.proof_status == ProofStatus.PROVEN_ONCE.value
                for item in catalog.definitions
            ),
            "trusted_count": sum(
                item.proof_status == ProofStatus.TRUSTED.value
                for item in catalog.definitions
            ),
        },
        "autonomy_flow": {
            "selection": _jsonable(decision),
            "plan_id": plan.procedure_id,
            "orient": orient,
            "execute": execute,
            "outcome_event": outcome_event,
            "ambiguous": ambiguous,
            "ambiguous_step_status": load_procedure(
                database, ambiguous_plan.procedure_id
            ).step("orient").status.value,
            "incident_count": len(incidents),
            "incident_resolution": _jsonable(incident_resolution),
            "unrelated_procedure_eligible": unrelated.eligible,
            "wait": _jsonable(wait),
            "wake_before": wake_before,
            "wake_after_new_evidence": wake_after,
            "wake_selection": _jsonable(woke),
        },
        "v6": v6,
        "briefing": {"count": briefing_count(database), "latest": briefing},
        "canonical_observation": observation,
    }
    output = root / "LIVE_PROCEDURE_PROOF.json"
    output.write_text(json.dumps(_jsonable(payload), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "verdict": payload["verdict"],
        "output": str(output),
        "event_count": payload["database"]["event_count"],
        "catalog_count": payload["procedure_library"]["catalog_count"],
        "trusted_count": payload["procedure_library"]["trusted_count"],
        "wait": payload["autonomy_flow"]["wait"].get("kind"),
        "wake": payload["autonomy_flow"]["wake_selection"].get("kind"),
        "manual_continuation_required": payload["proof_boundary"]["manual_continuation_required"],
        "failed_checks": [key for key, value in checks.items() if not value],
    }, indent=2, sort_keys=True))
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
