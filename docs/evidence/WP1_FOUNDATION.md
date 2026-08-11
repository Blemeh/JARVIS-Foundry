# WP1 Foundation Evidence

Status: IN PROGRESS — independent verification running
Updated: 2026-08-11T19:20+02:00

## Implemented

- SQLite WAL event ledger with full-synchronous writes.
- Payload hashing, schema versioning, correlations, causation, and idempotency.
- Transactional event-plus-projection commits.
- Mission transition validation and durable projections.
- Resource, directive, action-decision, lease, and Supervisor state.
- Deterministic projection rebuild from verified events.
- SQLite backup and verified restore.
- Git resource inspection with exact root, HEAD, branch, status, and remotes.
- Standing-mandate action policy with STOP, veto, capability, target, effect,
  and rollback checks.
- Independent Supervisor, singleton lease, heartbeat, STOP freeze, and doctor.
- Local `jarvis` CLI for init, status, doctor, STOP, resume, and event reading.
- Schema version 3 with durable `agent_runs`, `evidence_bundles`,
  `verification_verdicts`, and `budgets` projections, event-driven through
  `agent_run.started|heartbeat|completed|failed`, `evidence.created`,
  `verification.started|passed|failed|blocked`, and
  `budget.allocation_recorded|usage_recorded`.
- Forward migration v1->v3 and v2->v3 with old-database compatibility tests
  covering seeded ledger and projection data on both old schema versions.
- All-or-nothing migrations: every DDL statement executes inside the caller's
  transaction (`split_sql_statements` replaces `executescript`), so a crash
  mid-migration rolls back completely and the next initialization heals.
- Subprocess crash fixture (`tests/foundry/crash_fixture.py`) that is
  hard-killed between transaction statements; tests prove no partial event,
  projection, or schema survives and committed state is preserved.
- Deterministic reconciliation diagnostics (`jarvis_foundry.reconcile`) that
  rebuild every projection from the verified ledger in-memory and report exact
  mismatches (table, key column, key, column, expected, actual) without ever
  mutating the live database; wired into `jarvis doctor`.
- Atomic evidence artifact hashing and storage (`jarvis_foundry.artifacts`)
  below project-local `var/evidence`: temp-file + fsync + `os.replace`,
  sha256 sidecars, traversal-safe names, tamper detection.

Note: the schema-3, crash-recovery, reconcile, and artifacts work originated in
a frozen headless run and is recorded here only after independent inspection
and green test reproduction in this session (see incident
`docs/evidence/INCIDENT_2026-08-11_WP1_STAGING.md`).

## Automated Evidence

`python -m pytest tests/foundry -q`:

```text
45 passed
```

Coverage includes schema-1-to-3 and schema-2-to-3 migration with preserved
ledger and projections, fresh schema-3 initialization, subprocess crash
between event and projection statements (no partial state), crash after
committed state (committed state preserved), crash mid-migration (partial
schema rolls back, next init heals to v3), agent-run lifecycle, heartbeat,
completion, and failure projections, evidence-bundle projection, verification
verdict pass/fail/blocked projections, budget allocation and usage
accumulation including usage without prior allocation, deterministic rebuild
of all new projections, reconciliation exact mismatch/missing/unexpected/integrity
reporting and read-only guarantee, artifact atomicity, sidecar, tamper
detection, and traversal rejection, duplicate and concurrent delivery,
idempotency conflict, invalid payload hash, invalid mission transition
rollback, deterministic projection rebuild, ledger tamper detection, verified
backup restore, Supervisor operation without Hermes, STOP freeze, singleton
lease conflict, expired lease recovery, unparseable sentinel fail-closed
behavior, registered target allowance, path escape, owner veto, STOP action
rejection, and unready-capability rejection.

## Combined Baseline

- Foundry suite: 45 passed (fresh run, captured in evidence bundle).
- Selected Hermes baseline (`.h` checkout, isolated `var/wp1-hermes-home`):
  `tests/test_estop.py` 24 passed; state/WAL/health suite 317 passed,
  8 skipped (same 8 skips as the WP0-documented run; superset of WP0's
  25+8 selection).
- Live Hermes and canonical Foundry databases untouched.

## Real Staging Evidence

Project-local staging database: `var/staging-foundation/foundry.db` (canonical,
read-only).

```yaml
staging_resource: hermes-development
staging_resource_role: HERMES_DEVELOPMENT_CHECKOUT_NOT_ACCEPTANCE_PROVING_RESOURCE
resource_state: READY
resource_head: 244d296646909aca1dd16c9759491da0ef4cd163
schema_version: 2
durable_start_active: true
supervisor_start: RUNNING
supervisor_heartbeat: RUNNING
registered_read_action: ALLOWED
database_quick_check: ok
hermes_required_for_supervisor: false
```

The CLI STOP/status/resume cycle also passed in `var/cli-proof` and required
reorientation on resume.

Old-database compatibility was proven on copies of the real artifacts in
`var/compat-proof/`; the canonical staging and cli-proof databases were left
untouched at their recorded schema versions and migrate automatically when
the runtime next initializes them:

- `var/compat-proof/staging-v2-copy.db`: v2 with 11 events -> v3 with 11
  events, all four new tables present, `quick_check=ok`.
- `var/compat-proof/cliproof-v1-copy.db`: v1 with 0 events -> v3, all four
  new tables present, `quick_check=ok`.
- `var/compat-proof/staging-home/foundry.db`: real v2 staging database opened
  through the `jarvis` CLI (`status`), migrated to v3, 11 events, one
  mission, one resource, and `start_active=1` all preserved.
- Reconciliation of the migrated real staging copy:
  `consistent=true`, event_count 11, no mismatches, no missing/unexpected
  rows, no integrity failures (report stored in evidence bundle).

## Evidence Bundle

Atomic hashed artifacts below `var/evidence/wp1-gate/` (sha256 sidecars via
`jarvis_foundry.artifacts`, manifest in `manifest.json`):

- `foundry-tests.txt` — fresh full-suite capture.
- `hermes-baseline-estop.txt` — fresh estop baseline capture.
- `canonical-databases.json` — size/sha256/quick_check/schema/event count for
  all canonical and wp1-staging databases.
- `reconcile-staging-copy.json` — reconciliation report on the migrated real
  staging copy.

## Remaining WP1 Gate Work

- Integrate the independent verification report (in progress, fresh isolated
  verifier session; same provider family as builder — limitation recorded per
  architecture contract 14.3).
- Repair any verifier findings.
- Record the WP1 PASS verdict, update `PROJECT_STATE.md`, and activate WP2
  automatically.

Verdict: NOT_TESTED until the independent review completes and passes.
