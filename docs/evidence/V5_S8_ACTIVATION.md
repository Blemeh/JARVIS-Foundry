# V5-S8 — Available-Connector Activation and Provider Evidence

**Status: CLOSED — PASS (round-2)** (2026-08-13)
**Code commits:** `b6a9b9d` (feat(v5-s8)) + `a26eba0` (fix(v5-s8):
round-1 findings)
**Round-1 verifier:** `deleg_19385768` — **PASS** with 1 MEDIUM + 1 LOW
+ 3 INFO (`verdict-deleg_19385768.json`).
**Round-2 verifier:** `deleg_e14d91ce` — **PASS** (5 probe groups +
full-suite re-run; 3 INFO, none blocking;
`verdict-deleg_e14d91ce-r2.json` sha256 `35264286…`).

## Slice

Activate ONLY connectors with REAL interfaces, per-connector mini-gate,
honest BLOCKED/NOT_TESTED — never make the phase look green by
replacing provider evidence with a local simulation. The one genuinely
available local connector is the filesystem: a REAL connector bound to
a DEDICATED scratch root (created by discovery — the owner's files are
never in reach), real read/write/append/delete with real content-hash
after-states, canonicalization refusing every escape. Online kinds
(browser, email, calendar, messaging, cloud, git_remote) carry durable
BLOCKED activation records — no credentials/interface on this host,
resumable, zero fabricated connector instances. Provider-side
reconciliation is NOT_TESTED everywhere (R-EXT-01), recorded honestly.
Work package: `docs/planning/V5_S8_WORK_PACKAGE.yaml`.

## Design (files)

- `src/jarvis_foundry/connectors/activation.py` (new) — `FsConnector`
  (the real interface: bounded root, lock-serialized performs,
  canonicalization, content-hash after-states), `discover_local_connectors`
  (deterministic + idempotent, real `.health-probe` round-trip),
  `record_online_blockers` (6 kinds, durable + resumable),
  `activate_local_connectors` (discovery + gate + blockers + record),
  `mini_gate` (identity/declared semantics/gaps/capabilities/real
  probe/quota/NOT_TESTED; unknown ids → deterministic NOT_READY row),
  `activation_matrix` (discovered connectors × action classes + blocked
  kinds).
- `tests/foundry/test_connector_activation.py` — 10 tests + 2 repair
  regressions.

## Round-1 findings and repairs (deleg_19385768, PASS)

| # | Severity | Finding | Repair (a26eba0) |
|---|---|---|---|
| M1 | MEDIUM | FsConnector.perform not thread-safe — concurrent appends lost bytes (5/6 corrupted runs); unreachable from the single-writer S7 driver | per-connector `threading.Lock` serializes every perform; regression: 2×120 concurrent appends → exactly 240 lines |
| L1 | LOW | mini_gate on an unknown connector id raised ConnectorUnavailable | deterministic NOT_READY row (identity False, health UNKNOWN, provider NOT_TESTED) — never an exception; regression |
| I1 | INFO | different-root re-discovery → EventConflict (deterministic refusal to silently re-point the root) | recorded — same-root re-discovery idempotent |
| I2 | INFO | discovery on a deleted root re-creates it and re-probes honestly | recorded — honest health either way |
| I3 | INFO | activate without an explicit root → NOT_READY conservative (never READY without an in-gate probe) | recorded — the close passes the root explicitly |

A/B discrimination (var/evidence/v5_s8_ab_flip.py, baseline b6a9b9d):
both discriminators FAIL on the round-1 code, PASS on the repaired
tree.

## Tests (10 + 2 repair regressions)

real round-trips + sha256 after-states; traversal/symlink/absolute
refusals; discovery determinism + idempotency; honest deleted-root;
mini-gate matrix (READY vs DEGRADED); online blockers (no fabrication);
REAL end-to-end fs effect through the S7 pipeline; outside-path
refusal; secret-like REFUSED + zero ledger hits; thread-safety
regression; unknown-id gate regression. Suite: 708 (feat) → **710
EXIT=0** (repairs) — `var/evidence/v5-s8-activation/suite-v5-s8-r2.txt`.

## Evidence (close)

- Rehearsal: `var/evidence/v5-s8-activation/rehearsal-live-copy.json`
  (live v20 snapshot; consistent before+after full rebuild).
- Round-2 INFO findings (all recorded, none blocking): the lock is
  per-instance — two separate FsConnector instances on the same root
  could still race, unreachable in the supported topology (one
  dedicated root per connector, EventConflict on re-point, single-
  writer driver); cross-run byte ORDER varies under interleaving while
  the zero-loss + multiset guarantees are deterministic (byte order
  would need ordered queues, not a mutex); pytest 9's doubled `-q`
  suppresses the "N passed" summary line (artifact shape explained —
  the verifier's own full-suite run reproduced 710 dots EXIT=0).
- Live close: activates the REAL filesystem connector on the live
  ledger (dedicated root
  `C:\Users\b\AppData\Local\hermes\foundry\connector-root` — the
  slice's in-scope transition), records the 6 online blockers, appends
  `connector.activated` + `verification.passed:verdict-v5-s8-activation:round1`
  + `evidence.created:v5-s8-activation-close` (schema stays v20).
