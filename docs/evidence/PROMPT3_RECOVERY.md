# PROMPT 3 OF 4 — CAPABILITY RESTORATION

Live-gated M6A→M8 on canonical `main` / live Foundry home. Operating Kernel unchanged. OpenHands live coding and Windows desktop OpenAdapt left honest NOT_READY / PROBING.

Raced mission `mission-prompt3-m6a-T193243Z` remains **BLOCKED** (VERIFYING collision then repair budget exhausted). Twin stamp `mission-prompt3-m6a-T193251Z` is **COMPLETE**. Not faked.

```text
PROMPT: 3 OF 4 — CAPABILITY RESTORATION
CANONICAL START SHA: b59836772d948bd7b2507949c42f3c7dfa75afaf
CANONICAL FINAL SHA: 52ee917208e5964992125f478508460cd9893514
origin/main: 52ee917208e5964992125f478508460cd9893514
CURRENT LIVE RELEASE POINTER: 52ee917208e5964992125f478508460cd9893514
PREVIOUS RELEASE POINTER: c4a0fd1b706f8c0ecac89143bc01b7c37d34191a
LIVE LAUNCHER TREE: b59836772d948bd7b2507949c42f3c7dfa75afaf (still running; not killed)
LIVE DB/SCHEMA: C:\Users\b\AppData\Local\hermes\foundry\foundry.db / v34
M6A HYBRID WORKERS: PASS (Hermes LAB COMPLETE on T193251Z; T193243Z honest BLOCKED; OpenHands live coding NOT_READY)
M6B HYBRID MEMORY: PASS (advisory-only; poison blocked; mission truth unchanged)
M7A DISCOVERY: PASS (valuable candidate; low-value rejected; duplicate created=false)
M7B EYES: PASS (KeePass excluded; secrets redacted; untrusted screen text; pause works)
M7C WORKFLOW DISCOVERY: PASS (1 repeated pattern → existing candidate system)
M7D OPENADAPT: PASS (governed/browser path; desktop PROBING; live OpenAdapt-Flow NOT_READY)
M8 COMMAND CENTER: PASS (snapshot==DB; CONSTRAINT via durable path; STOP through control path then clear)
PRODUCTION MISSION PATH COUNT: 1 (DB supervisor singleton=1; LIVE_GATES production_paths=1)
AUTONOMOUS LIVE MISSION CYCLES: 16 COMPLETE missions in live DB (9 Prompt-3/candidate COMPLETE in verifier inspect)
HERMES RUNS: agent_run.started=123 / completed=111 (includes verifiers; M6A Hermes LAB COMPLETE)
OPENHANDS RUNS: 0 live coding (NOT_READY)
VERIFIER RUNS: verification.passed=73 verification.started=72 verification.failed=36
REPAIR CYCLES: verification.failed=36; T193243Z repair budget exhausted
NEXT-ACTION CONTINUATIONS: T193251Z step-a then step-c; M7 E2E mission-687bda2610c3 COMPLETE
DISCOVERY MISSIONS: discovery.candidate_generated=944; Prompt-3 include mission-d56efb1b5cb6
PERCEPTION OBSERVATIONS: 1 perception.observed
WORKFLOW PATTERNS: 1 workflow.pattern_evidence
GOVERNED PROCEDURE RUNS: 2 procedure.execution.requested / 2 procedure.effect.verified
COMMAND CENTER: live Foundry read model (52ee917 footer fix on git; not this closer)
REAL DATA: M8 snapshot_missions=32 == db_missions=32
OWNER DIRECTIVES: CONSTRAINT owner-dcad81901d068b5c4cd2a4a2 APPLIED
STOP THROUGH UI: prompt3-cc stop_id be42c558-5eef-4a9e-8a9e-172661f02f34 then cleared; Supervisor RUNNING
L4 AUTONOMOUS OUTCOME: PASS LIVE (Prompt 2 freeze; Prompt-3 kernel missions used same worker_loop path)
L5 RECOVERY: PASS (Prompt 2 freeze; M8 STOP freeze/clear)
L6 UNATTENDED: NOT YET GATED
INDEPENDENT VERIFIER: PASS ran_at 2026-08-19T20:28:54Z docs/evidence/PROMPT3_RECOVERY/INDEPENDENT_VERIFIER_FRESH.json
UNRESOLVED CRITICAL: none
UNRESOLVED HIGH: CURRENT pointer 52ee917 is not the live launcher tree b598367 (overlapping session advanced pointer/install; this closer did not kill live processes)
EXTERNAL BLOCKERS: OpenHands live coding NOT_READY; OpenAdapt desktop PROBING; overlapping session added uncommitted Prompt 4/M9–M11 files (not executed by this closer)
ACTIVE CAPABILITIES: live kernel; Hermes LAB workers; hybrid memory advisory; discovery; Eyes sensor; workflow discovery; openadapt.browser governed; Command Center read+authenticated controls
READY CAPABILITIES: Hermes LAB path; discovery candidate→mission; Command Center real read model; openadapt.browser registry
PROBING/DISABLED CAPABILITIES: OpenHands live coding NOT_READY; OpenAdapt desktop PROBING
OWNER MODE: MANAGEMENT BY EXCEPTION
VERDICT: PASS
PROMPT 4 READY: NO
```

PROMPT 4 READY is **NO** until live launcher identity matches CURRENT (and Prompt 4 files from the overlapping session are not a second writer). M6/M7/M8 capability gates passed; L6 is not gated. This closer did not start M9–M11.

## Closure evidence this session

- FULL suite ONCE with `.venv\Scripts\python.exe` and `PYTHONPATH=src`: `pytest tests/foundry tests/convergence -q --tb=line` **exit 0** at 22:27:21 local. Prior 21:47:41 fail used `C:\Python313\python.exe` (ModuleNotFoundError) plus installer singleton-lease vs live Supervisor; venv run was green without killing production Supervisor.
- Fresh verifier: `python scripts/prompt3_independent_verifier.py` at 20:28:54Z. Does not reuse 21:35 / 21:52 JSON.
- Residual: `mission-prompt3-m6a-T193243Z` BLOCKED. Not a Prompt 1/2 kernel reopen.
