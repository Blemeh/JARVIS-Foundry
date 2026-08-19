# PROMPT 3 OF 4 — CAPABILITY RESTORATION

Live-gated M6A→M8 on canonical `main` / live Foundry home. Operating Kernel unchanged. OpenHands live coding and Windows desktop OpenAdapt left honest NOT_READY / PROBING.

Raced mission `mission-prompt3-m6a-T193243Z` remains **BLOCKED** (VERIFYING collision then repair budget exhausted). Twin stamp `mission-prompt3-m6a-T193251Z` is **COMPLETE**. Not faked.

This closer did not CreateGoal/UpdateGoal, did not start M9–M11, did not kill the live Supervisor, did not rewind origin.

```text
PROMPT: 3 OF 4 — CAPABILITY RESTORATION
CANONICAL START SHA: b59836772d948bd7b2507949c42f3c7dfa75afaf
CANONICAL FINAL SHA (capability install): 52ee917208e5964992125f478508460cd9893514
git HEAD / main / origin/main: 8880a0e9c212c19ae85208d123c43fd71187b047 (docs-ahead of CURRENT; includes prior Prompt 4 evidence commits 9d34fde/8880a0e)
CURRENT LIVE RELEASE POINTER: 52ee917208e5964992125f478508460cd9893514
PREVIOUS RELEASE POINTER: c4a0fd1b706f8c0ecac89143bc01b7c37d34191a
LIVE LAUNCHER TREE: 52ee917208e5964992125f478508460cd9893514 PIDs 30600/24292 started 2026-08-19 22:31:24 local (Prompt 4 restart; NOT the Prompt 3 close; NOT b598367)
LIVE DB/SCHEMA: C:\Users\b\AppData\Local\hermes\foundry\foundry.db / v34
M6A HYBRID WORKERS: PASS (prior LIVE_GATES; Hermes LAB COMPLETE on T193251Z; T193243Z honest BLOCKED; OpenHands live coding NOT_READY)
M6B HYBRID MEMORY: PASS (advisory-only; poison blocked; mission truth unchanged)
M7A DISCOVERY: PASS (valuable candidate; low-value rejected; duplicate created=false)
M7B EYES: PASS (KeePass excluded; secrets redacted; untrusted screen text; pause works)
M7C WORKFLOW DISCOVERY: PASS (1 repeated pattern → existing candidate system)
M7D OPENADAPT: PASS (governed/browser path; desktop PROBING; live OpenAdapt-Flow NOT_READY)
M8 COMMAND CENTER: PASS (snapshot==DB; CONSTRAINT via durable path; STOP through control path then clear)
PRODUCTION MISSION PATH COUNT: 1 (DB supervisor singleton=1; LIVE_GATES production_paths=1; one live release tree)
AUTONOMOUS LIVE MISSION CYCLES: 16 COMPLETE missions in live DB (9 Prompt-3/candidate COMPLETE in verifier inspect)
HERMES RUNS: agent_runs=123 (DB); M6A Hermes LAB COMPLETE
OPENHANDS RUNS: 0 live coding (NOT_READY)
VERIFIER RUNS: verification.passed=73 verification.failed=36 verification_verdicts=72
REPAIR CYCLES: verification.failed=36; T193243Z repair budget exhausted
NEXT-ACTION CONTINUATIONS: T193251Z step-a then step-c; M7 E2E mission-687bda2610c3 COMPLETE
DISCOVERY MISSIONS: candidates=944; Prompt-3 include mission-d56efb1b5cb6
PERCEPTION OBSERVATIONS: 1 perception.observed
WORKFLOW PATTERNS: 1 workflow.pattern_evidence
GOVERNED PROCEDURE RUNS: 2 procedure.execution.requested / 2 procedure.effect.verified
COMMAND CENTER: live Foundry read model (52ee917 footer fix on git)
REAL DATA: M8 snapshot_missions=32 == db_missions=32 (LIVE_GATES; not re-run this closer)
OWNER DIRECTIVES: CONSTRAINT owner-dcad81901d068b5c4cd2a4a2 APPLIED
STOP THROUGH UI: prompt3-cc stop_id be42c558-5eef-4a9e-8a9e-172661f02f34 then cleared; Supervisor RUNNING; STOP sentinel absent
L4 AUTONOMOUS OUTCOME: PASS LIVE (Prompt 2 freeze; Prompt-3 kernel missions used same worker_loop path)
L5 RECOVERY: PASS (Prompt 2 freeze; M8 STOP freeze/clear)
L6 UNATTENDED: NOT YET GATED
INDEPENDENT VERIFIER: FAIL ran_at 2026-08-19T20:52:27Z docs/evidence/PROMPT3_RECOVERY/INDEPENDENT_VERIFIER_FRESH.json
UNRESOLVED CRITICAL: none
UNRESOLVED HIGH: full pytest suite not proven green from artifacts (junit 1429 tests / 1 failure)
EXTERNAL BLOCKERS: OpenHands live coding NOT_READY; OpenAdapt desktop PROBING; Prompt 4 already wrote M9–M11 evidence then deferred M11 (last mtimes ~22:29; no writer at close); live launcher was restarted onto CURRENT by that session
ACTIVE CAPABILITIES: live kernel on CURRENT 52ee917; Hermes LAB workers; hybrid memory advisory; discovery; Eyes sensor; workflow discovery; openadapt.browser governed; Command Center read+authenticated controls
READY CAPABILITIES: Hermes LAB path; discovery candidate→mission; Command Center real read model; openadapt.browser registry
PROBING/DISABLED CAPABILITIES: OpenHands live coding NOT_READY; OpenAdapt desktop PROBING
OWNER MODE: MANAGEMENT BY EXCEPTION
VERDICT: FAIL
PROMPT 4 READY: NO
```

PROMPT 4 READY is **NO**: independent verifier is FAIL (section 16 does not hold). CURRENT does match the live launcher (52ee917 / PIDs 30600+24292), and M6/M7/M8 remain recorded PASS in LIVE_GATES.json, but an honest verifier PASS is required. L6 is not gated. This closer did not start M9–M11.

## Identity (do not relabel)

- git HEAD == origin/main == `8880a0e` (evidence/docs commits after capability SHA).
- Installed CURRENT == live launcher == `52ee917`. Close did **not** happen on `b598367`; that tree is no longer running.
- Supervisor instance `supervisor-2cd24ecb-55e5-4eb2-931a-c9cd3c7a8000`, singleton=1, RUNNING. Production path count 1.

## Closure evidence this session

- STEP 1 exclusivity: no python/pytest writer on the repo tree. Prompt 4 scripts/JSON idle (last write M11_RESUME.md 22:29:46). Live launcher is the release venv, not a repo writer.
- Verifier no longer hardcodes `full_suite.exit_code=0` / `finished_local=22:27:21`. It consumes junit+log or omits full_suite and fails.
- REAL FULL suite ONCE: `.venv` python 3.13.5, `PYTHONPATH=src`, `pytest tests/foundry tests/convergence -q --tb=line --junitxml=docs/evidence/PROMPT3_RECOVERY/FULL_SUITE.junit.xml` **exit 1** at 22:50:58 local. 1429 tests, 1 failure, 0 errors. Installer tests did not fail against live CURRENT (no singleton-lease live-interference this run). Production Supervisor was not killed.
- The one failure was `test_prompt3_independent_verifier_cannot_hardcode_full_suite_pass` matching computed `"exit_code": 0 if ok else 1`. One repair batch: assertion tightened. Affected test PASS (`docs/evidence/PROMPT3_RECOVERY/REPAIR_AFFECTED.junit.xml`). Full suite was not run a second time.
- Verifier recheck after suite+repair still consumes the **first** full-suite junit (failures=1) → FAIL. No third loop.
- Residual: `mission-prompt3-m6a-T193243Z` BLOCKED. OpenHands NOT_READY. OpenAdapt desktop PROBING. Not a Prompt 1/2 kernel reopen.
