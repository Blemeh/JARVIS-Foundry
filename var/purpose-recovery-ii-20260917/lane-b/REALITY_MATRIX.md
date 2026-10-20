# LANE B REALITY MATRIX — runtime / operations

Worktree: `C:\temp\JARVIS\purpose-recovery-ii-b-runtime-operations-20260917`  
Branch: `candidate/purpose-recovery-ii-b-runtime-operations-20260917`  
Base: `ddec34efc96b9a0154eb9821d6cc496ef76906a3`

Classification measured on base ddec34efc96b before Lane B repairs. The final classification after repair is in 'final_classification'.

| Domain | Start | Final | Root gap | Repair | Acceptance |
| --- | --- | --- | --- | --- | --- |
| B1 mission-creates-capability-binding | REAL_BUT_MANUAL | REAL_AUTONOMOUS | Capability binding worked only when the harness named the recipe (metadata.recipe_id) and hand-built recipe_params; cap.workflow.run was added to required capabilities only when recipe_id was present; the canonical Fabric intent resolver (TaskIntent/select_capability/run_intent) was exercised only by tests, never by the mission path. | mission/intent.py: normalized MissionIntent + deterministic need->workflow/action resolver with role-based input binding and honest GAP; recipes declare satisfied needs and parameter bind roles; portfolio materialization resolves intents when no recipe_id is supplied. | UNSCRIPTED_RUNTIME_PROOF.json, focused tests 8/8, VERIFIER_ROUTING_REPAIR_PROOF.json (R6) |
| B2 workflow-run-product-runtime | WIRED_BUT_UNPROVEN | REAL_AUTONOMOUS | Normal dispatch worked only with a harness-authored recipe envelope; campaign-child reuse was proven, but a normal mission could not reach it without a developer naming the recipe. | resolver-selected recipe envelopes (B1); focused tests for binding + failure policy | UNSCRIPTED_RUNTIME_PROOF.json + CAMPAIGN_CONTINUITY_PROOF.json + VERIFIER_ROUTING_REPAIR_PROOF.json (R6) |
| B3 digital-operator-selection-seam | REAL_BUT_MANUAL | REAL_AUTONOMOUS | selection required a recipe id; no normalized need vocabulary existed | need vocabulary + recipe need declarations + deterministic resolution | UNSCRIPTED_RUNTIME_PROOF.json (recipe selected from needs only) |
| B4 browser-runtime | REAL_AUTONOMOUS | REAL_AUTONOMOUS | none at base; re-verify from a resolved mission intent | none expected; re-proof | UNSCRIPTED_RUNTIME_PROOF.json + FAILURE_REPAIR_PROOF.json (expiry injection) |
| B5 windows-native-operator | REAL_AUTONOMOUS | REAL_AUTONOMOUS | none at base; re-verify from a resolved mission intent | none expected; re-proof | UNSCRIPTED_RUNTIME_PROOF.json (native Word assert leg) |
| B6 artifact-general-work | REAL_AUTONOMOUS | REAL_AUTONOMOUS | none at base; re-verify from a resolved mission intent | none expected; re-proof | UNSCRIPTED_RUNTIME_PROOF.json (artifact written + read back) |
| B7 cross-system-composition | REAL_BUT_MANUAL | REAL_AUTONOMOUS | chain required a developer-named recipe and developer-built params | B1 resolution; re-proof the chain from an intent | UNSCRIPTED_RUNTIME_PROOF.json (browser->artifact->native->git) |
| B8 effect-truth-exactly-once | REAL_AUTONOMOUS | REAL_AUTONOMOUS | none at base; re-verify under crash/restart in this lane | none expected; re-proof | UNSCRIPTED_RUNTIME_PROOF.json + FAILURE_REPAIR_PROOF.json duplicate counters 0 |
| B9 git-github-effects | REAL_AUTONOMOUS | REAL_AUTONOMOUS | none at base; re-verify; GitHub write stays owner-config-gated | none expected; re-proof | UNSCRIPTED_RUNTIME_PROOF.json (disposable remote SHA equality) |
| B10 missionloop-repair | REAL_AUTONOMOUS | REAL_AUTONOMOUS | none at base; re-verify with an injected real failure | none expected; re-proof | FAILURE_REPAIR_PROOF.json + CAMPAIGN_CONTINUITY_PROOF.json (failure_repaired_verified) + VERIFIER_ROUTING_REPAIR_PROOF.json (R6) |
| B11 campaigns-long-horizon | REAL_AUTONOMOUS | REAL_AUTONOMOUS | none at base; re-run in this lane with a process kill | none expected; re-proof | CAMPAIGN_CONTINUITY_PROOF.json (process loss continue-once, duplicates 0) |
| B12 campaign-child-workflow-run | REAL_AUTONOMOUS | REAL_AUTONOMOUS | none at base (repaired 2026-09-16); re-verify | none expected; re-proof | CAMPAIGN_CONTINUITY_PROOF.json (campaign_child_workflow_passed) |
| B13 real-capability-gap-detection | WIRED_BUT_UNPROVEN | REAL_AUTONOMOUS | gap detection existed, but the intent->capability path never produced a natural gap from a high-level mission; gap creation was a developer action in prior proofs. | intent resolution emits a GAP resolution with lineage + resume target | SELF_EXPANSION_PROOF.json (gap from a mission requirement) |
| B14 self-expansion-mcp | REAL_AUTONOMOUS | REAL_AUTONOMOUS | none at base; re-prove from a naturally detected gap | none expected; re-proof | SELF_EXPANSION_PROOF.json (acquire -> verified -> same objective resumes) |
| B15 provider-resilience | REAL_AUTONOMOUS | REAL_AUTONOMOUS | none at base; re-verify through the existing test seam | none expected; re-proof | PROVIDER_STOP_PROOF.json (26 provider/stop tests green) |
| B16 process-restart-durability | REAL_AUTONOMOUS | REAL_AUTONOMOUS | none at base; re-verify in this lane | none expected; re-proof | CAMPAIGN_CONTINUITY_PROOF.json + PROVIDER_STOP_PROOF.json + VERIFIER_ROUTING_REPAIR_PROOF.json (R7 binding drift) |
| B17 stop-pause-resume | REAL_AUTONOMOUS | REAL_AUTONOMOUS | none at base; re-verify | none expected; re-proof | PROVIDER_STOP_PROOF.json (scenario E 12/12) |
| B18 command-center-runtime-truth | WIRED_BUT_UNPROVEN | REAL_AUTONOMOUS | needs verification that every required runtime fact is projected from real candidate data | recorded per verification result | COMMAND_CENTER_PROOF.md (real candidate data, 8/8) + 34 CC tests |
| B19 notifications-briefings | REAL_BUT_MANUAL | REAL_AUTONOMOUS | native winotify toast was never exercised (package not installed) | install the optional backend in the lane venv and prove a real OS toast, or record the honest environment limit | in-app toast/dedupe/preference proven; native winotify toast NOT_TESTED (backend absent) |

## Full rows

### B1 mission-creates-capability-binding

- Required product behavior: A selected mission/action intent resolves itself into the required capability or workflow without a developer or owner naming a recipe, with inputs bound from the mission and evidence, readiness checked, and honest failure when the intent cannot be realized.
- Start classification: `REAL_BUT_MANUAL`
- Final classification: `REAL_AUTONOMOUS`
- Source components: `executive/portfolio_bridge.materialize_portfolio_selection`, `executive/autonomy.generate_missions_for_objective`, `mission/contracts.MissionStepContract`, `fabric/loop.select_capability (TaskIntent)`, `fabric/recipes/registry`
- Normal runtime entrypoint: OwnerControlService GOAL -> executive.sync_owner_goals -> run_autonomy_cycle -> portfolio materialization -> MissionLoop
- Upstream intent contract: objective metadata required recipe_id + recipe_params before this lane; no normalized intent contract existed
- Real effect: n/a (binding layer)
- Readback: mission_step_contracts rows
- Verification: mission loop verifier per contract; a step whose capability resolves to a Fabric action kind is certified by the runtime's own re-derived predicate (R6), never by a model verdict
- Recovery: materialization blocked reason recorded
- Root gap: Capability binding worked only when the harness named the recipe (metadata.recipe_id) and hand-built recipe_params; cap.workflow.run was added to required capabilities only when recipe_id was present; the canonical Fabric intent resolver (TaskIntent/select_capability/run_intent) was exercised only by tests, never by the mission path.
- Repair: mission/intent.py: normalized MissionIntent + deterministic need->workflow/action resolver with role-based input binding and honest GAP; recipes declare satisfied needs and parameter bind roles; portfolio materialization resolves intents when no recipe_id is supplied.
- Acceptance: UNSCRIPTED_RUNTIME_PROOF.json, focused tests 8/8, VERIFIER_ROUTING_REPAIR_PROOF.json (R6)

### B2 workflow-run-product-runtime

- Required product behavior: workflow.run is invoked by normal mission dispatch (not a callable API only), with typed parameter binding, checkpoint/resume, nested receipt verification, failure policy, no duplicate effects after restart.
- Start classification: `WIRED_BUT_UNPROVEN`
- Final classification: `REAL_AUTONOMOUS`
- Source components: `fabric/executors.workflow_run`, `fabric/recipes/runner.run_recipe`, `fabric/recipes/checkpoint.py`, `fabric/recipes/preflight.resolve_params`
- Normal runtime entrypoint: MissionLoop step contract capability cap.workflow.run
- Upstream intent contract: MissionStepContract.capability + instructions envelope
- Real effect: recipe step effects (browser, files, native, git)
- Readback: nested receipts + effect journal
- Verification: fabric verify_action workflow.run: nested receipts all VERIFIED; deterministic nested steps are re-derived by the runtime predicate (R6)
- Recovery: recipe checkpoint + job journal
- Root gap: Normal dispatch worked only with a harness-authored recipe envelope; campaign-child reuse was proven, but a normal mission could not reach it without a developer naming the recipe.
- Repair: resolver-selected recipe envelopes (B1); focused tests for binding + failure policy
- Acceptance: UNSCRIPTED_RUNTIME_PROOF.json + CAMPAIGN_CONTINUITY_PROOF.json + VERIFIER_ROUTING_REPAIR_PROOF.json (R6)

### B3 digital-operator-selection-seam

- Required product behavior: Normalized needs (read web, authenticated web, artifact, native verify, spreadsheet, local app, repository preserve) select the correct existing digital capability/workflow without exposing browser/UIA/Git internals to owner-level goals.
- Start classification: `REAL_BUT_MANUAL`
- Final classification: `REAL_AUTONOMOUS`
- Source components: `fabric/recipes/builtins (v14.* recipes)`, `fabric/catalog.specs_for_action`, `fabric/loop.select_capability`
- Normal runtime entrypoint: Executive objective -> mission contract -> Fabric
- Upstream intent contract: needs vocabulary (defined in this lane)
- Real effect: n/a (selection layer)
- Readback: resolution provenance in contract provenance + lane evidence
- Verification: resolver unit tests + unscripted proof
- Recovery: GAP resolution records a durable gap with resume target
- Root gap: selection required a recipe id; no normalized need vocabulary existed
- Repair: need vocabulary + recipe need declarations + deterministic resolution
- Acceptance: UNSCRIPTED_RUNTIME_PROOF.json (recipe selected from needs only)

### B4 browser-runtime

- Required product behavior: browser.web.inspect/read/execute with session restore, auth, frames, popups, downloads, uploads, reauth, expiry, readback -- usable from a normal mission action.
- Start classification: `REAL_AUTONOMOUS`
- Final classification: `REAL_AUTONOMOUS`
- Source components: `connectors/web_research.py`, `fabric/executors.py (browser.*)`
- Normal runtime entrypoint: recipe step browser.web.read / browser.web.execute
- Upstream intent contract: recipe parameters (source_url, site_origin, session state)
- Real effect: authenticated page read + interactive submit receipts
- Readback: session.readback + effect journal
- Verification: independent browser verify (MATCH on readback)
- Recovery: session expiry -> reauth provider (proved in scenario A/E)
- Root gap: none at base; re-verify from a resolved mission intent
- Repair: none expected; re-proof
- Acceptance: UNSCRIPTED_RUNTIME_PROOF.json + FAILURE_REPAIR_PROOF.json (expiry injection)

### B5 windows-native-operator

- Required product behavior: PID/create-time/executable identity, owned-window binding, focus, control selection, paste/type, file dialogs, Word native readback, artifact assert, stale-process handling, single-instance refusal -- usable from a mission.
- Start classification: `REAL_AUTONOMOUS`
- Final classification: `REAL_AUTONOMOUS`
- Source components: `fabric/personal_operator.py`, `fabric/computer_operator.py`
- Normal runtime entrypoint: recipe step desktop.open_artifact_and_assert
- Upstream intent contract: recipe parameters (application, process_id, artifact)
- Real effect: native document opened and asserted in the owner application
- Readback: native readback (_WwG) captured into evidence
- Verification: independent native readback verify
- Recovery: stale process -> close and reopen; owner app never adopted
- Root gap: none at base; re-verify from a resolved mission intent
- Repair: none expected; re-proof
- Acceptance: UNSCRIPTED_RUNTIME_PROOF.json (native Word assert leg)

### B6 artifact-general-work

- Required product behavior: Mission intent becomes a real artifact (create/update, document/spreadsheet verbs, content readback, native verification path, safe file targeting, recipe bindings) without the harness building files.
- Start classification: `REAL_AUTONOMOUS`
- Final classification: `REAL_AUTONOMOUS`
- Source components: `fabric/general_work.py`, `fabric/executors.py (file.*)`
- Normal runtime entrypoint: recipe step file.write / file.read / artifact.* verbs
- Upstream intent contract: recipe parameters (artifact_target, artifact_title)
- Real effect: owned-root artifact written with content
- Readback: artifact hash + content readback
- Verification: independent artifact readback
- Recovery: idempotency keys per artifact target
- Root gap: none at base; re-verify from a resolved mission intent
- Repair: none expected; re-proof
- Acceptance: UNSCRIPTED_RUNTIME_PROOF.json (artifact written + read back)

### B7 cross-system-composition

- Required product behavior: A normal mission composes browser -> extract -> artifact -> native verification -> Git effect through runtime-selected recipes, not by manual subsystem calls.
- Start classification: `REAL_BUT_MANUAL`
- Final classification: `REAL_AUTONOMOUS`
- Source components: `fabric/recipes/builtins.v14.digital_operator.close`, `fabric/compose.py`
- Normal runtime entrypoint: MissionLoop -> cap.workflow.run -> recipe runner
- Upstream intent contract: needs profile (this lane)
- Real effect: full chain in one mission
- Readback: nested receipts per step
- Verification: workflow.run nested receipt verification + independent verifier
- Recovery: on_failure policy per recipe step
- Root gap: chain required a developer-named recipe and developer-built params
- Repair: B1 resolution; re-proof the chain from an intent
- Acceptance: UNSCRIPTED_RUNTIME_PROOF.json (browser->artifact->native->git)

### B8 effect-truth-exactly-once

- Required product behavior: intent hash / effect identity / STARTED / execution / readback / verification / COMPLETED-or-reconciled for consequential actions; crash after mutation, retry, restart, ambiguous provider, already-applied remote produce no duplicate effect and no false success.
- Start classification: `REAL_AUTONOMOUS`
- Final classification: `REAL_AUTONOMOUS`
- Source components: `effects.py`, `fabric/loop.finalize_verified_effect`
- Normal runtime entrypoint: every consequential Fabric action
- Upstream intent contract: idempotency key per action payload
- Real effect: external state mutated once
- Readback: readback + reconciliation state
- Verification: verify MATCH/UNKNOWN; UNKNOWN never advances
- Recovery: reconcile path
- Root gap: none at base; re-verify under crash/restart in this lane
- Repair: none expected; re-proof
- Acceptance: UNSCRIPTED_RUNTIME_PROOF.json + FAILURE_REPAIR_PROOF.json duplicate counters 0

### B9 git-github-effects

- Required product behavior: branch/commit/push/remote readback/push reconciliation/frozen-effect discovery/persisted recovery intent in the normal runtime; real GitHub write only where owner-authorized config exists (else honest refusal).
- Start classification: `REAL_AUTONOMOUS`
- Final classification: `REAL_AUTONOMOUS`
- Source components: `fabric/executors.py (git.*)`, `connectors/github_write.py`
- Normal runtime entrypoint: recipe step git.commit / git.push / git.inspect
- Upstream intent contract: recipe parameters (worktree, remote, branch, message)
- Real effect: commit + push to disposable remote with SHA equality
- Readback: git rev-parse local/remote
- Verification: SHA equality + effect journal
- Recovery: push reconciliation on ambiguous remote state
- Root gap: none at base; re-verify; GitHub write stays owner-config-gated
- Repair: none expected; re-proof
- Acceptance: UNSCRIPTED_RUNTIME_PROOF.json (disposable remote SHA equality)

### B10 missionloop-repair

- Required product behavior: A failed action is repaired automatically: failure evidence -> repair path -> bounded repair action -> fresh execution -> independent verification -> continuation, without the owner saying try again.
- Start classification: `REAL_AUTONOMOUS`
- Final classification: `REAL_AUTONOMOUS`
- Source components: `mission/loop.py (run_step, _repair_or_block)`, `roles/dispatcher.py`
- Normal runtime entrypoint: MissionLoop RUNNING -> REPAIRING
- Upstream intent contract: mission contract verification criteria
- Real effect: repaired artifact/effect
- Readback: repair evidence + fresh verifier verdict
- Verification: independent verifier on the repaired step; deterministic steps re-derived by the runtime predicate (R6)
- Recovery: repair budget bounded; exhausted -> honest BLOCKED
- Root gap: none at base; re-verify with an injected real failure
- Repair: none expected; re-proof
- Acceptance: FAILURE_REPAIR_PROOF.json + CAMPAIGN_CONTINUITY_PROOF.json (failure_repaired_verified) + VERIFIER_ROUTING_REPAIR_PROOF.json (R6)

### B11 campaigns-long-horizon

- Required product behavior: objective -> children -> dependencies -> WAIT_FOR_RESOURCE -> capacity release -> wake exactly once -> failure/repair -> verifier -> next child -> checkpoint -> process loss -> rebuild -> continue -> terminal result, with zero duplicate missions/effects/continuations.
- Start classification: `REAL_AUTONOMOUS`
- Final classification: `REAL_AUTONOMOUS`
- Source components: `executive/campaigns.py`, `executive/continuous.py`
- Normal runtime entrypoint: Executive campaign runtime + startup resume pass
- Upstream intent contract: campaign objective + child missions
- Real effect: child missions executed through MissionLoop
- Readback: campaign projection from events
- Verification: campaign acceptance + child verification
- Recovery: checkpoint + startup_resume_pass (continue-once)
- Root gap: none at base; re-run in this lane with a process kill
- Repair: none expected; re-proof
- Acceptance: CAMPAIGN_CONTINUITY_PROOF.json (process loss continue-once, duplicates 0)

### B12 campaign-child-workflow-run

- Required product behavior: A campaign child binds to workflow.run through normal dispatch; outcome and receipts reconcile; terminal state follows canonical acceptance; repeat workflow does not deadlock; capability readiness updates correctly.
- Start classification: `REAL_AUTONOMOUS`
- Final classification: `REAL_AUTONOMOUS`
- Source components: `executive/campaigns.py`, `mission/loop._reconcile_accepted_campaign_child`
- Normal runtime entrypoint: campaign dispatch -> child mission contract
- Upstream intent contract: child mission objective
- Real effect: child workflow executed once
- Readback: child terminal state + receipts
- Verification: independent verifier + campaign reconcile
- Recovery: stranded RUNNING child reconciled after verified PASS
- Root gap: none at base (repaired 2026-09-16); re-verify
- Repair: none expected; re-proof
- Acceptance: CAMPAIGN_CONTINUITY_PROOF.json (campaign_child_workflow_passed)

### B13 real-capability-gap-detection

- Required product behavior: A mission that reaches a real capability gap declares it durably with objective lineage, required semantic capability, evidence of absence, constraints, and resume target -- without a developer creating the gap.
- Start classification: `WIRED_BUT_UNPROVEN`
- Final classification: `REAL_AUTONOMOUS`
- Source components: `fabric/loop.select_capability/record_gap`, `fabric/gap_bridge.py`, `capability_requirements.py`
- Normal runtime entrypoint: Fabric selection/gap bridge inside a mission
- Upstream intent contract: mission required capability/need
- Real effect: gap record + WAIT + wake
- Readback: fabric.capability_gap.* events
- Verification: catalog state after acquisition
- Recovery: wake fires exactly once, resumes the original objective
- Root gap: gap detection existed, but the intent->capability path never produced a natural gap from a high-level mission; gap creation was a developer action in prior proofs.
- Repair: intent resolution emits a GAP resolution with lineage + resume target
- Acceptance: SELF_EXPANSION_PROOF.json (gap from a mission requirement)

### B14 self-expansion-mcp

- Required product behavior: mission blocked on a real gap -> catalog/reuse search -> candidate discovery -> isolated trial -> verifier -> probation registration -> wake the exact original objective -> use the new capability -> continue; failed trial disables the capability; no second MCP backend.
- Start classification: `REAL_AUTONOMOUS`
- Final classification: `REAL_AUTONOMOUS`
- Source components: `self_expansion.py`, `fabric/expansion.py`, `fabric/gap_bridge.py`, `mcp_backend/`
- Normal runtime entrypoint: Gap bridge expansion port (bounded)
- Upstream intent contract: gap record with needed action
- Real effect: capability registered PROBATION then verified
- Readback: capability.registered + verification
- Verification: independent trial verification before probation wake
- Recovery: failed trial -> disable + honest block
- Root gap: none at base; re-prove from a naturally detected gap
- Repair: none expected; re-proof
- Acceptance: SELF_EXPANSION_PROOF.json (acquire -> verified -> same objective resumes)

### B15 provider-resilience

- Required product behavior: preferred provider outage -> retry -> fallback provider -> WAIT_PROVIDER -> local independent work -> recovery -> exact mission resume; provider failure never erases mission truth.
- Start classification: `REAL_AUTONOMOUS`
- Final classification: `REAL_AUTONOMOUS`
- Source components: `roles/router.py`, `resilience/`, `workers/`
- Normal runtime entrypoint: Role dispatch through the canonical router
- Upstream intent contract: role manifest
- Real effect: no lost mission; fallback or honest WAIT
- Readback: agent run events with failure_class
- Verification: terminal events clear failure state
- Recovery: WAIT_PROVIDER + resume
- Root gap: none at base; re-verify through the existing test seam
- Repair: none expected; re-proof
- Acceptance: PROVIDER_STOP_PROOF.json (26 provider/stop tests green)

### B16 process-restart-durability

- Required product behavior: Supervisor/runtime process kill -> fresh process reconstructs -> active mission/campaign truth restored -> no duplicate action/effect -> same objective continues.
- Start classification: `REAL_AUTONOMOUS`
- Final classification: `REAL_AUTONOMOUS`
- Source components: `supervisor/`, `executive/campaigns.startup_resume_pass`, `fabric/recipes/checkpoint.py`
- Normal runtime entrypoint: fresh process startup
- Upstream intent contract: durable event ledger
- Real effect: continuation without duplicate effects
- Readback: ledger rebuild + duplicate counters
- Verification: reconciled counters from the ledger
- Recovery: continue-once; a verification pass resumed after the frozen binding drifted refuses honestly (BLOCKED + the ledger's reason) instead of raising EventConflict out of the loop (R7)
- Root gap: none at base; re-verify in this lane
- Repair: none expected; re-proof
- Acceptance: CAMPAIGN_CONTINUITY_PROOF.json + PROVIDER_STOP_PROOF.json + VERIFIER_ROUTING_REPAIR_PROOF.json (R7 binding drift)

### B17 stop-pause-resume

- Required product behavior: STOP blocks new consequential effects; queued work stops per canonical semantics; active controllable action freezes or interrupts honestly; restart keeps STOP; RESUME reorients before work; no duplicate effect; STOP never routes through the LLM.
- Start classification: `REAL_AUTONOMOUS`
- Final classification: `REAL_AUTONOMOUS`
- Source components: `control/estop.py`, `control/broker.py`, `owner_controls.py`
- Normal runtime entrypoint: Owner STOP/PAUSE/RESUME through OwnerControlService + sentinel
- Upstream intent contract: owner directive
- Real effect: dispatch refused while stopped
- Readback: STOP sentinel + control events
- Verification: no new effects while stopped
- Recovery: RESUME reorientation
- Root gap: none at base; re-verify
- Repair: none expected; re-proof
- Acceptance: PROVIDER_STOP_PROOF.json (scenario E 12/12)

### B18 command-center-runtime-truth

- Required product behavior: Command Center shows truthful durable runtime facts: current mission/campaign, worker/action state, failures/repairs, verified outcome, effects, WAIT/blocker, release/provider/health, owner attention items.
- Start classification: `WIRED_BUT_UNPROVEN`
- Final classification: `REAL_AUTONOMOUS`
- Source components: `command_center.py`, `command_center_work.py`, `ui/command-center`
- Normal runtime entrypoint: Command Center API over the durable event ledger
- Upstream intent contract: mergeable projection contract (lane A decision truth)
- Real effect: n/a (projection)
- Readback: API payloads from candidate runtime
- Verification: contract tests + real candidate data probe
- Recovery: projection rebuild from events
- Root gap: needs verification that every required runtime fact is projected from real candidate data
- Repair: recorded per verification result
- Acceptance: COMMAND_CENTER_PROOF.md (real candidate data, 8/8) + 34 CC tests

### B19 notifications-briefings

- Required product behavior: Attention routing from durable events: no duplicate spam, preference respected, in-app notification works, native toast if the environment permits, an OS-toast failure never corrupts state; briefing generated from durable facts.
- Start classification: `REAL_BUT_MANUAL`
- Final classification: `REAL_AUTONOMOUS`
- Source components: `windows_toast.py`, `briefing_items.py`, `briefings.py`
- Normal runtime entrypoint: Attention items from durable events -> toast router
- Upstream intent contract: attention item projection
- Real effect: in-app toast emitted once per attention identity
- Readback: toast store + delivery result
- Verification: dedupe proof
- Recovery: toast failure isolated from state
- Root gap: native winotify toast was never exercised (package not installed)
- Repair: install the optional backend in the lane venv and prove a real OS toast, or record the honest environment limit
- Acceptance: in-app toast/dedupe/preference proven; native winotify toast NOT_TESTED (backend absent)

