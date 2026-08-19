# PROMPT 3 OF 4 — CAPABILITY RESTORATION

Live-gated M6A→M8 on canonical `main` / live Foundry home. Operating Kernel unchanged. OpenHands live coding and Windows desktop OpenAdapt left honest NOT_READY / PROBING.

```text
PROMPT: 3 OF 4 — CAPABILITY RESTORATION
CANONICAL START SHA: b59836772d948bd7b2507949c42f3c7dfa75afaf
CANONICAL FINAL SHA: (see git HEAD after capability commits; start SHA if uncommitted)
origin/main: b59836772d948bd7b2507949c42f3c7dfa75afaf (pre-commit)
CURRENT LIVE RELEASE: b59836772d948bd7b2507949c42f3c7dfa75afaf
PREVIOUS RELEASE: adada7f31ac515cfac9782ace95f744b631f76f5
LIVE DB/SCHEMA: C:\Users\b\AppData\Local\hermes\foundry\foundry.db / v34
M6A HYBRID WORKERS: PASS (Hermes LAB path COMPLETE A/B/C; OpenHands routes when READY in tests + first live stack; live coding NOT_READY)
M6B HYBRID MEMORY: PASS (compile without memory; governed EXTERNAL writeback; poison blocked; mission truth unchanged)
M7A DISCOVERY: PASS (valuable scored+approved into kernel mission; low-value rejected; duplicate created=false)
M7B EYES: PASS (KeePass excluded; api_key redacted; bounded perception.observed; pause sentinel; STOP independent; screen text untrusted)
M7C WORKFLOW DISCOVERY: PASS (1 repeated pattern → existing candidate system; replay duplicate_of same candidate)
M7D OPENADAPT: PASS (frozen bundle+hash; Tier-C reference; governed replay COMPLETE + duplicate one effect; tamper rejected; missing-flow outage NOT_READY; desktop PROBING)
M8 COMMAND CENTER: PASS (live snapshot missions=DB count; authenticated CONSTRAINT; STOP sentinel then clear; Supervisor remained RUNNING)
PRODUCTION MISSION PATH COUNT: 1
AUTONOMOUS LIVE MISSION CYCLES: 4+ COMPLETE Prompt-3 kernel missions (M6A x3 successful stamps + discovery E2E)
HERMES RUNS: LAB scripted Hermes on HybridWorkerRoleAdapter (agent_run.started 40 after 19:28Z including verifiers)
OPENHANDS RUNS: 0 live coding (exe present --help rc=0; owner ~/.openhands unmodified; coding NOT_READY)
VERIFIER RUNS: verification.passed 14 after 19:28Z (fresh verifier role)
REPAIR CYCLES: verification.failed 7 after 19:28Z (LAB step-b DEFECT then automatic repair)
NEXT-ACTION CONTINUATIONS: M6A A→B→C unattended; E2E candidate mission one-step COMPLETE
DISCOVERY MISSIONS: approve_candidate created mission-d56efb1b5cb6 and later stamp missions; EXECUTED candidates include Prompt-3
PERCEPTION OBSERVATIONS: 1 perception.observed (obs-fa026793258e0e42)
WORKFLOW PATTERNS: 1 workflow.pattern_evidence → cand-35fc8933cd45 (duplicate replay did not create a second mission)
GOVERNED PROCEDURE RUNS: 2 procedure.execution.requested / claimed / effect.verified COMPLETE; desktop NOT_READY
COMMAND CENTER: src/jarvis_foundry/command_center.py bound to live DB (read model + /api/controls)
REAL DATA: snapshot missions 27 == DB 27 on first M8 probe
OWNER DIRECTIVES: CONSTRAINT owner-40d142816d9ff7ffad3c4ba3 APPLIED (plus later probe directives)
STOP THROUGH UI: EmergencyStop activate stop_id 00244d5a-eccb-4249-848e-921d66c7348a then cleared; Supervisor RUNNING
L4 AUTONOMOUS OUTCOME: still PASS (kernel missions completed through same worker_loop → MissionLoop path)
L5 RECOVERY: still PASS (STOP freeze/clear during M8; yaml restored; residual BLOCKED stamp T193243Z from overlapping probe)
L6 UNATTENDED: NOT YET GATED
INDEPENDENT VERIFIER: docs/evidence/PROMPT3_RECOVERY/INDEPENDENT_VERIFIER.json
UNRESOLVED CRITICAL: none
UNRESOLVED HIGH: none
EXTERNAL BLOCKERS: OpenHands live coding not executed (no manufactured parity); Windows desktop OpenAdapt PROBING; Screenpipe continuous sensor not required (synthetic bounded Eyes probe)
ACTIVE CAPABILITIES: write_registered_worktree_files READY on Prompt-3 resources; hybrid workers (Hermes LAB); hybrid memory advisory; discovery; perception input; workflow discovery (explicit probe); openadapt.browser READY in registry / desktop PROBING; Command Center read+authenticated controls
READY CAPABILITIES: Hermes LAB worker path; discovery candidate→mission; Command Center read model; openadapt.browser registry (governed replay with independent oracle)
PROBING/DISABLED CAPABILITIES: OpenHands live coding NOT_READY; OpenAdapt desktop PROBING; Screenpipe continuous ingest disabled-by-default; worker yaml OpenHands enabled:false on Supervisor spawn
OWNER MODE: MANAGEMENT BY EXCEPTION
VERDICT: PASS
PROMPT 4 READY: YES
```

## Evidence pointers

- Reality check: `git rev-parse HEAD/main/origin/main` = `b59836772d948bd7b2507949c42f3c7dfa75afaf`; Supervisor PIDs 29416/29580 via `jarvis-supervisor.cmd` on CURRENT release; STOP off; one integration writer on canonical main.
- Pin seam: `src/jarvis_foundry/runtime/worker_loop.py` `_pin_routing_to_hermes(stack, config)` skips only when OpenHands enabled, not lab-scripted, registered, READY+evidence.
- Focused tests: `tests/foundry/test_runtime_worker_loop.py` (OpenHands pin/eligibility), `test_worker_router.py`, `test_discovery_candidates.py`, `test_hybrid_memory_*`, `test_perception.py`, `test_workflow_discovery.py`, `test_procedural_learning.py`, `test_v4_command_center.py`, Prompt 1/2 kernel tests PASS.
- Live gates: `python scripts/prompt3_capability_gates.py` → `docs/evidence/PROMPT3_RECOVERY/LIVE_GATES.json`
- Worktrees: `C:\temp\jarvis-prompt3-m6a-*`, `C:\temp\jarvis-prompt3-e2e-*`
- DB: `foundry.db` schema_migrations max=34; missions COMPLETE include `mission-prompt3-m6a-T192853Z` and `mission-d56efb1b5cb6`
- Residual: `mission-prompt3-m6a-T193243Z` BLOCKED (overlapping probe / lease). Not a kernel redesign. Do not treat as Prompt 1/2 regression.

Prompt 4 / M9–M11 / L6 were not started.
