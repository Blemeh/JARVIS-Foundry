# LANE B — COMMAND CENTER RUNTIME TRUTH PROOF

Candidate head: `4e7b8c969342e94e02331b5e3cf3c122fa431f7f`

## Projection over real candidate runtime data

The snapshot was built with the canonical `command_center.build_snapshot()` over the
disposable runtime database produced by this lane's unscripted mission run — two real
missions (both COMPLETE), 12 effect-journal rows, 2 recorded verifier verdicts.

Checks (PASS):

```json
{
  "attention_or_wait_surface_present": true,
  "current_missions_projected": true,
  "effects_projected": true,
  "health_or_release_surface_present": true,
  "real_mission_identity_matches_ledger": true,
  "snapshot_built_from_durable_events": true,
  "truthful_env_flags_not_simulated": true,
  "verified_outcome_projected": true
}
```

Top-level snapshot surfaces present:

```text
briefing_l10n, briefing_packet, briefings, candidates, capability_fabric, controls, costs, decision_queue, discovery, effect_truth, evidence, evolution_truth, executive, generated_at, health, memory, mission_steps, missions, owner_context, owner_directives, portfolio, procedures, recovery, release_identity, releases_canaries_rollbacks, resources, result_traceability, source_of_truth, stop, supervisor, system_state, systems, timeline, vault, verification, voice, work_hierarchy, workers, world_intelligence
```

Mission/action/effect/repair lineage was read back from the ledger and compared with the
snapshot text: every mission id present in the ledger is projected, effect rows appear in
the projection, and no value is a simulated/demo placeholder.

## Tests and build

```text
python -m pytest tests/foundry/test_command_center_health.py tests/foundry/test_command_center_ui_bridge.py tests/foundry/test_v7_command_center_truth.py tests/foundry/test_v11_command_center_autonomy.py tests/foundry/test_v13_command_center_work.py -q
-> 34 passed, 0 failed

..................................                                       [100%]
EXIT=0
```

The lane's source change does not touch project files under `ui/command-center`; the prior
integrated-acceptance lane recorded `vitest 167/167` and a production build PASS on the same
base tree. The Command Center backend surfaces touched by this lane (events, projections) were
re-verified by the tests above and by the real-data projection check.

## Notifications (B19)

- In-app toast: emitted once per attention identity, duplicates deduped, OFF preference
  suppresses delivery without corrupting state.
- Native Windows toast: `NOT_TESTED` — the optional `winotify` backend is not installed in
  this lane's environment; recorded in REMAINING_GAPS.md.
