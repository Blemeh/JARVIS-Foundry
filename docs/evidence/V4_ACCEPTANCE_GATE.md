# V4 ACCEPTANCE GATE — CONTINUOUS AUTONOMY (first gate review)

Status: **CLOSED — PASS (3/3, round-1)** (2026-08-13 ~02:20).
Independent adversarial gate verifier `deleg_a241797f` **PASS** at
frozen src HEAD `19fbb17` (docs `d15adae`; tree clean start→end).
Verifier findings: 2 MEDIUM (stale live-state snapshot totals — the
semantic claims hold; corrected above), 4 LOW/INFO (label + artifact
reference + doc-hash notes — preserved additively, see below). Verdict
JSON archived at `var/evidence/v4-acceptance-gate/verdict-deleg_
a241797f.json`; ledger `verification.passed:verdict-v4-acceptance-
gate:round1` + `evidence.created:v4-acceptance-gate-close`.

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

## Live system state (2026-08-13 ~02:10 — gate-verifier RE-MEASURED
## 02:20; the verifier's numbers supersede the draft's snapshot)

- Schema **v18** (v17 + v18 migrations applied live after copy
  rehearsals); events 15,983 at the verifier's read; claims **999**
  (998 supported / 1 unsupported — the S1 semantic claim "894 → 1
  unsupported" holds exactly; the draft's 954/953 was the S1-close
  snapshot and is superseded); candidates **678 NEW / 232 SCORED /
  3 EXECUTED / 2 SUPERSEDED / 19 REJECTED** (the REJECTED rows are
  duplicate records — 18 pre-existing from the old projection + 1 from
  the 01:59 loop — never selectable by design; the draft's 696/233 was
  the V3-gate snapshot and is superseded).
- **Reconcile consistent, 0 mismatches** (verifier re-ran it
  read-only: event_count 15,983, integrity_failures 0, version_mismatch
  none; atomic rebuilds converged 780 + 45 drift rows across the
  S1/S5 closes — derived-state corrections, events never rewritten).
- Monitors registry: 4 real sources registered + polled (2 live loop
  cycles; 8 discovery.monitor_polled events).
- Supervisor RUNNING, 5s heartbeats, 1 lease, STOP false, no veto.
- Daemon survived both rebuild windows (≤2 missed beats each).

## Gate-verifier findings — additive corrections (deleg_a241797f, PASS)

1. MEDIUM: the draft's live-state totals were the S1/V3 snapshots, not
   a 02:10 re-measure — corrected above with the verifier's numbers.
2. LOW: the S5 round-2 close event carries the label
   `verdict-v4-s5-monitors:round1` (close script's key) while docs say
   round2 — the verifier identity (deleg_9decc685), PASS status and
   the verdict-JSON hash are correct; the historical event is
   preserved as-is (append-only; label discrepancy recorded here and
   in DECISION_LOG D-037).
3. LOW: the S5 R2 evidence.created references the pre-fix suite
   artifact (suite-v4-s5.txt, 468) rather than suite-v4-s5-r1.txt
   (485) — historical event preserved; the 485 EXIT=0 is corroborated
   by both suite-v4-s5-r1.txt and suite-v4-s6.txt.
4. INFO: "388 session start" is un-evidenced in-repo; the artifact
   progression is 414→429→438→441→462→468→485 (all EXIT=0).
5. INFO: honest-WAITING loops ≥ 8 (the WP12 "6" was a conservative
   undercount, documented by the V3 verifier).
6. INFO: ledger doc artifact hashes for MR-1/S1/S6 no longer match the
   on-disk docs (R8 edits after close — expected); all verdict/rescan/
   backup hashes DO match the ledger (verifier evidence integrity
   holds).

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
