# V5-S7 — Mission-Loop Integration and Autonomous Operation

**Status: CLOSED — PASS (round-4)** (2026-08-13)
**Code commits:** `0762d76` (feat) + `2eb3a81` + `d07327e` + `d4ec8be`
(repair rounds 1-3)
**Verifiers:** round-1 `deleg_0b2cbd47` FAIL (`verdict-deleg_0b2cbd47.json`
sha256 `d57b9631…`); round-2 `deleg_8b1eef76` FAIL
(`verdict-deleg_8b1eef76-r2.json` sha256 `8ec62410…`); round-3
`deleg_204ef719` FAIL (`verdict-deleg_204ef719-r3.json` sha256
`8a03efc9…`); **round-4 `deleg_1bbacf1e` — PASS** (7 probe groups,
~60+ assertions; 4 INFO, none blocking;
`verdict-deleg_1bbacf1e-r4.json`).

## Slice

Wire the gate-passed V5 substrate into JARVIS's autonomous mission
loop without bypassing deterministic control: observe (correlated
manifest + connector state) → select (frozen scores, eligibility) →
envelope → authorize (broker ALLOW ticket + S3 capability) → execute
(S6 driver: one execution under the effect key, fresh STOP/veto, crash
reap) → reconcile (honest NOT_TESTED without a provider) → verify →
learn (outcome events with the transparent JARVIS identity). Connector
outages isolate; STOP blocks queued/in-flight work; the journal is the
resume point; no duplicate mission/effect; no external or financial
action without the full READY path.
Work package: `docs/planning/V5_S7_WORK_PACKAGE.yaml`.

## Design (files)

- `src/jarvis_foundry/execution/pipeline.py` (new) — `IDENTITY`
  (representational: name/role/acting_for), `ActionCandidate`,
  `build_envelope` (broker-contract fields + the mission-loop
  ingestion boundary guard), `observe`, `select_candidate` (highest
  frozen score, tie → lowest action id, never re-scored),
  `authorize` (decide + record_decision + idempotent S3 issuance),
  `execute` (ticket/STOP/veto gates BEFORE capability consumption,
  then the S6 driver; idempotent re-delivery returns recorded
  outcomes), `reconcile` (NOT_TESTED without a provider),
  `verify` (journal terminal + after-state), `learn` (outcome events
  with identity + full-payload keys), `run_candidate` (isolated per
  candidate), `run_mission` (deterministic order, STOP semantics,
  idempotent-first re-delivery with recorded details).
- `tests/foundry/test_pipeline.py` — 11 tests + 4 repair regressions.

## Round-1 findings and repairs (deleg_0b2cbd47, FAIL)

| # | Severity | Finding | Repair (2eb3a81) |
|---|---|---|---|
| M1 | MEDIUM | Idempotent re-delivery appended NEW mission.outcome.recorded events with consequence=None (the terminal path dropped after_state → learn()'s payload-hash key no longer matched → event drift after rebuild) | terminal re-delivery results now carry the RECORDED details (after_state/cost/reasons) so learn() dedupes; regression proves 1 outcome event across runs + rebuild with the real consequence |
| M2 | MEDIUM | Secret-like free-text candidate fields (goal_alignment, expected_effect(s), before_state_capture, …) landed VERBATIM in action.decided / system.action_* payloads — no boundary guard at the new mission-loop ingestion point | `build_envelope` refuses secret-bearing candidates (S1-style `_reject_secrets` guard, all 7 free-text fields); run_mission/run_candidate REFUSE the candidate in isolation (never a crash, other candidates unaffected); regression proves zero ledger events carry the secret |
| L1 | LOW | Result-dict asymmetry: fresh executions omitted performed/cost | uniform shape — fresh executions carry performed=True + cost; re-deliveries performed=False with the same consequence/cost shape |
| I1 | INFO | `execute()` consumed the capability BEFORE the S6 ticket check — a foreign envelope could burn a legitimate capability | ticket/STOP/veto gates (the driver's own shared helpers) now run BEFORE `use_capability`; regression proves capability.used stays 0 for refused attempts |
| I2 | INFO | Duplicate action_ids in one candidate list → deterministic (first COMPLETED, second REFUSED one-shot) | recorded — mission loops must mint unique action ids |

## Round-2 findings and repairs (deleg_8b1eef76, FAIL)

| # | Severity | Finding | Repair (d07327e) |
|---|---|---|---|
| M1′ | MEDIUM | FAILED-effect outcome drift: the LOW repair's `setdefault("cost", budget_estimate)` made fresh FAILED outcomes record cost={} while re-deliveries record cost=None → a second outcome event | cost/after_state now derive from the JOURNAL for every path (one source of truth); fresh FAILED == re-delivery == None; COMPLETED == {} on both; regressions for COMPLETED/FAILED/FROZEN |
| M2′ | MEDIUM | run_mission crashed with an uncaught ValueError on unregistered-system candidates (effect_identity raised before the eligibility gate) — violated per-candidate isolation | the eligibility gate runs BEFORE identity resolution; effect_identity is wrapped (REFUSED in isolation); regressions: unregistered system + relative-path targets never crash the mission |
| L1′ | LOW | The crash-reap result still lacked performed/cost (early-return path skipped the normalization) | the reap branch normalizes explicitly (performed=False, cost=None); FROZEN re-delivery matches — one outcome event |
| I1′ | INFO | The I1 regression didn't discriminate (the S3 scope check already refused the old shape on baseline) | reworked to the true discriminating shape (same S3 binding, no ticket) — proves the ticket gate fires before capability consumption |
| I2′ | INFO | budget_estimate keys (copied into action.decided/action_completed cost) were outside the M2 guard | budget_estimate keys joined into the boundary scan (secret-like labels refused; numeric keys pass) |

## Round-3 findings and repairs (deleg_204ef719, FAIL)

| # | Severity | Finding | Repair (d4ec8be) |
|---|---|---|---|
| M-A | MEDIUM | budget_estimate secret-like KEY LABELS still reached the ledger (dict-join yielded bare 'api_key' which the assignment pattern misses) | budget keys probed as `key=x` form (the S3 redact_payload pattern) — secret-like labels refused at build_envelope; regression + zero-ledger-hits proof |
| M-B | MEDIUM | NEW regression: eligibility before the terminal check broke idempotent re-delivery under a degraded connector (COMPLETED → NOT_READY + second outcome event) | order restored: identity (wrapped) → terminal-check FIRST → STOP → eligibility; regression: degraded-connector re-delivery returns the recorded outcome, exactly one event; fresh work still NOT_READY |
| M-C | MEDIUM | non-numeric budget VALUE + matching numeric policy crashed the WHOLE mission (uncaught TypeError in math.isfinite) | broker `_budget_gap` fails closed on non-numeric/non-finite estimates (NOT_READY, deterministic — F2 class); run_candidate's authorize catch broadened to Exception (per-candidate isolation); regressions at both levels |

A/B discrimination: `v5_s7_ab_flip.py` — round-1 set vs `0762d76`,
round-2 set vs `2eb3a81`, round-3 set vs `d07327e` (all proven by
the verifiers).

## Tests (11 + 4 repair regressions — tests/foundry/test_pipeline.py)

end-to-end chain + event counts; per-step composition; no-duplicate
mission/effect; outage isolation; STOP queued + completed-under-STOP;
interruption/resume reap; selection determinism (ties, frozen scores,
ineligible candidates); identity transparency + non-spoofability;
outcome-drift regression; secret-boundary regression; uniform shape;
capability-not-burned regression. Suite: 688 (feat) → **692 EXIT=0**
(repairs) — `var/evidence/v5-s7-loop/suite-v5-s7-r2.txt`.

## Evidence (close)

- Rehearsal: `var/evidence/v5-s7-loop/rehearsal-live-copy.json`
  (live v20 snapshot; consistent before+after full rebuild).
- Round-4 INFO findings (all recorded, none blocking): composite
  secret-like labels ('my_secret_key') are pattern-family scope shared
  with the S1/S3 boundaries (pre-existing design scope; all round-3
  acceptance labels refused); unregistered-system candidates relabeled
  REFUSED (isolated — acceptance holds); 'sk-live-9999' is the
  canonical fake test placeholder (same convention as 'sk-live-1234');
  the live DB's 74 V4-era verification.* events + 6 v5-slice-close
  evidence.created (max seq 24138 = V5-S6 close) are unrelated prior
  history.
- Live close: `verification.passed:verdict-v5-s7-loop:round1` +
  `evidence.created:v5-s7-loop-close` (schema stays v20 — no
  migration in S7).
