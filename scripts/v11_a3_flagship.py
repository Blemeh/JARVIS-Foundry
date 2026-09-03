"""Run the disposable real-source, real-worker V11 A3 flagship proof.

The driver creates only a disposable v39 ledger/runtime and local Git
worktree.  Candidate selection, objective/mission creation, worker dispatch,
verification, and local commits all flow through the normal JARVIS runtime.
It never merges, pushes, deploys, sends mail, or touches the live JARVIS home.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from jarvis_foundry.connectors.github_research import GitHubResearchConnector
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.discovery_sources import SourceSpec
from jarvis_foundry.events import Event
from jarvis_foundry.executive.autonomy import rebuild_executive_state
from jarvis_foundry.executive.owner_context import seed_owner_context
from jarvis_foundry.fabric.catalog import record_outcome as record_fabric_outcome
from jarvis_foundry.fabric.loop import seed_builtins
from jarvis_foundry.fabric.model import VerifyVerdict
from jarvis_foundry.initiative_a3 import A3AdmissionPolicy
from jarvis_foundry.paths import FoundryPaths
from jarvis_foundry.reconcile import reconcile
from jarvis_foundry.runtime.config import WorkerRuntimeConfig
from jarvis_foundry.runtime.provider_config import load_provider_settings
from jarvis_foundry.runtime.worker_loop import run_cycle
from jarvis_foundry.world_model.gaps import WorldKnowledgeGapStore
from jarvis_foundry.world_model.research_handoff import handoff_to_research
from jarvis_foundry.world_model.signals import WorldSignalStore


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout.strip()


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _iso_after(hours: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat().replace("+00:00", "Z")


def _seed_owner_authority(database: FoundryDatabase, owner_repo: Path) -> None:
    database.append(Event.create(
        "system.start_build_recorded",
        {
            "received_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "standing_mandate": True,
            "scope": "V11 A3 one small reversible isolated owner-repository build",
        },
        source_type="owner",
        source_id="v11-a3-owner-mandate",
        idempotency_key="system.start-build",
    ))
    seed_owner_context(
        database,
        goals=[{
            "text": "Make JARVIS Foundry more reliable through bounded, verified autonomous improvements",
            "source_ref": "IDEA.md",
            "source_hash": _sha(owner_repo / "IDEA.md"),
        }],
        projects=[{
            "text": "JARVIS Foundry",
            "source_ref": "docs/CURRENT_STATE.md",
            "source_hash": _sha(owner_repo / "docs" / "CURRENT_STATE.md"),
        }],
        standing_rejections=[{
            "text": "No merge, push, deploy, email, live runtime, production database, or Scheduler mutation",
            "source_ref": "docs/handoffs/JARVIS_FROZEN_MASTER_ROADMAP_TO_1_0_2026-09-01.md",
            "source_hash": _sha(
                owner_repo / "docs" / "handoffs"
                / "JARVIS_FROZEN_MASTER_ROADMAP_TO_1_0_2026-09-01.md"
            ),
        }],
        actor="v11-a3-owner-context",
    )


def _prove_capabilities(database: FoundryDatabase, owner_repo: Path, evidence_root: Path) -> dict[str, Any]:
    probe = evidence_root / ".write-probe"
    probe.write_text("A3 disposable write probe\n", encoding="utf-8")
    write_ready = probe.read_text(encoding="utf-8").startswith("A3")
    probe.unlink()
    python_probe = subprocess.run(
        ["python", "-c", "print('A3_SCRIPT_READY')"],
        capture_output=True, text=True, timeout=30, check=False,
    )
    git_status = _git(owner_repo, "status", "--porcelain=v1")
    git_diff = subprocess.run(
        ["git", "-C", str(owner_repo), "diff", "--quiet", "--no-ext-diff"],
        timeout=30, check=False,
    )
    git_identity = bool(_git(owner_repo, "config", "user.name")) and bool(
        _git(owner_repo, "config", "user.email")
    )
    checks = {
        "cap.file.read": (owner_repo.joinpath("IDEA.md").is_file(), "repository file read"),
        "cap.file.write": (write_ready, "disposable-root write/read/delete"),
        "cap.script.run": (python_probe.returncode == 0 and "A3_SCRIPT_READY" in python_probe.stdout, "local Python probe"),
        "cap.git.inspect": (not git_status, "clean owner-repository inspection"),
        "cap.git.diff": (git_diff.returncode == 0, "clean Git diff probe"),
        "cap.git.commit": (git_identity, "local Git identity present"),
    }
    seed_builtins(database)
    for capability_id, (ready, note) in checks.items():
        record_fabric_outcome(
            database,
            capability_id,
            success=bool(ready),
            verified=VerifyVerdict.MATCH if ready else VerifyVerdict.MISMATCH,
            note=f"V11 A3 flagship preflight: {note}",
        )
    if not all(ready for ready, _note in checks.values()):
        raise RuntimeError(f"required Fabric preflight failed: {checks}")

    github_attempt, _document = GitHubResearchConnector().read_api(
        "github.repo.read", "/repos/Blemeh/JARVIS-Foundry")
    github_ready = github_attempt.outcome == "COMPLETED"
    record_fabric_outcome(
        database,
        "cap.github.repo.read",
        success=github_ready,
        verified=VerifyVerdict.MATCH if github_ready else VerifyVerdict.UNKNOWN,
        note="V11 A3 read-only GitHub repository preflight",
    )
    return {
        "required": {key: ready for key, (ready, _note) in checks.items()},
        "github_read": github_ready,
        "github_error": github_attempt.error_code,
    }


def _record_real_diagnostic_signals(database: FoundryDatabase, owner_repo: Path) -> dict[str, Any]:
    gaps = WorldKnowledgeGapStore(database)
    gap_id = gaps.record(
        "UNKNOWN_PROCEDURE",
        "A disposable knowledge gap used to exercise the canonical research handoff",
        gap_id="wg-v11-a3-research-handoff",
        subject_type="repository",
        subject_id="JARVIS Foundry",
        source_id="v11-a3-diagnostic",
    )
    diagnostic = handoff_to_research(
        database, gap_id, source_id="v11-a3-diagnostic")
    source_file = owner_repo / "src" / "jarvis_foundry" / "world_model" / "research_handoff.py"
    source_hash = _sha(source_file)
    if diagnostic.get("research_queued") is not False:
        raise RuntimeError("research handoff diagnostic no longer reproduces; no work manufactured")
    reason = str(diagnostic.get("reason") or "")
    diagnostic_hash = hashlib.sha256(
        json.dumps(diagnostic, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    signals = WorldSignalStore(database)
    strong = signals.record(
        "PROJECT_DRIFT",
        "Canonical world-model research handoff is disconnected from the research queue",
        signal_id=f"ws-a3-{source_hash[:12]}",
        summary=(
            "A fresh disposable call to handoff_to_research returned "
            f"research_queued=false: {reason[:220]}"
        ),
        affected_entities=["JARVIS Foundry"],
        evidence=[
            f"{source_file}#sha256:{source_hash}",
            f"diagnostic:sha256:{diagnostic_hash}",
        ],
        provenance={
            "authority": "SYSTEM_OBSERVATION",
            "verified": True,
            "verification": "PASS",
            "diagnostic": "handoff_to_research disposable v39 invocation",
            "source_sha256": source_hash,
        },
        confidence=0.96,
        importance=0.72,
        urgency=0.55,
        stale_after=_iso_after(24),
        recommended_action=(
            "Repair src/jarvis_foundry/world_model/research_handoff.py to use "
            "the canonical function-based research_queue API instead of the "
            "unavailable ResearchQueue class; add and run a focused regression test."
        ),
        source_id="v11-a3-diagnostic",
    )
    weak_file = owner_repo / "pyproject.toml"
    weak_hash = _sha(weak_file)
    weak = signals.record(
        "FOLLOW_UP",
        "Low-confidence packaging metadata follow-up",
        signal_id=f"ws-a3-weak-{weak_hash[:12]}",
        summary="Repository metadata was readable but no concrete defect was established.",
        affected_entities=["JARVIS Foundry"],
        evidence=[f"{weak_file}#sha256:{weak_hash}"],
        provenance={
            "authority": "SYSTEM_OBSERVATION",
            "verified": True,
            "verification": "PASS",
            "source_sha256": weak_hash,
        },
        confidence=0.05,
        importance=0.10,
        urgency=0.05,
        stale_after=_iso_after(24),
        recommended_action="Review packaging metadata for a possible improvement.",
        source_id="v11-a3-diagnostic",
    )
    return {"diagnostic": diagnostic, "strong": strong, "weak": weak}


def _sources(owner_repo: Path) -> tuple[SourceSpec, ...]:
    return (
        SourceSpec("github-jarvis", "github", "Blemeh/JARVIS-Foundry", 300, {
            "repo": "Blemeh/JARVIS-Foundry",
            "max_items": 10,
            "required_capabilities": ["cap.github.repo.read"],
        }),
        SourceSpec("project-jarvis", "project", str(owner_repo), 300, {
            "root": str(owner_repo),
            "project_id": "JARVIS Foundry",
            "max_files": 180,
            "max_dirs": 100,
            "max_bytes": 500_000,
            "required_capabilities": ["cap.git.inspect"],
        }),
        SourceSpec("world-signals", "world", "world:active-signals", 300, {
            "max_signals": 20,
        }),
    )


def _config(
    evidence_root: Path,
    owner_repo: Path,
    hermes_root: Path,
    hermes_python: Path,
    sources: tuple[SourceSpec, ...],
) -> WorkerRuntimeConfig:
    settings = load_provider_settings()
    return WorkerRuntimeConfig(
        enabled=True,
        status="PROBING",
        runtime_state_root=str(evidence_root / "runtime"),
        hermes={
            "enabled": True,
            "root": str(hermes_root),
            "python_exe": str(hermes_python),
            "timeout_seconds": 900,
            "allow_data_training_tiers_noninteractive": True,
        },
        openhands={"enabled": False},
        readiness={
            "hermes": {
                "state": "READY",
                "note": "isolated checkout/home and explicit one-run owner consent",
                "evidence": [
                    f"hermes-checkout:{_git(hermes_root, 'rev-parse', 'HEAD')}",
                    f"hermes-python:{hermes_python}",
                ],
            },
        },
        role_signals={
            "builder": {
                "bounded": True,
                "explicit_acceptance": True,
                "tests_available": True,
                "narrow_scope": True,
                "reversible": True,
            },
            "repair": {
                "ambiguous": True,
                "root_cause_required": True,
                "recovery": True,
            },
            "verifier": {
                "bounded": True,
                "explicit_acceptance": True,
                "tests_available": True,
                "narrow_scope": True,
            },
        },
        max_repairs=1,
        provider=settings.provider,
        model=settings.model,
        provider_settings=settings,
        initiative_a3_enabled=True,
        initiative_a3={
            "enabled": True,
            "sources": [
                {
                    "source_id": source.source_id,
                    "source_kind": source.source_kind,
                    "source_identity": source.source_identity,
                    "poll_interval_s": source.poll_interval_s,
                    "config": dict(source.config),
                }
                for source in sources
            ],
            "bounds": {
                "do_nothing_score": 0.30,
                "research_candidate_limit": 4,
                "research_budget_per_cycle": 4.0,
            },
            "policy": {
                "build_budget_units": 1.0,
                "autonomous_wip_cap": 1,
                "max_missions": 2,
                "max_retries": 1,
            },
            "worktree_parent": str(evidence_root / "worktrees"),
            "project_roots": {
                "JARVIS Foundry": str(owner_repo),
                "Blemeh/JARVIS-Foundry": str(owner_repo),
            },
        },
    )


def _event_payloads(database: FoundryDatabase, event_type: str) -> list[dict[str, Any]]:
    return [dict(event.payload) for event in database.events(event_types=(event_type,), readonly=True)]


def _summary(database: FoundryDatabase, owner_repo: Path, reports: list[dict[str, Any]]) -> dict[str, Any]:
    admissions = _event_payloads(database, "initiative.candidate_admitted")
    links = _event_payloads(database, "initiative.execution_linked")
    terminals = _event_payloads(database, "initiative.candidate_terminal")
    judgments = database.all("candidate_judgments", {})
    actions = _event_payloads(database, "action.allowed")
    public_types = [
        event.event_type for event in database.events(readonly=True)
        if (
            "push" in event.event_type.lower()
            or "email" in event.event_type.lower()
            or event.event_type.startswith("provider.write")
            or event.event_type.endswith(".write_completed")
        )
    ]
    worktree: dict[str, Any] = {}
    if admissions:
        path = Path(str(admissions[0].get("worktree") or ""))
        if path.is_dir():
            base = str(admissions[0].get("base_sha") or "")
            worktree = {
                "path": str(path),
                "branch": _git(path, "branch", "--show-current"),
                "head": _git(path, "rev-parse", "HEAD"),
                "status": _git(path, "status", "--porcelain=v1"),
                "commits": _git(path, "log", "--oneline", f"{base}..HEAD").splitlines() if base else [],
                "changed_files": _git(path, "diff", "--name-only", f"{base}..HEAD").splitlines() if base else [],
            }
    state = rebuild_executive_state(database)
    objective_rows = [row.to_payload() for row in state.objectives.values()]
    mission_rows = database.all("missions", {})
    action_ids = [str(row.get("action_id") or "") for row in actions]
    accepted = [row for row in terminals if row.get("status") == "ACCEPTED"]
    final = {
        "verdict": "V11_A3_BRIDGE_READY" if (
            len(admissions) == 1
            and len({row.get("objective_id") for row in admissions}) == 1
            and len(mission_rows) == 1
            and len({row.get("mission_id") for row in mission_rows}) == 1
            and len(_event_payloads(database, "initiative.worktree_prepared")) == 1
            and len(accepted) == 1
            and bool(accepted[0].get("verification_accepted"))
            and not worktree.get("status")
            and len(action_ids) == len(set(action_ids))
            and not public_types
            and reports
            and reports[-1].get("outcome") == "WAITING"
        ) else "V11_A3_BRIDGE_NOT_READY",
        "owner_repo": {
            "path": str(owner_repo),
            "head": _git(owner_repo, "rev-parse", "HEAD"),
            "status": _git(owner_repo, "status", "--porcelain=v1"),
        },
        "reports": reports,
        "admissions": admissions,
        "links": links,
        "terminals": terminals,
        "objectives": objective_rows,
        "missions": mission_rows,
        "worktree": worktree,
        "judgments": judgments,
        "counts": {
            "objectives": len(_event_payloads(database, "executive.objective_created")),
            "missions": len(mission_rows),
            "worktrees": len(_event_payloads(database, "initiative.worktree_prepared")),
            "action_effects": len(actions),
            "duplicate_action_effects": len(action_ids) - len(set(action_ids)),
            "public_writes": len(public_types),
            "verification_accepted": len(_event_payloads(database, "verification.accepted")),
            "verification_failed": len(_event_payloads(database, "verification.failed")),
        },
        "public_write_event_types": public_types,
        "reconcile": reconcile(database),
    }
    return final


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--owner-repo", type=Path, required=True)
    parser.add_argument("--hermes-root", type=Path, required=True)
    parser.add_argument("--hermes-python", type=Path, required=True)
    parser.add_argument(
        "--allow-data-training-tier",
        action="store_true",
        help="explicit owner acknowledgement for the contributor-tier provider",
    )
    args = parser.parse_args()
    if not args.allow_data_training_tier:
        raise SystemExit(
            "BLOCKED_BY_PROVIDER_POLICY: explicit --allow-data-training-tier "
            "is required because the configured contributor-tier model may train on prompts/completions"
        )
    evidence_root = args.evidence_root.expanduser().resolve()
    owner_repo = args.owner_repo.expanduser().resolve()
    hermes_root = args.hermes_root.expanduser().resolve()
    hermes_python = args.hermes_python.expanduser().resolve()
    if evidence_root.exists():
        raise SystemExit(f"refusing pre-existing evidence root: {evidence_root}")
    if not owner_repo.is_dir() or _git(owner_repo, "status", "--porcelain=v1"):
        raise SystemExit("owner repository must exist and be clean")
    if not hermes_root.is_dir() or not hermes_python.is_file():
        raise SystemExit("isolated Hermes checkout/interpreter is unavailable")
    evidence_root.mkdir(parents=True)
    home = evidence_root / "home"
    database = FoundryDatabase(home / "foundry.db")
    database.initialize()
    _seed_owner_authority(database, owner_repo)
    capabilities = _prove_capabilities(database, owner_repo, evidence_root)
    diagnostics = _record_real_diagnostic_signals(database, owner_repo)
    sources = _sources(owner_repo)
    config = _config(evidence_root, owner_repo, hermes_root, hermes_python, sources)
    config.validate()

    reports: list[dict[str, Any]] = []
    terminal_seen = False
    for index in range(1, 9):
        report = run_cycle(
            database,
            FoundryPaths(home),
            config,
            loop_id=f"a3-flagship-{index}",
            instance_id=f"a3-flagship-worker-{index}",
        )
        reports.append(report)
        print(json.dumps({"cycle": index, "outcome": report.get("outcome"), "reason": report.get("reason")}, sort_keys=True), flush=True)
        terminals = _event_payloads(database, "initiative.candidate_terminal")
        if terminals:
            terminal_seen = True
        if terminal_seen and report.get("outcome") == "WAITING":
            break

    summary = _summary(database, owner_repo, reports)
    summary["capabilities"] = capabilities
    summary["diagnostics"] = diagnostics
    summary["schema"] = 39
    report_path = evidence_root / "V11_A3_FLAGSHIP.json"
    report_path.write_text(json.dumps(summary, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    print(json.dumps({"verdict": summary["verdict"], "report": str(report_path)}, sort_keys=True))
    return 0 if summary["verdict"] == "V11_A3_BRIDGE_READY" else 1


if __name__ == "__main__":
    raise SystemExit(main())
