"""Disposable real-source proof for V11 continuous observation.

All JARVIS writes are confined to a new evidence root and its SQLite database.
GitHub, web, repository, and filesystem adapters are read-only. The harness
changes one tracked file in a disposable source repo solely to inject the
required meaningful observation delta.
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
from jarvis_foundry.discovery_monitors import configured_monitors
from jarvis_foundry.discovery_sources import SourceSpec
from jarvis_foundry.executive.owner_context import seed_owner_context
from jarvis_foundry.fabric.catalog import record_outcome
from jarvis_foundry.fabric.loop import seed_builtins
from jarvis_foundry.fabric.model import VerifyVerdict
from jarvis_foundry.initiative_a2 import (
    InitiativeA2Bounds,
    run_continuous_observation_cycle,
)
from jarvis_foundry.reconcile import reconcile


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git(repository: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repository), *args], capture_output=True,
        text=True, check=False, timeout=30,
    )


def _worktrees(repository: Path) -> dict[str, Any]:
    result = _git(repository, "worktree", "list", "--porcelain")
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "git worktree list failed")
    paths = [line[9:] for line in result.stdout.splitlines()
             if line.startswith("worktree ")]
    return {
        "count": len(paths),
        "sha256": hashlib.sha256(result.stdout.encode("utf-8")).hexdigest(),
        "paths": paths,
    }


def _seed(database: FoundryDatabase, repository: Path, delta_root: Path) -> None:
    idea = repository / "IDEA.md"
    state = repository / "docs" / "CURRENT_STATE.md"
    roadmap = (repository / "docs" / "handoffs"
               / "JARVIS_FROZEN_MASTER_ROADMAP_TO_1_0_2026-09-01.md")
    seed_owner_context(
        database,
        goals=[{
            "text": "Build JARVIS Foundry into a reliable evidence-driven autonomous personal AI organization",
            "source_ref": str(idea), "source_hash": _sha(idea),
        }],
        projects=[
            {"text": "JARVIS Foundry", "source_ref": str(state),
             "source_hash": _sha(state)},
            {"text": delta_root.name, "source_ref": str(delta_root),
             "source_hash": hashlib.sha256(str(delta_root).encode()).hexdigest()},
        ],
        standing_rejections=[{
            "text": "Continuous observation must not create objectives missions effects or external writes",
            "source_ref": str(roadmap), "source_hash": _sha(roadmap),
        }],
    )
    seed_builtins(database)


def _preflight(database: FoundryDatabase, repository: Path) -> dict[str, Any]:
    github_attempt, _ = GitHubResearchConnector().read_api(
        "github.repo.read", "/repos/Blemeh/JARVIS-Foundry")
    browser_health = PlaywrightBrowserConnector(
        allowed_hosts=("docs.python.org",)).health()
    readiness = {
        "github": github_attempt.outcome == "COMPLETED",
        "web": bool(browser_health.get("available")),
        "project": _git(repository, "status", "--porcelain=v1").returncode == 0,
    }
    for capability, ready in (
        ("cap.github.repo.read", readiness["github"]),
        ("cap.browser.web.read", readiness["web"]),
        ("cap.git.inspect", readiness["project"]),
    ):
        record_outcome(
            database, capability, success=ready,
            verified=VerifyVerdict.MATCH if ready else VerifyVerdict.UNKNOWN,
            note="V11 continuous-observation disposable read preflight",
        )
    return {
        **readiness,
        "github_error": github_attempt.error_code,
        "web_error": browser_health.get("error_code"),
    }


def _sources(repository: Path, delta_root: Path) -> tuple[SourceSpec, ...]:
    return (
        SourceSpec("github-jarvis", "github", "github:Blemeh/JARVIS-Foundry", 3600, {
            "repo": "Blemeh/JARVIS-Foundry", "max_items": 10,
            "required_capabilities": ["cap.github.repo.read"],
        }),
        SourceSpec("web-python-runtime", "web", "watch:python-3.14-runtime", 3600, {
            "urls": ["https://docs.python.org/3/whatsnew/3.14.html"],
            "allowed_hosts": ["docs.python.org"],
            "target_project": "JARVIS Foundry",
            "proposed_outcomes": {
                "https://docs.python.org/3/whatsnew/3.14.html":
                    "Review Python 3.14 release notes for JARVIS runtime compatibility",
            },
            "alias_keys": {
                "https://docs.python.org/3/whatsnew/3.14.html":
                    "jarvis:python-3.14-compatibility",
            },
            "required_capabilities": ["cap.browser.web.read"],
        }),
        SourceSpec("project-candidate", "project", str(repository), 3600, {
            "root": str(repository), "project_id": "JARVIS Foundry",
            "max_files": 150, "max_dirs": 60, "max_bytes": 400_000,
            "required_capabilities": ["cap.git.inspect"],
        }),
        SourceSpec("project-delta", "project", str(delta_root), 1, {
            "root": str(delta_root), "project_id": delta_root.name,
            "max_files": 20, "max_dirs": 10, "max_bytes": 100_000,
            "required_capabilities": ["cap.git.inspect"],
        }),
        SourceSpec("world-signals", "world", "world:active-signals", 3600, {
            "max_signals": 50,
        }),
        SourceSpec("gmail-optional", "gmail", "gmail:owner-inbox", 3600, {}),
    )


def _event_counts(database: FoundryDatabase) -> dict[str, int]:
    with database.connect(readonly=True) as connection:
        rows = connection.execute(
            "SELECT event_type, COUNT(*) FROM events GROUP BY event_type"
        ).fetchall()
    return {str(row[0]): int(row[1]) for row in rows}


def _zero_truth(database: FoundryDatabase) -> dict[str, int]:
    counts = _event_counts(database)
    return {
        "objectives_created": sum(counts.get(key, 0) for key in (
            "executive.objective_created", "executive.objective_recorded")),
        "missions_created": counts.get("mission.created", 0),
        "effects": sum(value for key, value in counts.items()
                       if key.startswith("effect.") or key.startswith("system.action_")),
        "external_writes": sum(value for key, value in counts.items()
                               if key.startswith("provider.write")
                               or key.endswith(".write_completed")),
    }


def _compact(report: dict[str, Any]) -> dict[str, Any]:
    metrics = dict(report.get("metrics") or {})
    return {
        "outcome": report.get("outcome"),
        "cycle_id": report.get("cycle_id"),
        "cycles_dispatched": report.get("cycles_dispatched"),
        "next_due_at": report.get("next_due_at"),
        "metrics": metrics,
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    repository = args.repository.resolve()
    evidence_root = args.evidence_root.resolve()
    if evidence_root.exists():
        raise FileExistsError(f"refusing to reuse proof root: {evidence_root}")
    evidence_root.mkdir(parents=True)
    delta_root = evidence_root / "delta-source"
    delta_root.mkdir()
    _git(delta_root, "init", "-q")
    _git(delta_root, "config", "user.email", "proof@example.invalid")
    _git(delta_root, "config", "user.name", "V11 Proof")
    tracked = delta_root / "state.md"
    tracked.write_text("baseline\n", encoding="utf-8")
    _git(delta_root, "add", "state.md")
    _git(delta_root, "commit", "-qm", "proof baseline")
    tracked.write_text("meaningful regression state one\n", encoding="utf-8")

    worktrees_before = _worktrees(repository)
    database = FoundryDatabase(evidence_root / "continuous-observation.db")
    schema = database.initialize()
    _seed(database, repository, delta_root)
    preflight = _preflight(database, repository)
    sources = _sources(repository, delta_root)
    bounds = InitiativeA2Bounds(
        source_limit=8, candidate_limit=20, research_candidate_limit=3,
        research_budget_per_cycle=3.0, do_nothing_score=0.20,
    )
    base = datetime.now(timezone.utc).replace(microsecond=0)

    cycle1 = run_continuous_observation_cycle(
        database, sources=sources, bounds=bounds, now=base)
    before_pre_due = database.event_count()
    pre_due = run_continuous_observation_cycle(
        database, sources=sources, bounds=bounds,
        now=base + timedelta(milliseconds=500))
    pre_due_delta = database.event_count() - before_pre_due
    cycle2 = run_continuous_observation_cycle(
        database, sources=sources, bounds=bounds,
        now=base + timedelta(seconds=1))

    tracked.write_text("meaningful regression state two\n", encoding="utf-8")
    cycle3 = run_continuous_observation_cycle(
        database, sources=sources, bounds=bounds,
        now=base + timedelta(seconds=2))

    restarted = FoundryDatabase(database.path)
    restarted.initialize()
    restart_wait = run_continuous_observation_cycle(
        restarted, bounds=bounds, now=base + timedelta(milliseconds=2500))
    restart_due = run_continuous_observation_cycle(
        restarted, bounds=bounds, now=base + timedelta(seconds=3))

    stop = evidence_root / "EMERGENCY_STOP"
    stop.write_text("owner stop proof", encoding="utf-8")
    before_stop = restarted.event_count()
    stopped = run_continuous_observation_cycle(
        restarted, bounds=bounds, now=base + timedelta(seconds=4),
        stop_path=stop)
    stop_event_delta = restarted.event_count() - before_stop

    source_rows = configured_monitors(restarted)
    with restarted.connect(readonly=True) as connection:
        cold_statuses = {
            str(row[0]): str(row[1]) for row in connection.execute(
                "SELECT source_class, status FROM initiative_source_runs "
                "WHERE cycle_id = ? ORDER BY source_class",
                (cycle1["cycle_id"],),
            ).fetchall()
        }
        candidate_count = int(connection.execute(
            "SELECT COUNT(*) FROM candidates").fetchone()[0])
        refreshed_count = int(connection.execute(
            "SELECT COUNT(*) FROM events WHERE event_type = "
            "'discovery.candidate_refreshed'").fetchone()[0])
    zero = _zero_truth(restarted)
    worktrees_after = _worktrees(repository)
    reconciliation = reconcile(restarted)
    report = {
        "schema": schema,
        "database": str(restarted.path),
        "real_source_preflight": preflight,
        "source_statuses_cycle_1": cold_statuses,
        "cycle_1": _compact(cycle1),
        "pre_due_wait": {
            **_compact(pre_due), "event_delta": pre_due_delta,
        },
        "cycle_2_unchanged": _compact(cycle2),
        "cycle_3_changed": _compact(cycle3),
        "restart": {
            "wait": _compact(restart_wait), "due": _compact(restart_due),
        },
        "stop": {
            **_compact(stopped), "event_delta": stop_event_delta,
        },
        "watch_sources": source_rows,
        "candidate_count": candidate_count,
        "candidate_refresh_events": refreshed_count,
        "zero_execution_truth": zero,
        "worktree_barrier": {
            "unchanged": worktrees_before == worktrees_after,
            "before": worktrees_before, "after": worktrees_after,
        },
        "quick_check": restarted.quick_check(),
        "reconciliation": {
            "consistent": reconciliation.consistent,
            "event_count": reconciliation.event_count,
            "mismatches": len(reconciliation.mismatches),
            "missing_rows": len(reconciliation.missing_rows),
            "unexpected_rows": len(reconciliation.unexpected_rows),
            "integrity_failures": len(reconciliation.integrity_failures),
        },
    }
    cold_metrics = report["cycle_1"]["metrics"]
    unchanged_metrics = report["cycle_2_unchanged"]["metrics"]
    changed_metrics = report["cycle_3_changed"]["metrics"]
    ready_classes = {key for key, value in cold_statuses.items()
                     if value == "READY"}
    report["passed"] = bool(
        schema == 40
        and "project" in ready_classes
        and (preflight["github"] or preflight["web"])
        and int(cold_metrics.get("observations_recorded", 0)) > 0
        and int(cold_metrics.get("unique_candidates", 0)) > 0
        and cycle1.get("outcome") == "WAIT"
        and pre_due.get("cycles_dispatched") == 0
        and pre_due_delta == 0
        and int(unchanged_metrics.get("observations_recorded", -1)) == 0
        and int(unchanged_metrics.get("unique_candidates", -1)) == 0
        and int(unchanged_metrics.get("candidates_refreshed", -1)) == 0
        and int(unchanged_metrics.get("research_calls", -1)) == 0
        and int(changed_metrics.get("observations_recorded", 0)) > 0
        and int(changed_metrics.get("candidates_refreshed", 0)) == 1
        and restart_wait.get("cycles_dispatched") == 0
        and restart_due.get("cycles_dispatched") == 1
        and stopped.get("cycles_dispatched") == 0
        and stop_event_delta == 0
        and all(value == 0 for value in zero.values())
        and worktrees_before == worktrees_after
        and restarted.quick_check() == "ok"
        and reconciliation.consistent
    )
    proof_path = evidence_root / "V11_CONTINUOUS_OBSERVATION_PROOF.json"
    proof_path.write_text(
        json.dumps(report, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    report["proof_path"] = str(proof_path)
    report["proof_sha256"] = _sha(proof_path)
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--evidence-root", type=Path, required=True)
    report = run(parser.parse_args())
    print(json.dumps(report, indent=2, sort_keys=True, default=str))
    return 0 if report["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
