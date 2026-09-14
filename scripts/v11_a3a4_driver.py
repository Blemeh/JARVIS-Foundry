"""Real V11 A3/A4 driver for the candidate-neutral proving harness.

The driver creates a disposable clone and schema-v40 ledger, derives work from
a pinned high-signal static-analysis scan, and then enters the existing
A2 -> Executive -> A3 -> MissionLoop -> real worker/verifier spine.  Its
fault orchestration kills only child processes it started and never points at
the live Foundry home, live runtime, Scheduler, main, or origin.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import uuid
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta
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
from jarvis_foundry.owner_controls import OwnerControlService, sign_command
from jarvis_foundry.paths import FoundryPaths
from jarvis_foundry.roles.reaper import (
    reap_stale_role_runs,
    recover_stale_mission_state,
    role_lease_key,
)
from jarvis_foundry.runtime.config import WorkerRuntimeConfig
from jarvis_foundry.runtime.provider_config import load_provider_settings
from jarvis_foundry.runtime.worker_loop import reclaim_orphaned_loop_lease, run_cycle
from jarvis_foundry.supervisor.leases import LeaseManager
from jarvis_foundry.supervisor.worker import (
    load_adapter_pids,
    pid_alive,
    process_creation_identity,
    terminate_owned_tree,
    unregister_adapter_pid,
)
from jarvis_foundry.world_model.signals import WorldSignalStore

DRIVER_SCHEMA = "jarvis.v11.a3a4.driver.v1"
HARD_EXIT = 86
OWNER_SECRET = b"v11-exit-proof-owner-control-key"
RULES = "F821,F822,F823,F401,F841"
RUFF_VERSION = "0.16.5"
MAX_SIGNALS = 12
SETTLE_LIMIT = 8
#: Bound for one child process of this driver.  A child must be able to
#: traverse the product's own frozen worst case for a single step: the worker
#: dispatch (hermes timeout_seconds, 900 by default), the bounded repair it
#: may earn (another full dispatch), and the independent verifier pass.  A
#: smaller harness bound would abort work the product is explicitly allowed to
#: do and would misreport it as a driver failure.
CHILD_TIMEOUT_SECONDS = 2700


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _after(hours: int) -> str:
    return (
        (datetime.now(UTC) + timedelta(hours=hours)).isoformat().replace("+00:00", "Z")
    )


def _sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha_file(path: Path) -> str:
    return _sha_bytes(path.read_bytes())


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _git(root: Path, *args: str, timeout: int = 120) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout.strip()


def _event_rows(
    database: FoundryDatabase, event_type: str
) -> list[tuple[Event, dict[str, Any]]]:
    return [
        (event, dict(event.payload))
        for event in database.events(event_types=(event_type,), readonly=True)
    ]


def _database_schema(database: FoundryDatabase) -> int:
    with database.connect(readonly=True) as connection:
        row = connection.execute(
            "SELECT MAX(version) FROM schema_migrations"
        ).fetchone()
    if row is None or row[0] is None:
        raise RuntimeError("Foundry schema-migration authority is unavailable")
    return int(row[0])


def _clone_source(source_repo: Path, target: Path, expected_sha: str) -> None:
    if target.exists():
        if _git(target, "rev-parse", "HEAD") != expected_sha:
            raise RuntimeError(
                "disposable owner clone drifted from the frozen proof SHA"
            )
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [
            "git",
            "clone",
            "--no-hardlinks",
            "--no-checkout",
            str(source_repo),
            str(target),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "disposable local clone failed")
    _git(target, "checkout", "--detach", expected_sha)
    _git(target, "config", "user.name", "JARVIS V11 Autonomous Worker")
    _git(target, "config", "user.email", "jarvis-v11-proof@example.invalid")
    _git(target, "remote", "set-url", "--push", "origin", "DISABLED")


def _seed_owner_authority(database: FoundryDatabase, owner_repo: Path) -> None:
    database.append(
        Event.create(
            "system.start_build_recorded",
            {
                "received_at": _utc_now(),
                "standing_mandate": True,
                "scope": "one bounded reversible V11 A3 improvement in a disposable exact-SHA clone",
            },
            source_type="owner",
            source_id="v11-exit-proof-owner",
            idempotency_key="system.start-build",
        )
    )
    seed_owner_context(
        database,
        goals=[
            {
                "text": "Improve JARVIS Foundry reliability through bounded independently verified repository fixes",
                "source_ref": "IDEA.md",
                "source_hash": _sha_file(owner_repo / "IDEA.md"),
            }
        ],
        projects=[
            {
                "text": "JARVIS Foundry",
                "source_ref": "docs/CURRENT_STATE.md",
                "source_hash": _sha_file(owner_repo / "docs" / "CURRENT_STATE.md"),
            }
        ],
        standing_rejections=[
            {
                "text": "No merge, push, deployment, email, live runtime, production database, or Scheduler mutation",
                "source_ref": "docs/CURRENT_STATE.md",
                "source_hash": _sha_file(owner_repo / "docs" / "CURRENT_STATE.md"),
            }
        ],
        actor="v11-exit-proof-owner-context",
    )


def _run_test_preflight(owner_repo: Path) -> tuple[bool, dict[str, Any]]:
    test_id = (
        "tests/foundry/test_v11_a3_initiative_bridge.py::"
        "test_durable_select_is_resumed_once_after_pre_admission_restart"
    )
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(owner_repo / "src")
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", test_id],
        cwd=owner_repo,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
        check=False,
    )
    return result.returncode == 0, {
        "command": f"python -m pytest -q {test_id}",
        "returncode": result.returncode,
        "stdout_sha256": _sha_bytes(result.stdout.encode("utf-8")),
        "stderr_sha256": _sha_bytes(result.stderr.encode("utf-8")),
    }


def _prove_capabilities(
    database: FoundryDatabase, owner_repo: Path, root: Path
) -> dict[str, Any]:
    probe = root / ".capability-write-probe"
    probe.write_text("v11 exit proof\n", encoding="utf-8")
    write_ready = probe.read_text(encoding="utf-8") == "v11 exit proof\n"
    probe.unlink()
    script = subprocess.run(
        [sys.executable, "-c", "print('V11_SCRIPT_READY')"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    tests_ready, test_evidence = _run_test_preflight(owner_repo)
    clean = not _git(owner_repo, "status", "--porcelain=v1")
    diff = (
        subprocess.run(
            ["git", "-C", str(owner_repo), "diff", "--quiet", "--no-ext-diff"],
            timeout=30,
            check=False,
        ).returncode
        == 0
    )
    identity = bool(_git(owner_repo, "config", "user.name")) and bool(
        _git(owner_repo, "config", "user.email")
    )
    checks: dict[str, tuple[bool, str]] = {
        "cap.file.read": (
            (owner_repo / "IDEA.md").is_file(),
            "exact-SHA repository file read",
        ),
        "cap.file.write": (write_ready, "disposable evidence-root write/read/delete"),
        "cap.script.run": (
            script.returncode == 0 and "V11_SCRIPT_READY" in script.stdout,
            "local Python probe",
        ),
        "cap.git.inspect": (clean, "clean disposable clone inspection"),
        "cap.git.diff": (diff, "clean Git diff probe"),
        "cap.git.commit": (identity, "local-only Git identity configured"),
        "cap.tests.run": (tests_ready, "focused exact-SHA durability test"),
    }
    seed_builtins(database)
    for capability_id, (ready, note) in checks.items():
        record_fabric_outcome(
            database,
            capability_id,
            success=ready,
            verified=VerifyVerdict.MATCH if ready else VerifyVerdict.MISMATCH,
            note=f"V11 exit-proof preflight: {note}",
        )
    if not all(ready for ready, _ in checks.values()):
        raise RuntimeError(f"required Fabric preflight failed: {checks}")

    attempt, _document = GitHubResearchConnector().read_api(
        "github.repo.read",
        "/repos/Blemeh/JARVIS-Foundry",
    )
    github_ready = attempt.outcome == "COMPLETED"
    record_fabric_outcome(
        database,
        "cap.github.repo.read",
        success=github_ready,
        verified=VerifyVerdict.MATCH if github_ready else VerifyVerdict.UNKNOWN,
        note="V11 exit-proof read-only GitHub repository preflight",
    )
    return {
        "required": {name: ready for name, (ready, _note) in checks.items()},
        "test_evidence": test_evidence,
        "github_read": github_ready,
        "github_error": attempt.error_code,
    }


def _finding_priority(
    finding: Mapping[str, Any],
    relative: str,
    counts: Mapping[tuple[str, str], int],
) -> tuple[Any, ...]:
    fix = finding.get("fix") if isinstance(finding.get("fix"), Mapping) else {}
    safe = str(fix.get("applicability") or "") == "safe"
    code = str(finding.get("code") or "")
    scope = (
        0
        if relative.startswith("scripts/")
        else 1
        if relative.startswith("tests/")
        else 2
    )
    return (
        0 if safe else 1,
        0 if code == "F401" else 1,
        counts.get((relative, code), 0),
        scope,
        relative,
        int(finding["location"]["row"]),
    )


def _scan_findings(owner_repo: Path, uv_exe: Path) -> tuple[str, list[dict[str, Any]]]:
    command = [
        str(uv_exe),
        "tool",
        "run",
        "--from",
        f"ruff=={RUFF_VERSION}",
        "ruff",
        "check",
        "src",
        "tests",
        "scripts",
        "--select",
        RULES,
        "--output-format",
        "json",
    ]
    result = subprocess.run(
        command,
        cwd=owner_repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
        check=False,
    )
    if result.returncode not in {0, 1}:
        raise RuntimeError(
            f"pinned Ruff diagnostic failed with exit {result.returncode}"
        )
    try:
        raw = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError("pinned Ruff diagnostic did not return JSON") from error
    if not isinstance(raw, list):
        raise TypeError("pinned Ruff diagnostic returned an invalid result")
    findings: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, Mapping) or not isinstance(
            item.get("location"), Mapping
        ):
            continue
        try:
            relative = (
                Path(str(item["filename"])).resolve().relative_to(owner_repo).as_posix()
            )
        except ValueError:
            continue
        row = dict(item)
        row["relative_path"] = relative
        findings.append(row)
    counts = Counter(
        (str(row["relative_path"]), str(row.get("code") or "")) for row in findings
    )
    findings.sort(
        key=lambda row: _finding_priority(
            row,
            str(row["relative_path"]),
            counts,
        )
    )
    selected: list[dict[str, Any]] = []
    selected_paths: set[str] = set()
    for finding in findings:
        relative = str(finding["relative_path"])
        if relative in selected_paths:
            continue
        selected.append(finding)
        selected_paths.add(relative)
        if len(selected) == MAX_SIGNALS:
            break
    if len(selected) < 10:
        raise RuntimeError(
            f"candidate-neutral diagnostic produced only {len(selected)} bounded findings"
        )
    version = subprocess.run(
        [
            str(uv_exe),
            "tool",
            "run",
            "--from",
            f"ruff=={RUFF_VERSION}",
            "ruff",
            "--version",
        ],
        cwd=owner_repo,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    ).stdout.strip()
    return version, selected


def _record_real_signals(
    database: FoundryDatabase,
    owner_repo: Path,
    root: Path,
    uv_exe: Path,
) -> dict[str, Any]:
    version, findings = _scan_findings(owner_repo, uv_exe)
    scan_payload = {
        "tool": version,
        "rules": RULES,
        "repository_sha": _git(owner_repo, "rev-parse", "HEAD"),
        "captured_at": _utc_now(),
        "findings": findings,
    }
    scan_path = root / "REAL_STATIC_OBSERVATION.json"
    _atomic_json(scan_path, scan_payload)
    scan_hash = _sha_file(scan_path)
    record_fabric_outcome(
        database,
        "cap.system.diagnose",
        success=bool(findings),
        verified=VerifyVerdict.MATCH if findings else VerifyVerdict.MISMATCH,
        note=(
            f"V11 exit-proof pinned Ruff {RUFF_VERSION} diagnostic; "
            f"artifact sha256:{scan_hash}"
        ),
    )
    store = WorldSignalStore(database)
    required = list(A3AdmissionPolicy().required_fabric_capabilities)
    signal_ids: list[str] = []
    for index, finding in enumerate(findings):
        relative = str(finding["relative_path"])
        code = str(finding.get("code") or "UNKNOWN")
        line = int(finding["location"]["row"])
        message = str(finding.get("message") or "static-analysis finding")
        material = f"{_git(owner_repo, 'rev-parse', 'HEAD')}:{relative}:{line}:{code}:{message}"
        digest = _sha_bytes(material.encode("utf-8"))
        fix = finding.get("fix") if isinstance(finding.get("fix"), Mapping) else {}
        safe_fix = str(fix.get("applicability") or "") == "safe"
        importance = max(0.40, 0.94 - index * 0.035)
        confidence = 0.98 if safe_fix else 0.82
        action = (
            f"Resolve pinned-Ruff {code} at {relative}:{line}: {message}. "
            "Make the smallest useful change and verify with "
            f"`{uv_exe} tool run --from ruff=={RUFF_VERSION} ruff check {relative} --select {code}`"
            + (
                " and run the focused test file."
                if relative.startswith("tests/")
                else "."
            )
        )
        signal_id = f"ws-v11-static-{digest[:16]}"
        result = store.record(
            "PROJECT_DRIFT",
            f"Pinned Ruff {code} finding in {relative}:{line}",
            signal_id=signal_id,
            summary=f"{version} observed {message} at exact proof source {relative}:{line}.",
            affected_entities=["JARVIS Foundry"],
            evidence=[
                f"{relative}#sha256:{_sha_file(owner_repo / relative)}",
                f"{scan_path}#sha256:{scan_hash}",
                f"finding:sha256:{digest}",
            ],
            provenance={
                "authority": "SYSTEM_OBSERVATION",
                "verified": True,
                "verification": "PASS",
                "tool": version,
                "rules": RULES,
                "repository_sha": _git(owner_repo, "rev-parse", "HEAD"),
                "required_capabilities": required,
                "target_project": "JARVIS Foundry",
            },
            confidence=confidence,
            importance=importance,
            urgency=max(0.35, 0.65 - index * 0.02),
            stale_after=_after(72),
            recommended_action=action,
            source_id="v11-exit-proof-static-observer",
        )
        if result.get("signal_id"):
            signal_ids.append(str(result["signal_id"]))
    return {
        "tool": version,
        "rules": RULES,
        "scan_sha256": scan_hash,
        "finding_count": len(findings),
        "signal_ids": signal_ids,
    }


def _sources(owner_repo: Path) -> tuple[SourceSpec, ...]:
    return (
        SourceSpec(
            "github-jarvis",
            "github",
            "Blemeh/JARVIS-Foundry",
            300,
            {
                "repo": "Blemeh/JARVIS-Foundry",
                "max_items": 10,
                "required_capabilities": ["cap.github.repo.read"],
            },
        ),
        SourceSpec(
            "project-jarvis",
            "project",
            str(owner_repo),
            300,
            {
                "root": str(owner_repo),
                "project_id": "JARVIS Foundry",
                "max_files": 200,
                "max_dirs": 120,
                "max_bytes": 750_000,
                "required_capabilities": ["cap.git.inspect"],
            },
        ),
        SourceSpec(
            "world-static-findings",
            "world",
            "world:active-signals",
            300,
            {
                "max_signals": 20,
            },
        ),
    )


def _config(args: argparse.Namespace, owner_repo: Path) -> WorkerRuntimeConfig:
    settings = load_provider_settings()
    canonical = WorkerRuntimeConfig.from_yaml(
        args.source_repo / "config" / "worker_routing.yaml",
        provider_settings=settings,
    )
    if canonical.hermes.get("allow_data_training_tiers_noninteractive") is not True:
        raise RuntimeError("canonical training-tier policy is not owner-authorized")
    sources = _sources(owner_repo)
    config = replace(
        canonical,
        enabled=True,
        status="PROBING",
        resource_id="v11-exit-proof-observation",
        capability="cap.file.read",
        runtime_state_root=str(args.state_root / "runtime"),
        stop_sentinel_path=str(args.state_root / "home" / "EMERGENCY_STOP"),
        hermes={
            **canonical.hermes,
            "enabled": True,
            "root": str(args.hermes_root),
            "python_exe": str(args.hermes_python),
            "timeout_seconds": int(canonical.hermes.get("timeout_seconds", 900)),
        },
        openhands={"enabled": False},
        readiness={
            "hermes": {
                "state": "READY",
                "note": "isolated checkout, isolated run home, exact executable, owner-authorized canonical policy",
                "evidence": [
                    f"hermes-checkout:{_git(args.hermes_root, 'rev-parse', 'HEAD')}",
                    f"hermes-python:{args.hermes_python}",
                    f"proof-sha:{args.expected_sha}",
                ],
            },
        },
        max_repairs=1,
        executive_selection_enabled=False,
        executive_autonomy_enabled=False,
        portfolio_selection_enabled=False,
        discovery_cycle_enabled=False,
        continuous_enabled=False,
        initiative_a2_enabled=False,
        continuous_observation_enabled=False,
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
                "candidate_limit": 20,
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
            "worktree_parent": str(args.state_root / "worktrees"),
            "project_roots": {
                "JARVIS Foundry": str(owner_repo),
                "Blemeh/JARVIS-Foundry": str(owner_repo),
            },
        },
        provider=settings.provider,
        model=settings.model,
        provider_settings=settings,
    )
    config.validate()
    return config


def _initialize(args: argparse.Namespace) -> dict[str, Any]:
    marker = args.state_root / "DRIVER_STATE.json"
    if marker.is_file():
        state = json.loads(marker.read_text(encoding="utf-8"))
        if state.get("expected_sha") != args.expected_sha:
            raise RuntimeError("driver state expected SHA mismatch")
        owner_repo = Path(str(state.get("owner_repo") or ""))
        if (
            _git(args.source_repo, "rev-parse", "HEAD") != args.expected_sha
            or _git(args.source_repo, "status", "--porcelain=v1")
            or _git(owner_repo, "rev-parse", "HEAD") != args.expected_sha
            or _git(owner_repo, "status", "--porcelain=v1")
        ):
            raise RuntimeError("frozen proof source or disposable owner clone drifted")
        return state
    identity = {
        "head": _git(args.source_repo, "rev-parse", "HEAD"),
        "status": _git(args.source_repo, "status", "--porcelain=v1"),
    }
    if identity != {"head": args.expected_sha, "status": ""}:
        raise RuntimeError("driver source must be the clean frozen proof SHA")
    if not args.hermes_root.is_dir() or not args.hermes_python.is_file():
        raise RuntimeError("isolated Hermes checkout/interpreter unavailable")
    args.state_root.mkdir(parents=True, exist_ok=True)
    owner_repo = args.state_root / "owner-repo"
    _clone_source(args.source_repo, owner_repo, args.expected_sha)
    database = FoundryDatabase(args.state_root / "home" / "foundry.db")
    schema = database.initialize()
    if schema != 40:
        raise RuntimeError(f"V11 driver requires schema 40, got {schema}")
    _seed_owner_authority(database, owner_repo)
    capabilities = _prove_capabilities(database, owner_repo, args.state_root)
    observation = _record_real_signals(
        database, owner_repo, args.state_root, args.uv_exe
    )
    config = _config(args, owner_repo)
    state = {
        "schema": DRIVER_SCHEMA,
        "expected_sha": args.expected_sha,
        "initialized_at": _utc_now(),
        "database_schema": schema,
        "owner_repo": str(owner_repo),
        "owner_repo_head": _git(owner_repo, "rev-parse", "HEAD"),
        "source_repo": str(args.source_repo),
        "training_tier_policy": config.hermes.get(
            "allow_data_training_tiers_noninteractive"
        ),
        "provider": config.provider_settings.redacted(),
        "capabilities": capabilities,
        "real_observation": observation,
    }
    _atomic_json(marker, state)
    return state


def _read_json(raw: Any, default: Any) -> Any:
    try:
        return json.loads(str(raw))
    except (TypeError, ValueError):
        return default


def _snapshot(
    args: argparse.Namespace,
    request: Mapping[str, Any],
    *,
    status: str,
    fault_status: str | None = None,
    stop_blocked: bool = False,
    reports: Sequence[Mapping[str, Any]] = (),
    fault_evidence: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    database = FoundryDatabase(args.state_root / "home" / "foundry.db")
    admissions = {
        str(payload.get("candidate_id") or ""): payload
        for _event, payload in _event_rows(database, "initiative.candidate_admitted")
    }
    judgments = {
        str(row.get("candidate_id") or ""): row
        for row in database.all("candidate_judgments")
    }
    observations: list[dict[str, Any]] = []
    for row in database.all("world_observations"):
        provenance = _read_json(row.get("provenance_json"), {})
        observations.append(
            {
                "id": str(row["observation_id"]),
                "identity_key": str(row["observation_id"]),
                "real": True,
                "source_kind": row.get("source_kind"),
                "source_ref": row.get("source_ref"),
                "provenance": provenance or {"authority": "recorded source adapter"},
            }
        )
    candidates: list[dict[str, Any]] = []
    for row in database.all("candidates"):
        candidate_id = str(row["candidate_id"])
        judgment = judgments.get(candidate_id, {})
        status_value = (
            "SELECTED"
            if candidate_id in admissions
            else str(judgment.get("outcome") or row.get("status") or "UNKNOWN")
        )
        candidates.append(
            {
                "id": candidate_id,
                "identity_key": str(row.get("fingerprint") or candidate_id),
                "status": status_value,
                "objective": row.get("objective"),
                "reason_code": judgment.get("reason_code"),
                "reason": judgment.get("reason_detail"),
                "score": judgment.get("score"),
            }
        )
    executive = rebuild_executive_state(database)
    objectives: list[dict[str, Any]] = []
    for objective in executive.objectives.values():
        if objective.metadata.get("initiative") != "V11_A3":
            continue
        candidate_id = str(objective.provenance.get("candidate_id") or "")
        objectives.append(
            {
                "id": objective.objective_id,
                "identity_key": candidate_id or objective.objective_id,
                "candidate_id": candidate_id,
                "status": objective.status,
                "objective": objective.objective,
            }
        )
    with database.connect(readonly=True) as connection:
        mission_rows = [
            dict(row)
            for row in connection.execute(
                "SELECT * FROM missions ORDER BY rowid"
            ).fetchall()
        ]
        agent_rows = [
            dict(row)
            for row in connection.execute(
                "SELECT * FROM agent_runs ORDER BY rowid"
            ).fetchall()
        ]
    links = {
        str(payload.get("mission_id") or ""): payload
        for _event, payload in _event_rows(database, "initiative.execution_linked")
    }
    missions = [
        {
            "id": str(row["mission_id"]),
            "identity_key": str(
                links.get(str(row["mission_id"]), {}).get("objective_id")
                or row["mission_id"]
            ),
            "objective_id": links.get(str(row["mission_id"]), {}).get("objective_id"),
            "state": row.get("state"),
            "objective": row.get("objective"),
        }
        for row in mission_rows
    ]
    builders = [
        {
            "id": str(row["run_id"]),
            "identity_key": f"{row['mission_id']}:{row.get('step_id')}:{row.get('role')}:completed",
            "mission_id": row.get("mission_id"),
            "step_id": row.get("step_id"),
            "role": row.get("role"),
            "provider": row.get("provider"),
            "model": row.get("model"),
            "status": row.get("status"),
        }
        for row in agent_rows
        if row.get("role") in {"builder", "repair"} and row.get("status") == "COMPLETED"
    ]
    effects = [
        {
            "id": event.event_id,
            "identity_key": str(payload.get("action_key") or event.event_id),
            "mission_id": (payload.get("request_identity") or {}).get("mission_id"),
            "action_key": payload.get("action_key"),
        "status": _effect_status(database, str(payload.get("action_key") or "")),
        }
        for event, payload in _event_rows(database, "system.action_started")
    ]
    acceptances = [
        {
            "id": event.event_id,
            "identity_key": str(payload.get("verdict_id") or event.event_id),
            "mission_id": payload.get("mission_id"),
            "builder_run_id": payload.get("builder_run_id"),
            "verifier_run_id": payload.get("verifier_run_id"),
            "independent": (
                bool(payload.get("builder_run_id"))
                and bool(payload.get("verifier_run_id"))
                and payload.get("builder_run_id") != payload.get("verifier_run_id")
            ),
            "verdict": "PASS" if payload.get("decision") == "ACCEPT" else "FAIL",
        }
        for event, payload in _event_rows(database, "verification.accepted")
    ]
    outcomes = [
        {
            "id": str(payload.get("outcome_id") or event.event_id),
            "identity_key": str(
                payload.get("mission_id") or payload.get("outcome_id") or event.event_id
            ),
            "mission_id": payload.get("mission_id"),
            "candidate_id": payload.get("candidate_id"),
            "status": payload.get("status"),
            "truth_accepted": payload.get("truth_accepted") is True,
            "acceptance_id": payload.get("acceptance_identity"),
        }
        for event, payload in _event_rows(database, "mission.outcome_finalized")
    ]
    learning: list[dict[str, Any]] = []
    for event, payload in _event_rows(database, "memory.observed"):
        sources = (
            payload.get("source_ids")
            if isinstance(payload.get("source_ids"), list)
            else []
        )
        outcome_id = next(
            (str(item) for item in sources if str(item).startswith("outcome-")), ""
        )
        learning.append(
            {
                "id": str(payload.get("memory_id") or event.event_id),
                "identity_key": outcome_id
                or str(payload.get("mission_id") or event.event_id),
                "mission_id": payload.get("mission_id"),
                "outcome_id": outcome_id or None,
            }
        )
    repairs = [
        {
            "id": str(row["run_id"]),
            "identity_key": f"{row['mission_id']}:{row.get('step_id')}:{row['run_id']}",
            "mission_id": row.get("mission_id"),
            "status": row.get("status"),
        }
        for row in agent_rows
        if row.get("role") == "repair"
    ]

    stages: list[str] = []
    if observations:
        stages.append("OBSERVATION")
    if candidates:
        stages.append("CANDIDATES")
    if any(
        payload.get("decision") == "SELECT"
        for _event, payload in _event_rows(database, "executive.decision_recorded")
    ):
        stages.append("SELECTION")
    if objectives:
        stages.append("OBJECTIVE")
    if missions:
        stages.append("MISSION")
    if any(row.get("role") in {"builder", "repair"} for row in agent_rows):
        stages.append("BUILD")
    if effects:
        stages.append("EFFECT")
    if acceptances:
        stages.append("ACCEPTANCE")
    if learning:
        stages.append("LEARNING")
    if status == "WAIT":
        stages.append("WAIT")
    owner_repo = Path(
        json.loads((args.state_root / "DRIVER_STATE.json").read_text(encoding="utf-8"))[
            "owner_repo"
        ]
    )
    worktrees = []
    for payload in admissions.values():
        path = Path(str(payload.get("worktree") or ""))
        if path.is_dir():
            worktrees.append(
                {
                    "path": str(path),
                    "branch": _git(path, "branch", "--show-current"),
                    "head": _git(path, "rev-parse", "HEAD"),
                    "status": _git(path, "status", "--porcelain=v1"),
                    "base_sha": payload.get("base_sha"),
                    "changed_files": _git(
                        path, "diff", "--name-only", f"{payload.get('base_sha')}..HEAD"
                    ).splitlines(),
                }
            )
    provider = load_provider_settings().redacted()
    snapshot: dict[str, Any] = {
        "schema": DRIVER_SCHEMA,
        "cycle_id": request["cycle_id"],
        "status": status,
        "stages": stages,
        "unbounded_loop_detected": (
            status == "RUNNING" and len(reports) >= SETTLE_LIMIT
        )
        or any(
            bool((report.get("metrics") or {}).get("unbounded_loop_detected"))
            for report in reports
        ),
        "records": {
            "observations": observations,
            "candidates": candidates,
            "objectives": objectives,
            "missions": missions,
            "builder_runs": builders,
            "effects": effects,
            "acceptances": acceptances,
            "outcomes": outcomes,
            "learning": learning,
            "repairs": repairs,
        },
        "fault": {
            "point": request.get("fault_point"),
            "status": fault_status,
        }
        if request.get("fault_point")
        else None,
        "stop_new_dispatch_blocked": stop_blocked,
        "metadata": {
            "captured_at": _utc_now(),
            "expected_sha": args.expected_sha,
            "schema": _database_schema(database),
            "database_quick_check": database.quick_check(),
            "source_repo": {
                "head": _git(args.source_repo, "rev-parse", "HEAD"),
                "status": _git(args.source_repo, "status", "--porcelain=v1"),
            },
            "owner_clone": {
                "head": _git(owner_repo, "rev-parse", "HEAD"),
                "status": _git(owner_repo, "status", "--porcelain=v1"),
                "push_url": _git(owner_repo, "remote", "get-url", "--push", "origin"),
            },
            "provider": provider,
            "training_tier_policy": True,
            "worktrees": worktrees,
            "reports": [dict(report) for report in reports],
            "agent_attempts": [
                {
                    "run_id": row.get("run_id"),
                    "mission_id": row.get("mission_id"),
                    "step_id": row.get("step_id"),
                    "role": row.get("role"),
                    "status": row.get("status"),
                    "failure_class": row.get("failure_class"),
                }
                for row in agent_rows
            ],
            "fault_evidence": dict(fault_evidence or {}),
        },
    }
    return snapshot


def _effect_status(database: FoundryDatabase, action_key: str) -> str | None:
    """Journal status for one effect, from the append-only stream."""
    if not action_key:
        return None
    from jarvis_foundry.effects import effect_state
    try:
        return str(effect_state(database, action_key).get("status") or "")
    except Exception:
        return None


def _child_command(
    args: argparse.Namespace,
    *,
    cycle_id: str,
    result_path: Path,
    fault_hook: str | None,
) -> list[str]:
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--state-root",
        str(args.state_root),
        "--source-repo",
        str(args.source_repo),
        "--expected-sha",
        args.expected_sha,
        "--hermes-root",
        str(args.hermes_root),
        "--hermes-python",
        str(args.hermes_python),
        "--uv-exe",
        str(args.uv_exe),
        "--child",
        "--cycle-id",
        cycle_id,
        "--child-result",
        str(result_path),
    ]
    if fault_hook:
        command.extend(["--fault-hook", fault_hook])
    return command


def _reclaim_outer(database: FoundryDatabase) -> bool:
    return reclaim_orphaned_loop_lease(database)


def _run_child(
    args: argparse.Namespace,
    cycle_id: str,
    *,
    fault_hook: str | None = None,
    expect_hard_exit: bool = False,
    mid_build_kill: bool = False,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    token = uuid.uuid4().hex
    result_path = args.state_root / "child-results" / f"{token}.json"
    result_path.parent.mkdir(parents=True, exist_ok=True)
    command = _child_command(
        args,
        cycle_id=cycle_id,
        result_path=result_path,
        fault_hook=fault_hook,
    )
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(args.source_repo / "src")
    database = FoundryDatabase(args.state_root / "home" / "foundry.db")
    evidence: dict[str, Any] = {"child_cycle_id": cycle_id}
    if not mid_build_kill:
        result = subprocess.run(
            command,
            cwd=args.source_repo,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=CHILD_TIMEOUT_SECONDS,
            check=False,
        )
        evidence["child_returncode"] = result.returncode
        evidence["stdout_sha256"] = _sha_bytes(result.stdout.encode("utf-8"))
        evidence["stderr_sha256"] = _sha_bytes(result.stderr.encode("utf-8"))
        if result.returncode:
            evidence["outer_loop_lease_reclaimed"] = _reclaim_outer(database)
        if expect_hard_exit and result.returncode != HARD_EXIT:
            raise RuntimeError(
                f"fault child exited {result.returncode}, expected {HARD_EXIT}"
            )
        if not expect_hard_exit and result.returncode:
            raise RuntimeError(f"driver child failed with exit {result.returncode}")
    else:
        process = subprocess.Popen(
            command,
            cwd=args.source_repo,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        pid_table = args.state_root / "home" / "adapter_worker_pids.json"
        deadline = time.monotonic() + 180.0
        selected: tuple[str, dict[str, Any], dict[str, Any]] | None = None
        while time.monotonic() < deadline and process.poll() is None:
            records = load_adapter_pids(pid_table)
            for run_id, record in records.items():
                row = database.row("agent_runs", "run_id", run_id) or {}
                if row.get("role") != "builder" or row.get("status") != "RUNNING":
                    continue
                expected = record.get("creation_identity") or {}
                observed = process_creation_identity(int(record["pid"]))
                if (
                    pid_alive(int(record["pid"]))
                    and expected.get("create_time") is not None
                    and expected.get("create_time") == observed.get("create_time")
                ):
                    selected = (run_id, record, row)
                    break
            if selected:
                break
            time.sleep(0.05)
        if selected is None:
            if process.poll() is None:
                terminate_owned_tree(process.pid, process=process)
            stdout, stderr = process.communicate(timeout=30)
            raise RuntimeError(
                "mid-build injector did not observe a registered live builder PID "
                f"(child={process.returncode}, stdout={_sha_bytes(stdout.encode())}, stderr={_sha_bytes(stderr.encode())})"
            )
        run_id, record, row = selected
        time.sleep(0.5)
        targeted = terminate_owned_tree(process.pid, process=process)
        stdout, stderr = process.communicate(timeout=30)
        provider_pid = int(record["pid"])
        if pid_alive(provider_pid) or process.poll() is None:
            raise RuntimeError("owned mid-build process tree did not terminate")
        evidence.update(
            {
                "child_returncode": process.returncode,
                "owned_root_pid": process.pid,
                "targeted_pid_count": len(targeted),
                "registered_builder_run_id": run_id,
                "registered_builder_pid": provider_pid,
                "registered_builder_creation_identity": record.get("creation_identity"),
                "registered_builder_confirmed_dead": True,
                "stdout_sha256": _sha_bytes(stdout.encode("utf-8")),
                "stderr_sha256": _sha_bytes(stderr.encode("utf-8")),
                "outer_loop_lease_reclaimed": _reclaim_outer(database),
            }
        )
        lease = role_lease_key(
            str(row["role"]), str(row["mission_id"]), row.get("step_id")
        )
        evidence["exact_role_lease_released"] = LeaseManager(database).release(
            lease, run_id
        )
        unregister_adapter_pid(pid_table, run_id)
        evidence["reaped_runs"] = reap_stale_role_runs(database)
        evidence["recovered_missions"] = recover_stale_mission_state(database)
    report = (
        json.loads(result_path.read_text(encoding="utf-8"))
        if result_path.is_file()
        else None
    )
    return report, evidence


def _settle(
    args: argparse.Namespace, cycle_id: str
) -> tuple[str, list[dict[str, Any]]]:
    reports: list[dict[str, Any]] = []
    terminal_seen = False
    for attempt in range(1, SETTLE_LIMIT + 1):
        report, _evidence = _run_child(args, f"{cycle_id}-settle-{attempt}")
        if report is None:
            raise RuntimeError("settle child did not return a report")
        reports.append(report)
        database = FoundryDatabase(args.state_root / "home" / "foundry.db")
        terminal_seen = bool(_event_rows(database, "initiative.candidate_terminal"))
        if terminal_seen and str(report.get("outcome") or "") == "WAITING":
            return "WAIT", reports
        if (
            str(report.get("outcome") or "") in {"BLOCKED", "FAILED"}
            and not terminal_seen
        ):
            return "FAILED", reports
    return "RUNNING", reports


def _control(args: argparse.Namespace) -> OwnerControlService:
    return OwnerControlService(
        FoundryDatabase(args.state_root / "home" / "foundry.db"),
        secret=OWNER_SECRET,
        owner_id="v11-exit-proof-owner",
        stop_path=args.state_root / "home" / "EMERGENCY_STOP",
    )


def _fault_cycle(
    args: argparse.Namespace, request: Mapping[str, Any]
) -> dict[str, Any]:
    point = str(request.get("fault_point") or "")
    resume = request.get("resume") is True
    cycle_id = str(request["cycle_id"])

    def child_loop_id(phase: str) -> str:
        """Loop identity for one child process of this durable cycle.

        The harness keeps a single durable *cycle* identity across the fault
        injection and its resume (request/response ``cycle_id``), but every
        child process must carry its own worker-loop identity.  Production
        never replays a loop id from a different process: the CLI derives a
        fresh id per bounded cycle, and ``run_cycle`` records
        ``worker_loop.cycle_started:<loop_id>`` with the dispatching process
        identity in the payload, so a replay from a new process is an
        idempotency conflict rather than a resume.
        """
        return f"{cycle_id}-{phase}"

    status = "RUNNING"
    reports: list[dict[str, Any]] = []
    evidence: dict[str, Any] = {}
    stop_blocked = False
    if point == "post-candidate":
        hook = "post initiative.selection" if resume else "after_candidate_generation"
        _report, evidence = _run_child(
            args,
            child_loop_id("candidate-resume" if resume else "candidate-inject"),
            fault_hook=hook,
            expect_hard_exit=True,
        )
    elif point == "post-selection-objective":
        hook = (
            "post initiative.mission_created"
            if resume
            else "post initiative.objective_created"
        )
        _report, evidence = _run_child(
            args,
            child_loop_id("objective-resume" if resume else "objective-inject"),
            fault_hook=hook,
            expect_hard_exit=True,
        )
    elif point == "mid-build":
        if resume:
            _report, evidence = _run_child(
                args,
                child_loop_id("build-resume"),
                fault_hook="post effect dispatch",
                expect_hard_exit=True,
            )
        else:
            _report, evidence = _run_child(
                args, child_loop_id("build-inject"), mid_build_kill=True
            )
    elif point == "post-effect-pre-acceptance":
        if resume:
            status, reports = _settle(args, cycle_id)
        else:
            _report, evidence = _run_child(
                args,
                child_loop_id("effect-inject"),
                fault_hook="post verification.started",
                expect_hard_exit=True,
            )
    elif point == "stop-resume":
        database = FoundryDatabase(args.state_root / "home" / "foundry.db")
        before = {
            "missions": database.event_count(event_type="mission.created"),
            "builders": sum(
                row.get("role") in {"builder", "repair"}
                for row in _snapshot(args, request, status="RUNNING")["metadata"][
                    "agent_attempts"
                ]
            ),
            "effects": database.event_count(event_type="system.action_started"),
        }
        if resume:
            text = "resume bounded V11 exit proof after durable STOP"
            nonce = f"resume-{request['run_id']}"
            _control(args).resume(
                nonce=nonce,
                signature=sign_command(OWNER_SECRET, "RESUME", "GLOBAL", text, nonce),
                exact_text=text,
            )
            status, reports = _settle(args, cycle_id)
            stop_blocked = True
        else:
            text = "durable V11 exit-proof STOP fault"
            nonce = f"stop-{request['run_id']}"
            _control(args).stop_now(
                reason=text,
                nonce=nonce,
                signature=sign_command(
                    OWNER_SECRET, "STOP_EVERYTHING", "GLOBAL", text, nonce
                ),
            )
            report, evidence = _run_child(args, child_loop_id("stop"))
            if not report or report.get("outcome") != "FROZEN":
                raise RuntimeError(
                    "STOP fault did not freeze the canonical worker loop"
                )
            status = "STOPPED"
            after = {
                "missions": database.event_count(event_type="mission.created"),
                "builders": sum(
                    row.get("role") in {"builder", "repair"}
                    for row in _snapshot(args, request, status="RUNNING")["metadata"][
                        "agent_attempts"
                    ]
                ),
                "effects": database.event_count(event_type="system.action_started"),
            }
            if after != before:
                raise RuntimeError(
                    f"STOP allowed new dispatch/effect: before={before}, after={after}"
                )
            evidence["before"] = before
            evidence["after"] = after
    elif point == "process-restart":
        if resume:
            status, reports = _settle(args, cycle_id)
        else:
            _report, evidence = _run_child(
                args,
                child_loop_id("restart-inject"),
                fault_hook="post worker_loop.cycle_started",
                expect_hard_exit=True,
            )
    else:
        raise RuntimeError(f"unsupported fault point {point}")
    return _snapshot(
        args,
        request,
        status=status,
        fault_status="RECOVERED" if resume else "INJECTED",
        stop_blocked=stop_blocked,
        reports=reports,
        fault_evidence=evidence,
    )


def _run_request(args: argparse.Namespace) -> dict[str, Any]:
    request = json.loads(args.request.read_text(encoding="utf-8"))
    if request.get("schema") != DRIVER_SCHEMA:
        raise RuntimeError("driver request schema mismatch")
    if request.get("expected_sha") != args.expected_sha:
        raise RuntimeError("driver request expected SHA mismatch")
    _initialize(args)
    if request.get("fault_point"):
        return _fault_cycle(args, request)
    status, reports = _settle(args, str(request["cycle_id"]))
    return _snapshot(args, request, status=status, reports=reports)


def _run_child_entry(args: argparse.Namespace) -> int:
    state = json.loads(
        (args.state_root / "DRIVER_STATE.json").read_text(encoding="utf-8")
    )
    owner_repo = Path(state["owner_repo"])
    database = FoundryDatabase(args.state_root / "home" / "foundry.db")
    config = _config(args, owner_repo)

    def fault(point: str) -> None:
        if args.fault_hook and point == args.fault_hook:
            os._exit(HARD_EXIT)

    report = run_cycle(
        database,
        FoundryPaths(args.state_root / "home"),
        config,
        loop_id=args.cycle_id,
        instance_id=f"v11-proof-{args.cycle_id}",
        fault_hook=fault if args.fault_hook else None,
    )
    _atomic_json(args.child_result, report)
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-root", type=Path, required=True)
    parser.add_argument("--source-repo", type=Path, required=True)
    parser.add_argument("--expected-sha", required=True)
    parser.add_argument("--hermes-root", type=Path, required=True)
    parser.add_argument("--hermes-python", type=Path, required=True)
    parser.add_argument("--uv-exe", type=Path, required=True)
    parser.add_argument("--request", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--child", action="store_true")
    parser.add_argument("--cycle-id")
    parser.add_argument("--child-result", type=Path)
    parser.add_argument("--fault-hook")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    args.state_root = args.state_root.expanduser().resolve()
    args.source_repo = args.source_repo.expanduser().resolve()
    args.hermes_root = args.hermes_root.expanduser().resolve()
    args.hermes_python = args.hermes_python.expanduser().resolve()
    args.uv_exe = args.uv_exe.expanduser().resolve()
    if args.child:
        if not args.cycle_id or args.child_result is None:
            raise SystemExit("child mode requires --cycle-id and --child-result")
        return _run_child_entry(args)
    if args.request is None:
        raise SystemExit("driver mode requires --request")
    snapshot = _run_request(args)
    if args.output is not None:
        _atomic_json(args.output.expanduser().resolve(), snapshot)
    if not args.quiet:
        print(json.dumps(snapshot, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
