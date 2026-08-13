# V5-S6 — External-Effect Execution, Idempotency, and Reconciliation

**Status: CLOSED — PASS (round-3)** (2026-08-13)
**Code commits:** `e28bc8c` (feat) + `a2af060` (round-1 repairs) +
`832ee95` (round-2 repairs)
**Round-1 verifier:** `deleg_2d03fb7f` — **FAIL** (3 MEDIUM + 1 LOW +
1 INFO; `verdict-deleg_2d03fb7f.json` sha256 `b9cd0c50baedc260…`).
**Round-2 verifier:** `deleg_fd714bbc` — **FAIL** (1 MEDIUM + 1 INFO;
`verdict-deleg_fd714bbc-r2.json` sha256 `62058cd48067687d…`).
**Round-3 verifier:** `deleg_875fbfc2` — **PASS**, 7 probe groups
(40+ probes, A/B discrimination vs a2af060 proven), at frozen HEAD
`832ee95`. Verdict archived
`var/evidence/v5-s6-effects/verdict-deleg_875fbfc2-r3.json` sha256
`3d851e2122110d21…`.
**Ledger close:** v20 applied live (rehearsed) +
`verification.passed:verdict-v5-s6-effects:round1` +
`evidence.created:v5-s6-effects-close`.
**Live schema note:** the close applies the rehearsed v20 migration
(rehearsal: `var/evidence/v5-s6-effects/rehearsal-live-copy.json` —
23,598 events preserved, consistent before+after rebuild).

## Slice

Make retries safe and prove actual state after an effect (§7 steps
10–15, R-IDM-01, R-EXT-01): the effect driver executes an ALLOWed
envelope through the selected connector EXACTLY ONCE under a key bound
to (system, canonical target, payload identity); the journal is
monotonic (STARTED → COMPLETED/FAILED/FROZEN, never regresses); a
crash between start and completion is reaped deterministically (never
a second execution); ambiguous effects freeze with an append-only
compensation path; provider reconciliation is honest (NOT_TESTED
without a real provider); financial consequences are recorded.
Work package: `docs/planning/V5_S6_WORK_PACKAGE.yaml`.

## Design (files)

- `src/jarvis_foundry/effects.py` (new) — `effect_identity` (key bound
  to system + canonical target + payload identity incl. expected
  effect), `execute_effect` (ticket → fresh STOP/veto → identity →
  crash-window reap → terminal refusal → start journal → perform →
  completion/failure; failure reasons REDACTED; never self-tickets),
  `effect_state` (event-derived, journal-row truth, rebuild-only
  window covered), `freeze_effect`, `compensate_effect`,
  `reconcile_effect` (match → confirmed; mismatch → FROZEN),
  `EffectStateError`.
- `src/jarvis_foundry/control/broker.py` (modified) —
  `veto_identities_for` (the FULL veto identity set — GLOBAL, mission,
  capability, expected effect, resolved targets, F1 bare ids — shared
  by decide() and every executor-side re-check),
  `require_allowance_for_envelope` (envelope-scoped ticket check:
  mission/kind/resolved targets/capabilities/idempotency/budget must
  match the decided envelope).
- `src/jarvis_foundry/migrations.py` — **v20**: system_actions rebuilt
  with FROZEN in the CHECK (rows preserved).
- `src/jarvis_foundry/projections.py` — monotonic guards:
  completed/failed only from STARTED; effect.frozen STARTED-only;
  effect.reconcile_mismatch COMPLETED/FAILED → FROZEN.

## Round-1 findings and repairs (deleg_2d03fb7f, FAIL)

| # | Severity | Finding | Repair (a2af060) |
|---|---|---|---|
| M1 | MEDIUM | Execute-time veto re-check ignored GLOBAL / mission / resolved 'system:<id>' spellings — the effect executed under a veto the broker itself would honor | `veto_identities_for` extracted and shared; the driver re-checks with the FULL identity set (regressions: resolved, GLOBAL, mission + bare-id + capability probes) |
| M2 | MEDIUM | STARTED event present + journal row missing (rebuild-only window, §10.4) → re-perform (R-IDM-01 violation) | `effect_state` derives from EVENTS when the row is missing (STARTED → crash-reap FROZEN; COMPLETED/FAILED → idempotent terminal) — a crash can never look fresh |
| M3 | MEDIUM | Ticket bound to action_id only — a foreign envelope (other mission, other payload) executed under an existing ticket | `require_allowance_for_envelope`: mission/kind/resolved targets/capabilities/idempotency/budget must match the DECIDED envelope; resolved-vs-raw target comparison |
| L1 | LOW | Changed reconcile claim / compensation ref under one key → EventConflict crash | full-payload idempotency keys for frozen/compensated/reconciled/mismatch events — changed observations record append-only history; identical re-delivery still dedupes |
| I1 | INFO | Handoff count 23,588 vs authoritative rehearsal 23,598 | corrected in the close script + this doc |

## Round-2 findings and repairs (deleg_fd714bbc, FAIL)

| # | Severity | Finding | Repair (832ee95) |
|---|---|---|---|
| M3′ | MEDIUM | M3 repair incomplete: the ticket scope still never verified the effect PAYLOAD identity (expected_effect / expected_effects / before_state_capture) — exactly what the action key hashes; a foreign payload executed under an existing ticket | `record_decision` now records the three payload fields; `require_allowance_for_envelope` compares them (regressions: expected_effect / expected_effects / before_state_capture variants) |
| I2 | INFO | Event-derived terminal precedence (completed-before-failed) could disagree with the projection when BOTH terminal events exist + row missing | derivation now mirrors the projection: the FIRST terminal event in sequence order wins; reconcile_mismatch is the strongest (any terminal → FROZEN); regression proves derived == projected after rebuild in both orders |

A/B discrimination: round-1 set proven against `e28bc8c`; the
round-2 set (`v5_s6_ab_flip.py a2af060`) proven before round-3
dispatch.

## Tests (17 + 6 repair regressions — tests/foundry/test_effects.py)

execution + before/after state; idempotent retry (performed=False);
conflicting payload → distinct keys; no-ticket PermissionError;
budget-exceeded no ticket; crash-reap once (FROZEN, never re-perform);
terminal never regresses (late events + direct effect.frozen guard);
failure/timeout redacted; partial success honest (no fabricated
after-state); freeze/compensation append-only; reconcile
match/mismatch/terminal-only; v20 CHECK + rebuild; rebuild
determinism; fresh-veto matrix (resolved/GLOBAL/mission); missing-row
crash-reap (+ projection catch-up); cross-mission ticket refusal;
changed observations record history. Suite: 667 (feat) → **673 EXIT=0**
(repairs) — `var/evidence/v5-s6-effects/suite-v5-s6-r2.txt`.

## Evidence (close)

- Rehearsal: `var/evidence/v5-s6-effects/rehearsal-live-copy.json`
  (live snapshot v19 → v20 migrated copy; **23,598 events preserved**;
  consistent before+after full rebuild).
- Round-3 suite artifact: `var/evidence/v5-s6-effects/suite-v5-s6-r3.txt`
  (677 passed EXIT=0 — verifier re-ran the focused files itself).
- A/B discrimination: `v5_s6_ab_flip.py` (baseline e28bc8c for the
  round-1 set; baseline a2af060 for the round-2 set — both proven by
  the verifiers).
- Live close: v20 applied (rehearsed transition), two events appended;
  daemon 5 s heartbeats, STOP false.
- Round-3 verifier INFO findings (recorded, no action): (1) the
  redactor is assignment-based by documented contract — a bare
  secret-like token in PROSE inside a failure message is stored as-is
  (consistent with §8.2 and the S1 guard family); (2) old-shape ALLOW
  tickets recorded before the payload fields deterministically refuse
  non-empty-payload envelopes (only an all-empty-payload envelope
  passes — ticket scope widens to nothing, never to something else).

## Known honest limitations

- Verifier family shared (owner constraint); independence via separate
  sessions, frozen artifacts, 40+ adversarial checks + A/B flips.
- Provider-side reconciliation is NOT_TESTED: no real provider exists
  on this host; `reconcile_effect` is proven against fake provider
  observations (R-EXT-01 — no fabricated provider evidence).
- The redactor's assignment-based scope (INFO above) is the documented
  contract; S9's secret-leak scan uses the same deterministic pattern.
- The driver is not wired into live missions until S7.
