# V5-S1 — System Registry (Multi-System Intelligence, slice 1)

**Status: CLOSED — PASS (round-1)** (2026-08-13)
**Code commits:** `e6ac89f` (feat(v5-s1)) + `8426558` (verifier finding
repairs: reconciled/plain-STOPPED regression tests + docstring truth)
**Independent verifier:** `deleg_d7639766` — **PASS**, 24 probes, at src
HEAD `e6ac89f` (code byte-identical to `3120cd3` for src/tests; the
executor's two repair commits landed after the verdict and are
documented below).
**Verdict archive:** `var/evidence/v5-s1-systems/verdict-deleg_d7639766.json`
sha256 `eb8ac515539feb6647047b527251fbc99ebeef8c1c28b78a3a424be0855db2ef`
**Ledger close:** `verification.passed:verdict-v5-s1-systems:round1`
(seq 22079) + `evidence.created:v5-s1-systems-close` (seq 22080) on the
LIVE ledger; live schema **v18 → v19** (rehearsed on a WAL-safe live
copy first — see Rehearsal).

## Slice

The durable catalog + policy gate + external-effect journal that every
later V5 adapter builds on (roadmap §8: "V4 PASS queues V5
automatically" — activated by the V4 acceptance gate close). No live
external connections in this slice; readiness is honest
READY/UNREADY/NOT_TESTED, never fabricated. Work package:
`docs/planning/V5_S1_WORK_PACKAGE.closed.yaml` (was V5_WORK_PACKAGE.yaml).

## Exit evidence (roadmap §8 → code + tests)

1. **Revocation and STOP immediately block new actions** —
   `systems_allowed_by_policy` (stop marker, active STOP_EVERYTHING /
   VETO_ACTION directives, FROZEN/SUSPENDED supervisor state). Verified
   semantics (probes P2a–P2e + regression tests): plain-STOPPED
   precursor state is NOT a freeze; a reconciled STOP stays blocked
   (projection moves the supervisor to SUSPENDED) until
   `supervisor.started` reopens the gate — fail-closed by design.
2. **Retries do not duplicate external effects** —
   `external_action_key` binds (system, target, payload-hash); the
   `system_actions` journal row is UNIQUE on the triple; phase-distinct
   event keys (started/completed never collide); identical re-delivery
   dedupes at the event AND projection level; changed payload = new
   effect (probes P3a–P3d).
3. **Secrets never appear** — secret-like capability/probe payloads are
   refused at the API boundary (deterministic pattern guard) + a
   ledger-scan test asserts no secret-like payloads exist (probes
   P4a–P4c).

## Verifier findings and resolution (deleg_d7639766, PASS)

| # | Severity | Finding | Resolution |
|---|---|---|---|
| P2(d) | MEDIUM | frozen→reconciled stays blocked (reconciled sets SUSPENDED; gate reopens only via supervisor.started); the "reconciled-existence" clause is currently dead code; probe-matrix expectation conflicted with the context spec | Fail-closed/spec-compliant; the probe expectation was wrong, not the code. Corrected: regression test `test_reconciled_after_frozen_blocks_until_started` pins the verified semantics (blocked until `supervisor.started`); evidence doc text corrected; dead clause kept (defense in depth) and noted in RK risk record for S2 wiring |
| P2(e) | LOW | docstring said gate requires "not FROZEN/STOPPED/SUSPENDED" but code blocks only FROZEN/SUSPENDED | Docstring corrected in `8426558`; plain-STOPPED-not-blocking pinned by `test_plain_stopped_supervisor_state_not_blocking` |
| P9(b) | LOW | VETO_ACTION matching is exact/case-sensitive; variants bypass | Consistent with the pre-existing ActionPolicy convention (control/policy.py); the gate has no production caller yet. Canonicalization of action kinds is S2 scope (ActionEnvelope normalization step 2) — recorded in the slice manifest |
| P9(a) | INFO | secret guard covers capability/probe payloads only; name/kind/target_ref unguarded (direct event craft bypasses — same trust boundary as hand-crafting any event) | Deliberate boundary (identifiers are free-form and must accept e.g. "secrets-backup" folders); documented in the module docstring + risk register |
| P9(c) | INFO | UNIQUE triple blocks a second row even with a different action_key (direct INSERT); crafted mismatched-key event silently IGNOREd at the projection | Trust-boundary behavior; key consistency holds via the API (`external_action_key` derives deterministically from the triple) |
| INFO | row() allowlist not extended with systems/system_actions | Bounded-read helper only; PROJECTION_TABLES + reconcile key map DO cover both (verifier P6: tamper detection works) — extended in S2 when the registry gets a read surface |
| INFO | doc claimed test coverage for 'reconciled'/'plain-STOPPED' that the test file lacked | Fixed: the two regression tests above (22 tests in the file, suite 505 → 507) |

## Tests (22 in tests/foundry/test_system_registry.py; suite 505 → 507)

catalog + idempotent registration; honest readiness transitions
(NOT_TESTED→READY→UNREADY→READY); unknown-system probes fail
atomically; policy gate matrix (marker/directive/veto/frozen/reconciled-
blocks-until-started/plain-STOPPED-not-blocking); effect keys + dedup +
phase-distinct events + changed-payload-is-a-new-effect; secret refusal
matrix + no-false-positives + ledger scan; migration v19
tables/constraints; reconcile coverage.

## Evidence

- Full suite: **507 passed EXIT=0** — artifact
  `var/evidence/v5-s1-systems/suite-v5-s1-close.txt` sha256
  `950c62859563cdadcd4595f8805e47c206c206421215f78ac1f385557ccfb5d5`
  (the pre-repair artifact `suite-v5-s1.txt`, 505 EXIT=0, remains as the
  verdict-time record).
- Rehearsal (D-036): `var/evidence/v5-s1-systems/rehearsal-v19-live-copy.json`
  sha256 `d9502846d303b5d6bcd53a6a99a721f2c7cc54a8213e20b646cf4ba401bc8b1f`
  — WAL-safe backup of the live DB (22,023 events at snapshot) →
  initialize() → v19; systems + system_actions with UNIQUE triple +
  readiness CHECK; all data preserved (events/claims/candidates
  identical); full projection rebuild over 22,023 events; reconcile
  **consistent, 0 mismatches**.
- Live close: schema **v18 → v19** (first attempt migrated v19 and
  rolled the event batch back on a NOT NULL contract issue in the
  evidence handler; retry appended the two close events — before
  22,078 / after 22,080 events, recorded in the close payload's
  before_state). Live systems/system_actions tables present, 0 rows
  (registry not yet used live — correct for S1). Daemon uninterrupted,
  5 s heartbeats, STOP false.
- V4 guards intact: monitor poll integrity, candidate supersession,
  reconcile, event store, v3 loop regressions all green (verifier P7).

## Known honest limitations

- Verifier shares the DeepSeek provider family (owner constraint) —
  independence via separate sessions, frozen artifacts, 24 adversarial
  probes.
- Action-kind canonicalization (veto bypass variants) is deferred to
  S2's ActionEnvelope normalization step; until then the gate has no
  production caller.
- The registry is NOT wired into live action selection (that is V5-S2/S7
  scope); live systems tables hold zero rows.
