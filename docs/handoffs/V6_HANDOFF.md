# V6 Handoff — after V5 Final Acceptance

**Status: READY (drafted; finalizes on the V5 gate PASS)**
**Frozen baseline:** commit `e2d20a6` (or the gate-close commit) —
branch `main`, schema v20.

## What V5 delivered (the substrate V6 builds on)

- **Deterministic control plane**: ActionEnvelope (contract §7) +
  7-code broker with a 15-step decision order, scope-checked ALLOW
  tickets (record_decision), emergency STOP, veto families
  (GLOBAL/action/mission/kind/resolved-target/capability/effect).
- **Scoped capability + credential broker**: short-lived capabilities
  bound to mission/run/kind/target, one-shot consumption, redaction
  (assignment-form secret pattern), fail-closed budget gate.
- **Connector substrate**: contract validation (S2-F3 semantics gaps),
  registry with health/quota/rerouting, deterministic fakes (test-only)
  and ONE real connector (conn-fs, dedicated root, lock-serialized).
- **Correlation layer**: cross-system entities (confidence matching,
  ambiguity preserved, no destructive merge) + bounded deterministic
  worker manifests.
- **Effect journal**: monotonic STARTED→COMPLETED/FAILED/FROZEN,
  idempotent re-delivery, crash-reap, freeze/compensation, event-based
  derivation (rebuild-only windows).
- **Mission-loop pipeline**: observe→select (frozen scores)→envelope→
  authorize→execute→reconcile→verify→learn, outage isolation, STOP
  semantics, idempotent-first re-delivery, transparent JARVIS identity,
  secret-bearing candidates refused at the ingestion boundary.
- **Activation**: real fs connector LIVE READY; online kinds durable
  BLOCKED (resumable); provider reconciliation NOT_TESTED.

## What V6 may build on (with re-verification)

- Autonomous operation loops that CALL `run_mission` with candidates —
  but every NEW connector activation requires the S8 mini-gate ritual:
  real interface evidence, real probes, per-connector gate, honest
  status. No online connector may be activated with invented
  credentials — a credential arriving in the future unblocks the
  durable BLOCKED records via the same gate.
- Provider-side reconciliation: implement a REAL provider evidence
  adapter (the `reconcile_effect` interface exists and is tested with
  fake provider states) — until then NOT_TESTED is the honest status.
- V6 self-evolution begins ONLY after this handoff is delivered AND a
  separate explicit V5 final PASS handoff is acknowledged (scope
  boundary from the V5 master prompt).

## Re-verification obligations (per the proven ritual)

- Any V6 change touching a closed slice's contract re-runs that
  slice's discriminating tests + an independent adversarial verifier
  round before claiming the change.
- V4 modules (`control/policy.py`, `control/capabilities.py`,
  `control/action_envelope.py`, knowledge_entities + projection) stay
  untouched-by-V5 — verified byte-identical at every gate.
- The live DB: schema v20; append-only; `initialize()` applies pending
  migrations — read-only inspection MUST use mode=ro URIs.

## Honest limitations carried into V6

1. Provider reconciliation NOT_TESTED (no real provider on this host).
2. Online connectors BLOCKED/resumable (no credentials).
3. FsConnector per-instance lock; byte-order nondeterminism under
   cross-instance interleaving (zero-loss guaranteed; single-writer
   topology).
4. Secret-pattern family: composite labels shared scope with S1/S3.
5. D-037: the S3 close's `:round1` label on a round-2 close — the
   payload's `verifier_run_id` is the true identity; history is never
   rewritten.

## Evidence inventory (var/evidence/, gitignored)

per-slice: verdict JSONs (FAIL + PASS rounds), suite artifacts, live
rehearsals, A/B flip scripts, close scripts; `docs/evidence/`:
V5_S0_ENTRY, V5_S1_SYSTEMS, V5_S2_BROKER, V5_S3_CAPABILITIES,
V5_S4_CONNECTORS, V5_S5_CORRELATION, V5_S6_EFFECTS, V5_S7_LOOP,
V5_S8_ACTIVATION, V5_ACCEPTANCE; gate scans: secret-scan-repo /
secret-scan-ledger.
