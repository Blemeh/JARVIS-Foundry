# WP3 Evidence — Autonomous Action Control And Resource Registry

Status: **PASS** (gate closed 2026-08-11, continuation run)
Updated: 2026-08-11T21:00+02:00

## Gate Close Record

- Independent verifier deleg_d335614d completed its full battery (full suite 94
  passed; 65-test adversarial scratch suite B1-B6 all PASS; real Windows
  junction probes; critical negative result: no `open()`/`resolve()` divergence).
  Full summary: `subagent-summary-0-20260811_201639_515049.txt` — verdict PASS
  on all six criteria with two fail-loud integrity findings (B5, B6) and the B2
  precedence nuance.
- **Repairs (continuation run 2, per run brief):** B5 — registration
  idempotency key now covers the full payload (`project.registered:{resource_id}:
  {payload_hash(resource)}`) so same-HEAD re-registration with changed dirty
  status updates instead of raising EventConflict. B6 — decision idempotency key
  now covers the full payload (`action.decision:{action_id}:{decision}:
  {payload_hash(payload)}`) so changed reason/target records a new decision
  (projection upsert, last wins) while identical re-delivery dedupes. B2
  precedence nuance (unready capability + matching veto => NOT_READY, denial
  preserved) documented and locked by test. Regression tests:
  `test_registration.py::test_same_head_re_registration_updates_dirty_state`,
  `test_action_policy.py::test_decision_redelivery_dedupes_and_changed_reason_records`,
  `test_action_policy.py::test_unready_capability_with_matching_veto_denies_not_ready`.
- Executive re-verification: full suite 133 passed fresh; frozen-state
  battery green (estop 24 passed, canonical DBs unchanged).
- Frozen snapshot: var/evidence/wp3-gate/ (re-frozen after repairs; snapshot
  sha256 `425b9f92603ce3783cf5ea2f5288ea046a6335614bd9d83ad1dc7d6c4fd3fa22`).
- Full detail: `docs/evidence/WP3_GATE_REPORT.md`.

## Implemented

- Envelope contract validation (`_validate_envelope` in
  `src/jarvis_foundry/control/policy.py`): identity, kind, capability,
  expected effects, rollback contract, and risk class (C0-C3) are enforced
  deterministically BEFORE any state lookup; a malformed envelope can never
  reach capability or resource evaluation.
- Capability states: READY and ACTIVE are usable; every other state denies
  with NOT_READY and the exact state in the reason.
- Durable capability transitions (`src/jarvis_foundry/control/capabilities.py`):
  `capability.state_changed` events projected into `resources.capabilities_json`
  (append-only, rebuildable, idempotent per transition; invalid states and
  unknown resources rejected atomically).
- Distinct `action.invalid_target` event type (previously conflated with
  action.not_ready) recorded with decision INVALID_TARGET and exact reason.
- Registration idempotency: `project.registered` upserts on resource_id, so
  re-registration after a commit updates baseline/HEAD/dirty state without
  duplicate rows; registration events are deduplicated by idempotency key.
- Adversarial path-boundary proof on Windows: traversal, absolute-outsider,
  drive-root, and junction escapes all denied deterministically (INVALID_TARGET,
  no mutation authority).

## Automated Evidence

`python -m pytest tests/foundry -q`:

```text
94 passed
```

New coverage (16 tests):

- `test_registration.py` (5): exact git reality recording (root/HEAD/branch/
  status/remotes), dirty repository -> PROBING not READY, registration
  idempotency + re-registration update, non-repository raises, event shape.
- `test_capability_states.py` (5): durable + rebuildable transitions,
  idempotent per-transition events, invalid state rejection, policy
  READY/ACTIVE vs PROBING semantics, unknown-resource atomic rollback.
- `test_path_boundaries.py` (6): traversal, absolute outside, drive root and
  drive-letter, junction escape (real junction via mklink /J), distinct
  invalid-target event + decision record, malformed-envelope denial.

## WP3 Gate Criteria (CURRENT_WORK_PACKAGE.yaml)

| ID | Criterion | Status |
|---|---|---|
| B1 | Traversing/symlink/junction/case-aliased/unresolvable targets denied deterministically | PASS (junction, traversal, drive, absolute-outsider tested on Windows) |
| B2 | Active owner vetoes outrank standing mandate and readiness | PASS (existing veto tests) |
| B3 | Unready capabilities produce NOT_READY, no mutation authority | PASS (PROBING/DISCOVERED/ACTIVE semantics tested) |
| B4 | Capability state changes are durable events, rebuild deterministically | PASS |
| B5 | Git resource registration records root/HEAD/branch/dirty/remotes/readiness, idempotent | PASS |
| B6 | Every decision recorded with action id/reason/resolved target; duplicates produce one decision | PASS (existing + invalid_target) |

## Remaining WP3 Gate Work

- Combined baseline re-run.
- Independent verification (fresh isolated session, adversarial).
- Frozen-state snapshot at gate close.
- Record WP3 PASS, update PROJECT_STATE, activate WP4.

Verdict: NOT_TESTED until independent review completes and passes.
