# V6-S5 — INDEPENDENT VERIFIER BOUNDARY

```text
STATUS: PASS
PHASE: V6_CONTINUOUS_SELF_EVOLUTION
SLICE: S5 — independent verifier boundary
OBJECTIVE: verification runs outside candidate control against frozen
  source, artifacts, metrics and manifests; structured verdicts
  (PASS/FAIL/BLOCKED/NOT_TESTED) bound to exact hashes; exit code zero
  alone is NOT_TESTED (D-032); one verdict per verification; verdict
  without a request refused (forgery guard); verifier identity recorded
  with D-031 enforcement (opencode_go + deepseek-v4-flash, no silent
  substitution); same-provider limitations flagged honestly; a
  candidate can never invoke a trusted promotion verdict for itself.
BASE COMMIT: 8b6a095 (S4 close)
CANDIDATE RELEASE: none (machinery slice)
RISK CLASS: R3-class machinery (verification control) built and verified
  inside the isolated V6 construction; live surfaces untouched
HYPOTHESIS: n/a (machinery; exercised by the S11 ladder)
BASELINE METRIC: full suite 792 EXIT=0 at S4 close
SUCCESS THRESHOLD: requests recorded (fresh per attempt, event-level
  dedup); verdicts bound to requests and frozen artifact hashes;
  structured-verdict-only (D-032); one verdict per verification;
  self-certification refused (builder session == verifier session);
  D-031 identity enforced; same-provider limitation recorded; trusted-
  verdict helper correct; all five projection touch points wired;
  suite green
GUARDRAILS: isolated runtime DB only (var/v6-runtime/v6.db, now schema
  v25); live DB stays v20; no live writes; verdicts are recorded by the
  controller, never by candidates
ACTIONS TAKEN: TDD red (15 tests) -> implemented evolution/verify.py
  (record_verification_request, record_verification_verdict,
  verification_status, is_trusted_verdict, VerificationError) ->
  migration v25 + PROJECTION_TABLES + reconcile key map + database
  row/all/count allowlists -> focused green (87 total across S1-S5) ->
  runtime DB migrated to v25 (reconcile consistent) -> full suite
FILES CHANGED: src/jarvis_foundry/evolution/verify.py (new);
  tests/foundry/test_evolution_verify.py (new, 15 tests);
  src/jarvis_foundry/migrations.py (SCHEMA_VERSION 25 + migration 25);
  src/jarvis_foundry/projections.py (verification.requested +
  verification.verdict_recorded handlers, PROJECTION_TABLES);
  src/jarvis_foundry/reconcile.py (key map); src/jarvis_foundry/
  database.py (row/all/count allowlists)
LIVE SYSTEMS CHANGED: NONE (verified read-only at close time)
TESTS: 15 new S5 tests + S1-S4 regression (72) + FULL SUITE 807 passed
  EXIT=0 (330.22s; var/evidence/v6-s5/suite-v6-s5-full.txt,
  sha256 b49d97a7038d28fac854629d6a6ff7e2a064e17fc6ba8b610c918fb9eeb6af4a)
VERIFIER: <fill after dispatch>
EVIDENCE: var/evidence/v6-s5/ (suite artifacts, verdict JSON);
  var/v6-runtime/v6.db (isolated runtime, schema v25, consistent)
CANARY: none (machinery slice)
PROMOTION OR ROLLBACK: none
COST AND RESOURCE USE: local only (pytest runs)
UNRESOLVED RISKS: same-provider verifier limitation (D-031) — recorded
  honestly on every verdict (the flag is structurally 1 under the
  directive; provider diversity is impossible by design and never
  hidden); verification sessions are recorded identities, not live
  process attestation — the S8 promotion gate consumes
  is_trusted_verdict + frozen hashes
NEXT AUTONOMOUS ACTION: S6 — zero-effect shadow mode
OWNER INPUT REQUIRED: NONE
```

## Design

`src/jarvis_foundry/evolution/verify.py`:

- `record_verification_request(db, *, improvement_id, subject_ref,
  criteria, artifacts, builder_identity, controller)` — the CONTROLLER
  creates the request (a candidate can never request its own trusted
  verification); artifact hashes 64-hex; criteria secret-scanned;
  builder identity must satisfy D-031. Fresh id per attempt; event
  re-delivery dedupes.
- `record_verification_verdict(...)` — refusals: no recorded request
  (forgery guard); a verdict already recorded (one verdict per
  verification); verdict not in PASS/FAIL/BLOCKED/NOT_TESTED; EMPTY
  criteria results (exit-code-zero alone is NOT_TESTED, D-032); per-
  criterion status outside the verdict vocabulary; verifier identity
  violating D-031 (no silent substitution); verifier session ==
  builder session (self-certification); artifact hashes differing from
  the request (the verdict must bind the frozen artifacts). The
  verdict_hash binds verdict + criteria results + findings + identity +
  evidence. same_provider_limitation recorded honestly.
- `is_trusted_verdict(db, verification_id, builder_identity)` — the
  S8 promotion gate: TRUE only for a VERDICTED PASS whose verifier
  session differs from the builder's session.
- `verification_status(...)` — REQUESTED/VERDICTED.

Projection `verifications` (migration v25, status CHECK REQUESTED/
VERDICTED, verdict CHECK vocabulary via handler, identity JSON columns,
verdict_hash, same_provider_limitation) wired into PROJECTION_TABLES,
the reconcile key map, and database row/all/count allowlists. Handlers
validate mandatory keys, the verdict vocabulary, and refuse verdicts
without a REQUESTED row inside the append transaction (forgery or
double verdict -> rollback).

## Tests (15)

request recorded + fresh-per-attempt + event-level dedup; unknown
improvement refused; secret criteria refused; verdict recorded (PASS,
limitation flag 1 honestly); verdict without request refused (forgery);
invalid verdict values refused; second verdict refused; empty criteria
results refused (D-032); artifact hash mismatch refused; FAIL verdict
accepted; self-certification refused (builder session); foreign
identity refused (D-031); same-provider limitation recorded honestly;
trusted-verdict helper (PASS/distinct session True; unknown False; FAIL
False); projection/schema integrity (pinned to the imported
SCHEMA_VERSION constant >= 25).

## 8. Verifier findings — repair record (deleg_036ee808, PASS)

Verdict JSON archived (var/evidence/v6-s5/verdict-deleg_036ee808.json) —
the delegation harness truncated the JSON at 8760 chars; the verdict
value (PASS) and per-criterion statuses (V1-V7 PASS) plus all findings
were recovered from the delivered summary prose
(verdict-recovery-note.txt). 19 adversarial probes; V1-V7 PASS.

| Severity | Finding | Resolution |
|---|---|---|
| MEDIUM | criteria_results criteria are NOT membership-checked against the request's frozen criteria (foreign criteria accepted; dropped criteria accepted — still hash-bound and detectable). | REPAIRED: every criteria_results criterion must be a MEMBER of the request's criteria list; a SUBSET verdict (some criteria unverified) remains allowed — each verified criterion must be a member. Regressions: test_foreign_criterion_in_verdict_refused, test_dropped_criterion_in_verdict_accepted. |
| MEDIUM | Artifact hash validation is length-only: a 64-char NON-hex value (e.g. 'z'*64) was accepted (records.py uses a strict hex regex; verify.py did not). | REPAIRED: _HEX64 (^[0-9a-f]{64}$) enforced at request time. Regression: test_non_hex_artifact_hash_refused. |
| LOW | Session comparison is exact-match (prefix/suffix variants pass self-certification). | ACCEPTED as designed: exact-match is the identity semantics — a different session string IS a different identity; documented. |
| LOW | is_trusted_verdict does not shape-check builder_identity (any dict with a session_id works). | REPAIRED: fails closed on malformed/empty/missing session_id. Regression: test_trusted_verdict_malformed_builder_identity. |
| LOW | The brief claim "zero verification.% events" on the LIVE DB was imprecise: V5-era verification.passed/failed events exist in the live ledger (the claim's substance is V6 machinery events). | REPAIRED (claim precision): the live-DB claim is stated as zero V6 verification.requested/verdict_recorded events; V5 verification.* vocabulary is pre-existing. |

Round-2: fresh verifier with A/B discrimination dispatched after this
commit.

## 9. Round-2 verdict (deleg_f74c21d1, PASS — A/B discriminated)

Fresh round-2 verifier: E1 hex artifact hashes PASS (A/B: old length-
only, new _HEX64; z*64 and A*64 refused, lowercase hex accepted); E2
frozen-criteria membership PASS (foreign criterion refused, status
stays REQUESTED; subset verdict accepted); E3 trusted-gate shape check
PASS (5 malformed identities -> False); E4 claim precision PASS (doc
records V6 verification.requested/verdict_recorded only; live DB has 77
legacy V5 verification.passed/started/failed/blocked events, zero V6);
E5 no regression PASS (4 regression tests, focused 19/19, suite 811
EXIT=0 sha256 db75e2e4c2b9496b427799113a3a5478ae11d51c60a8896c627453c0b958d28a,
collect-only 811, runtime v25 consistent, live v20 zero V6 events, both
checkouts unchanged). 17/17 probes. Verdict JSON:
var/evidence/v6-s5/verdict-deleg_f74c21d1-r2.json (harness-truncated
tail; recovery note appended — verdict value + per-criterion statuses
complete). Round-1 findings fully closed (2 MEDIUM repaired, 1 LOW
repaired, 1 LOW accepted-as-designed).

**S5 CLOSED — PASS (rounds 1+2).** Next: S6 (zero-effect shadow mode).
