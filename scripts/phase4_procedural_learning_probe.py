"""Run one bounded local browser procedure through canonical JARVIS seams.

This script is intentionally explicit and one-shot.  It does not start a
learning loop, create missions, approve candidates, or activate desktop
execution.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.procedural_learning import (
    LocalStateOracle,
    OpenAdaptAdapter,
    OpenAdaptConfig,
    adopt_openadapt_bundle,
    governed_replay,
    register_openadapt_capabilities,
    register_tier_c_reference,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", required=True)
    parser.add_argument("--bundle", required=True)
    parser.add_argument("--flow-executable", required=True)
    parser.add_argument("--deployment", required=True)
    parser.add_argument("--browser-url", required=True)
    parser.add_argument("--oracle-url", required=True)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--request-id", required=True)
    parser.add_argument("--task", required=True)
    parser.add_argument("--option", required=True)
    parser.add_argument("--stop-path", required=True)
    args = parser.parse_args()

    database = FoundryDatabase(Path(args.database))
    database.initialize()
    config = OpenAdaptConfig(
        Path(args.flow_executable),
        deployment_config=Path(args.deployment),
        browser_url=args.browser_url,
    )
    register_openadapt_capabilities(database, config=config)
    bundle = adopt_openadapt_bundle(
        Path(args.bundle),
        procedure_id="phase4-probe-bundle",
        source_workflow_evidence=["phase1-qualified-demonstration"],
        source_candidate_id="phase4-local-candidate",
        source_mission_id="phase4-local-mission",
        input_schema={"task": {"type": "string"}, "option": {"type": "string"}},
        expected_effects=["local synthetic record created"],
        verification_plan={"kind": "local-state-oracle", "url": args.oracle_url},
    )
    tier_c_id = register_tier_c_reference(
        database,
        bundle,
        name="Phase 4 local synthetic browser procedure",
        description="A bounded, harmless local record demonstration.",
        trigger_terms=["phase4", "synthetic", "browser"],
    )
    outcome = governed_replay(
        database,
        tier_c_procedure_id=tier_c_id,
        mission_id="phase4-local-mission",
        run_id=f"phase4-{args.request_id}",
        request_id=args.request_id,
        params={"task": args.task, "option": args.option},
        adapter=OpenAdaptAdapter(config),
        run_dir=Path(args.run_dir),
        oracle=LocalStateOracle(args.oracle_url),
        stop_path=Path(args.stop_path),
    )
    print(json.dumps({
        "outcome": outcome.status,
        "request_id": args.request_id,
        "action_id": outcome.action_id,
        "bundle_hash": outcome.bundle_hash,
        "model_calls": outcome.model_calls,
        "report_path": outcome.report_path,
        "effect_state": outcome.effect_state,
        "recovery_classification": outcome.recovery_classification,
        "tier_c_procedure_id": tier_c_id,
        "phase5_started": False,
        "continuous_learning": False,
        "desktop": "PROBING",
    }, sort_keys=True))
    return 0 if outcome.status == "COMPLETED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
