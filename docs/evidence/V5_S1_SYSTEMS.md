# V5-S1 — System Registry (Multi-System Intelligence, slice 1)

**Commit:** `e6ac89f` (feat(v5-s1): System Registry)
**Date:** 2026-08-13
**Status:** IMPLEMENTED — independent verifier <PENDING at e6ac89f>

## Slice

The durable catalog + policy gate + external-effect journal that every
later V5 adapter builds on (roadmap §8: "V4 PASS queues V5
automatically" — activated by the V4 acceptance gate close). No live
external connections in this slice; readiness is honest
READY/UNREADY/NOT_TESTED, never fabricated. Work package:
docs/planning/V5_WORK_PACKAGE.yaml.

## Exit evidence (roadmap §8 → code + tests)

1. **Revocation and STOP immediately block new actions** —
   `systems_allowed_by_policy` (stop marker, active STOP_EVERYTHING /
   VETO_ACTION directives, FROZEN/SUSPENDED supervisor state,
   frozen-without-reconciled).
2. **Retries do not duplicate external effects** —
   `external_action_key` binds (system, target, payload-hash); the
   `system_actions` journal row is UNIQUE on the triple; phase-distinct
   event keys (started/completed never collide); identical re-delivery
   dedupes at the event AND projection level.
3. **Secrets never appear** — secret-like capability/probe payloads are
   refused at the API boundary (deterministic pattern guard) + a
   ledger-scan test asserts no secret-like payloads exist.

## Tests (20 new — tests/foundry/test_system_registry.py)

catalog + idempotent registration; honest readiness transitions
(NOT_TESTED→READY→UNREADY→READY); unknown-system probes fail
atomically; policy gate matrix (marker/directive/veto/frozen/
reconciled, plain-STOPPED not blocking); effect keys + dedup +
phase-distinct events + changed-payload-is-a-new-effect; secret
refusal matrix + no-false-positives + ledger scan; migration v19
tables/constraints; reconcile coverage.

## Evidence

- Full suite: 505 passed EXIT=0 (was 485) — artifact regenerated at
  `var/evidence/v5-s1-systems/suite-v5-s1.txt`.
- Next: independent adversarial verifier (freeze discipline), ledger
  close after PASS (verification.passed:verdict-v5-s1-systems:round1).
