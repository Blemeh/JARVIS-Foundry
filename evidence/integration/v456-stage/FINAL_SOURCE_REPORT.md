# V4/V5/V6 + Cinematic integration source report

Stage: `integration/v456-cinematic-stage-20260823`

Verified source checkpoint: `f8b896b` (the report/evidence commit follows this
checkpoint; later documentation commits must remain descendants of it).

## Source result

**PASS.** V4 owner controls/briefings, V5 multi-system effects/lineage, V6
governed self-evolution, and the existing cinematic Command Center are integrated
in one isolated lane. The cross-lane snapshot, briefing, packet, and Owner
Management projections preserve unknown/failed/blocked states and keep UI output
non-authoritative.

The repository-wide source run is `var/evidence/v456-stage/full-suite-20260823-r3.xml`:
1,981 collected, 1,980 passed, 0 failed, 0 errors, and one explicit
symlink/reserved-ledger boundary skip. The V6 verifier refuses NOT_TESTED criteria
as promotion PASS; cycle fixtures now include a real syntax-checkable artifact so
the promotion tests prove evidence rather than relying on absence.

## Supporting gates

- Command Center: typecheck, lint, 115 Vitest tests, production build, security
  scan, and 212/212 product checks PASS.
- V3 operational E2E reconciliation PASS; V3 hard focused/supporting/migration/
  independent suites PASS; the bounded eight-cycle soak PASS.
- V3 hard gate remains **BLOCKED/FAIL-CLOSED** for AC-17 because the measured
  four-hour disposable proving window was not run.
- Migration rehearsal PASS across nine disposable/read-only-source scenarios at
  schema 36 after replacing a stale hard-coded schema-33 assertion with the
  canonical migration constant.
- Adversarial truth/lifecycle/secret/injection/recovery campaign: 13 PASS, 0 FAIL,
  one documented semantic-info limitation.
- Disposable release identity build/start/rollback PASS with exact Git commit and
  tree provenance; no production mutation.

## Live boundary

Live remains **BLOCKED/NOT_TESTED for promotion**. The read-only runtime check
observed CURRENT `769715a7...`, filesystem-only release authority, schema v34,
STOP false, and an active Supervisor/worker-loop. The integrated source expects
schema v36. No live database write, migration, scheduler mutation, process kill,
provider call, release switch, or production UI restart was performed.

Canonical source promotion can be considered only after a fresh main/origin graph
check. Production mutation requires a separate ownership and authorization
checkpoint; absence of that proof is not a PASS.
