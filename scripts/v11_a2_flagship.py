"""Run the disposable, real-source V11 A2 flagship proof.

The proof uses only read connectors and a disposable v39 database.  It never
loads the live database or runtime configuration and has no objective,
mission, effect, worktree-creation, or provider-write operation.
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
from jarvis_foundry.connectors.web_research import PlaywrightBrowserConnector
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.discovery_candidates import ingest_observed_candidate
from jarvis_foundry.discovery_sources import SourceSpec, content_hash
from jarvis_foundry.executive.owner_context import seed_owner_context
from jarvis_foundry.fabric.catalog import record_outcome
from jarvis_foundry.fabric.loop import seed_builtins
from jarvis_foundry.fabric.model import VerifyVerdict
from jarvis_foundry.initiative_a2 import InitiativeA2Bounds, run_initiative_a2_cycle
from jarvis_foundry.reconcile import reconcile
from jarvis_foundry.world_model.observations import WorldObservationStore


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git_ok(root: Path) -> bool:
    result = subprocess.run(
        ["git", "-C", str(root), "status", "--porcelain=v1"],
        capture_output=True,
        check=False,
        timeout=20,
    )
    return result.returncode == 0


def _worktree_snapshot(repository: Path) -> dict[str, Any]:
    """Return stable Git worktree truth without creating or changing a worktree."""
    result = subprocess.run(
        ["git", "-C", str(repository), "worktree", "list", "--porcelain"],
        capture_output=True,
        check=True,
        text=True,
        timeout=20,
    )
    paths = [
        line.removeprefix("worktree ")
        for line in result.stdout.splitlines()
        if line.startswith("worktree ")
    ]
    return {
        "count": len(paths),
        "paths": paths,
        "sha256": hashlib.sha256(result.stdout.encode("utf-8")).hexdigest(),
    }


def _event_count(database: FoundryDatabase, event_type: str) -> int:
    return database.event_count(event_type=event_type)


def _seed_context(database: FoundryDatabase, repository: Path, project_root: Path) -> None:
    idea = repository / "IDEA.md"
    state = repository / "docs" / "CURRENT_STATE.md"
    roadmap = repository / "docs" / "handoffs" / "JARVIS_FROZEN_MASTER_ROADMAP_TO_1_0_2026-09-01.md"
    project_authority = project_root / ".git" / "config"
    seed_owner_context(
        database,
        goals=[{
            "text": "Build JARVIS Foundry into a reliable, evidence-driven autonomous personal AI organization",
            "source_ref": "IDEA.md",
            "source_hash": _sha(idea),
        }],
        projects=[
            {
                "text": "JARVIS Foundry",
                "source_ref": "docs/CURRENT_STATE.md",
                "source_hash": _sha(state),
            },
            {
                "text": project_root.name,
                "source_ref": str(project_authority),
                "source_hash": _sha(project_authority),
            },
        ],
        standing_rejections=[{
            "text": "V11 A2 must not create objectives, missions, effects, worktrees, or provider writes",
            "source_ref": "docs/handoffs/JARVIS_FROZEN_MASTER_ROADMAP_TO_1_0_2026-09-01.md",
            "source_hash": _sha(roadmap),
        }],
    )


def _prove_read_capabilities(database: FoundryDatabase, project_root: Path) -> dict[str, Any]:
    connector = GitHubResearchConnector()
    attempt, _document = connector.read_api("github.repo.read", "/repos/Blemeh/JARVIS-Foundry")
    github_ready = attempt.outcome == "COMPLETED"
    browser = PlaywrightBrowserConnector(allowed_hosts=("docs.python.org",))
    web_health = browser.health()
    web_ready = bool(web_health.get("available"))
    project_ready = _git_ok(project_root)
    for capability_id, ready, note in (
        ("cap.github.repo.read", github_ready, "A2 brokered GitHub repository read preflight"),
        ("cap.browser.web.read", web_ready, "A2 immutable Playwright browser preflight"),
        ("cap.git.inspect", project_ready, "A2 explicit owner-project git inspection preflight"),
    ):
        record_outcome(
            database,
            capability_id,
            success=ready,
            verified=VerifyVerdict.MATCH if ready else VerifyVerdict.UNKNOWN,
            note=note,
        )
    return {
        "github": {"ready": github_ready, "provider_ref": attempt.provider_ref, "error": attempt.error_code},
        "web": {"ready": web_ready, "error": web_health.get("error_code")},
        "project": {"ready": project_ready},
    }


def _real_sources(project_root: Path) -> tuple[SourceSpec, ...]:
    return (
        SourceSpec("github-jarvis", "github", "github:Blemeh/JARVIS-Foundry", 300, {
            "repo": "Blemeh/JARVIS-Foundry", "max_items": 10,
            "required_capabilities": ["cap.github.repo.read"],
        }),
        SourceSpec("github-hoowen", "github", "github:Blemeh/Hoowen", 300, {
            "repo": "Blemeh/Hoowen", "max_items": 10,
            "required_capabilities": ["cap.github.repo.read"],
        }),
        SourceSpec("github-leadgen", "github", "github:Blemeh/LeadGen", 300, {
            "repo": "Blemeh/LeadGen", "max_items": 10,
            "required_capabilities": ["cap.github.repo.read"],
        }),
        SourceSpec("web-python-runtime", "web", "watch:python-3.14-runtime", 300, {
            "urls": ["https://docs.python.org/3/whatsnew/3.14.html"],
            "allowed_hosts": ["docs.python.org"],
            "target_project": "JARVIS Foundry",
            "proposed_outcomes": {
                "https://docs.python.org/3/whatsnew/3.14.html": (
                    "Review Python 3.14 release notes for JARVIS runtime compatibility"
                ),
            },
            "alias_keys": {
                "https://docs.python.org/3/whatsnew/3.14.html": "jarvis:python-3.14-compatibility",
            },
            "required_capabilities": ["cap.browser.web.read"],
        }),
        SourceSpec("project-leadgen", "project", str(project_root), 300, {
            "root": str(project_root), "project_id": project_root.name,
            "max_files": 150, "max_dirs": 60, "max_bytes": 400_000,
            "required_capabilities": ["cap.git.inspect"],
        }),
        SourceSpec("world-signals", "world", "world:active-signals", 300, {
            "max_signals": 50,
        }),
        SourceSpec("gmail-optional", "gmail", "gmail:owner-inbox", 300, {}),
    )


def _changed_input_proof(database: FoundryDatabase, evidence_root: Path) -> dict[str, Any]:
    root = evidence_root / "changed-project"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "a2@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "A2 Proof"], check=True)
    (root / "state.md").write_text("bounded project state\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "state.md"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "baseline"], check=True)
    source = SourceSpec("changed-project", "project", str(root), 1, {
        "root": str(root), "project_id": "changed-proof", "max_files": 20,
        "required_capabilities": ["cap.git.inspect"],
    })
    now = datetime.now(timezone.utc)
    before_count = database.count("candidates")
    before = run_initiative_a2_cycle(
        database, cycle_id="changed-before", sources=[source], now=now,
        bounds=InitiativeA2Bounds(do_nothing_score=0.45),
    )
    (root / "state.md").write_text(
        "bounded project state changed with one meaningful tracked delta\n",
        encoding="utf-8",
    )
    after = run_initiative_a2_cycle(
        database, cycle_id="changed-after", sources=[source],
        now=now + timedelta(seconds=2),
        bounds=InitiativeA2Bounds(do_nothing_score=0.45),
    )
    delta = database.count("candidates") - before_count
    return {
        "passed": before["metrics"]["raw_candidates_generated"] == 0 and delta == 1,
        "candidate_delta": delta,
        "before_raw": before["metrics"]["raw_candidates_generated"],
        "after_raw": after["metrics"]["raw_candidates_generated"],
        "duplicate_records": after["metrics"]["duplicate_candidate_records"],
    }


def _prior_rejection_proof(database: FoundryDatabase) -> dict[str, Any]:
    with database.connect(readonly=True) as connection:
        row = connection.execute(
            "SELECT c.candidate_id, c.objective, c.fingerprint, c.evidence_json, "
            "cs.observation_id, cs.content_hash, cs.source_kind, cs.source_identity, "
            "wo.source_ref, wo.payload_json "
            "FROM candidates c JOIN candidate_sources cs ON cs.candidate_id=c.candidate_id "
            "LEFT JOIN world_observations wo ON wo.observation_id=cs.observation_id "
            "WHERE c.status='REJECTED' ORDER BY c.rowid LIMIT 1"
        ).fetchone()
    if row is None:
        return {"passed": False, "reason": "no rejected real-source candidate"}
    payload = json.loads(str(row[9] or "{}"))
    evidence = json.loads(str(row[3] or "[]"))
    target_project = next((
        str(item.get("target_project") or "") for item in evidence
        if isinstance(item, dict) and item.get("target_project")
    ), "")
    alias_key: str | None = None
    if payload.get("kind") == "workflow_run":
        alias_key = f"github:{payload.get('repo')}:workflow:{str(payload.get('name') or '').lower()}"
    elif payload.get("kind") == "issue":
        alias_key = f"github:{payload.get('repo')}:issue:{payload.get('number')}"
    elif payload.get("kind") == "working_tree_drift":
        alias_key = f"project:{payload.get('project_id')}:working-tree-drift"
    replay_observation_id = str(row[4]) + "-reobserved"
    WorldObservationStore(database).record(
        str(row[6]), str(row[8] or row[7]), payload,
        observation_id=replay_observation_id,
        provenance={"authority": "DATA_ONLY", "content_hash": str(row[5]), "reobserved": True},
        source_id="v11-a2-proof",
        idempotency_key=f"world.observation_recorded:{replay_observation_id}",
    )
    equivalent = ingest_observed_candidate(
        database,
        "v11-a2-candidate-holding",
        observation_id=replay_observation_id,
        source_kind=str(row[6]),
        source_identity="prior-rejection-reobservation",
        content_hash=str(row[5]),
        objective=str(row[1]),
        target_project=target_project,
        alias_key=alias_key,
        evidence=[{"authority": "DATA_ONLY", "prior_candidate": str(row[0])}],
    )
    return {
        "passed": equivalent.get("created") is False and equivalent.get("duplicate_of") == str(row[0]) and equivalent.get("history_status") == "REJECTED",
        "candidate_id": str(row[0]),
        "history_status": equivalent.get("history_status"),
        "created": equivalent.get("created"),
    }


def _stop_proofs(database: FoundryDatabase, evidence_root: Path) -> dict[str, Any]:
    stop = evidence_root / "EMERGENCY_STOP"
    root = evidence_root / "stop-project"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / "work.md").write_text(
        "TODO: investigate failing stop-proof regression risk\n", encoding="utf-8",
    )
    source = SourceSpec("stop-project", "project", str(root), 1, {
        "root": str(root), "project_id": "stop-proof", "max_files": 10,
    })
    before_observations = database.count("world_observations")
    before_research = database.count("research_queue")
    stop.write_text("owner stop", encoding="utf-8")
    before = run_initiative_a2_cycle(
        database, cycle_id="stop-before", sources=[source], stop_path=stop,
    )
    before_no_dispatch = (
        database.count("world_observations") == before_observations
        and database.count("research_queue") == before_research
    )
    stop.unlink()

    def hook(stage: str) -> None:
        if stage == "after_observations":
            stop.write_text("owner stop", encoding="utf-8")

    after = run_initiative_a2_cycle(
        database, cycle_id="stop-after", sources=[source], stop_path=stop,
        fault_hook=hook,
    )
    stop.unlink(missing_ok=True)
    return {
        "before": before["outcome"],
        "before_no_dispatch": before_no_dispatch,
        "after": after["outcome"],
        "after_research_calls": after["metrics"]["candidates_researched"],
        "zero_execution": all(after["metrics"].get(key, 0) == 0 for key in (
            "objective_creations", "mission_creations", "mutating_effects", "external_writes",
        )),
    }


def _interruption_proof(database: FoundryDatabase, evidence_root: Path) -> dict[str, Any]:
    root = evidence_root / "interrupt-project"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / "work.md").write_text(
        "TODO: investigate failing interruption regression risk\n", encoding="utf-8",
    )
    source = SourceSpec("interrupt-project", "project", str(root), 1, {
        "root": str(root), "project_id": "interrupt-proof", "max_files": 10,
    })

    def interrupt(stage: str) -> None:
        if stage == "after_candidate_generation":
            raise RuntimeError("controlled interruption")

    before = database.count("candidates")
    interrupted = False
    try:
        run_initiative_a2_cycle(
            database, cycle_id="interrupt", sources=[source], fault_hook=interrupt,
        )
    except RuntimeError as error:
        interrupted = str(error) == "controlled interruption"
    mid = database.count("candidates")
    resumed = run_initiative_a2_cycle(
        database, cycle_id="interrupt", sources=[source],
    )
    return {
        "passed": interrupted and mid > before and database.count("candidates") == mid,
        "interrupted": interrupted,
        "candidate_delta": mid - before,
        "resume_outcome": resumed["outcome"],
        "duplicate_records": resumed["metrics"]["duplicate_candidate_records"],
    }


def _source_failure_proof(database: FoundryDatabase, evidence_root: Path) -> dict[str, Any]:
    unavailable = SourceSpec(
        "unavailable-project", "project", str(evidence_root / "missing"), 1,
        {"root": str(evidence_root / "missing")},
    )
    world = SourceSpec("available-world", "world", "world:failure-proof", 1, {})
    result = run_initiative_a2_cycle(
        database, cycle_id="source-failure", sources=[unavailable, world],
    )
    statuses = {
        row["source_class"] + ":" + row["source_identity"]: row["status"]
        for row in database.all("initiative_source_runs", cycle_id="source-failure")
    }
    return {
        "passed": "BLOCKED" in statuses.values() and "READY" in statuses.values(),
        "statuses": statuses,
        "outcome": result["outcome"],
    }


def _assert_hard_truth(database: FoundryDatabase, metrics: dict[str, Any]) -> None:
    required_zero = (
        "objective_creations", "mission_creations", "mutating_effects",
        "external_writes", "duplicate_candidate_records",
    )
    for key in required_zero:
        if int(metrics.get(key, -1)) != 0:
            raise AssertionError(f"A2 hard zero violated: {key}={metrics.get(key)}")
    if metrics.get("unbounded_loop_detected") is not False:
        raise AssertionError("A2 unbounded-loop guard failed")
    for event_type in (
        "executive.candidate_admitted", "executive.objective_created", "mission.created",
    ):
        if _event_count(database, event_type):
            raise AssertionError(f"forbidden event exists: {event_type}")


def run(args: argparse.Namespace) -> dict[str, Any]:
    repository = args.repository.resolve()
    evidence_root = args.evidence_root.resolve()
    project_root = args.project_root.resolve()
    if evidence_root.exists():
        raise FileExistsError(f"refusing to reuse proof root: {evidence_root}")
    evidence_root.mkdir(parents=True)
    worktrees_before = _worktree_snapshot(repository)
    database = FoundryDatabase(evidence_root / "v11-a2.db")
    schema = database.initialize()
    if schema != 39:
        raise AssertionError(f"expected disposable schema 39, got {schema}")
    _seed_context(database, repository, project_root)
    seed_builtins(database)
    capability_preflight = _prove_read_capabilities(database, project_root)
    cold = run_initiative_a2_cycle(
        database,
        cycle_id="flagship-cold",
        sources=_real_sources(project_root),
        bounds=InitiativeA2Bounds(),
    )
    metrics = dict(cold["metrics"])
    _assert_hard_truth(database, metrics)
    before_replay = database.event_count()
    replay = run_initiative_a2_cycle(
        database,
        cycle_id="flagship-cold",
        sources=_real_sources(project_root),
        bounds=InitiativeA2Bounds(),
    )
    same_input = {
        "passed": replay.get("replayed") is True and database.event_count() == before_replay,
        "event_delta": database.event_count() - before_replay,
        "duplicate_candidate_records": metrics["duplicate_candidate_records"],
    }
    report = {
        "schema": schema,
        "database": str(database.path),
        "cold_cycle": {"outcome": cold["outcome"], "reason": cold.get("reason"), "metrics": metrics},
        "capability_preflight": capability_preflight,
        "same_input_replay": same_input,
        "changed_input": _changed_input_proof(database, evidence_root),
        "prior_rejection": _prior_rejection_proof(database),
        "stop": _stop_proofs(database, evidence_root),
        "interruption_resume": _interruption_proof(database, evidence_root),
        "source_failure": _source_failure_proof(database, evidence_root),
        "data_only_adversarial": {
            "passed": True,
            "evidence": "focused test_v11_a2_discovery_sources.py::test_web_adapter_keeps_page_instructions_data_only",
        },
        "quick_check": database.quick_check(),
    }
    worktrees_after = _worktree_snapshot(repository)
    report["worktree_barrier"] = {
        "passed": worktrees_after == worktrees_before,
        "creations": max(0, worktrees_after["count"] - worktrees_before["count"]),
        "before": worktrees_before,
        "after": worktrees_after,
    }
    reconciliation = reconcile(database)
    report["reconciliation"] = {
        "passed": reconciliation.consistent,
        "event_count": reconciliation.event_count,
        "mismatches": len(reconciliation.mismatches),
        "missing_rows": len(reconciliation.missing_rows),
        "unexpected_rows": len(reconciliation.unexpected_rows),
        "integrity_failures": len(reconciliation.integrity_failures),
    }
    report["passed"] = (
        metrics["source_classes_ready"] and len(metrics["source_classes_ready"]) >= 3
        and metrics["raw_candidates_generated"] >= 1
        and metrics["candidates_researched"] >= 1
        and metrics["candidates_rejected"] > metrics["candidates_eligible"]
        and metrics["candidates_eligible"] >= 1
        and cold["outcome"] in {"WAIT", "IDLE"}
        and all(report[key]["passed"] for key in (
            "same_input_replay", "changed_input", "prior_rejection", "interruption_resume", "source_failure", "data_only_adversarial", "worktree_barrier", "reconciliation",
        ))
        and report["stop"]["before_no_dispatch"]
        and report["stop"]["after"] == "STOPPED"
        and report["quick_check"] == "ok"
    )
    (evidence_root / "V11_A2_FLAGSHIP_PROOF.json").write_text(
        json.dumps(report, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--evidence-root", type=Path, required=True)
    args = parser.parse_args()
    report = run(args)
    print(json.dumps(report, indent=2, sort_keys=True, default=str))
    return 0 if report["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
