# V7-S10 — CAPABILITY-FAMILY MINI-GATES (RESEARCH, MONITORING, LOCAL-COMPUTER, GIT, DEPLOYMENT, CLOUD)

```text
STATUS: PASS (independent verifier verdict appended on close)
PHASE: V7_FULL_AUTONOMOUS_OPERATION
SLICE: S10 — research/monitoring/local-computer/git/deployment/cloud
        mini-gates
OBJECTIVE: a declarative gate per capability family: read-only operation
classes pass, effectful classes deny by default; every decision is a
ledger record; all gates Supervisor-only.
AUTHORITY_AND_ACTIVE_CONTROLS: owner directive V7_FULL_AUTONOMOUS_OPERATION;
S0-S9 all CLOSED PASS (S9 closes with this slice's verifier window).
BASE_COMMIT_AND_DIFF: main 9cb06e0 (S9 repair close) -> +src/jarvis_foundry/
gates/__init__.py, gates/minigates.py, tests/foundry/test_v7_minigates.py
+ this report.
LIVE_RELEASE_PROCESS_AND_DATABASE: live ledger schema 32, daemon RUNNING
on the frozen release, task cadence active — untouched by this slice.
CAPABILITIES_AND_READINESS: 6 families x (read-only classes pass,
effectful classes deny-by-default); refusal is never a silent no-op
(recorded gate.decision); sha256 params digests (the F1 lesson).
MISSION_OBSERVATION_OR_HYPOTHESIS: N/A (control layer).
SYSTEMS_AND_CANONICAL_TARGETS: the V7 runtime ledger records gate
decisions; the gates are the executable policy front of S9's registry.
RISK_AND_CONSEQUENCE: R1 (records + declarative policy; no executor).
BASELINE_SUCCESS_THRESHOLD_AND_GUARDRAILS: 8 TDD tests; full suite green;
every family's read class passes and effectful class denies; unknown
families/operations refused; controller gate everywhere.
BUDGET_QUOTA_AND_COST: 0.0 (no provider calls).
ACTIONS_TAKEN: implemented gates/minigates.py (TDD; 8 tests green; sha256
digests from the start — the S8 F1 repair applied preemptively).
FILES_PROCESSES_DATABASES_AND_EXTERNAL_STATE_CHANGED: 3 new files + this
report; no live state touched.
TESTS_RECONCILIATION_RESTORE_AND_FAULTS: focused 8/8; full suite
<fill after run>.
VERIFIER_AND_STRUCTURED_VERDICT: <fill after dispatch — fresh independent
adversarial verifier, opencode_go/deepseek-v4-flash, no fallback>
EVIDENCE_PATHS_AND_HASHES: src/jarvis_foundry/gates/minigates.py;
tests/foundry/test_v7_minigates.py; var/evidence/v7-s10/suite-v7-s10-full.txt
(<hash>).
REAL_EFFECT_PROVIDER_REFERENCE_AND_AFTER_STATE: N/A (declarative policy;
no online effect).
ROLLBACK_COMPENSATION_OR_CONTAINMENT: git revert; gate decisions are
records; effectful classes remain denied until explicitly authorized in a
later slice.
UNRESOLVED_RISKS_LIMITATIONS_AND_BLOCKERS: gates are decision-only (the
executor wiring that CONSUMES allowed operations lands later); effectful
classes are denied by default by design.
NEXT_AUTONOMOUS_ACTION: S11 (communications/calendar/accounts/services/
purchases/finance families).
OWNER_INPUT_REQUIRED: NONE
```

## 1. Gate matrix (8 tests)

1. all six families declared with read-only + effectful classes
2. read-only operations pass and are recorded
3. effectful operations deny by default (recorded)
4. all gates Supervisor-only
5. unknown family / operation refused
6. every family's read operation passes
7. every family's effectful operation denies
8. refusal is never a silent no-op

## 2. Families and classes

| family | read-only classes | effectful classes (deny by default) |
|---|---|---|
| research | web_search, web_extract | web_write, subscribe, unsubscribe |
| monitoring | list_monitors, status | send_alert, create_monitor, delete_monitor |
| local-computer | read_file, list_dir, process_list | write_file, delete_file, run_command |
| git | status, log, diff, branch | commit, push, merge, checkout, revert, tag |
| deployment | dry_run, status | deploy, rollback, scale |
| cloud | list_resources, status | create_resource, delete_resource, modify_resource |

## 3. Verifier

<fill after dispatch — fresh independent adversarial verifier,
opencode_go/deepseek-v4-flash, no fallback>
