# V4 ACCEPTANCE GATE — CONTINUOUS AUTONOMY (first gate review)

Status: **IN REVIEW — round-1 verifier dispatched** (2026-08-13).
Engineering + operational evidence below; frozen HEAD for the review:
`5b22066` (docs) / src `19fbb17` — the verifier evaluates the src/tests
tree byte-identical to 19fbb17.

## Gate criteria (CURRENT_WORK_PACKAGE.yaml)

| # | Criterion | Evidence | Verdict |
|---|---|---|---|
| 1 | The support-semantics repair mission (FIRST MISSION) PASSES with independent verification | V4-S1: 982f472/f2e3ea3, verifier deleg_12682a02 **PASS** (17 probes); live re-scan 953/954 supported (was 894 unsupported); 2 false-premise candidates superseded additively | PASS |
| 2 | Continuous loop cadence evidenced on the ledger (observe→select→execute→verify→learn) without owner prompting | V3: 3 complete real missions + 6 honest-WAITING loops (WP12 gate, deleg_894de0e1 PASS). V4: live loop 2026-08-13T01:59 (real observe/register/poll/score, honest WAITING — no bar-clearing candidate exists; no invented work); second cadence loop running | PASS (cadence) |
| 3 | V4 acceptance review gate PASS | this review | IN REVIEW |

## Reliability slices closed this phase (each: TDD + full suite + frozen
## HEAD + independent adversarial verifier PASS + ledger close)

| Slice | Directive item | Code commit | Verifier (probes) | Ledger | Live effect |
|---|---|---|---|---|---|
| MR-1 model routing | routing §7 | bab5830/2939415 | deleg_debc2612 PASS (10) | 14050–51 | every role → opencode_go/deepseek-v4-flash, no fallbacks; enforcement before any mutation |
| S2 verifier truth | item 1 | e456c22/19ea656 | deleg_81eea974 PASS (10) | 14270–71 | exit-0 never PASS → NOT_TESTED; artifact-hash-bound evidence |
| S3 lease + monotonic | item 2 | 7e02091/2db5bdd | deleg_18d9f7b0 PASS (9) | 14475–76 | heartbeats renew leases; terminal states monotonic |
| S4 exactly-once | item 3 | f1dd903/1eeede5 | deleg_a3e54630 PASS (10) | 14702–03 | resume never re-runs builder; fresh verifier id; dangling verdicts corrected additively |
| S1 support semantics (FIRST MISSION) | items 4–5 | 982f472/f2e3ea3 | deleg_12682a02 PASS (17) | 14950–53 | kind-aware support; run_id normalization; v17 (SUPERSEDED); live rebuild 14,945 events; 953/954 |
| S5 monitors | item 6 | fea9167 → 19fbb17 | **R1 FAIL** deleg_71efdc9e (15) → **R2 PASS** deleg_9decc685 (16) | 15472–74 | registry registered+polled live; duplicates REJECTED; v18 UNIQUE; live rebuild 15,490 events |
| S6 scoring | item 7 | b2b50e2/19fbb17 | deleg_66e30870 PASS (9) | 15699–701 | bounded context factors; frozen recorded scores; threshold 0.35 unchanged |

Suite: 388 (session start) → **485 passed EXIT=0** (+97 tests, all
red-green verified). Two suite artifacts flaked once under
two-overlapping-pytest contention (supervisor-recovery timing tests;
pass in isolation; regenerated artifacts EXIT=0 — documented).

## Live system state (2026-08-13 ~02:10, verified read-only)

- Schema **v18** (v17 + v18 migrations applied live after copy
  rehearsals); events 15,701+; claims 954 (953 supported); candidates
  696 NEW / 233 SCORED / 3 EXECUTED / 2 SUPERSEDED (+2 this phase:
  1 vault-poll candidate, 1 duplicate REJECTED record).
- **Reconcile consistent, 0 mismatches** (atomic rebuilds converged 780
  + 45 drift rows across the S1/S5 closes — derived-state corrections,
  events never rewritten).
- Monitors registry: 4 real sources registered + polled (first live
  loop 01:59).
- Supervisor RUNNING, 5s heartbeats, 1 lease, STOP false, no veto.
- Daemon survived both rebuild windows (≤2 missed beats each).

## Doc reconciliation (R8)

PROJECT_STATE v22; CURRENT_WORK_PACKAGE (FIRST MISSION DONE +
slices status); DECISION_LOG D-031..D-036; RISK_REGISTER RK-26..RK-29;
RECOVERY_NOTES current; evidence docs V4_MR1/V4_S2..S6/V4_M3 all
CLOSED/current with verifier lineage. Historical statements remain
historical (v21 state preserved below the v22 header).

## Known honest limitations (for the record)

- All verifiers share the DeepSeek-only provider family (owner
  constraint) — independence via separate sessions, frozen artifacts,
  deterministic adversarial probes; provider diversity unavailable by
  directive.
- The first REAL post-fix worker dispatch (deepseek-v4-flash on an
  agent_run row) is PENDING — no bar-clearing candidate exists; the
  routing enforcement makes it structurally impossible to dispatch
  otherwise (see V4_M3_REAL_LOOP.md).
- Supervisor-recovery tests are timing-sensitive under pytest
  contention (2 documented flakes, both pass in isolation).
- Freshness age is ledger-relative (latest recorded_at), spec-
  compliant for the live loop.
