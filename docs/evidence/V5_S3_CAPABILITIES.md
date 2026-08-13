# V5-S3 — Scoped Capability and Credential Broker

**Status: CLOSED — PASS (round-2)** (2026-08-13)
**Code commits:** `d7753b9` (feat(v5-s3)) + `02da6af` (fix(v5-s3):
round-1 findings)
**Round-1 verifier:** `deleg_deff8b38` — **FAIL** (2 MEDIUM + 2 LOW +
3 INFO; verdict archived
`var/evidence/v5-s3-capabilities/verdict-deleg_deff8b38.json` sha256
`ad2c5c7db97fb9f2…`).
**Round-2 verifier:** `deleg_d661002c` — **PASS**, 8 probe groups
(69/69 contract checks, A/B discrimination proven), at frozen HEAD
`02da6af`. Verdict archived
`var/evidence/v5-s3-capabilities/verdict-deleg_d661002c-r2.json` sha256
`ba8a2221bf58ad309452cde0ecebcd31692579b06d8caf5d4d36d70d1ffa6323`.
**Ledger close:** `verification.passed` (seq 22899 — see the label
note below) + `evidence.created:v5-s3-capabilities-close` (seq 22900);
live schema unchanged (v19).
**Label note (D-037 class):** the close script's fixed key reads
`verification.passed:verdict-v5-s3-capabilities:round1` while this is
the ROUND-2 verdict — the payload's `verifier_run_id` is the true
round identity (`deleg_d661002c`, and the round-1 FAIL JSON is
archived separately); the historical event is preserved as-is and the
discrepancy is recorded additively (DECISION_LOG D-038).

## Slice

Workers receive a short-lived CAPABILITY, never a reusable secret
(architecture contract §8): bound to mission/agent run/canonical
action/target/expiry; one-shot (replay refused); immediately dead on
STOP/veto; dead outside scope (escalation, forwarding); provider auth
resolved at execution time — identifier only, never a value; honest
NOT_READY (CapabilityUnavailable) without a registered READY
credentials system. Also resolves the S2 carried findings F1 (bare
system-id veto) and F2 (non-finite budget estimate).
Work package: `docs/planning/V5_S3_WORK_PACKAGE.yaml`.

## Design (files)

- `src/jarvis_foundry/control/capability_broker.py` (new) —
  `issue_capability` (fixed per-id event key; identical re-delivery
  dedupes), `issue_capability_for_envelope` (requires the broker's
  ALLOW ticket; capability id derived from envelope identity; binding
  uses RESOLVED target identities), `capability_state` (derived:
  ISSUED/USED/REVOKED/EXPIRED/UNKNOWN — expiry computed, no event),
  `use_capability` (one-shot, scope-checked, STOP/veto-immediate,
  agent-run-id required), `revoke_capability`, `resolve_provider_auth`
  (identifier-only auth capability or honest CapabilityUnavailable).
  Events `capability.issued/used/revoked` — append-only, full-payload
  idempotency keys for used/revoked. No projection, no migration
  (live schema stays v19).
- `src/jarvis_foundry/control/redaction.py` (new) — deterministic
  redactor (`redact_text`, `redact_payload`, `contains_secret_like`);
  same pattern family as the S1 boundary guard, extended for quoted-key
  JSON (round-1 M2).
- `src/jarvis_foundry/control/broker.py` (modified) — F1 (bare system
  id joins the veto identity set), F2 (`math.isfinite` budget guard);
  shared `emergency_stop_reason` / `matching_veto_reason` /
  `resolve_envelope_targets` extracted so the broker and the capability
  broker can never disagree about STOP/veto/targets.
- `src/jarvis_foundry/clock.py` — `add_seconds` (TTL arithmetic).
- `src/jarvis_foundry/control/capabilities.py` — **UNTOUCHED** (the V4
  module; a mid-slice clobber was recovered byte-identical — verified
  by the round-1 verifier, probe P2).

## Round-1 findings and repairs (deleg_deff8b38, FAIL)

| # | Severity | Finding | Repair (02da6af) |
|---|---|---|---|
| M1 | MEDIUM | VETO_ACTION on a bare system id denied the envelope but NOT an issued capability's use (F1 incomplete in use_capability) | bare ids added to the capability veto identity set; regression `test_use_veto_on_bare_system_id_revokes` |
| M2 | MEDIUM | `redact_text`/`contains_secret_like` blind to quoted-key JSON (`"token": "abc"` passes untouched) — the most common serialization on §8.2 surfaces | pattern extended with optional JSON quoting around key and value; regression `test_redact_text_quoted_json` (4 forms) |
| L1 | LOW | `use_capability` with NO presented context succeeded — anti-forwarding depended on the executor presenting the run id | agent run id now REQUIRED at use time; regression `test_use_requires_agent_run_id` |
| L2 | LOW | expiry derivation trusts the caller-supplied `now` (test seam) | recorded limitation — real flows get the clock from the supervisor (repo-wide convention) |
| I1–I3 | INFO | case-variant canonicalization is by design; revoked-id re-issue stays REVOKED (safe direction); docstring examples contain sample literals | docstring examples reworded to output-form only |

A/B discrimination (var/evidence/v5_s3_ab_flip.py): the 6
discriminating regressions FAIL against d7753b9's modules and PASS
against the repaired tree — proven before round-2 dispatch.

## Tests (37 + 6 repair regressions — tests/foundry/test_capability_broker.py)

issuance/binding/dedup/unknown-state; expiry boundary (T0+299 OK /
T0+300 EXPIRED; ttl≤0 refused); one-shot + replay refusal + idempotent
re-delivery; scope escalation (mission/run/action-variant/target);
STOP marker/directive/frozen; veto canonical + bare-id + prefixed;
explicit revoke; agent-run-id requirement; redaction matrix + quoted
JSON + benign strings + recursive payloads; ledger scans; provider
auth NOT_READY + READY (identifier-only); envelope issuance
(ticket-gated, resolved identities, same-id dedupe); rebuild
determinism. Suite: 598 (feat) → **604 EXIT=0** (repairs) —
`var/evidence/v5-s3-capabilities/suite-v5-s3-r2.txt`.

## Evidence (close)

- Rehearsal: `var/evidence/v5-s3-capabilities/rehearsal-live-copy.json`
  (live snapshot v19, 22,530 events at first run; reconcile consistent
  before+after full rebuild).
- Round-2 suite artifact: `var/evidence/v5-s3-capabilities/suite-v5-s3-r2.txt`
  sha256 `5381b22d82d7d214d665a726ce7e9f1920a827f666f95efb2f30c742757386a0`
  (604 passed EXIT=0 — verifier re-ran the full suite itself).
- A/B discrimination: `var/evidence/v5_s3_ab_flip.py` — OLD modules
  fail 6/6 discriminating regressions, NEW modules pass (proven by the
  verifier, probe P0).
- Live close: events 22,898 → 22,900; schema stays v19; daemon 5 s
  heartbeats, STOP false.
- Round-2 verifier INFO findings (recorded, no action): the redactor
  terminates values at comma/semicolon even inside quotes (same
  documented limit as the bare form and the S1 guard — not an M2
  regression); the repair diff carries synthetic secret-like literals
  only in docstrings and test fixtures.

## Known honest limitations

- Verifier shares the DeepSeek provider family (owner constraint);
  independence via separate sessions, frozen artifacts, 69/69
  adversarial checks + A/B flip.
- Expiry derivation trusts the caller-supplied `now` (repo-wide
  injectable-clock test seam); real flows get the clock from the
  supervisor.
- Provider AUTHENTICATION values remain out of scope until S8: this
  slice proves the identifier-only resolution contract and honest
  NOT_READY; no real credentials exist on this host.
- The capability broker is not wired into live missions until S7.
