# M2 Executive Intelligence — Evidence Bundle

Promoted 2026-08-21. Main 7ac0b23, release 769715a (CURRENT), previous 9a10910.

| File | What it proves |
|---|---|
| M2_PROMOTE.json | build/promote/rollback-drill/return all validated; CURRENT=769715a |
| M2_LIVE_PROOF.json | 12/12 end-to-end proofs through production code paths (sha256 938b6b2c...) |
| FULL_SUITE_R3.junit.xml | 1559 tests, 0 failures/errors/skipped |
| R4_PROBE_SUITE.py | adversarial probes P1-P6: forged-admission inertness, replay idempotency, STOP blocks selection, latest-decision-newest, bait damping, FAILED-neg/BLOCKED-neutral calibration |
| VERIFIER_ROUND1_TRANSCRIPT.log | full trace of independent round-1 review; its P4 probe found a real defect (CC latest_decision returned oldest) -> fixed in 24f5a15 with discriminating regression |
| M2_INDEPENDENT_VERIFIER.json | round-4 execution-verifier artifact: PASS (focused 32/32, live proof 12/12, probes 6/6, junit parse) |
| MANIFEST.sha256 | sha256 of every file above at archival time |

## Verifier history

Rounds 1-3 (full adversarial reviews) each died on provider HTTP errors
mid-review. Round 1 completed its probe sweep and found the CC-ordering
defect before dying; the fix + regression is commit 24f5a15 on main.
Round 3's orphaned full-suite rerun later delivered its junit
(1558/0/0/0) independently. Round 4 (execution-only design) survived,
reproduced everything, and wrote the verdict artifact. Limitation:
same-model verification (x-preview); provider diversity was unavailable.
