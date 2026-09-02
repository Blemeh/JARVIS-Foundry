"""Real, bounded, read-only proof for the V11 observation intelligence pack."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from jarvis_foundry.connectors.github_research import GitHubResearchConnector
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.discovery_sources import (
    SourceSpec,
    SourceReadResult,
    content_hash,
    read_github_source,
    read_web_source,
    read_world_source,
    run_source_observation_cycle,
)
from jarvis_foundry.events import Event
from jarvis_foundry.world_model.signals import WorldSignalStore
from jarvis_foundry.world_model.entities import WorldEntityStore


def _start(database: FoundryDatabase, cycle_id: str) -> None:
    database.append(Event.create(
        "initiative.cycle_started", {"cycle_id": cycle_id, "status": "RUNNING"},
        source_type="proof", source_id="v11-observation-intelligence-proof",
        idempotency_key=f"initiative.cycle_started:{cycle_id}",
    ))


def _event_counts(database: FoundryDatabase) -> dict[str, int]:
    with database.connect(readonly=True) as connection:
        rows = connection.execute(
            "SELECT event_type, COUNT(*) FROM events GROUP BY event_type"
        ).fetchall()
    return {str(row[0]): int(row[1]) for row in rows}


class ReadAudit:
    def __init__(self) -> None:
        self.connector = GitHubResearchConnector()
        self.calls: list[dict[str, str]] = []

    def read_api(self, action: str, endpoint: str):
        self.calls.append({"action": action, "endpoint": endpoint})
        return self.connector.read_api(action, endpoint)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--world-database", type=Path)
    args = parser.parse_args()
    output_root = args.output_root.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    database_path = output_root / "observation-intelligence.db"
    if database_path.exists():
        raise SystemExit(f"refusing to overwrite existing proof database: {database_path}")
    database = FoundryDatabase(database_path)
    schema = database.initialize()
    project_root = args.project_root.resolve()
    git_head = subprocess.run(
        ["git", "-C", str(project_root), "rev-parse", "HEAD"],
        capture_output=True, check=True, text=True, timeout=15,
    ).stdout.strip()
    git_branch = subprocess.run(
        ["git", "-C", str(project_root), "branch", "--show-current"],
        capture_output=True, check=True, text=True, timeout=15,
    ).stdout.strip()
    WorldEntityStore(database).record(
        "PROJECT", "JARVIS Foundry observation candidate",
        entity_id="proof-project-jarvis-observation",
        attributes={"root": str(project_root), "git_head": git_head, "branch": git_branch},
        authority="VERIFIED_EVIDENCE", confidence=1.0,
        provenance={
            "verification": "git rev-parse HEAD", "verified": True,
            "source": str(project_root),
        },
        idempotency_key="world.entity_recorded:proof-project-jarvis-observation",
    )
    audit = ReadAudit()
    opportunity = "project:JARVIS:python-runtime-compatibility"
    sources = (
        SourceSpec("github-cpython", "github", "python/cpython", 1, {
            "repo": "python/cpython", "max_items": 6, "recent_days": 365,
            "affects_current_runtime": True, "target_project": "JARVIS",
            "opportunity_key": opportunity,
            "important_paths": ["pyproject.toml", "configure", "PCbuild", "Tools/requirements"],
        }),
        SourceSpec("local-jarvis", "project", str(args.project_root.resolve()), 1, {
            "root": str(project_root), "project_id": "JARVIS Foundry",
            "max_files": 100, "max_dirs": 40, "max_bytes": 256_000,
            "important_files": ["pyproject.toml", "uv.lock", "docs/CURRENT_STATE.md"],
            "stale_after_days": 30,
        }),
        SourceSpec("web-python-314", "web", "watch:python-314", 1, {
            "urls": ["https://docs.python.org/3/whatsnew/3.14.html"],
            "allowed_hosts": ["docs.python.org"],
            "proposed_outcomes": {
                "https://docs.python.org/3/whatsnew/3.14.html":
                    "Review Python 3.14 changes for JARVIS runtime compatibility",
            },
            "alias_keys": {"https://docs.python.org/3/whatsnew/3.14.html": opportunity},
            "target_project": "JARVIS", "candidate_on": "always",
        }),
        SourceSpec("world-existing", "world", "world:existing-verified", 1, {
            "max_signals": 25, "max_facts": 25,
        }),
    )

    readers = {
        "github": lambda spec, stop: read_github_source(
            spec, connector=audit, stop_check=stop,
        ),
        "web": lambda spec, stop: read_web_source(spec, stop_check=stop),
        "world": lambda spec, stop: read_world_source(
            spec, store=WorldSignalStore(database), stop_check=stop,
        ),
    }
    world_available = bool(args.world_database and args.world_database.is_file())
    upstream_world_snapshot = None
    if world_available:
        upstream_database = FoundryDatabase(args.world_database.resolve())
        upstream = read_world_source(
            sources[-1], store=WorldSignalStore(upstream_database),
        )
        upstream_world_snapshot = {
            "database": str(args.world_database.resolve()),
            "status": upstream.status, "observations": len(upstream.observations),
            "kinds": sorted({str(item.payload.get("kind")) for item in upstream.observations}),
            "reason": upstream.reason or None,
        }

    started = datetime.now(timezone.utc).replace(microsecond=0)
    _start(database, "real-cold")
    cold = run_source_observation_cycle(
        database, "real-cold", sources, now=started,
        readers=readers, candidate_limit=50,
    )
    _start(database, "real-replay")
    replay = run_source_observation_cycle(
        database, "real-replay", sources, now=started + timedelta(seconds=1),
        readers=readers, candidate_limit=50,
    )
    web_spec = next(source for source in sources if source.source_kind == "web")
    real_web = read_web_source(web_spec)
    if real_web.status == "READY" and real_web.observations:
        raw_only = replace(
            real_web.observations[0],
            raw_content_hash=content_hash({
                "real_raw": real_web.observations[0].raw_content_hash,
                "controlled_perturbation": "formatting-only",
            }),
        )
        _start(database, "real-web-raw-only")
        noise_probe = run_source_observation_cycle(
            database, "real-web-raw-only", [web_spec],
            now=started + timedelta(seconds=2), candidate_limit=50,
            readers={"web": lambda _spec, _stop: SourceReadResult("READY", (raw_only,))},
        )
    else:
        noise_probe = {
            "status": "UNKNOWN", "observations_recorded": 0,
            "candidates_created": 0,
            "source_digest": {"noise_suppressed": 0},
        }
    counts = _event_counts(database)
    with database.connect(readonly=True) as connection:
        quick_check = connection.execute("PRAGMA quick_check").fetchone()[0]
        candidate_count = connection.execute("SELECT COUNT(*) FROM candidates").fetchone()[0]
        source_link_count = connection.execute("SELECT COUNT(*) FROM candidate_sources").fetchone()[0]
        correlated_links = connection.execute(
            "SELECT COUNT(*) FROM candidate_sources WHERE source_identity LIKE 'github:%' "
            "OR source_identity LIKE 'web:%'"
        ).fetchone()[0]
    forbidden = {
        "objectives": sum(value for key, value in counts.items()
                          if key in {"executive.objective_created", "executive.objective_recorded"}),
        "missions": counts.get("mission.created", 0),
        "effects": sum(value for key, value in counts.items()
                       if key.startswith("effect.") or key.startswith("system.action_")),
        "external_writes": sum(value for key, value in counts.items()
                               if key.startswith("provider.write") or key.endswith(".write_completed")),
    }
    candidate_opportunities = [
        row for row in cold["source_digest"]["candidate_worthy_signals"]
        if row.get("opportunity_key") == opportunity
    ]
    payload = {
        "verdict": "V11_OBSERVATION_INTELLIGENCE_READY" if (
            schema == 40 and quick_check == "ok"
            and cold["observations_recorded"] > 0
            and replay["observations_recorded"] == 0
            and noise_probe["observations_recorded"] == 0
            and noise_probe["source_digest"]["noise_suppressed"] == 1
            and len(candidate_opportunities) >= 2
            and not any(forbidden.values())
            and all(call["action"].endswith(".read") for call in audit.calls)
        ) else "V11_OBSERVATION_INTELLIGENCE_NOT_READY",
        "schema": schema,
        "started_at": started.isoformat(),
        "real_sources": {
            "github": "https://github.com/python/cpython",
            "web": "https://docs.python.org/3/whatsnew/3.14.html",
            "project": str(project_root),
            "world_database": str(database_path),
            "world_fact": {
                "entity_id": "proof-project-jarvis-observation",
                "git_head": git_head, "branch": git_branch,
                "authority": "VERIFIED_EVIDENCE",
            },
            "upstream_world_database_checked_readonly": upstream_world_snapshot,
        },
        "github_read_audit": {
            "calls": audit.calls,
            "read_calls": len(audit.calls),
            "non_read_calls": [call for call in audit.calls if not call["action"].endswith(".read")],
        },
        "cold_cycle": cold,
        "unchanged_replay": replay,
        "formatting_noise_probe": {
            "method": "real extracted web semantics with a controlled raw-only hash perturbation",
            "result": noise_probe,
        },
        "correlation": {
            "opportunity_key": opportunity,
            "candidate_worthy_signal_count": len(candidate_opportunities),
            "signals": candidate_opportunities,
            "candidate_count": candidate_count,
            "candidate_source_links": source_link_count,
            "github_or_web_links": correlated_links,
        },
        "forbidden": forbidden,
        "sqlite_quick_check": quick_check,
    }
    proof_path = output_root / "V11_OBSERVATION_INTELLIGENCE_PROOF.json"
    encoded = json.dumps(payload, indent=2, sort_keys=True, default=str).encode("utf-8")
    proof_path.write_bytes(encoded)
    print(json.dumps({
        "proof": str(proof_path), "sha256": hashlib.sha256(encoded).hexdigest(),
        "verdict": payload["verdict"], "schema": schema,
        "cold_observations": cold["observations_recorded"],
        "cold_candidates": cold["candidates_created"],
        "replay_observations": replay["observations_recorded"],
        "noise_suppressed": noise_probe["source_digest"]["noise_suppressed"],
        "correlated_signals": len(candidate_opportunities),
        "forbidden": forbidden,
    }, sort_keys=True))
    return 0 if payload["verdict"] == "V11_OBSERVATION_INTELLIGENCE_READY" else 1


if __name__ == "__main__":
    raise SystemExit(main())
