# JARVIS Foundry — GPT-5.6 Sol High Hardening Report

**Prepared:** 2026-08-13 (Europe/Budapest)  
**Purpose:** evidence-backed handoff for a final independent hardening pass by
GPT-5.6 Sol at high reasoning effort.  
**Scope:** review and, where justified, repair the Hermes/DeepSeek-built V5
implementation without narrowing the original autonomous-institution vision.  
**Change policy for this report:** this inspection changed no source code, no
database, no provider, and no external system.

## 1. Executive verdict

The implementation is moving in the correct architectural direction. The
durable Foundry control plane, independent Supervisor, STOP/veto path, policy
broker, capability broker, connector registry, correlation layer, and effect
journal are now represented by real code and slice evidence. This is not yet a
truthful “V5 complete” state.

The current evidence-supported classification at inspection time is:

| Area | Evidence-backed status | What it means |
|---|---|---|
| V5-S0 through S5 | **PASS at their recorded gates** | Their artifacts and verifier records exist; historical findings and label discrepancies remain documented rather than rewritten. |
| V5-S6 implementation | **IMPLEMENTED / NOT YET CLOSED** | HEAD `e28bc8c` contains the effect driver, schema v20 migration, 17 focused tests, and suite artifact `667 EXIT=0`. |
| V5-S6 independent verification | **RUNNING when inspected** | Hermes delegation `deleg_2d03fb7f` was still active. A green builder suite is not the verifier verdict. |
| V5-S7 | **DRAFT / NOT_TESTED** | An untracked `V5_S7_WORK_PACKAGE.yaml` appeared while Hermes was running; no independent gate evidence was observed. |
| V5-S8 | **NOT_TESTED or BLOCKED per connector** | No real online credentials/provider evidence is available in the local contract; simulations must not be promoted to provider PASS. |
| V5-S9 | **NOT_STARTED** | No final engineering + operational gate, capability matrix, or V6 handoff is proven. |
| Operational readiness | **NOT_PROVEN** | The current CLI/runtime is not yet the full V4 command center described by the roadmap, and Hermes observability has known saturation/continuation risks. |

This report is a review baseline, not a gate verdict. Sol must refresh every
row from the current checkout, ledger, live runtime, and fresh tests before
repairing or promoting anything.

## 2. Sources inspected

The review used the current `JARVIS-Foundry` checkout, its architecture,
governance, planning, evidence, tests, and live-read-only Hermes artifacts. It
also used the earlier Codex session exports and the referenced project-plan
folder, including the sessions that created and audited the V5 anti-drift
pack. The most relevant session files were:

- `C:\Users\b\.codex\sessions\2026\08\11\rollout-2026-08-11T15-25-28-019ff0ff-ded1-7a12-ab53-83fa7b418de2.jsonl`
- `C:\Users\b\.codex\sessions\2026\08\12\rollout-2026-08-12T10-42-53-019ff523-861c-70f0-9945-08482431da05.jsonl`
- `C:\Users\b\.codex\sessions\2026\08\12\rollout-2026-08-12T14-44-37-019ff600-d66b-7161-9183-a5417506f683.jsonl`
- `C:\Users\b\.codex\sessions\2026\08\13\rollout-2026-08-13T08-20-10-019ff9c7-3667-7592-8d4b-7df157f3c1be.jsonl`

The referenced plan set was under:

`C:\Users\b\Documents\Codex\2026-08-11\referenced-chatgpt-conversation-this-is-an\outputs\JARVIS_FOUNDRY_V2\`

The governing repository sources included:

- `PROJECT_STATE.md`, `docs/planning/CURRENT_WORK_PACKAGE.yaml`
- `docs/planning/V5_MASTER_PROMPT.md`, `docs/planning/V5_READ_FIRST.md`
- `docs/architecture/02_ARCHITECTURE_CONTRACT.md`,
  `docs/architecture/05_POST_V1_ROADMAP.md`
- `docs/governance/CONSTITUTION.md`, `docs/governance/AUTONOMY_MANDATE.md`
- `docs/planning/REQUIREMENTS_TRACEABILITY.md`,
  `docs/planning/RISK_REGISTER.md`, `docs/planning/DECISION_LOG.md`
- V5 evidence and closed work packages for S0–S5, plus the active S6 package
- `docs/handoffs/RECOVERY_NOTES.md`, Hermes logs, delegation state, and the
  live-read-only runtime file timestamps.

If a transcript or this report conflicts with current repository/runtime
evidence, current evidence wins. A transcript never closes a gate.

## 3. V5 slice evidence at the review baseline

The following is the observed lineage. Hashes and counts are anchors for the
fresh audit, not permission to skip a recheck.

| Slice | Observed implementation / close | Independent evidence | Suite/artifact | Carry-forward |
|---|---|---|---|---|
| S0 entry | `213fb06` | V5 entry evidence | baseline/manifest | Reconcile draft vs canonical phase truth. |
| S1 systems | `8497cc2` after `8426558` repair | `deleg_d7639766`, 24 probes | suite 507; schema v19 rehearsal | STOP/frozen reconciliation and registry boundary. |
| S2 broker | `2d4ea6e` | `deleg_6c96be6d` | suite 561 | F1/F2/F3 were carried to later slices; verify one authoritative policy path. |
| S3 capabilities | `c204c91` after `02da6af` repair | `deleg_d661002c`, round-2, 69/69 + A/B | suite 604 | Historical close key says `round1`; D-038 preserves the discrepancy. |
| S4 connectors | `1627e3d` after `5876cff` repair | `deleg_761e4d85`, 83/83 | suite 634 | Declaration-based semantics and reroute/readiness boundaries. |
| S5 correlation | `3e6abbd` | `deleg_dfaec04d`, 75/75 incl. R-SEC-01 | suite 650 | Live mission wiring is explicitly deferred to S7. |
| S6 effects | `e28bc8c` | `deleg_2d03fb7f` was running at inspection | focused/full artifact `var/evidence/v5-s6-effects/suite-v5-s6.txt`, `EXIT=0`, 667 | No closed S6 evidence package was observed at that moment; provider-side proof is still unavailable. |
| S7 loop wiring | draft package only | none observed | none observed | Must prove the full observe→select→authorize→execute→reconcile→verify→learn path. |
| S8 connectors | package contract only | none observed | none observed | Each real connector needs its own provider/credential/STOP/reconcile mini-gate. |
| S9 final | not started | none observed | none observed | Requires engineering PASS and operational PASS, not only tests. |

### Important live-state note

During inspection, `C:\Users\b\AppData\Local\hermes\processes.json` was
empty while `agent.log`, the live state database, and the S6 verifier log were
being updated. This is an observability inconsistency, not evidence that
Hermes had stopped. A final operator view must derive status from durable
session/delegation evidence and health signals, not one process-list file.

## 4. Findings from earlier sessions that Sol must retain

### 4.1 Real failures that were repaired — keep the regression evidence

Earlier independent rounds found and repaired defects. Sol must not “clean up”
these protections or delete the failure artifacts:

1. S2/S3 bare system-id veto did not initially deny capability use.
2. A `NaN` budget estimate could pass a comparison gate.
3. Quoted-key JSON secrets were missed by the first redaction scan.
4. Capability use succeeded without a presented agent-run context.
5. S4 reported a registered-but-failing preferred connector as “not
   registered.”
6. Historical verifier-close labels used `round1` for a round-2 result in two
   places. D-037 and D-038 correctly require additive correction; the original
   append-only event must not be rewritten.

The correct Sol action is to rerun adversarial A/B probes and inspect the
regressions. Do not infer safety from a later green aggregate suite alone.

### 4.2 High-value review leads (not yet proven bugs)

These are concrete places where a stronger independent review should try to
disprove the implementation. They are deliberately labelled review leads,
not claims that the current code is wrong:

- **Canonical target binding in S6:** `effect_identity()` resolves a canonical
  target, then derives `target_ref` from the first raw
  `envelope.target_resources` entry. Test aliases, equivalent paths, prefixed
  system IDs, multiple targets, and connector changes. Idempotency must bind to
  the same canonical target the policy/capability layers authorized.
- **Effect-key completeness:** verify that payload identity includes every
  action semantic that can change the provider result, and excludes irrelevant
  caller noise. Check the relationship among the envelope's
  `idempotency_key`, the derived effect key, system/target identity, action
  kind/type compatibility, connector identity, and payload.
- **Partial failure reconciliation:** the current local contract can record a
  `FAILED` effect without an after-state when a `perform()` callback mutates and
  then raises. Verify that provider reconciliation cannot treat “provider
  exists” as proof when the local claimed after-state is unknown. A partial or
  ambiguous effect should be frozen/reconciled honestly, not silently marked
  confirmed.
- **Exception boundary:** review the S6 `BaseException` catch. Process-level
  interrupts and cancellation must not be converted into a false ordinary
  provider failure or leave a misleading terminal journal state.
- **Journal concurrency:** race two deliveries, two crash reapers, and a
  freeze/reconcile call against the same key. Prove SQLite transaction,
  uniqueness, event idempotency, and projection status remain monotonic under
  concurrency, not only sequential tests.
- **Compensation authority:** compensation is append-only, but its reference,
  capability, target, and authorization must still be validated. A free-form
  compensation record must not become a bypass around STOP/veto or the broker.
- **One policy path:** audit the old `ActionPolicy`/system checks and the S2
  `PolicyBroker` for bypasses. There must be one authoritative decision path
  before an effect can execute.
- **Naming compatibility:** the architecture contract calls the field
  `action_type` while the implementation uses `action_kind`. Establish one
  canonical serialized contract or a fully tested compatibility adapter; do
  not leave two subtly different policy paths.
- **Allowance threat model:** `require_allowance()` relies on the persisted
  decision ticket. Confirm the intended single-writer trust boundary, direct
  database insertion resistance where required, and fail-closed behaviour for
  stale, conflicting, revoked, or mismatched tickets.
- **Declared connector semantics:** a connector declaration must not be able
  to label a mutating action as read-only and thereby bypass consequence
  gates. Test the contract from the action implementation and not only its
  metadata.

## 5. Current handoff and documentation drift risks

The project has a strong anti-drift plan, but several documents are snapshots
and must be reconciled rather than blindly followed:

- `docs/handoffs/RECOVERY_NOTES.md` still describes HEAD `d0ee0dc`, S5 as
  running, and S6 as “next,” while the checkout had advanced to S6 commit
  `e28bc8c` and an S7 draft appeared.
- `docs/planning/V5_SLICE_MANIFEST.md` retains an old baseline narrative even
  though its later entries cover newer V5 slices.
- `PROJECT_STATE.md` and `docs/planning/CURRENT_WORK_PACKAGE.yaml` still name
  V4 as the canonical active package. The V5 master prompt intentionally says
  not to rewrite canonical phase truth prematurely. Sol must perform the final
  reconciliation at the correct evidence gate, not make the draft look active.
- Untracked owner briefs (`V5_MASTER_PROMPT.md`, `V5_READ_FIRST.md`) and the
  untracked S7 draft belong to the active work and must be preserved. Never
  reset, clean, restore, stash, or overwrite them.
- V5 S3/S5 and older V4 close labels have known round/artifact-reference
  discrepancies. Preserve history and record additive corrections.

## 6. Hermes/session findings that affect final hardening

Earlier overnight and loop reviews produced important operational evidence:

- The overnight interruption correlated with a planned Windows Update
  `MoUsoCoreWorker.exe` restart; Hermes' unclean-exit record did not indicate
  suspected OOM. Hermes was not proven to be the root source-code crash.
- The large Hermes session itself became a renderer risk (hundreds of
  messages/tool calls/API calls). The remedy is bounded sessions, durable
  checkpoints, and resumable handoffs—not an assumption that a long context is
  durable state.
- The “9 loops” were monitoring cycles, not nine jobs. They honestly stayed
  `WAITING` because the top candidate score was 0.25 below the 0.30 threshold;
  no worker was dispatched. Two command timeouts did not stop the loop. The
  operator interface must make `WAITING`, `BUILDING`, `TESTING`, `VERIFYING`,
  `BLOCKED`, and `MONITORING` visibly different.
- Current Hermes logs also showed upstream 503s/empty streams and blocked or
  malformed tool requests. Retry/backoff and provider unavailability must be
  visible and must never silently substitute a different internal model under
  the DeepSeek-only routing decision.

## 7. Hardening priorities for Sol

### P0 — control, security, and irreversible effects

1. Prove STOP, veto, capability revocation, and emergency-stop behaviour for
   queued, in-flight, crashed, retried, and reconciled actions. The control
   path must work when Hermes, the UI, the model, or the network is down.
2. Prove that retrieved text, connector content, notes, summaries, and model
   output cannot create owner directives, remove vetoes, or elevate authority.
3. Prove one deterministic policy/allowance path; reject stale, mismatched,
   forged, non-finite, or duplicated tickets.
4. Run repository/log/prompt/report secret scans, including quoted JSON and
   exception paths. Do not include credential values in a test artifact.
5. Finish S6 adversarially: canonical target identity, complete effect-key
   semantics, exactly-once/retry/crash behaviour, partial/ambiguous outcomes,
   monotonic journal transitions, compensation authority, and provider
   reconciliation. Provider-side evidence must stay `NOT_TESTED`/`BLOCKED`
   when no real provider is present.

### P0 — S7 integration

6. Wire only the gate-passed services into one observable mission loop:
   observe → select → envelope → authorize → capability → execute → reconcile
   → verify → learn. Search for alternate direct execution paths.
7. Prove connector-outage isolation, queued/in-flight STOP, interruption and
   resume, no duplicate mission/effect, identity transparency, cost/consequence
   recording, and fail-closed external/financial paths.

### P1 — durable truth and migrations

8. Rehearse v19→v20 and all relevant replay, rebuild, and reconciliation
   paths on disposable copies, including old-schema data, malformed events,
   duplicate events, crash points, and concurrent writers.
9. Verify projection drift, event immutability, correction events, sequence
   ordering, idempotency conflicts, and no destructive historical relabeling.
10. Reconcile stale handoffs, `PROJECT_STATE`, `CURRENT_WORK_PACKAGE`, slice
    manifest, evidence hashes, and V5/V6 transition records only at their
    proper gates.

### P1 — operator truth and resilience

11. Make the CLI/command-center facts usable: current mission, worker,
    evidence, health, resource, cost, budget, provider, STOP/veto, and next
    action; show why a loop is waiting and its threshold. A loop count is not a
    job count.
12. Test restart/recovery after Windows interruption, Hermes renderer/session
    saturation, worker timeout, API 503, empty stream, malformed tool result,
    and stale delegation. Durable state must resume without duplicate work.
13. Ensure every report has a bounded size, exact artifact paths/hashes, and a
    visible distinction between building, testing, verifying, waiting, and
    monitoring.

### P1 — provider and operational honesty

14. For every connector actually available, run an isolated mini-gate for
    identity, capabilities, credentials, policy, health, quota/cost,
    before/after state, provider evidence, retries, reconciliation, STOP, and
    rollback. For every unavailable connector, record a resumable blocker;
    never use a simulation to claim online readiness.
15. Do not call V5 operationally PASS until the required observation window,
    real loop evidence, and provider limitations are explicit. Engineering
    green is not operational PASS.

### P2 — final polish

16. Improve type/contract consistency, error messages, package boundaries,
    structured evidence, documentation cross-links, and test determinism only
    after P0/P1 invariants are proven. “Optimization” must not remove a guard,
    lower a threshold, or hide an unresolved limitation.

## 8. Required Sol gate discipline

For each finding, use exactly one of these classifications:

- `CONFIRMED` — fresh evidence reproduces it;
- `REPAIRED` — exact cause fixed and regression plus fresh verifier pass;
- `NOT_REPRODUCED` — the adversarial probe did not reproduce it, with commands
  and artifacts recorded;
- `NOT_TESTED` — the required proof did not run completely;
- `BLOCKED` — a genuine unavailable dependency prevents the proof, with a
  precise resume condition.

The final V5 verdict must be one of `PASS`, `FAIL`, `BLOCKED`, or
`NOT_TESTED`. V5 closes only when both engineering and operational gates are
`PASS`, all mandatory exit evidence exists, no severity-one issue remains, and
an independent verifier agrees. S8 provider limitations may remain honest
`NOT_TESTED`/`BLOCKED`; they cannot be silently upgraded.

## 9. Definition of a successful handoff

Sol should leave:

1. a frozen baseline and exact diff manifest;
2. focused regression tests for every repaired issue;
3. full-suite, migration/replay/rebuild, STOP/veto, secret-scan, and
   end-to-end loop artifacts;
4. a fresh independent adversarial verifier result (with any same-model or
   provider-diversity limitation stated);
5. a reconciled V5 capability/readiness matrix;
6. a final report naming unresolved limitations and their resume conditions;
7. a V6 handoff only if V5 truly passes — never V6 implementation in this
   hardening session.

The north star remains a durable autonomous digital organization. The final
pass may harden, simplify, or correct the implementation, but it must not
turn that institution into a collection of demos, one endless chat, a fake
provider catalogue, or an approval queue.

