# V5-S4 — Connector Contract, Registry, Health, Quota, and Routing

**Status: CLOSED — PASS (round-1)** (2026-08-13)
**Code commits:** `5fae064` (feat(v5-s4)) + `5876cff` (fix(v5-s4):
verifier LOW — reroute reason truth)
**Independent verifier:** `deleg_761e4d85` — **PASS**, 12 probe groups
(83/83 checks), at frozen HEAD `5fae064` (src/tests clean start→end).
**Verdict archive:**
`var/evidence/v5-s4-connectors/verdict-deleg_761e4d85.json` sha256
`becdc5bc4220651323c7c6ff34023edb333a373413c4d874881b8c69dfec21f7`
**Ledger close:** `verification.passed:verdict-v5-s4-connectors:round1`
+ `evidence.created:v5-s4-connectors-close`; live schema unchanged (v19).

## Slice

Make systems pluggable and isolate connector failures (roadmap §8): a
versioned connector contract with declared action semantics
(read/write/execute/communicate/external_effect), a durable registry
with health samples, quota/cost accounting, disable/enable control,
and deterministic rerouting ONLY among verified, policy-allowed
connectors. One connector's outage never stops unrelated work. No
online connectors are claimed — real interfaces/auth are S8; the local
fakes are deterministic test substrates. Also delivers the S2 verifier
F3 structural fix: per-kind declared action semantics.
Work package: `docs/planning/V5_S4_WORK_PACKAGE.yaml`.

## Design (files)

- `src/jarvis_foundry/connectors/contract.py` (new) — `ConnectorSpec`
  (id, kind, version, actions→semantics, capabilities, retry policy),
  `validate_connector_spec` (semver-like version, exact semantic
  vocabulary, capability states, retry policy sanity), `is_mutating`,
  `check_action_semantics` (F3: a mutating DECLARED action requires
  expected effects + before-state capture; unknown connector /
  undeclared action → no claim), `latest_spec` (derived from the
  append-only registration stream).
- `src/jarvis_foundry/connectors/registry.py` (new) —
  `register_connector` (full-payload keys: identical re-delivery
  dedupes, a new version records new history), `record_health_sample`
  (HEALTHY/DEGRADED/DISABLED/UNKNOWN; latest wins),
  `record_quota_use` (used/limit accounting), `disable_connector` /
  `enable_connector`, `connector_status` (derived state),
  `select_connector` (deterministic gates: STOP → veto → health →
  disabled → capability → quota → stable id order; preferred-first
  with reroute). Raw `connector.*` events, no projection, no migration.
- `src/jarvis_foundry/connectors/fakes.py` (new) — `FakeConnector`
  (deterministic read/write/execute + `fail_next` outage simulation),
  `ConnectorFailure`.

## Verifier findings and resolution (deleg_761e4d85, PASS)

| # | Severity | Finding | Resolution |
|---|---|---|---|
| F1 | LOW | Reroute reason text misattributes the cause: the preferred-path for/else always executed its else, so a registered-but-failing preferred connector was reported as "not registered" (routing decisions themselves correct) | Fixed in `5876cff` — the preferred path now names the REAL gate gap; regression `test_reroute_reason_names_the_real_gap` (DEGRADED + quota cases, asserts "not registered" absent) |
| I1 | INFO | Prefixed connector-id veto (`connector:conn-fs`) doesn't match — bare id is the canonical identity (S3 F1 carry) | Recorded; owner-authoring convention only, no system path uses the prefixed form |
| I2 | INFO | A connector may DECLARE a mutating action as 'read' (declaration trust boundary) — F3's recorded limitation: semantics come from the declaration, not the kind string | Recorded; honest declaration-based model |
| I3 | INFO | health/disable events can be recorded for never-registered connectors (orphan events); status still raises | Recorded; harmless orphans, guarded at use |
| I4 | INFO | version regex accepts leading zeros; derived version follows the latest record even when lower | Consistent with "semver-like" + "derived follows latest"; recorded |

## Tests (29 + 1 repair regression — tests/foundry/test_connectors.py)

contract validation (valid + 10 invalid); ACTION_SEMANTICS exact;
F3 action-semantics matrix (8 variants); registration idempotency +
version history; health derivation + dedup + invalid statuses; quota
accounting + boundary (used == limit blocks); disable/enable;
outage isolation (the gate probe); reroute (+ reason truth);
capability mismatch incl. all non-ready states; STOP (marker +
STOP_EVERYTHING); veto (id/kind/capability); determinism (stable id
order); rebuild/replay determinism; adversarial edge probes
(canonicalization direction, int+float quota boundaries, orphan
events, downgrade versions, secret scan across all connector.*
payloads — zero hits). Suite: 633 (feat) → **634 EXIT=0** (repair) —
`var/evidence/v5-s4-connectors/suite-v5-s4-r2.txt`.

## Evidence

- Rehearsal: `var/evidence/v5-s4-connectors/rehearsal-live-copy.json`
  (live snapshot v19; reconcile consistent before+after full rebuild).
- Live close: two events appended; schema stays v19; daemon 5 s
  heartbeats, STOP false.

## Known honest limitations

- Verifier shares the DeepSeek provider family (owner constraint);
  independence via separate sessions, frozen artifacts, 83/83 probes.
- No REAL online connectors exist on this host — the registry's live
  table stays empty; readiness claims for online connectors are
  honestly NOT_READY until S8.
- Declaration-based semantics are the trust boundary (I2); envelope↔
  connector semantics validation is wired into execution in S7.
- The registry is not wired into live missions until S7.
