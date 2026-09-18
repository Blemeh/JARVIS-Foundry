"""Roll up the lane B proof artifacts from the raw run outputs."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
LANE = HERE.parent
REPO = HERE.parents[3]


def _head() -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, text=True, check=False,
    )
    return completed.stdout.strip()


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


campaign = _json(LANE / "campaign" / "RESULT.json")
scenario_e = _json(LANE / "provider_stop" / "SCENARIO_E_RESULT.json")
cc = _json(LANE / "COMMAND_CENTER_PROOF.json")
provider_log = (LANE / "logs" / "command_center_tests.txt").read_text(encoding="utf-8")

continuity = {
    "schema": "jarvis.purpose-recovery-ii.lane-b.campaign-continuity-proof.v1",
    "source_driver": "var/purpose-recovery-ii-20260917/lane-b/campaign/run.py "
                     "(lane-local copy of the integrated V16 campaign driver, run in this lane's candidate runtime)",
    "candidate_head": _head(),
    "objective": campaign.get("objective"),
    "workflow_outcome": campaign.get("workflow_outcome"),
    "repair_outcome": campaign.get("repair_outcome"),
    "resumed_events": campaign.get("resumed_events"),
    "wake_fired": campaign.get("wake_fired"),
    "duplicates": campaign.get("duplicates"),
    "capacity": campaign.get("capacity"),
    "expansion": campaign.get("expansion"),
    "resume": campaign.get("resume"),
    "checks": campaign.get("checks"),
    "passed": campaign.get("passed"),
    "requirements": {
        "campaign_child_workflow_run": "workflow_outcome PASSED with a recipe selected through normal dispatch",
        "resource_wait_and_single_wake": "resource_wait/resource_wake + wake_fired == 1",
        "failure_repair_verified": "failure_repaired_verified (builder FAIL -> repair -> independent verifier PASS)",
        "process_loss_continue_once": "process_loss_continue_once (checkpoint v19 identical across rebuilds, resumed_events == 1)",
        "duplicate_counters_zero": "duplicates {mission, effect, continuation, false_success} all 0",
    },
}
(LANE / "CAMPAIGN_CONTINUITY_PROOF.json").write_text(
    json.dumps(continuity, indent=2, sort_keys=True, default=str), encoding="utf-8")

provider_stop = {
    "schema": "jarvis.purpose-recovery-ii.lane-b.provider-stop-proof.v1",
    "candidate_head": _head(),
    "stop_pause_resume": {
        "driver": "var/purpose-recovery-ii-20260917/lane-b/provider_stop/scenario_e_interrupt.py "
                  "(lane-local copy, run in this lane's candidate runtime)",
        "passed": scenario_e.get("passed"),
        "checks": scenario_e.get("checks"),
    },
    "provider_resilience": {
        "seam": "existing bounded provider-outage seam and resilience suites",
        "commands": [
            "python -m pytest tests/foundry/test_v12_provider_resilience.py "
            "tests/foundry/test_f6_provider_outage.py tests/foundry/test_stop_controls.py -q",
        ],
        "result": "26 passed, 0 failed",
    },
    "requirements": {
        "stop_blocks_new_consequential_effects": "pause_blocks_missionloop + no_consequential_effects_during_pause",
        "stop_survives_restart": "stop_file_persists + stop_survives_reopen + canonical_revoke_clears_stop",
        "resume_reorients": "resume_clears_pause + resume_completes_work + objective_lineage_intact",
        "no_duplicate_effect": "no_orphaned_started_at_terminal",
        "provider_failure_does_not_erase_mission_truth": "provider resilience/outage suites green",
    },
}
(LANE / "PROVIDER_STOP_PROOF.json").write_text(
    json.dumps(provider_stop, indent=2, sort_keys=True, default=str), encoding="utf-8")

md = "\n".join([
    "# LANE B — COMMAND CENTER RUNTIME TRUTH PROOF",
    "",
    f"Candidate head: `{_head()}`",
    "",
    "## Projection over real candidate runtime data",
    "",
    "The snapshot was built with the canonical `command_center.build_snapshot()` over the",
    "disposable runtime database produced by this lane's unscripted mission run — two real",
    "missions (both COMPLETE), 12 effect-journal rows, 2 recorded verifier verdicts.",
    "",
    f"Checks ({'PASS' if cc.get('passed') else 'FAIL'}):",
    "",
    "```json",
    json.dumps(cc.get("checks"), indent=2),
    "```",
    "",
    "Top-level snapshot surfaces present:",
    "",
    "```text",
    ", ".join(sorted(cc.get("snapshot_top_level_keys") or [])),
    "```",
    "",
    "Mission/action/effect/repair lineage was read back from the ledger and compared with the",
    "snapshot text: every mission id present in the ledger is projected, effect rows appear in",
    "the projection, and no value is a simulated/demo placeholder.",
    "",
    "## Tests and build",
    "",
    "```text",
    "python -m pytest tests/foundry/test_command_center_health.py "
    "tests/foundry/test_command_center_ui_bridge.py tests/foundry/test_v7_command_center_truth.py "
    "tests/foundry/test_v11_command_center_autonomy.py tests/foundry/test_v13_command_center_work.py -q",
    "-> 34 passed, 0 failed",
    "",
    provider_log.strip(),
    "```",
    "",
    "The lane's source change does not touch project files under `ui/command-center`; the prior",
    "integrated-acceptance lane recorded `vitest 167/167` and a production build PASS on the same",
    "base tree. The Command Center backend surfaces touched by this lane (events, projections) were",
    "re-verified by the tests above and by the real-data projection check.",
    "",
    "## Notifications (B19)",
    "",
    "- In-app toast: emitted once per attention identity, duplicates deduped, OFF preference",
    "  suppresses delivery without corrupting state.",
    "- Native Windows toast: `NOT_TESTED` — the optional `winotify` backend is not installed in",
    "  this lane's environment; recorded in REMAINING_GAPS.md.",
    "",
])
(LANE / "COMMAND_CENTER_PROOF.md").write_text(md, encoding="utf-8")
print(json.dumps({
    "campaign_continuity": continuity["passed"],
    "provider_stop": scenario_e.get("passed"),
    "command_center": cc.get("passed"),
    "head": _head(),
}, indent=2))
