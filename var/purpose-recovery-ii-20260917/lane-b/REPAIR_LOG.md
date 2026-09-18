# LANE B — REPAIR LOG (runtime / operations)

Base: `ddec34efc96b9a0154eb9821d6cc496ef76906a3` (tree `7c8b4e5c4915fb411a55cbe1aa852d6886129a91`)
Worktree: `C:\temp\JARVIS\purpose-recovery-ii-b-runtime-operations-20260917`
Branch: `candidate/purpose-recovery-ii-b-runtime-operations-20260917`
Live JARVIS / `main` / origin: NOT touched.

Each repair below was found by tracing the runtime chain (mission intent →
capability resolution → input binding → readiness → execution → effect →
readback → verification → continuation), not by reading a report.

## R1 — mission intent had to name the recipe (B1/B3, root gap)

**Symptom.** A selected mission could reach a workflow only if the caller put
`recipe_id` + a fully formed `recipe_params` block into the objective
metadata; `cap.workflow.run` was added to the required capabilities *only* when
a recipe id was present. The canonical Fabric intent resolver
(`TaskIntent` / `select_capability` / `run_intent`) existed but was exercised
only by tests, so capability infrastructure worked "when a developer names it".
Unsupported intent fell through to prose plan/execute contracts instead of
failing honestly.

**Repair.**
- `src/jarvis_foundry/mission/intent.py` (new): normalized `MissionIntent`
  (objective, needs, role-keyed inputs, success criteria, evidence refs),
  the bounded need vocabulary, and `resolve_mission_intent()` — a deterministic
  binder that returns `WORKFLOW` (registry-selected recipe + bound params),
  `ACTIONS` (bounded action plan), `GAP` (durable capability gap) or `BLOCKED`
  (missing supplied inputs). Candidate ranking is declared-need coverage, then
  smallest extra-need footprint, then recipe id; every considered candidate is
  recorded in the resolution provenance.
- `src/jarvis_foundry/fabric/recipes/model.py` + `builtins.py` +
  `registry.py`: recipes declare the normalized `needs` they satisfy and the
  input role each parameter binds to (`bind`), so binding is declared data.
- `executive/portfolio_bridge.py`: owner-goal materialization resolves the
  intent when no recipe id is supplied, binds the Catalog-proven capability
  onto the resource, and materializes either the workflow envelope or the
  action plan. An unrealizable intent records `portfolio.intent_unresolved`
  and does not dispatch.
- `executive/autonomy.py`: the intent block travels with the work item
  (`generate_missions_for_objective`), so resolution happens at the
  materialization boundary rather than in the caller.

**Proof.** `UNSCRIPTED_RUNTIME_PROOF.json` — the harness supplied only a
high-level GOAL plus needs/role inputs; the runtime selected
`v14.digital_operator.close`, bound 19 parameters, and completed the chain.

## R2 — a freshly registered resource could never become eligible

**Symptom.** `run_autonomy_cycle` produced "no objective selected"; ranking
reported `write_registered_worktree_files:UNKNOWN`. Registration declares the
capability readiness (`inspect_git_resource`), but nothing projected it into
the durable capability plane, where a missing record is UNKNOWN/not-ready. The
prior lane worked around this by calling `set_capability_state` from the
scenario driver — i.e. a developer step on the normal path.

**Repair.** `control/capabilities.capability_state_recorded()` +
`control/registration.sync_registered_resource_capabilities()`, called once per
Executive cycle before ranking. Only the FIRST record per (resource,
capability) is written: later control-plane transitions own the state and are
never overwritten by re-projection.

## R3 — browser session expiry was unrecoverable in normal dispatch

**Symptom.** The canonical reauthentication seam existed in the browser
connector and in `ExecutorContext`, but `try_fabric_dispatch` /
`FabricRoleAdapter` never carried a reauth provider, so a mission step that met
an expired session could only fail.

**Repair.** `fabric/adapter.py`: `browser_reauth_provider` is plumbed through
the Fabric adapter into the executor context. Evidence that the injection is
real (`FAILURE_REPAIR_PROOF.json`): the same canonical action with the seam
disabled refuses with `claimed: BLOCKED`, `error_code: session_expired`,
`session.state: EXPIRED`, `reason: session expiry URL observed`.

## R4 — post-recovery read receipt described the pre-recovery page

**Symptom.** After a successful reauth, the receipt kept the first
navigation's `final_url` (the login redirect) even though the document came
from the authenticated page — a receipt that disagrees with its own artifact
(`Source: .../login` for a dashboard document).

**Repair.** `fabric/executors.browser_read`: when the first navigation ran
against a non-authenticated session and recovery succeeded, the requested
target is read again on the recovered session so the receipt and the document
describe the same page. Verified by the artifact-source check in both proofs.

## R5 — an unrealizable intent left a stranded mission (B10 continuation)

**Symptom.** When intent resolution returned GAP/BLOCKED, the mission row
created by the materialization boundary stayed in READY with no contract; it
blocked portfolio capacity (`active_missions: 1`) and could never resume, so
the objective could not continue after the capability appeared.

**Repair.** `executive/portfolio_bridge._record_unrealizable_intent()`: the
attempt is recorded as failed on the plan step (with the missing `cap.*`
requirements attached, so the canonical gap/WAIT/expansion bridge owns them),
the created mission is advanced to BLOCKED with an explicit resume
instruction, and the Executive's bounded retry is what resumes the same
objective once the capability exists.

**Proof.** `SELF_EXPANSION_PROOF.json` — the same objective resumed after the
runtime acquired `cap.hash.file.sha256` and the mission bound the acquired
capability.

## R6 — a model was asked to judge what the runtime can recompute (B2/B10)

**Symptom.** The Fabric spine independently re-derived a deterministic local
action's effect (`hash.file.sha256` recomputed and matched, receipt journaled as
`fabric.spine.result` with `state: VERIFIED`), but the mission's own verifier
step for that step was dispatched to a *worker role*: `resolve_general_work_dispatch`
only grants verifier replay for a contract whose `source` is `v13-general-work`,
so an intent-resolved action plan fell through to the model adapter. The
verdict came back FAIL with the finding *"model PASS is not backed by a
JARVIS-owned artifact predicate"* and the mission ended BLOCKED although the
effect was proven. `SELF_EXPANSION_PROOF.json` recorded it as an open gap
rather than a success, which is why the gap was visible at all.

**Repair.**
- `fabric/loop.canonical_predicate_verdict()` exposes the *exact* predicate the
  spine applies to an action (Factory artifact replay when the capability is a
  canonical adapter, otherwise the action verifier plus declared
  postconditions).
- `fabric/adapter.canonical_verifier_dispatch()` answers a verifier session for
  a step whose contract capability resolves to a Fabric action kind by
  re-running that predicate over the step's recorded `fabric.spine.result`
  receipt. It reads only the recorded *observation* — the receipt's own
  `verified`/`success` fields are never trusted — requires the recorded subject
  to still name the declared subject, records
  `fabric.canonical_verification_observed` with the effect/receipt lineage, and
  **declines** (`None`) when the step has no receipt, the predicate is undecided
  (UNKNOWN), or the subject diverged, leaving the judgment path untouched.
- `mission/loop` accepts a re-derived JARVIS predicate as the artifact predicate
  the acceptance rule demands (the rule is not weakened: it still refuses a
  model verdict, including a model verdict carrying the marker text) and records
  `verifier_answered_by` next to the frozen binding's routed provider, because
  the binding is frozen before the session and cannot name the answerer.

**Proof.** `VERIFIER_ROUTING_REPAIR_PROOF.json` — A/B discrimination on the same
flow: with the seam patched out of the module namespace (pre-repair behaviour)
the mission is BLOCKED with the predicate refusal; with the seam present the
same flow is PASSED and the verdict carries
`verification:canonical-fabric-predicate` / `verifier_answered_by: fabric`.
`SELF_EXPANSION_PROOF.json` re-ran at the repaired head: 9/9 checks, the resumed
mission now PASSED, and its `repair_lineage` reads the pre-repair receipt back
from Git (commit `5c78b2a`, BLOCKED + two FAIL verdicts) instead of retyping it.

## R7 — a resumed pass whose frozen binding drifted crashed the loop (B16)

**Symptom.** Found by the R6 regression work, not by reading a report. The
verdict identity is content-addressed from the *pass contract* (mission, step,
builder run, verifier run, contract digest) and does not include the working
tree, while the frozen `verification.started` payload does (`diff_hash`,
`artifact_hashes`, `head`). A pass interrupted after the binding was frozen and
resumed after anything in the working root changed therefore tried to re-freeze
a different binding under the same identity; the ledger refused it with
`EventConflict` and the exception escaped `MissionLoop`.
`src/jarvis_foundry/mission/verifier_binding.py` is byte-identical to the lane
base `ddec34e`, so this was **pre-existing**, not a regression.

**Repair.** `mission/loop` catches the ledger's refusal at the capture site and
refuses honestly: a BLOCKED verdict carrying the ledger's own reason, the
mission advanced to BLOCKED, and the resume left to a fresh attempt (a fresh
builder attempt derives a fresh identity, so the path stays resumable). No
identity derivation, gate check or shared signature was changed.

**Evidence of the un-repaired path's own refusal.**
`probe/VERIFIER_BINDING_DRIFT_PROBE.before.json` (unhandled
`EventConflict: idempotency conflict for verification.started:verdict-v2-…`)
versus `.after.json` (`BLOCKED`, reason *"the frozen verification binding changed
while a verification pass was open"*).

## Verification

- `tests/foundry/test_purpose_recovery_ii_b_intent_binding.py` — 8 tests
  (selection from needs only, role→parameter binding, missing inputs →
  BLOCKED, unknown need vocabulary, durable gap, gap→acquire→action-plan
  resume, end-to-end owner-goal materialization without a recipe id,
  gap-blocked materialization with a durable unresolved record).
- Focused regression batch (v14_v16_convergence, v14_workflow_composition,
  v12_campaign_runtime, effects, lane tests): 89 passed, 0 failed.
- `UNSCRIPTED_RUNTIME_PROOF.json`: 11/11 checks true.
- `FAILURE_REPAIR_PROOF.json`: 8/8 checks true.
- `SELF_EXPANSION_PROOF.json`: 8/8 checks true.

No repair weakens a fail-closed rule: unsupported intents, missing inputs,
unregistered capabilities, out-of-scope targets and missing scoped fields all
still refuse, and no acceptance criterion was relaxed to obtain a PASS.
