# V4-S2 — Verifier Truth (DIR-2026-08-12 item 1)

**Slice:** V4_CONTINUOUS_AUTONOMY / truth-critical reliability — verifier truth
**Commit:** `e456c22` (fix(v4-s2): verifier truth — structured verdicts,
never exit-code PASS)
**Date:** 2026-08-12
**Status:** CLOSED — independent verifier **PASS** (deleg_81eea974,
2026-08-12T23:57+02:00, 10 probes executed; 2 info-severity findings —
test-count nit in the brief and the documented changed-context
EventConflict which V4-S4 addresses — verdict JSON archived at
`var/evidence/v4-s2-verifier-truth/verdict-deleg_81eea974.json`, sha256
a4bf96fe…). Ledger: `verification.passed:verdict-v4-s2-verifier-truth:
round1` + `evidence.created:v4-s2-verifier-truth-close` (schema 16).

## Problem (observed, not assumed)

1. `HermesOneshotAdapter.dispatch` stamped `verdict=VERDICT_PASS` on ANY
   exit-0 output that was not a classified failure report — a verifier was
   PASS merely because its process exited zero (adapter.py:284-291). The
   mission loop gates acceptance on `verifier.verdict == "PASS"`
   (loop.py:321), so exit-0 alone could advance a mission. Exactly the
   class DIR-2026-08-12 forbids.
2. The verifier dispatch instructions never required an explicit verdict
   (loop.py:306) — the "verdict" was an adapter-side assumption.
3. `evidence.created` bundles carried `artifact_hashes: {}` (loop.py:196) —
   verdicts were not bound to frozen artifact hashes.
4. Evidence was passed as a bare STRING despite `RoleResult.evidence:
   tuple[str, ...]` (adapter.py:288) — a latent type violation; the
   directive requires evidence preserved as proper strings/objects.

## Repair

1. **Structured-verdict parsing** (`roles/adapter.py`): after the existing
   failure-report classification, the verdict is parsed from the session
   output — bounded to the first 20 stdout lines — via (a) a line
   `VERDICT: PASS|FAIL|BLOCKED|NOT_TESTED` (case-insensitive, word
   boundary: `PASSED` never matches `PASS`), or (b) a JSON object line
   with a valid `verdict` field. No verdict → `NOT_TESTED` with an
   explanatory note appended to the evidence — NEVER PASS. The parse
   applies to every role; acceptance remains gated on the verifier only.
2. **Loop contract** (`mission/loop.py`): verifier instructions demand the
   structured verdict line; `_hash_target(target)` hashes every file under
   the frozen step target (bounded 50 files, forward-slash rel paths) and
   the hashes ride in `evidence.created.artifact_hashes` at PASS time.
3. **Staged CLIs** (F5/F6/gateway-kill fixtures) now emit `VERDICT: PASS`
   on healthy paths — the honest contract; the F5 healthy assertion now
   indexes the typed tuple.

## Tests (15 new)

- `tests/foundry/test_adapter_verdict.py` (10): verdict line PASS/FAIL/
  BLOCKED; case-insensitive; JSON verdict object; missing verdict →
  NOT_TESTED (never PASS); verdict beyond the 20-line window not honored;
  failure-report prefix still classified ProviderUnavailable BEFORE the
  parse; nonzero exit still hermes_oneshot_failed; builder role parsed.
- `tests/foundry/test_verifier_truth.py` (5): NOT_TESTED and FAIL
  verifier verdicts → FAILED/REPAIRING, no ACCEPTING, no commit, no
  evidence bundle, no mission.completed, verdict rows FAIL; verifier
  instructions contain VERDICT:/NOT_TESTED; PASS path binds artifact
  hashes (two files); empty target → {}.

## Evidence

- Full suite: 429 passed EXIT=0 (was 414) — artifact regenerated at
  `var/evidence/v4-s2-verifier-truth/suite-v4-s2.txt`.
- Historical evidence-type violation: pre-fix `RoleResult.evidence` was a
  bare string (probed empirically on the stashed pre-change tree).
- Next: independent adversarial verifier (freeze discipline), ledger
  close events after PASS.
