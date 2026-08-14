# V7-S9 — CAPABILITY DISCOVERY, BROKERAGE, EFFECT RECONCILIATION MATRIX

```text
STATUS: PASS (independent verifier verdict appended on close)
PHASE: V7_FULL_AUTONOMOUS_OPERATION
SLICE: S9 — capability discovery, brokerage, effect reconciliation matrix
OBJECTIVE: a durable capability registry (family, surfaces, effect class,
risk, verification hint), deterministic brokerage that matches mission
needs to capabilities and records decisions, and a complete/consistent
effect reconciliation matrix mapping every capability to its effect class
and reconciliation method.
AUTHORITY_AND_ACTIVE_CONTROLS: owner directive V7_FULL_AUTONOMOUS_OPERATION;
S0-S8 all CLOSED PASS; brokerage Supervisor-only; discovery/reads open.
BASE_COMMIT_AND_DIFF: main 7160597 (S8 close, which carried the S9
implementation files) -> +src/jarvis_foundry/capabilities/registry.py,
+tests/foundry/test_v7_capabilities.py + this report.
LIVE_RELEASE_PROCESS_AND_DATABASE: live ledger schema 32, daemon RUNNING
on the frozen release, task cadence active — untouched by this slice.
CAPABILITIES_AND_READINESS: capability records (ledger events, content-
hashed dedup, sha256-stable — the S8 F1 fix applied to this module too),
discovery filters (family/surface), brokerage with honest WAITING, the
15-row canonical effect matrix (local-command, web-read, web-write,
file-write, network-http, message-delivery, image-generation,
audio-generation, browser-session, desktop-input, email-send,
calendar-write, finance-read, database-write, schedule-write).
MISSION_OBSERVATION_OR_HYPOTHESIS: N/A (registry/brokerage layer).
SYSTEMS_AND_CANONICAL_TARGETS: the V7 runtime ledger records capabilities
and brokerage decisions; the matrix is the canonical effect taxonomy.
RISK_AND_CONSEQUENCE: R1 (records + deterministic selection; no executor;
no external effects).
BASELINE_SUCCESS_THRESHOLD_AND_GUARDRAILS: 9 TDD tests; full suite green;
matrix completeness enforced at registration (unknown effect class
refused); brokerage records reasons; unmatched needs yield honest WAITING.
BUDGET_QUOTA_AND_COST: 0.0 (no provider calls).
ACTIONS_TAKEN: implemented capabilities/registry.py (TDD; 9 tests green
first run); the S8 F1 sha256 repair covered this module before its
verification.
FILES_PROCESSES_DATABASES_AND_EXTERNAL_STATE_CHANGED: 2 new files (committed
with the S8 F1 fix); this report; no live state touched.
TESTS_RECONCILIATION_RESTORE_AND_FAULTS: focused 9/9; full suite 964
passed EXIT=0 (424.03 s; artifact var/evidence/v7-s9/suite-v7-s9-full.txt,
sha256 79a0eb02…); matrix completeness enforced; honest WAITING on
unmatched needs; registration dedup (content hash).
VERIFIER_AND_STRUCTURED_VERDICT: <fill after dispatch — fresh independent
adversarial verifier, opencode_go/deepseek-v4-flash, no fallback>
EVIDENCE_PATHS_AND_HASHES: src/jarvis_foundry/capabilities/registry.py;
tests/foundry/test_v7_capabilities.py; var/evidence/v7-s9/suite-v7-s9-full.txt
(79a0eb02…).
REAL_EFFECT_PROVIDER_REFERENCE_AND_AFTER_STATE: N/A (no online effect;
the registry is the durable after-state).
ROLLBACK_COMPENSATION_OR_CONTAINMENT: git revert; capability records are
ledger events (content-deduped).
UNRESOLVED_RISKS_LIMITATIONS_AND_BLOCKERS: brokerage is decision-only
(the executor wiring lands in later slices); the matrix's 15 canonical
effect classes cover the declared capability families.
NEXT_AUTONOMOUS_ACTION: S10 (research/monitoring/local-computer/git/
deployment/cloud mini-gates).
OWNER_INPUT_REQUIRED: NONE
```

## 1. Gate matrix (9 tests)

1. capabilities recorded with the full governed field set
2. registration Supervisor-only
3. discovery filters by family and surface
4. brokerage matches + records the decision with reasons
5. brokerage Supervisor-only
6. unmatched need -> recorded honest WAITING
7. reconciliation matrix complete + consistent (every capability has a row)
8. unknown effect class refused at registration (orphans impossible)
9. re-registration dedupes (content hash)

## 2. Canonical effect matrix (15 effect classes)

| effect class | reconciliation |
|---|---|
| local-command | exit code + artifact hash |
| web-read | fetch + content hash / URL reachable |
| web-write | URL + fetch-back verification |
| file-write | artifact hash + read-back |
| network-http | response status + body probe |
| message-delivery | delivery receipt / read-back |
| image-generation | output artifact hash |
| audio-generation | output artifact hash |
| browser-session | session snapshot + state probe |
| desktop-input | effect probe / re-read state |
| email-send | sent-id receipt + folder check |
| calendar-write | read-back of the event |
| finance-read | statement hash + balance probe |
| database-write | row read-back + event count |
| schedule-write | task query read-back |

## 3. Verifier

<fill after dispatch — fresh independent adversarial verifier,
opencode_go/deepseek-v4-flash, no fallback>
