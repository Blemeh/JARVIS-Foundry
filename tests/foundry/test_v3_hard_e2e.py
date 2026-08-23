"""Fresh end-to-end V3 proof for the hard gate.

The three runs are separate disposable labs so each worker has an isolated
worktree/artifact root.  Every run still exercises the same runtime path:
observe -> score -> select -> materialize -> builder -> verifier -> outcome ->
maintenance follow-up.
"""
from __future__ import annotations

from pathlib import Path

from jarvis_foundry.paths import FoundryPaths
from jarvis_foundry.runtime.config import WorkerRuntimeConfig
from jarvis_foundry.runtime.worker_loop import run_cycle

from test_v3_discovery_cycle import EPOCH, _cycle_env, _lab_candidate


def test_three_fresh_discovery_to_outcome_loops_with_continuation(tmp_path: Path) -> None:
    proofs: list[dict] = []
    for index in range(3):
        lab = tmp_path / f"loop-{index}"
        lab.mkdir(parents=True)
        database, _ = _cycle_env(lab)
        _lab_candidate(database, objective="Fix the failing nightly integration")
        config = WorkerRuntimeConfig(
            enabled=True,
            resource_id="resource-1",
            discovery_cycle_enabled=True,
            executive_activation_epoch=EPOCH,
            runtime_state_root=str(lab / "state"),
            stop_sentinel_path=str(lab / "STOP"),
            lab_scripted_hermes=True,
        )
        loop_id = f"hard-e2e-{index}"
        report = run_cycle(
            database,
            FoundryPaths(lab),
            config,
            loop_id=loop_id,
            instance_id=f"hard-e2e-inst-{index}",
            vault_root=lab / "vault",
            handoff_dir=lab / "handoffs",
        )
        assert report["outcome"] == "PASSED", report
        outcomes = database.all("candidate_outcomes")
        assert len(outcomes) == 1
        lineage = [e.payload for e in database.events()
                   if e.event_type == "discovery.cycle_lineage_completed"]
        assert len(lineage) == 1
        chain = lineage[0]
        for key in ("candidate_id", "decision_id", "mission_id",
                    "evidence_bundle_id", "mission_completed_event_id",
                    "outcome_id", "next_selection_id"):
            assert chain.get(key), (key, chain)
        followups = [e for e in database.events()
                     if e.event_type == "executive.next_selection_requested"]
        assert len(followups) == 1
        proofs.append({
            "loop_id": loop_id,
            "mission_id": report["mission_id"],
            "outcome_id": chain["outcome_id"],
            "next_selection_id": chain["next_selection_id"],
        })

    assert len(proofs) == 3
    assert len({proof["mission_id"] for proof in proofs}) == 3
    assert len({proof["outcome_id"] for proof in proofs}) == 3
