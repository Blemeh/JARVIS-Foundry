# V1 Continuous Build Sequence

Status: ACTIVE - WP0 PASS, WP1 ACTIVE

The normative work packages are in the V1 specification. This file defines
automatic promotion.

## Promotion Rule

One work package is active at a time. The executive activates the next package
automatically when:

1. predecessor evidence is PASS;
2. target state and user changes are captured;
3. required capabilities are READY or can be probed;
4. tests and rollback are defined;
5. independent verification is assigned;
6. no active owner veto conflicts.

FAIL triggers diagnosis, repair, and retest. A technically unavailable
dependency produces a resumable blocker while independent work continues.

## WP0 - Reality And Baseline

Autonomously inspect host, Hermes, Obsidian, providers, projects, credentials,
toolchains, tests, and recoverability. Create the isolated Hermes development
checkout and preserve the baseline. Do not preselect the V1 seven-day proving
mission merely because a resource is already available or convenient.

Gate: launch checklist PASS.

Verdict: PASS. The acceptance proving mission/resource remains unselected and
is selected later under `04_V1_ACCEPTANCE_GATE.md`.

## WP1 - Domain And Event Store

Build durable events, projections, missions, resources, capabilities, actions,
vetoes, evidence, verdicts, leases, budgets, and migrations.

Gate: rebuild, interruption, duplicate, corruption, and restore PASS.

Current state: ACTIVE. Verdict: NOT_TESTED.

## WP2 - Independent Supervisor And STOP

Build the process boundary, health, event wake, freeze, STOP, worker control,
restart recovery, and fake adapters.

Gate: STOP and recovery work with Hermes dead.

Current state: INACTIVE. Verdict: NOT_TESTED. Starter code is not gate proof.

## WP3 - Autonomous Action Control

Build standing-mandate evaluation, owner-veto checks, automatic resource
registration, target resolution, readiness states, ActionEnvelopes, credential
brokering, snapshots, and consequence-scaled controls.

Gate: injected commands cannot forge vetoes or readiness, targets are explicit,
and `NOT_READY` creates capability work without asking permission.

Current state: INACTIVE. Verdict: NOT_TESTED. Starter code is not gate proof.

## WP4 - Hermes Role Adapter

Build abstract executive, builder, verifier, critic, and repair roles with
structured results, context manifests, leases, timeouts, fallback, and provider
diversity.

Gate: fake and live adapters reconcile identities, evidence, and failure.

## WP5 - Autonomous Mission Loop

Build next-action selection, execution, independent verification, repair,
completion, next-mission selection, and honest waiting.

Gate: a failed step is repaired and work continues without owner prompting.

## WP6 - Obsidian Intelligence Projection

Build V1 write recovery, provenance, snapshots, expected hashes, conflicts,
semantic verification, rollback, and whole-vault retrieval.

Gate: concurrent edit and restore preserve meaning and bytes.

## WP7 - Integration And Automatic Activation

Build installer, uninstaller, Supervisor startup, CLI, tray/hotkey STOP,
configuration, diagnostics, and acceptance harness.

Gate: after PASS, the already-recorded `START BUILD` event causes automatic
installation and activation.

## WP8 - Seven-Day Acceptance

Run G1-G9, F1-F15, forced restarts, and at least ten useful verified cycles on
the autonomously selected proving project.

Gate: PASS marks V1 trusted, keeps autonomous operation running, and queues V2.

## Work Record

```yaml
work_package_id: null
status: CANDIDATE
objective: null
expected_value: null
target_resources: []
capabilities_required: []
active_owner_vetoes_checked: []
expected_effects: []
risk_class: null
budget_estimate: null
acceptance_criteria: []
tests_required: []
evidence_required: []
rollback_or_compensation: null
stop_conditions: []
builder: null
independent_verifier: null
next_package_on_pass: null
```

There is no owner-approval field.
