# V5-S2 — ActionEnvelope and Deterministic Policy Broker

**Status: CLOSED — PASS (round-1)** (2026-08-13)
**Code commit:** `28156f0` (feat(v5-s2))
**Independent verifier:** `deleg_6c96be6d` — **PASS**, 10 probe groups,
at frozen HEAD `28156f0` (src/tests clean start→end; docs-only files
added mid-run by the executor are unrelated to the certified tree).
**Verdict archive:**
`var/evidence/v5-s2-broker/verdict-deleg_6c96be6d.json` sha256
`ac7fee214b16ad71a71a85449feb7177cfe77ddf0344306c66ec6b9b99753e15`
**Ledger close:** `verification.passed:verdict-v5-s2-broker:round1`
(seq 22385) + `evidence.created:v5-s2-broker-close` (seq 22386);
live schema unchanged (v19 — no S2 migration).

## Slice

Every material action passes deterministic control before execution:
the architecture contract §7 ActionEnvelope fields and the exact
15-step processing order, with a broker returning exactly one of
`ALLOW` / `VETOED` / `NOT_READY` / `MISSING_RECOVERY_PATH` /
`BUDGET_EXCEEDED` / `EMERGENCY_STOPPED` / `INVALID_TARGET`. An LLM may
explain a decision but may never override it. Also resolves the S1
verifier LOW finding P9(b): action-kind canonicalization so
case/whitespace/separator variants cannot bypass a VETO_ACTION.
Work package: `docs/planning/V5_S2_WORK_PACKAGE.yaml`.

## Design (files)

- `src/jarvis_foundry/domain.py` — `ActionEnvelope` extended with the
  contract §7 fields (step_id, requested_by_agent_run_id,
  target_resources, capabilities_required, capability_states_required,
  goal_alignment, active_owner_vetoes_checked, expected_effect,
  reversibility, before_state_capture, verification_plan,
  rollback_plan, idempotency_key, budget_estimate,
  credential_requirements, created_at); `target` now optional. All new
  fields have defaults — V4 callers (ActionPolicy, mission loop)
  construct unchanged. New `BrokerDecision` enum with EXACTLY the 7
  codes. The V4 `ActionDecision`/`PolicyVerdict`/`ActionPolicy` are
  untouched.
- `src/jarvis_foundry/control/broker.py` (new) —
  `normalize_action_kind` (canonical: lowercase, no whitespace,
  '/'/':' → '.', repeated dots collapsed), `resolve_canonical_target`
  (rejects env vars, wildcards, drive roots, relative paths; resolves
  symlinks deterministically), `PolicyBroker.decide` (the 15-step
  order, fresh durable state per call — never cached),
  `record_decision` (terminal `action.decided` event, full-payload
  idempotency keys — identical re-delivery dedupes, changed envelope
  records a new audit event), `require_allowance` (executor ticket:
  the LATEST decision for the action must be ALLOW; no decision or a
  non-ALLOW decision refuses execution — a STOP raised while work is
  queued revokes the ticket).

Decision records are raw `action.decided` events (append-only audit
truth, like verification.passed); NO new projection and NO migration —
live schema stays v19.

## Contract checks implemented (in exact order)

1. emergency STOP (marker / STOP_EVERYTHING / FROZEN+SUSPENDED) →
   EMERGENCY_STOPPED;
2. target resolution + action-kind normalization → INVALID_TARGET on
   fuzzy/unresolvable targets;
3. registration/reconciliation (systems registry; file targets need a
   registered resource root) → INVALID_TARGET;
4. goals, vetoes (canonical kind + action/mission/resource/capability/
   target identities), capability readiness → VETOED / NOT_READY;
5. secret/credential requirements (honest NOT_READY — no fabricated
   credentials);
6. resource/monetary budgets (fail closed: estimate with no policy) →
   BUDGET_EXCEEDED / NOT_READY;
7. executable recovery (irreversible / C2 / C3 need rollback_plan
   method) → MISSING_RECOVERY_PATH;
8. before-state capture (mutating actions must declare ref/hash) →
   NOT_READY;
9. capability issuance (S3 wires issuance; planned issuance recorded);
10. idempotency key (one execution per effect) → NOT_READY;
11–13. after-state (executor), deterministic verification
    (verification_plan required), independent verification (C3) →
    NOT_READY;
14. commit/retain/freeze/rollback (validated in step 7);
15. terminal `action.decided` event via record_decision.

## Tests (54 new — tests/foundry/test_action_broker.py; suite 507 → 561)

normalization matrix (8 variants → one canonical form; rejects
empty/None); ALLOW path; malformed-envelope NOT_READY matrix; STOP
matrix + precedence (STOP outranks everything) + race (never cached);
target matrix (unknown system, 7 fuzzy/root forms, root escape,
unregistered root); veto matrix (6 spelling variants ALL blocked,
scope, GLOBAL, STOP_MISSION); capability readiness (NOT_TESTED /
UNREADY / unregistered); goal alignment; credential requirements
(NOT_READY → READY when a credentials system exists); budgets (exceed,
no-policy fail-closed, unknown key, precedence over recovery);
recovery (irreversible/C2/C3 missing method; with method ALLOW);
before-state; idempotency key; verification plan; C3 independent;
duplicate envelope (1 event dedup; changed → new event); worker bypass
(no decision / non-ALLOW / ALLOW / ticket revoked by later STOP);
rebuild determinism.

## Evidence

- Full suite: **561 passed EXIT=0** — artifact
  `var/evidence/v5-s2-broker/suite-v5-s2.txt` sha256
  `d702e58e3a40a7b805dd7265fbb2a63f48ca6771f1571946dc905afaa7c38fc3`
  (focused 54/54 re-run by the verifier itself).
- Rehearsal: `var/evidence/v5-s2-broker/rehearsal-live-copy.json` sha256
  `ec2e32cd24b1f529a85152266c3feeb9625d127415754d8e9d663ff9d03e5db1`
  — read-only live snapshot (v19, 22,255 events at first run) →
  reconcile **consistent** before AND after a full projection rebuild
  (22,255 events replayed); daemon uninterrupted.
- Live close: events 22,384 → 22,386; schema stays v19; daemon 5 s
  heartbeats, STOP false.

## Verifier findings and carry-forward (deleg_6c96be6d, PASS)

| # | Severity | Finding | Resolution |
|---|---|---|---|
| F1 | LOW | VETO_ACTION on a bare system id (`sys-browser`) does not match the resolved identity (`system:sys-browser`) — the prefixed canonical form is required. Contract §7 does not define veto-target grammar; V4 had the same limitation (not a regression) | **Carried to S3**: veto-target grammar — add the bare system id to the broker's veto identity set, with a regression test for both spellings |
| F2 | LOW | `budget_estimate` value `float('nan')` passes the budget gate (`nan > limit` is False) → ALLOW. Requires a fabricated NaN (JSON cannot natively produce one); real budget enforcement is executor-side | **Carried to S3**: non-finite budget-estimate guard (deterministic `math.isfinite` check → NOT_READY) with regression test |
| F3 | LOW | Declaration-based gates are spoofable by a dishonest envelope (`verification_required=False` + empty plan → ALLOW; a mutating-looking kind declaring no effects/before-state → ALLOW). The broker validates what the envelope declares — the same trust model as goal_alignment; the suite pins the declared-effects path | **Recorded limitation**: honest executors declare effects; a dishonest envelope is the trust-boundary class (same as S1 P9(a)). S4's connector contract declares per-kind action semantics — the structural fix lands there |
| F4 | INFO | `require_allowance` trusts `action.decided` rows; a party with direct DB append access could forge a ticket | Outside the worker threat model (contract §3.1 keeps DB access in the Supervisor); recorded |
| F5 | INFO | Docs changed mid-verification (executor's RECOVERY_NOTES refresh + S2/S3 drafts) | Docs only; src/tests stayed clean at the frozen HEAD — confirmed by the verifier |

## Known honest limitations

- Verifier shares the DeepSeek provider family (owner constraint) —
  independence via separate sessions, frozen artifacts, 10 adversarial
  probe groups.
- The broker is NOT wired into live action selection (V5-S7); the live
  ledger carries the close events only.
- `record_decision`/`require_allowance` are the S2 executor contract;
  real effect execution and before/after-state capture are S6.
