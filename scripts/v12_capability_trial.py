"""Run the V12 capability trial lifecycle against a fresh disposable DB.

This is an executable proof companion, not a live activation tool.  It uses
the tracked stdlib-hashlib solution, runs the adapter through the real Fabric
action path, reopens the database, and then disables the registration while
checking that the active catalog hash returns to its pre-trial value.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.fabric import (
    CapabilitySpec,
    CapabilityTrialPipeline,
    CapabilityTrust,
    DependencyPin,
    ExecutorContext,
    Kind,
    SelectedCapabilitySolution,
    TaskIntent,
    TrialCase,
    UnattendedMode,
    VerifyVerdict,
    capability_registration,
    catalog_truth_snapshot,
    execute_action,
    get_spec,
    observed_readiness,
    record_outcome,
    register_capability,
)
from jarvis_foundry.fabric.model import Semantic


def _descriptor(run_root: Path) -> SelectedCapabilitySolution:
    contract_input = run_root / "contract-input.bin"
    mission_input = run_root / "mission-input.bin"
    contract_input.write_bytes(b"V12 contract input\n")
    mission_input.write_bytes(b"V12 realistic capability mission\n")
    source = ROOT / "examples" / "capability_solutions" / "sha256" / "solution.py"
    return SelectedCapabilitySolution(
        solution_id="solution.python.stdlib.sha256",
        spec=CapabilitySpec(
            capability_id="cap.hash.file.sha256.trial",
            version="1.0.0",
            kind=Kind.ADAPTER,
            action_kind="hash.file.sha256",
            semantic=Semantic.READ,
            executor="selected-by-pipeline",
            unattended=UnattendedMode.ALLOWED,
            limitations=("owned-root files only", "read-only"),
        ),
        source_path=source,
        dependencies=(
            DependencyPin("python-stdlib", sys.version.split()[0], "runtime"),
            DependencyPin("hashlib", "stdlib", "python-runtime"),
        ),
        contract_cases=(
            TrialCase(
                "contract-input",
                {"path": str(contract_input)},
                {"claimed": "COMPLETED", "sha256": hashlib.sha256(contract_input.read_bytes()).hexdigest()},
            ),
        ),
        mission=TrialCase(
            "realistic-mission",
            {"path": str(mission_input)},
            {"claimed": "COMPLETED", "sha256": hashlib.sha256(mission_input.read_bytes()).hexdigest(), "bytes": mission_input.stat().st_size},
        ),
        config={"mode": "sha256", "network": "disabled", "effects": "read-only"},
        selection_reason="V12 bounded capability-trial proof",
    )


def run(evidence_root: Path) -> dict[str, object]:
    evidence_root.mkdir(parents=True, exist_ok=True)
    run_root = evidence_root / "run"
    suffix = 1
    while (run_root / "disposable.sqlite").exists():
        suffix += 1
        run_root = evidence_root / f"run-{suffix}"
    run_root.mkdir(parents=True, exist_ok=True)
    database = FoundryDatabase(run_root / "disposable.sqlite")
    database.initialize()
    descriptor = _descriptor(run_root)
    pipeline = CapabilityTrialPipeline(database, run_root / "trial-workspaces")
    before = catalog_truth_snapshot(database)
    adopted = pipeline.run(descriptor)
    registration = capability_registration(database, descriptor.spec.capability_id)
    probation_readiness = observed_readiness(database, descriptor.spec.capability_id).as_dict()
    owned = run_root / "owned"
    owned.mkdir(parents=True, exist_ok=True)
    post_adoption = owned / "post-adoption.bin"
    post_adoption.write_bytes(b"Fabric probation read")
    action = execute_action(
        database,
        ExecutorContext(database=database, owned_root=owned),
        TaskIntent(task_id="v12-real-trial", required_actions=("hash.file.sha256",)),
        "hash.file.sha256",
        {"path": str(post_adoption)},
    )
    replay = pipeline.run(descriptor)
    disabled = pipeline.disable(descriptor)
    reopened = FoundryDatabase(database.path)
    # Capture restoration before the separate irreversible-gate probe adds
    # its own intentionally disposable catalog entry.
    active_after_disable = catalog_truth_snapshot(reopened)

    # Explicitly exercise the irreversible probation gate in the same
    # disposable catalog.  No provider or effect transport is configured.
    irreversible = CapabilitySpec(
        capability_id="cap.trial.irreversible.probe",
        version="1.0.0",
        kind=Kind.ADAPTER,
        action_kind="gmail.send",
        semantic=Semantic.COMMUNICATE,
        executor="not-invoked",
        unattended=UnattendedMode.ALLOWED,
    )
    register_capability(
        database,
        irreversible,
        trust=CapabilityTrust.PROBATION,
        registration_id="fabric-registration:trial:irreversible-probe",
        adoption={"trial_id": "trial:irreversible-probe"},
    )
    record_outcome(database, irreversible.capability_id, success=True, verified=VerifyVerdict.MATCH, note="bounded gate probe")
    blocked = execute_action(
        database,
        ExecutorContext(database=database, owned_root=owned),
        TaskIntent(task_id="v12-probation-gate", required_actions=("gmail.send",)),
        "gmail.send",
        {"to": "owner@example.invalid", "body": "not sent"},
    )

    event_counts: dict[str, int] = {}
    for event in database.events():
        event_counts[event.event_type] = event_counts.get(event.event_type, 0) + 1
    report: dict[str, object] = {
        "protocol": "jarvis.v12.capability.trial.proof.v1",
        "base_sha": "30998ef06d9be73adb30387c10a7fa72ef910d28",
        "branch": "candidate/v12-capability-trial-20260902",
        "database": str(database.path),
        "trial_workspace": adopted.workspace,
        "adapter_manifest": str(Path(adopted.workspace) / "adapter-manifest.json"),
        "verification_evidence": str(Path(adopted.workspace) / "evidence" / "independent-verification.json"),
        "descriptor": descriptor.as_dict(),
        "trial_id": adopted.trial_id,
        "registration_id": descriptor.registration_id,
        "before_catalog_sha256": before["sha256"],
        "probation": {
            "status": adopted.status,
            "trust": registration.get("trust") if registration else None,
            "readiness": probation_readiness,
            "verification": adopted.verification,
        },
        "real_trial": {
            "claimed": action.claimed.value,
            "verified": action.verified.value,
            "success": action.success,
            "sha256": action.claimed_effect.get("sha256"),
        },
        "replay": {"status": replay.status, "registration_id": replay.registration_id},
        "rollback_disable": {
            "status": disabled.status,
            "restored": bool(disabled.disable and disabled.disable.get("restored")),
            "catalog_after_sha256": active_after_disable["sha256"],
            "capability_present_after_disable": get_spec(reopened, descriptor.spec.capability_id) is not None,
        },
        "probation_irreversible_gate": {
            "claimed": blocked.claimed.value,
            "note": blocked.note,
            "effect_started": any(event.event_type == "system.action_started" for event in database.events()),
        },
        "event_counts": event_counts,
    }
    report["passed"] = bool(
        adopted.status == "PROBATION"
        and registration is not None
        and registration.get("trust") == "PROBATION"
        and adopted.verification and adopted.verification.get("verdict") == "PASS"
        and action.success
        and replay.status == "PROBATION"
        and disabled.status == "RESTORED"
        and active_after_disable["sha256"] == before["sha256"]
        and get_spec(reopened, descriptor.spec.capability_id) is None
        and blocked.claimed.value == "BLOCKED"
        and not report["probation_irreversible_gate"]["effect_started"]
    )
    output = evidence_root / "V12_CAPABILITY_TRIAL_PROOF.json"
    report["evidence"] = str(output)
    output.write_text(json.dumps(report, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-root", type=Path, default=Path(r"C:\temp\JARVIS-V12-Capability-Trial-20260902-evidence"))
    args = parser.parse_args(argv)
    report = run(args.evidence_root.expanduser().resolve())
    print(json.dumps(report, indent=2, sort_keys=True, default=str))
    return 0 if report.get("passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
