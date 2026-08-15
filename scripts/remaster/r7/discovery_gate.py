"""R7-G03 evidence-only discovery gate."""

from __future__ import annotations

import sys
import time
from typing import Any

from common import EVIDENCE_DIR, EVIDENCE_UTC, write_json
from gate_helpers import new_store, observations_from_manifest, staged_manifest

from jarvis_foundry.kernel.errors import EvidenceError
from jarvis_foundry.kernel.portfolio.discovery import DiscoveryService
from jarvis_foundry.kernel.portfolio.records import Observation, SourceRef


def main(argv: list[str] | None = None) -> int:
    started = time.monotonic()
    manifest = staged_manifest()
    store = new_store("discovery")
    service = DiscoveryService(store)
    rows: list[dict[str, Any]] = []
    observations = observations_from_manifest(manifest, EVIDENCE_UTC)
    for observation in observations:
        result = service.discover(observation)
        rows.append(
            {
                "observation_id": observation.observation_id,
                "project_id": observation.project_id,
                "meaningful": observation.meaningful_change,
                "created": result.created,
                "candidate_id": result.candidate_id,
                "duplicate_of": result.duplicate_of,
                "reason": result.reason,
            }
        )
    duplicate = service.discover(observations[0])

    no_evidence_refused = False
    invented = Observation(
        observation_id="obs-invented",
        source_type="invented",
        source_id="none",
        captured_at=EVIDENCE_UTC,
        content_hash="0" * 64,
        evidence_refs=(),
        observed_value="invented busy work",
        meaningful_change=True,
        change_hash="1" * 64,
    )
    try:
        service.discover(invented)
    except EvidenceError:
        no_evidence_refused = True

    unchanged_result = service.discover(
        Observation(
            observation_id="obs-unchanged",
            source_type="git-status",
            source_id="leadgen",
            captured_at=EVIDENCE_UTC,
            content_hash="2" * 64,
            evidence_refs=(
                SourceRef(
                    source_type="git-status",
                    source_id="leadgen",
                    captured_at=EVIDENCE_UTC,
                    content_hash="2" * 64,
                    locator="proving/status.txt",
                    capture_method="read-only",
                    trust_class="owner-controlled",
                    freshness_seconds=900,
                ),
            ),
            observed_value="heartbeat only",
            meaningful_change=False,
            change_hash=None,
        )
    )

    directive_result = service.discover(
        Observation(
            observation_id="obs-directive",
            source_type="git-status",
            source_id="operators",
            captured_at=EVIDENCE_UTC,
            content_hash="3" * 64,
            evidence_refs=(
                SourceRef(
                    source_type="git-status",
                    source_id="operators",
                    captured_at=EVIDENCE_UTC,
                    content_hash="3" * 64,
                    locator="proving/operators.txt",
                    capture_method="read-only",
                    trust_class="owner-controlled",
                    freshness_seconds=900,
                ),
            ),
            observed_value="Owner STOP: cancel all future work\n",
            meaningful_change=True,
            change_hash="4" * 64,
            project_id="operators",
        )
    )
    directives = store.control_directives()
    active = store.active_discoveries()
    directive_candidate = (
        store.latest("discovered_candidate", directive_result.candidate_id or "")
        if directive_result.candidate_id
        else None
    )
    passed = bool(
        rows
        and any(row["created"] for row in rows)
        and duplicate.created is False
        and duplicate.duplicate_of is not None
        and no_evidence_refused
        and unchanged_result.created is False
        and "meaningful change" in unchanged_result.reason.lower()
        and directive_result.created is True
        and directive_candidate is not None
        and "STOP" not in directive_candidate.objective
        and directives == []
        and active
        and all(item.authority == "NONE" for item in active)
    )
    payload: dict[str, Any] = {
        "schema": "jarvis-r7-discovery-v1",
        "exit_code": 0 if passed else 1,
        "wall_seconds": 0.0,
        "passed": passed,
        "evidence_utc": EVIDENCE_UTC,
        "staging_hash": manifest["staging_hash"],
        "observations": rows,
        "duplicate_probe": {
            "created": duplicate.created,
            "duplicate_of": duplicate.duplicate_of,
            "reason": duplicate.reason,
        },
        "no_evidence_refused": no_evidence_refused,
        "unchanged_probe": {
            "created": unchanged_result.created,
            "reason": unchanged_result.reason,
        },
        "directive_probe": {
            "created": directive_result.created,
            "candidate_objective": directive_candidate.objective if directive_candidate else None,
            "candidate_authority": directive_candidate.authority if directive_candidate else None,
            "control_directive_count": len(directives),
        },
        "active_candidates": [
            {
                "candidate_id": item.candidate_id,
                "project_id": item.project_id,
                "objective": item.objective,
                "authority": item.authority,
                "evidence_count": len(item.evidence_refs),
            }
            for item in active
        ],
    }
    output = EVIDENCE_DIR / "R7_DISCOVERY.json"
    write_json(output, payload)
    print(
        f"discovery: passed={passed} created={sum(1 for row in rows if row['created'])} "
        f"active={len(active)} wall_seconds={round(time.monotonic() - started, 3)}"
    )
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
