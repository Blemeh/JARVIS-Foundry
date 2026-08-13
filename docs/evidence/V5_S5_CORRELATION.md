# V5-S5 — Cross-System Entities and Event Correlation

**Status: CLOSED — PASS (round-1)** (2026-08-13)
**Code commit:** `d0ee0dc` (feat(v5-s5))
**Independent verifier:** `deleg_dfaec04d` — **PASS**, 8 probe groups
(75/75 checks incl. the R-SEC-01 forge-control gate), at frozen HEAD
`d0ee0dc`.
**Verdict archive:**
`var/evidence/v5-s5-correlation/verdict-deleg_dfaec04d.json` sha256
`61d6eadbdb59914076b744a2e939b33aed304942e2f4077b386f3bdf0ca985dd`
**Ledger close:** `verification.passed:verdict-v5-s5-correlation:round1`
+ `evidence.created:v5-s5-correlation-close`; live schema unchanged (v19).

## Slice

Connect the same project, person, organization, resource, or action
across systems (roadmap §8) without losing provenance or uncertainty:
stable entity identities with source-specific references; deterministic
matching with confidence and ambiguity preservation (never destructive
merges); event correlation across sources with ordering, causation,
idempotency, and evidence references; bounded worker context manifests;
and malicious content can never forge owner control (R-SEC-01).
Work package: `docs/planning/V5_S5_WORK_PACKAGE.yaml`.

## Design (files)

- `src/jarvis_foundry/correlation/entities.py` (new) — `EntityRef`,
  `register_entity` (append-only; identical re-delivery dedupes;
  re-registration records history and derived state follows the latest;
  a source ref has exactly ONE holder — the second claim is refused and
  names the holder), `add_entity_ref` (atomic; unknown entity rolls
  back), `resolve_entity_ref`, `match_entities` (exact → 1.0,
  canonicalized → 0.9, duplicates ALL returned — ambiguity explicit).
- `src/jarvis_foundry/correlation/events.py` (new) — `correlation_chain`
  (cross-source ordering + causation links over the §10.3 envelope
  fields), `build_cross_system_manifest` (bounded deterministic worker
  manifests; truncation flags; self-excluding bookkeeping; full-payload
  idempotency keys).
- `src/jarvis_foundry/correlation/__init__.py` — package marker
  (verifier consistency nit).
- Raw `correlation.*` events; no projection, no migration. The V2
  epistemic layer (`knowledge_entities`, knowledge_* events) is
  untouched.

## Verifier findings (deleg_dfaec04d, PASS — all INFO, recorded)

| # | Severity | Finding | Resolution |
|---|---|---|---|
| I1 | INFO | Re-registration replaces the derived ref set (a re-registration omitting a ref unbinds it) — documented "derived state follows the latest record" semantics; history retains both events; one-holder-per-ref invariant preserved | Recorded; deterministic by design |
| I2 | INFO | Ref-holder conflict check runs outside the append transaction — TOCTOU window under concurrent writers; inert in the single-writer ledger (WAL, BEGIN IMMEDIATE); even if two claims land, resolution stays deterministic by first-registration sequence | Recorded; single-writer invariant |
| I3 | INFO | `Event.create` defaults correlation_id to the event's own event_id — every event is self-correlated; manifests bounded/deterministic/hash-stable; chain queries for NULL/'' return [] | Recorded; consistent with the envelope contract |
| I4 | INFO | `correlation/` had no `__init__.py` (PEP 420) | Fixed — package marker added |
| I5 | INFO | Tree state verified clean; commit = exactly 3 files; V2 layer untouched | Recorded |

## Tests (16 — tests/foundry/test_correlation.py)

registration + ref roundtrips; dedup; append-only re-registration;
atomic add (unknown entity rollback); ref-conflict refusal naming the
holder; matching confidence (1.0/0.9); duplicate-entity ambiguity (no
merge); stale canonical name stops matching with history preserved;
cross-source chains (ordering + causation); unknown chains; bounded
deterministic manifests (+ truncation + self-exclusion); R-SEC-01
forge-control guard (hostile names/refs/manifests create ZERO
directives, vetoes untouched, supervisor_state byte-identical);
provenance reconstruction after rebuild. Suite: 634 → **650 EXIT=0** —
`var/evidence/v5-s5-correlation/suite-v5-s5.txt`.

## Evidence

- Rehearsal: `var/evidence/v5-s5-correlation/rehearsal-live-copy.json`
  (live snapshot v19; reconcile consistent before+after full rebuild).
- Live close: two events appended; schema stays v19; daemon 5 s
  heartbeats, STOP false.

## Known honest limitations

- Verifier shares the DeepSeek provider family (owner constraint);
  independence via separate sessions, frozen artifacts, 75/75 checks.
- Correlation data is exactly that — DATA. The control plane
  (directives, vetoes, STOP) is only ever written by the directive
  projection path from owner-authorized events (proven by the R-SEC-01
  probes).
- Cross-system identity is reference-based, not semantic: nothing here
  claims to KNOW that two refs denote the same real-world thing beyond
  the deterministic rules; ambiguous matches are surfaced, never
  auto-merged.
- The correlation layer is not wired into live missions until S7.
