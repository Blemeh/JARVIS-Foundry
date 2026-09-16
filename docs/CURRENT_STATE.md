# JARVIS FOUNDRY — CURRENT STATE

**The anti-staleness surface of the repository.**

This document is the authoritative current-state record. The frozen roadmap
remains authoritative for sequencing and scope; its execution pointer is
`docs/roadmap/JARVIS_1_0_EXECUTION_STATE.yaml`.

Last reconciled: Wednesday, September 16, 2026 for isolated integrated
product acceptance on
`candidate/jarvis-integrated-acceptance-20260916` (product
`0bfdfa851f6c3f4d0203d1e75216967587ecc4d6`, receipts
`e9a8e34f60330399d7220fe8230491dc326b880a`). This candidate is source-only:
it does not merge `main`, push, promote, install, retarget live CURRENT/PREVIOUS,
mutate the live database, or change Scheduler/Supervisor/provider/trust.
It is built from Goal 01 convergence HEAD
`15a25536348d87be53ff115033c5012192d988be`. Live V10 and the frozen execution
pointer remain unchanged. Historical live A4/Prompt 3 notes below are retained
as live-surface history, not as a promotion claim for this candidate.

ISOLATED ACCEPTANCE CANDIDATE: ACCEPTED_FOR_LIVE_PROMOTION / SOURCE ONLY
ISOLATED ACCEPTANCE BRANCH:    candidate/jarvis-integrated-acceptance-20260916
ISOLATED ACCEPTANCE PRODUCT:   0bfdfa851f6c3f4d0203d1e75216967587ecc4d6
ISOLATED ACCEPTANCE RECEIPTS:  e9a8e34f60330399d7220fe8230491dc326b880a
ISOLATED ACCEPTANCE HANDOFF:   var/integrated-acceptance-20260916/HANDOFF.json
ISOLATED CONVERGENCE CANDIDATE: V14+V15+V16 COMPLETE / SOURCE ONLY
ISOLATED CANDIDATE BRANCH:     candidate/v14-v16-max-semantic-convergence-20260916
ISOLATED CANDIDATE PRODUCT:    5e0c6fa9d048e270b91f2d59b8a16bd567a22dfb
ISOLATED CANDIDATE HANDOFF:    var/convergence-20260916/HANDOFF.json
LIVE UNCHANGED:                YES

Last previously reconciled: Sunday, September 13, 2026 for the bounded A4 proving-driver
repair that followed the Prompt 3 provider recovery (see the next paragraph),
and for the validation-only
harness-stability repair that followed the first fresh preflight of the
cross-schema rollback source. That preflight of `843d183...` blocked on the
full suite: two v13 verifier predicates failed only from detached-HEAD
worktree state, one A2 seam fixture held a frozen `2026-09-03` clock that had
passed the 168-hour candidate TTL, and two checks were load-dependent client
or cleanup deadlines that pass in isolation. Repair `fb9cb43...` anchors the
A2 seam fixture to the current UTC clock, mirroring `b52c2e5...`. Harness
`c132bb6...` replaces a 3.0s localhost bridge client budget with a named 30s
budget and weakens no assertion. Rehearsal, crash/re-entry, migration, and
packaging passed on `843d183...` with `live_mutated: false`. This work does
not deploy or promote a release, retarget the live Supervisor or Scheduler,
migrate the live database, run a fresh provider/A3/A4/48h gate, or restore
production trust. Prompt 3 remains blocked until a fresh independent
preflight validates this final canonical source. V10 remains live and
unchanged.

The bounded A4 proving repair supersedes that blocking note for the proving
harness only. `82271cc...` gives every fault-campaign child process its own
worker-loop identity: the driver previously passed one durable cycle id to
both the fault-injection child and its resume child, and `run_cycle` records
`worker_loop.cycle_started:<loop_id>` with the dispatching process identity in
the payload, so every resume raised `EventConflict` and the A4 campaign failed
at its first fault point. Production never replays a loop id; the worker-loop
CLI derives a fresh id per bounded cycle. A real campaign on the frozen
candidate now injects and recovers all six fault points with zero duplicate
candidates, objectives, missions, builder runs, effects, or learning updates.
That campaign also disclosed two non-duplicate findings recorded in the
Prompt 3 evidence root: the mid-build kill can leave one run-scoped commit
effect `STARTED` when the mission re-verifies under a new run id, and a
`NOT_TESTED` verifier blocks the mission instead of producing a verified
terminal outcome. Neither is repaired here. This commit changes proving and
documentation surfaces only; no schema, runtime, live release, Supervisor,
Scheduler, STOP, capability, or trust claim is advanced by it.

A second bounded repair, `87dba7b...`, closes a real gap in the guarded
cross-schema rollback. The activation-suffix whitelist hardens
`worker_loop.lease_reclaimed` but omitted
`runtime.control.lease_reclaimed`, which `supervisor/control.py` records as
`CONTROL_LEASE_RECLAIMED` whenever the independent Supervisor takes over a
dead predecessor and reclaims the singleton lease. Because that takeover is
exactly what happens while activating a failed release, the real rehearsal
refused with "activation suffix contains post-snapshot authority:
runtime.control.lease_reclaimed" at its baseline scenario. The event is now
accepted only with the proven dead-owner shape (the `lease-reconciler`
source, a positive pid, and one of the two fail-closed service reasons);
anything else still refuses. The rehearsal then completed all ten scenarios
with `live_mutated: false`.

A third bounded repair, `1ee2fa4...`, closes the two A4 findings that blocked
the pre-live gate after the provider recovered. `9d46463...` reads an honest
verifier verdict that carries its own justification (the live contract tells
the worker to end its response with the verdict, and a real session wrote
`VERDICT: PASS (criteria confirmed by direct inspection)`, which the strict
whole-line grammar had recorded as `NOT_TESTED` and which then blocked the
mission), and closes a superseded run's commit claim on the commit path.
`92e52dc...` closes the same class of claim at the mission's terminal
transition, where a claim can otherwise never be resolved because the mission
never reaches commit again. `df16309...` raises the A4 driver's per-child
bound from 1800 s to 2700 s so a child can traverse the product's own frozen
worst case (worker dispatch plus the bounded repair a FAIL earns plus the
independent verifier), which is what caused the mid-build and
post-effect resumes to be aborted as driver failures. `1ee2fa4...` replaces
the harness's one-effect-per-campaign rule with one completed commit per
verified step plus every effect terminal, because the canonical A3 mission
runs an ordered two-step plan and the product's own A4 durability test
asserts exactly that shape.

The A4 current equivalent now PASSES on `1ee2fa4...`: all six fault points
inject and recover, every check and hard assertion is true, and every
duplicate counter is zero (candidates, objectives, missions, builder runs,
effects, learning, wakes, resumes), with zero false successes, zero
unverified completions, zero unbounded loops, and zero unreconciled
ambiguous effects. Evidence: `A4_RESULT_PASS.json` and `bundle-a4r7` under
`C:\temp\JARVIS-Prompt3Cont-20260914\e-a4`.

DOCUMENTATION STATE BASE SHA: `e9a8e34f60330399d7220fe8230491dc326b880a`

The SHA above is the immediate parent of this final docs-only reconciliation
commit, as required by `scripts/doc_staleness_check.py`. It names the isolated
acceptance receipts commit, not a live install. Product repair remains
`0bfdfa851f6c3f4d0203d1e75216967587ecc4d6`. The next owner-authorized action
for this candidate is `docs/agent/workflows/promotion.md`; this pin does not
perform that promotion.

## Repair-path scope fix (2026-09-15)

The failed live owner mission `mission-699bf14dd056` exposed a recovery-path
source defect: a durable FAIL moves a mission to REPAIRING, and MissionLoop
prepends the repair context, which embeds the verifier findings verbatim.
Findings legitimately quote JSON -- the builder's own `.jarvis-result.json`
that the verifier is reporting on. The envelope scanner took the FIRST brace in
the text, so that incidental quoted fragment was treated as the fabric scope
payload; the scope check then demanded a scoped field the step never carried
and hard-blocked the repair before dispatch with `invalid_workforce_scope` /
"fabric action file.write is missing scoped field target". Repair budget was
consumed by a scope error rather than by the work.

`src/jarvis_foundry/fabric/adapter.py` now treats only a JSON object that
TERMINATES the instructions as an envelope. A real envelope still drives the
structured fabric fast path; prose declines it and the real worker executes
inside the registered resource root, where JARVIS-owned evidence reconciles the
work against that root. Scope is not weakened: out-of-scope targets, missing
scoped fields, forbidden paths, and malformed envelopes all still fail closed,
and the evidence reconciler's rejection of dishonest or mismatched artifact
claims is untouched. Where prose quotes an object and a real envelope follows
it, the trailing envelope is the one that resolves (the new test asserts this
explicitly); that ordering is intended, not a fail-closed case.

Residual, recorded honestly by the independent review: because a trailing JSON
object is textually indistinguishable from a quoted fragment, a fragment that
happens to end the instructions is treated as the envelope, and if a real
envelope precedes it the trailing one wins. Containment is unaffected -- such
a payload is still constrained to the registered root, and the escaping,
forbidden-path, and missing-field variants all fail closed -- so this is a
fidelity residual (Medium), not a confinement defect. No follow-up change is
made here; it is recorded for the next authorized phase.

Regression coverage: `tests/foundry/test_v13_repair_path_scope.py` fails on the
pre-repair base with the exact live error and passes after the fix.

```text
V10 ACCEPTED PREDECESSOR:     042942680687b0ee0382b439b1ddba5a9db7f089
V10 PROMOTED PRODUCT SHA:     faa861c67399d5a3755d778224ff65513bd933a1
V10 CANONICAL LANDING SHA:    faa861c67399d5a3755d778224ff65513bd933a1
V10 LIVE RELEASE ID:          rel-faa861c67399-5afeb024
V10 ROADMAP AUTHORITY SHA:    e9b2155bd669887c4778db0b16a4ef7861d2c9ff
PROMOTION BASE MAIN:          2905c206fed0461bf3085bc0cb43773be913300a
PROMOTION BASE ORIGIN/MAIN:   2905c206fed0461bf3085bc0cb43773be913300a
CANONICAL PROMOTION BRANCH:   candidate/v13-canonical-source-promotion-20260906
CANONICAL SOURCE STATE SHA:   006045095f6e6f4bf5ca2bb383e558c4ad81afbd
PROMPT 2 VERIFIER REPAIR SHA: fb0362684eb5cad9fbcf72c814dded51e6db894a
ROLLBACK REPAIR BASE MAIN:    ecbd5d5a0482ad17040c768eca49ba0105610f9e
ROLLBACK RUNTIME SOURCE SHA:  ba422d2643fc45bcd23023915d5d02d11383944c
ROLLBACK RUNTIME TREE SHA:    e129dcb340bf911a48cf997596f19dbb6e382871
ROLLBACK TEST FIX SHA:        b52c2e57444bb7702d10b13ea5ef8890a76c3942
ROLLBACK TEST FIX TREE SHA:   a6fae17ed3006d7ee3dd559b4341f7863ae369db
A2 SEAM REPAIR SHA:           fb9cb433f7aef8f5934c322ec1d69593719acb97
A2 SEAM REPAIR TREE SHA:      654e74d2ce1cf4716f3086769ed90dd021d85f26
HARNESS STABILITY SHA:        c132bb6ded7dff30a50f80ef6a73dc55bf15dd50
HARNESS STABILITY TREE SHA:   3b65d07bb09cb6e0fe1bf522cdeedfc586356ebc
ROLLBACK CONTROLLER:          REPAIRED / SOURCE ONLY / NOT LIVE
ROLLBACK REHEARSAL:           PASS / 10 PATHS / LIVE MUTATED NO
ROLLBACK REVIEW:              PASS / CRITICAL 0 / HIGH 0 / 1 KNOWN MEDIUM
PROMPT 3 ENTRY:               OPEN / IN PROGRESS (provider recovered)
FRESH PROVIDER PROBE:          PASS (opencode_go / deepseek-v4.1-flash)
FRESH A3:                      PASS (one bounded, independently verified candidate)
FRESH A4:                      PARTIAL (6/6 fault points; two end-state findings open)
48H TRUST WINDOW:              NOT_STARTED
CURRENT/CANDIDATE SCHEMA:     v40 (canonical source candidate only)
LIVE SCHEMA:                  v38
V2 KNOWLEDGE SCHEMA:          v35 (historical additive migration)
V3 DISCOVERY/BUILDING SCHEMA: v36 (historical additive migration)
v34 live baseline:            historical predecessor retained for migration proof
V10 STATUS:                   CLOSED
V10 EXIT GATE:                PASSED
CURRENT PHASE:                V11_INITIATIVE
CURRENT GATE:                 A3_A4_EXIT / BLOCKED_BY_PROVIDER
EXECUTION STATUS:             V11_INITIATIVE / A3_A4_EXIT / BLOCKED_BY_PROVIDER
V11 STARTED:                  YES (candidate only)
V11 COMPLETE:                 NO (A3 real-provider and A4 endurance gates remain)
V11 PROMOTED/LIVE:            NO
V11 LATEST PROOF SHA:         afddb8d5ca3d5c3459a4f5394e84affb92d13738
V13 STATUS:                   V13_PERSONAL_EXECUTIVE_CONVERGED / SOURCE PROMOTION
V13 FROZEN BRANCH:            candidate/v13-final-personal-executive-convergence-20260905
V13 FROZEN FINAL SHA:         d58debf04c6f53a11dcfb071f999b5349f01c9b1
V13 FROZEN FINAL TREE SHA:    97ff027f908202e84d2ccb11d8e3f73ffa5cefde
V13 SOURCE SHA:               1002e9919258d8ded33d125cd579bbdff7091f3a
V13 SOURCE TREE SHA:          3ff1757aabd778a25b1cabc7a96945b942ed0d34
V13 EVIDENCE SHA:             a0d3ee0eb90ae98ad1fd672f457788de65c9d203
V13 PROOF SHA-256:            fb48a986699793d11c4ff034094cee785c3d54d6e0f7ec661af1ec7e77aa7e9a
V13 PRODUCT PROOFS A-F:       PASS
V13 FOCUSED MATRIX:           PASS / 553 TESTS / 0 FAILURES / 1 SKIPPED
V13 OPERATOR COMPATIBILITY:   PASS / 70 TESTS / 0 FAILURES
V13 FULL SUITE:               PASS / 3411 TESTS / 0 FAILURES / 2 SKIPPED
V13 FRONTEND:                 PASS / 162 TESTS + TYPE/LINT/BUILD
V13 SOURCE-BACKED E2E:        PASS / 3 PLAYWRIGHT JOURNEYS
V13 INDEPENDENT VERIFIER:     PASS / NO CRITICAL OR HIGH FINDINGS
V13 SOURCE PROMOTION:         PROMPT 2 NORMAL GIT / NO FORCE
V13 LIVE ACTIVATION:          NO
STRANDED ACCEPTED DELTAS:     0
MATERIAL UNKNOWN ITEMS:       0
POWERPOINT PROOF:             OWNER-DIRECTED OMIT
PPTX PRODUCT CAPABILITY:      RETAINED
V12 STATUS:                   V12_FINAL_SOURCE_CANDIDATE / NOT ACTIVATED
V12 BRANCH:                   candidate/v12-final-product-convergence-20260903
V12 SOURCE SHA:               7f153851517b5529dd1e582725a0c2a6d96ddc9b
V12 EVIDENCE SHA:             cba101ea6750a3822dee147d1f479462c0997cde
V12 PROOF SHA-256:            027c9dc07ff0d027ba8b231c573f787ed8a60e74f305f024f7671b84965c1f66
V12 A5 DISPOSABLE PROOF:      PASS_BUILD_AHEAD / GATE NOT CLOSED
V12 A6 DISPOSABLE PROOF:      PASS_BUILD_AHEAD / GATE NOT CLOSED
V12 FULL CANONICAL SUITE:     PASS / 2644 TESTS / 0 FAILURES / 1 SKIPPED
V12 INDEPENDENT VERIFIER:     PASS / NO BLOCKING FINDINGS
A2 IMPLEMENTATION SHA:        91c3deaeefb3fc71ffd091ad0f4abc2e2fd8023c
PRECONVERGENCE SOURCE SHA:    51eb998642fee713458998bd3f44219a76ab670d
CONTINUOUS OBSERVATION INPUT: 8004a8c16aceb888db1d1401c3bc67842cb4746f
INTEGRATION BASE 2 SOURCE:    899e2d3affee41d02145514acb9c308db57afaf6
A4 INPUT SHA:                 daae32cb14d6d8882166d30d515daa9d796c6857
OUTCOME-TRUTH INPUT SHA:      02e62a85a50bd642a1abdc6e415a2215ac5d2689
AUTHORITATIVE V11 INPUT:      ae9ac06b6f9174c810b0e658daaf81c7b683ae8b
SOURCE FREEZE:                f7cee034384ac191dfbac9f910dd6f6d3eb520d4
A3/A4 HARNESS INPUT:          3716294bf7d104f03e1fa2d990b2a28654ada1cb
EXIT-PROOF SOURCE SHA:        ea6be6ddebe21f95f10021db4b1972b2dcf2c00a
TRAINING-TIER POLICY:         OWNER_AUTHORIZED / CANONICAL CONFIG ENABLED
GMAIL READ/SEND:              DEFERRED_BY_OWNER
GMAIL OBSERVATION:            DISABLED
VOICE/LANGUAGE:               DEFERRED_BY_OWNER
V11 48H GATE:                 NOT_STARTED
```

## Prompt 2 canonical source reconciliation

Audit A completed a 237-ref, 215-worktree Git inventory and conservatively
flagged 89 records, including every one of the 30 dirty worktrees. Audit B
classified 62 material semantic candidates and found no stranded accepted
source. The master reconciliation manifest outside the repository contains 155
rows: 129 `ALREADY_REPRESENTED`, 3 `PARTIALLY_ABSORBED`, 11 `SUPERSEDED`, 10
`EVIDENCE_ONLY`, 1 `FAILED_REJECTED`, and 1 `ABANDONED_INCOMPLETE`. It contains
zero `UNIQUE_ACCEPTED_DELTA` rows and zero material UNKNOWN rows.

Local `main` and `origin/main` were identical at `2905c206...`, which is the
merge base and direct ancestor of frozen V13 final `d58debf...`. The canonical
promotion branch therefore starts at the frozen V13 final, preserves its
history, and adds current documentation/accounting repairs plus one bounded
verifier branch-binding repair. No product source port, product behavior change,
schema change, historical-evidence rewrite, or dirty-worktree consumption is
required. Dirty owner data remains untouched.

The Audit B accounting failure was evidence-only: the product tree and final
evidence tree needed explicit separation, the V13 section below named a stale
evidence parent, and the historical proof used decorated accounting labels.
This current-state record separates the identities, corrects the parent, and
uses the Prompt 2 taxonomy in the external master manifest without rewriting
the historical proof.

The first independent final source review then found seven Audit A rows whose
tip commits were documentation or evidence, but whose full histories contained
accepted Delegation, Computer Operator, Command Center, campaign-runtime, MCP,
or project-context product work. The repaired manifest maps all seven histories
to `ALREADY_REPRESENTED`, names their equivalent or stronger V13
representations, and retains their evidence/reflog provenance. No product source
port or behavior change was required.

The first exact-candidate V13 focused-matrix run at `73c624ec...` completed 553
tests with 551 passed, 1 skipped, and 1 failed. Every world-intelligence
semantic, source-SHA, ancestry, main/origin preservation, and fresh detached
replay check passed; only the verifier's frozen branch-name default rejected the
new canonical promotion branch. Repair `fb036268...` keeps the frozen V13 branch
as the default, accepts an externally declared expected branch, and preserves
strict proof/source binding. Its focused proof, replay, and tamper-rejection
tests pass 3/3. The failed JUnit remains retained outside the repository and
does not count as a passing gate; the full V13 matrix requires a fresh rerun on
the final docs-pinned candidate.

## V13 final Personal Executive convergence source candidate

The isolated branch
`candidate/v13-final-personal-executive-convergence-20260905` in
`C:\temp\JARVIS-V13-Final-Personal-Executive-Convergence-20260905` starts from
exact V12 Final `51d2cfcce1be847fd252d1c857105f09f5f2c0e4`.
The product source is `1002e9919258d8ded33d125cd579bbdff7091f3a`,
tree `3ff1757aabd778a25b1cabc7a96945b942ed0d34`; the final evidence parent is
`a0d3ee0eb90ae98ad1fd672f457788de65c9d203`. Schema remains one authoritative
v40 chain. Prompt 2 carries this source history to canonical Git only after its
ordered gates; it does not deploy, activate, or apply it to the live database
or runtime.

Input histories were classified and integrated semantically rather than
blindly merged. Owner Context, Personal Operator, Delegation, AI Workforce,
General Work, Computer Operator, World Intelligence, Chief of Staff, and
Command Center contribute their accepted unique behavior. Lane 2 uses its
reconstructed tested tree; Lane 6 uses the strongest repaired implementation;
accepted UI `509e288...` is already represented more strongly by Lane 7; old
V12 component tips are already represented by V12 Final.

One authority spine remains: durable owner context -> Continuous Executive ->
objective/campaign -> canonical `MissionLoop` -> Fabric Catalog -> canonical
effects/evidence -> independent verifier -> outcome/learning -> retry, repair,
replan, wait, close, or next action. Delegation admits intent, AI Workforce
orchestrates workers, General Work composes capabilities, the Personal and
Computer Operators implement capabilities behind Fabric, World Intelligence
and Chief of Staff remain advisory, and Command Center remains a projection
and canonical control transport. There is no second Executive, MissionLoop,
scheduler, Catalog writer, provider authority, effect journal, owner-control
service, verifier, or MCP runtime.

The fresh integrated proof is
`evidence/v13-final-personal-executive-convergence/proof/proof.json` (SHA-256
`fb48a986699793d11c4ff034094cee785c3d54d6e0f7ec661af1ec7e77aa7e9a`).
Proofs A-F pass for `Fix it.`, `Prepare me for tomorrow.`, `Handle this.`,
`Find the next useful thing.`, STOP/restart/recovery, and source-backed Command
Center truth. The shared v40 database has 313 integrity-verified events and
SQLite quick check `ok`. Every required duplicate and integrity counter is
zero, including owner intents, objectives, campaigns, missions, WorkPackages,
worker attempts, effects, registrations, wakes, resumes, false successes,
unverified consequential completions, unnecessary owner questions, and
unrelated process impact.

Verification against exact source `1002e991...` passed 553 focused tests, 262
V12 authority/effect/provider/campaign regressions, 70 Personal/Computer
Operator compatibility tests, 162 frontend tests plus typecheck/lint/build,
three source-backed Playwright journeys, and the serialized 3,411-test Foundry
and convergence suite with two expected skips. Lock validation, compileall,
complete source diff check, wheel/sdist build, isolated wheel install, imports,
and schema v40 also pass. The final fresh independent verifier checks the exact
documentation-only HEAD and returns PASS with no Critical or High findings.
Its first exact-final run found one package byte-identity High and no Critical
findings; rebuilding from raw Git source blobs repaired the wheel and sdist
without changing product source, and the affected package predicate passed
before the permitted final verifier recheck.

PowerPoint deliverables and actions were excluded from the V13 proof by owner
direction, but the product PPTX create/update/copy/readback capability remains
in source. The document, PDF, and spreadsheet General Work paths remain proved.
Gmail
read/send/observation remains `DEFERRED_BY_OWNER` and was not authenticated,
probed, or enabled. Voice and English/Albanian localization remain deferred.
Provider production trust remains `SUSPENDED`. No public external mutation,
deployment, promotion, activation, reboot, or live-runtime conversion was
performed.

## V12 final product convergence source candidate

The isolated branch `candidate/v12-final-product-convergence-20260903` in
`C:\temp\JARVIS-V12-Final-Product-Convergence-20260903` starts from exact V11
proof SHA `afddb8d5ca3d5c3459a4f5394e84affb92d13738`. The source candidate is
`7f153851517b5529dd1e582725a0c2a6d96ddc9b`; refreshed source-bound evidence is
commit `cba101ea6750a3822dee147d1f479462c0997cde`. Schema remains one
authoritative v40 chain. Neither commit was promoted, deployed, or applied to
the live database or runtime.

The convergence semantically consumes:

- Endurance final `49b2c2337a0579519d0b782a5a260673079eb398`
  through implementation `43da572ca0fb24ae82222cdd02c2e23a76bf95f6`.
- Self-Expansion final `e159437daeca4f5c5008bf0345adfeafae469c66`
  through source/evidence `ceede0eeb766216765add95525f840f9ac382224`.
- MCP/Effects final `1d72dbfe7af40e35d37ce20edacc6bfaeb8badd6`
  through implementation `b730b708451b0399165e8adbdb037150126cc6c1`.

One authority spine is preserved: Executive campaigns remain subordinate to
the existing Executive; `MissionLoop` remains the mission runtime; the
existing wake/scheduler seams own continuation; Fabric Catalog is the sole
writable capability authority; provider registry/router remains canonical;
`effects.py` plus Fabric execution own external-effect identity and terminal
truth; and `FabricMcpBackend` is the only MCP runtime.

The disposable integrated proof is
`evidence/v12-final-product-convergence/proof.json` (SHA-256
`027c9dc07ff0d027ba8b231c573f787ed8a60e74f305f024f7671b84965c1f66`).
A5 proves a real missing capability, failed first trial, verified adaptation,
READY/PROBATION registration, one wake, restart identity, resumption of the
same objective, mission completion, rollback, and stale-receipt refusal. A6
proves bounded parallel children, compatible provider fallback, all-down WAIT,
provider recovery, deadline/replan, STOP/RESUME, hard-kill restart, ambiguity
reconciliation before fallback, ALL/ANY joins, and completion. The real
disposable MCP proof covers stdio, stable projection/reconnect identity,
read-only and mutating typed calls, canonical effects, and FROZEN
timeout-after-effect behavior.

Independent review found three convergence defects before this freeze:
selected MCP capabilities were not reconstructed by the canonical worker
stack, failed completion-receipt persistence could leave a process-local
backend and probation registration active, and the A5 proof reported three
integrity values as literals. Source `7892241...` repairs all three, and source
`50e5a65...` preserves worker-loop cleanup compatibility with legacy
stack-shaped test adapters while the canonical `WorkerStack` still owns and
closes its MCP backend. Fresh final review then found that degraded selected
MCP restoration could fall through to a generic worker and that the complete
candidate range contained one blank line at EOF. Source `7f15385...` keeps
degraded MCP dispatch inside the canonical Fabric spine, records a truthful
`BLOCKED/UNKNOWN` receipt with zero generic routes, and removes the whitespace
violation. The worker stack restores completed durable adoptions into one `FabricMcpBackend`,
owns and closes that backend per cycle, commits adoption verification and the
completion receipt atomically, disables an unreceipted projection on failure,
and measures duplicate missions, false successes, and unreconciled accepted
effects from the ledger. The 19-file convergence matrix passed 248 tests after
these repairs. In the first canonical rerun, every product/runtime test passed;
only the two expected pre-pin documentation-staleness assertions failed. The
final clean canonical rerun then passed 2,644 tests with zero failures, zero
errors, and one host-dependent symlink skip. Fresh independent verifier
re-review returned `PASS` with no blocking findings.

All required integrity counters in the refreshed proof are zero: duplicate
missions, effects, registrations, wakes, resumes, false successes, and
unreconciled accepted effects. Gmail read and send are
`DEFERRED_BY_OWNER`; Gmail observation is `DISABLED`. No Gmail authentication,
probe, send, observation, or credential requirement occurred.

This is build-ahead source evidence only. A5 and A6 are not roadmap gate
closures because V11 A3/A4 has not passed on a promoted V11 base. V11 exit
remains `PENDING`, the 48-hour state remains `NOT_STARTED`, production trust
remains `SUSPENDED`, and the next authorized action remains the real V11 A3
restart when the provider recovers.

## V11 A3/A4 exit-proof source candidate

The isolated proof candidate is branch `candidate/v11-exit-proof-20260903` in
`C:\temp\JARVIS-V11-Exit-Proof-20260903`. It starts from the exact overnight
candidate `ae9ac06b6f9174c810b0e658daaf81c7b683ae8b`, retains source freeze
`f7cee034384ac191dfbac9f910dd6f6d3eb520d4`, and remains schema v40. Exit-proof
implementation source is `ea6be6ddebe21f95f10021db4b1972b2dcf2c00a`;
the final proof SHA is the clean docs-only commit immediately following that
source, as enforced by `scripts/doc_staleness_check.py`.

Eligible clean input lanes and their source SHAs were:

- Backend Stack: `c8006303fc769cf6d46e22f3cc3203a70bf4aa73`
- A3 initiative bridge: `630304b7809de85850e53307191f0f054d86ad61`
- Command Center final: `af1911392915c87d369baa12e5ea02d9c1cf7304`
- A3/A4 proving harness: `3716294bf7d104f03e1fa2d990b2a28654ada1cb`

Integration was semantic and ordered Backend Stack, A3, Command Center, then
the proving harness. Documentation-only and historical terminal commits were
not consumed. The candidate preserves one Executive, scheduler, Fabric Catalog,
MissionLoop/effects authority, WIP=1, STOP, idempotent effect identity, and
final-outcome truth.

The autonomous loop is:

`observe -> meaningful signal -> candidate -> dedupe -> research dossier ->
score/reject -> capability requirements -> project context -> admission ->
objective -> verification plan -> MissionLoop/Fabric -> isolated build/test ->
reconciliation -> verification.accepted -> final outcome -> learning ->
briefing -> WAIT/continue`

The A3 source bridge is integrated into the existing Executive/MissionLoop
spine, with explicit A3/continuous-observation mutual exclusion and the
`cap.tests.run` admission requirement. The owner explicitly authorized the
existing canonical `security.allow_data_training_tiers_noninteractive: true`
policy for unattended proving. The repository authority is enabled through
`config/worker_routing.yaml`; no bypass or alternate policy mechanism exists.
The real `opencode_go/muse-spark-1.2-contributor` flagship is
`BLOCKED_BY_PROVIDER` after its bounded HTTP 500 failures against the final
V11 proof state.

The A4 proving tooling is integrated and now includes an exact-SHA,
candidate-neutral real driver, immediate six-point fault campaign, durable
post-candidate and post-selection recovery, hard-zero duplicate and false-
success assertions, and the separately guarded `--allow-48h` runner. Full
fault-injection remains `NOT_STARTED`, and elapsed 48-hour proving remains
`NOT_STARTED` at this freeze.

The first real A3 attempt against proof SHA `cb85fa8a4717fbed4866b18d6c745fd3dbd23381`
correctly ended `BLOCKED`: the observer treated TODO text inside a Python test
fixture string as work, and the real provider returned HTTP 500 after its three
bounded retries. It created no effect or accepted completion. The observer now
uses Python comment tokens and anchored markers, and the pinned Ruff receipt
truthfully establishes `cap.system.diagnose`; all affected proof restarts from
the new exact SHA.

The repaired cold restart against `0cb5b21ea4731e36179e5c7b8e1afd951be8b37f`
produced the intended 13-candidate ledger (12 rejected, one selected) but the
same provider returned HTTP 500 after three retries, again with zero effects.
An isolated no-tools `PROBE_OK` request reproduced the HTTP 500, ruling out
repository context or mission size. A snapshot-only schema label was also
corrected to read the canonical `schema_migrations` authority (v40), not
SQLite's unused `user_version`; the database schema itself did not change.

Accepted final mission outcomes now feed calibration, World Intelligence, and
Tier-C memory exactly once by outcome identity. Failed and blocked outcomes
remain distinct from success learning and are projected as human-readable
`mission outcome <status>` memory summaries. Command Center autonomy is an
additive read projection/API/UI surface with candidate-scoped optional payloads;
it does not create a competing authority. Frontend checks passed: 132 Vitest
tests, TypeScript build, ESLint, and production Vite build. Twenty-four
simulated/source-labelled truth screenshots were captured and visually
inspected across eight routes and three viewports.

One broad Foundry regression was executed exactly once: `2493 passed, 14
skipped, 3 initial failures`. Two failures were the expected stale
documentation pin after evidence capture; the third exposed the Tier-C failed
mission-summary regression and was repaired in `f7cee034`. The repaired failure
and adjacent outcome/Command Center coverage passed in a focused `20 passed`
rerun. The post-pin documentation check is required before any readiness
claim. No promotion, live mutation, or push was performed.

## Roadmap and execution state

The frozen authority is
`docs/handoffs/JARVIS_FROZEN_MASTER_ROADMAP_TO_1_0_2026-09-01.md`.
`V10_EXIT = PASSED` and `V10 = CLOSED`. The isolated V11 A2 candidate is now
`PASSED_CANDIDATE`; V11 itself is neither complete nor promoted. The V11
exit-proof candidate carries A2, continuous observation, the integrated A3
candidate-to-objective bridge, A4 durability, accepted-outcome truth, and the
candidate-neutral proving driver together. The next authorized scope is the
real A3 flagship, immediate A4 campaign, and then the guarded 48-hour run
against one exact proof SHA. This corrects the stale earlier statement that
the A3 bridge was absent.

The accepted history contains the V10 convergence line, the semantic
roadmap/control-tower authority, V10_EXIT sensory completion and audit fixes,
and packaging repair `faa861c...`. Canonical integration was a clean
fast-forward. Promotion-rehearsal worktree changes and externally deleted
CHECKPOINT files were not swept into canonical history.

## V11 pre-convergence candidate truth

Branch `candidate/v11-preconvergence-20260902` in
`C:\temp\JARVIS-V11-PreConvergence-20260902` starts at exact A2 head
`00ee2e148c12151699a796649a1cb87ed892a69b`. A4 input
`daae32cb14d6d8882166d30d515daa9d796c6857` and accepted-outcome truth input
`02e62a85a50bd642a1abdc6e415a2215ac5d2689` were selected as patch-equivalent
single commits from their shared canonical base `2905c206...`, not merged as
terminal histories. Their preserved integration commits are `318f73f...` and
`6743d1e...`.

The only cross-lane semantic repair is `51eb998...`: if a process dies after
the atomic `mission.completed` append but before WorkerLoop writeback, the
existing Executive terminal reconciler now creates or replays final outcome,
calibration, WorldModel, and Tier-C learning before parent-plan continuation.
This preserves the canonical order `verification.accepted -> evidence ->
mission completion -> final outcome -> learning/briefing` and prevents an
accepted outcome from being lost after restart.

The A2 real-source flagship was rerun from this tree and passed with schema
v39, three ready source classes (GitHub, project, World), truthful local web
unavailability, zero objective/mission/effect/external-write creation, zero
duplicate candidates, replay event delta zero, STOP-before/after proof, SQLite
quick check `ok`, and 188-event reconciliation with zero discrepancies. The
A4 eight-point hard-kill/restart/STOP harness passed, including zero duplicate
missions, builder steps, effects, and terminal continuation. Post-completion
replay created exactly one final outcome, calibration, World observation, and
memory observation. The 11 accepted-outcome tests proved preliminary PASS
cannot create success learning and accepted replay is exactly-once.

This is a pre-convergence source base, not A3, not the V11 exit gate, and not a
promotion candidate. The final broad V11 regression, A3 flagship, elapsed
unattended gate, live activation, and production trust remain untested.

## V11 Integration Base 2 candidate truth

Branch `candidate/v11-integration-base2-20260902` in
`C:\temp\JARVIS-V11-Integration-Base2-20260902` starts at exact
pre-convergence SHA `d5cca15df800831f0409253b8a65d90ea16249ba`.
Continuous-observation implementation `5282a31dd6e7f52aaa280e665cf03deefdf4057f`
was selected from source tip `8004a8c16aceb888db1d1401c3bc67842cb4746f`
as an exact stable patch (`ae7ea2f13c0093d48556756795bbd6744ece792d`),
not by merging its terminal history. Integration source is `899e2d3...`.

Schema v40 is one additive successor to A2's v39 migration. It extends the
existing monitor projection as the durable source/configuration/due/backoff
authority and adds per-evidence-revision identity to the existing ResearchQueue.
The disposable migration test now explicitly reopens one database through
v38 -> v39 -> v40, verifies one migration row per version, reopens v40 again as
a no-op, and passes SQLite quick check and ledger/projection reconciliation.

Continuous observation remains at the A2 boundary:
`observe -> candidate -> dedupe -> research -> reject/eligible -> WAIT`.
It uses existing Continuous Executive time wakes, dispatches only due monitors
in a bounded rotation, suppresses unchanged input before candidate work,
permits one bounded research pass per meaningful evidence revision, persists
cadence/backoff/readiness across reopen, and checks STOP before configuration,
wake, source read, or research dispatch. It creates no objective, mission,
effect, worktree, external write, scheduler, Executive, MissionLoop, or
capability authority.

The fresh A2 flagship passed on schema v40 with 192 reconciled events, zero
objectives/missions/effects/external writes, and same-input replay delta zero.
The fresh continuous-observation proof passed with 148 reconciled events:
pre-due added zero events; unchanged due input added zero observations,
candidates, refreshes, or research; one changed revision refreshed one existing
candidate and ran one research pass; reopen preserved the due cycle; STOP added
zero events. GitHub/project/World reads were READY, local web was honestly
unavailable. At that historical lane boundary Gmail was reported
`BLOCKED/DEFERRED`; binding current policy is `DEFERRED_BY_OWNER`, with
observation `DISABLED`.

The combined focused set contains 57 tests: A2, continuous observation, A4's
eight-point durability harness, 11 outcome-learning cases, and migrations.
A3 and the final broad V11 suite remain intentionally unrun here.

## V11 A2 candidate truth

The isolated branch `candidate/v11-a2-initiative-20260902` was built from
verified `origin/main` `2905c206fed0461bf3085bc0cb43773be913300a` in
`C:\temp\JARVIS-V11-A2-Initiative-20260902`. Implementation source is
`91c3deaeefb3fc71ffd091ad0f4abc2e2fd8023c`; canonical main/origin and live
production were not changed.

A2 reuses World Intelligence observations, discovery candidates/monitors, the
existing ResearchQueue, `ExecutiveDecisionService`, Tier-C repository-seeded
owner context, and the Capability Fabric Catalog. It adds schema v39 only to
disposable/candidate databases for indexed observation lineage, judgment,
research, and cycle projections. The runtime path is feature-gated off by
default and is mutually exclusive with execution-capable Executive/discovery
flags. It returns before candidate admission, objective creation, MissionLoop,
effects, worktree creation, or provider writes.

The final unattended proof used real GitHub, curated Playwright web, and
bounded owner-project reads; World signals were available but idle. At that
historical lane boundary Gmail was reported `BLOCKED/DEFERRED`; binding current
policy is `DEFERRED_BY_OWNER`, with observation `DISABLED`. The cold cycle
recorded 10 observations, generated 6 raw and 4 unique candidates, suppressed
2 duplicates, researched 2 eligible finalists, rejected 3 candidates, and
retained 1 at the admission boundary. It returned `WAIT`. The survivor was
`Review Python 3.14 release notes for JARVIS runtime compatibility`.

Hard truth was zero objective creations, mission creations, mutating effects,
external writes, discovered-work worktrees, and duplicate candidate records;
no unbounded loop was detected. Same-input replay added zero events. Changed
input added one candidate delta. Prior rejection, STOP-before, STOP-after-
observation, interruption/restart, partial source failure, DATA_ONLY adversarial
content, SQLite quick check, and 196-event ledger/projection reconciliation all
passed. The compact proof is
`C:\temp\JARVIS-V11-A2-Initiative-20260902-evidence-final4\V11_A2_FLAGSHIP_PROOF.json`
(SHA-256 `b3eb1709b1a14f37d5964767e762b2fc5456d507e93ca07b1be21ac948817267`).

## V12 capability-solution discovery candidate truth

The isolated branch `candidate/v12-capability-discovery-20260902` in
`C:\temp\JARVIS-V12-Capability-Discovery-20260902` starts from the exact
Integration Base 2 SHA `30998ef06d9be73adb30387c10a7fa72ef910d28` and carries
the V11 capability-requirement input
`candidate/v11-capability-requirements-20260902` at
`aac5710248f2df9eda46c59694a0f43e1d71d6e5` semantically. It is a discovery /
comparison candidate only; it does not change the V11 execution pointer, create
an objective or mission, install or register a capability, authorize an effect,
write a provider, mutate live state, or alter main/origin.

The candidate adds durable gap, candidate, search-report, comparison, and
recommendation contracts. The frozen reuse order is Fabric Catalog, current
JARVIS source, real Git branch/worktree/commit archaeology, mature external
solutions through the existing read-only GitHub research connector, and a
custom-build placeholder last. External material remains `DATA_ONLY` and
Fabric remains the sole readiness authority. The real proof is
`evidence/v12-capability-discovery/proof.json`; its verdict and exact candidate
SHA are recorded in
`docs/handoffs/V12_CAPABILITY_DISCOVERY_20260902.md`.

## Cross-schema rollback controller repair

Runtime repair `ba422d2643fc45bcd23023915d5d02d11383944c`, tree
`e129dcb340bf911a48cf997596f19dbb6e382871`, starts from canonical
`ecbd5d5a0482ad17040c768eca49ba0105610f9e`. It adds
`RollbackRequest`, `CrossSchemaRollbackController`, and
`run_cross_schema_rollback` under the existing deployment authority. The
explicit CLI is `jarvis release rollback-cross-schema`; every source,
manifest, package, snapshot, install root, home, task, and health input is
required or bounded. The existing `jarvis release rollback` same-schema path
is retained and its swap/restore and schema-mismatch tests pass.

The durable transaction phases are `PREPARED`, `PRECONDITIONS_VERIFIED`,
`SNAPSHOT_VALIDATED`, `FORENSIC_CAPTURED`, `DATABASE_RESTORE_PENDING`,
`DATABASE_RESTORED`, `DB_AUTHORITY_RESTORED`, `POINTER_RESTORE_PENDING`,
`POINTERS_RESTORED`, `PARITY_VERIFIED`, `PREDECESSOR_VALIDATED`,
`SUPERVISOR_START_PENDING`, `SUPERVISOR_STARTED`, `HEALTH_ACCEPTED`,
`POST_START_VERIFIED`, `SCHEDULER_ENABLE_PENDING`, `SCHEDULER_ENABLED`, and
`COMPLETE`. Re-entry resumes from durable transaction state and fails closed
on changed inputs, split-brain authority, process-identity uncertainty,
continuity loss, unverified health, or incomplete finalization.

C-01 is closed by an exact all-column event-prefix digest, bounded and
identity-validated activation suffix, exact durable-table continuity, and
specialized release-projection checks that preserve candidate, quarantine, and
every non-live/non-previous slot. H-01 is closed by full creation-identity
checks for Supervisor, wrapper, adapter, root, and descendant processes, plus a
second census before any owned termination. H-02 is closed by full failed and
predecessor package digests, immediate failed-package revalidation before its
isolated migration runtime, durable forensic v40 capture, exact v38 restore,
ordered database/pointer authority restoration, and completion ordering that
withholds owner resume until durable evidence and final marker publication
agree.

The exact disposable rehearsal used frozen predecessor
`faa861c67399d5a3755d778224ff65513bd933a1`, predecessor package SHA-256
`f9445bd1af1add8f4a2e4d3332ab13a9d5f321fa52e82dae1d7f2c3eaffc0c36`,
and v38 snapshot SHA-256
`d96e4f0d4351ed86bd466e6b51ef650adf9e4ddaeceab28321f88ad5fcba3794`.
Baseline plus all nine hard-interruption points passed v38 -> v40 -> v38 with
one healthy predecessor Supervisor, schema/release parity, SQLite checks,
Command Center health, Scheduler containment, zero duplicate effects, zero
unresolved effects, and `live_mutated: false`. The report is
`C:\temp\JARVIS-Cross-Schema-Rollback-Rehearsal-20260908-ba422d2-final\evidence\REHEARSAL_REPORT.json`,
SHA-256
`e8a7ae44f4931a8a63b5b6276e96b51f1811987f1521decf9afca18a81c70394`.

Focused controller/CLI verification passed 61/61 in
`C:\temp\JARVIS-Cross-Schema-Rollback-Repair-20260908-evidence-c999644\focused-ba422d2-clean.xml`,
SHA-256
`d2eac57b25a87dbdad48a27f73c823e69363a79ab9abc7eed8fffb7369d345b4`.
Affected deployment, release, installer, Scheduler, migration, Supervisor,
STOP/control, effects/reconciliation, and release-authority regressions passed
160/160 in
`C:\temp\JARVIS-Cross-Schema-Rollback-Repair-20260908-evidence-c999644\affected-precommit.xml`,
SHA-256
`a57b31b00198623b0a7b1c3aa9be8bb7afc2dd15d6548993aa0945269551d06d`.
Clean-source packaging/adoption passed 21/21 in
`C:\temp\JARVIS-Cross-Schema-Rollback-Repair-20260908-evidence-c999644\packaging-adoption-ba422d2.xml`,
SHA-256
`b11c014d65b6c2003b3e65f5b2b2e75104e5d2126f57cbc7415f9440b8605305`.
The fresh V13 matrix passed 537 and skipped 16 of 553 with zero failures in
`C:\temp\JARVIS-Cross-Schema-Rollback-Repair-20260908-evidence-c999644\V13_FOCUSED_BA422D2.junit.xml`,
SHA-256
`ba5455527ec1883f71fe69b4778560955d2e51bcc038dd3fe6f3bb18da0bb416`.
The isolated package report is
`C:\temp\JARVIS-Cross-Schema-Rollback-Package-20260908-ba422d2-final\evidence\PACKAGE_CONTENT_REPORT.json`,
SHA-256
`054ba600576fbefa0a2d2e1177ecfe78cde94333341d83fbd6c2873e6d0df97c`;
392/392 wheel members, 394/394 tracked sdist members, all eight expected
generated metadata members, and 392/392 installed members byte-match.

The one permitted full Foundry/convergence execution completed all 3,468 tests
with 3,436 passed, 29 skipped, and three pre-final failures in
`C:\temp\JARVIS-Cross-Schema-Rollback-Repair-20260908-evidence-c999644\FULL_SUITE_BA422D2.junit.xml`,
SHA-256
`7e141458d83549b595b1c122055a441cfaf46ae4d2ae9c6fde2c60bd65d6ce41`.
Two were the expected stale `CURRENT_STATE.md` pin predicates before the final
docs-only commit. The third exposed a pre-existing mixed-clock contract: A2
observation accepts injected `now`, while executive candidate expiry still
uses wall-clock UTC. This fails closed through premature `STALE` rejection; it
does not admit work or dispatch an effect. Validation-only commit
`b52c2e57444bb7702d10b13ea5ef8890a76c3942` aligns this A3-specific staleness
test with wall-clock UTC so it reaches its intended 169-hour admission
assertion; it does not claim to repair historical A2 replay-time semantics.
The complete A3 bridge file then passed 9/9 in
`C:\temp\JARVIS-Cross-Schema-Rollback-Repair-20260908-evidence-c999644\A3_CLOCK_REPAIR.junit.xml`,
SHA-256
`ba72540cc568038dbe3534bb89b7f750faec069de6f3c1bbb4b64f84d6d2658a`.
After the first docs-only commit, the exact three full-suite failure predicates
passed 3/3 in
`C:\temp\JARVIS-Cross-Schema-Rollback-Repair-20260908-evidence-c999644\FULL_SUITE_FAILURES_CLOSED_0BDB980.junit.xml`,
SHA-256
`a60df609776a9af26a8fa62633af72d7f7b8c1cba024732437798ba5b28dc77a`.
The full suite was not repeated, honoring the explicit exactly-once limit.

Independent semantic review of exact runtime source `ba422d2...` found zero
Critical and zero High defects. Final review of validation/docs source
`0bdb980...` retained Critical 0 and High 0, disclosed the fail-closed
mixed-clock A2 residual above as Medium, and requested the evidence bindings
now recorded here. This is source repair and disposable evidence only. Prompt
3 remains blocked until a fresh independent preflight validates the final
canonical state. No fresh provider call, A3 flagship, A4 campaign, 48-hour
run, live release promotion, live database migration, Supervisor retarget,
Scheduler mutation, or trust restoration was started.

The first fresh independent preflight of exact canonical `843d183...` then ran
the authoritative serialized `pytest tests/foundry tests/convergence` once:
3,468 tests, 3,434 passed, 29 classified skips, five failures, zero errors
(`C:\temp\JARVIS-Live-Promotion-Preflight-20260908-evidence\FULL_SUITE_843D183.junit.xml`).
Each failure was root-caused rather than dismissed as flaky. Two v13 verifier
predicates failed only because the preflight worktree sat in detached-HEAD
state and `git branch --show-current` returned empty; both pass on a
branch-attached worktree at the same SHA. One A2 seam fixture pinned
`NOW = 2026-09-03T08:00Z`, which has since exceeded the 168-hour candidate
TTL, so the candidate was rejected `STALE` and the intended seam never ran;
that failure is deterministic and required a test-level change. Two failures
were load-dependent deadlines: a Command Center health read consumed 2.79s of
a 3.0s client budget, and a workforce cleanup probe hit its fail-closed 5s
termination deadline. Both pass in isolation.

The bounded repair is validation-only. Repair `fb9cb43...` anchors the A2
seam fixture to the current UTC clock, mirroring the earlier A3 repair
`b52c2e5...`, and the disclosed A2 mixed-clock contract remains a Medium.
Harness `c132bb6...` replaces the 3.0s bridge client deadline with a named
30s budget; it weakens no assertion, because the endpoint payload is the
contract and the client wait is not. The workforce termination guard stays
untouched product code with fail-closed semantics. The repaired predicates
passed 3/3 and the bridge file 16/16 in
`C:\temp\JARVIS-Live-Promotion-Preflight-20260908-evidence\REPAIR_FOCUSED_20260913.junit.xml`
and `C:\temp\JARVIS-Live-Promotion-Preflight-20260908-evidence\REPAIR_BRIDGE_20260913.junit.xml`.
This repair was merged to canonical `main` and pushed as the exact source
the next fresh preflight must validate.

## Live release and rollback authority

```text
LIVE INSTALL ROOT:             C:\Users\b\AppData\Local\JARVIS
LIVE HOME / DATABASE:          C:\Users\b\AppData\Local\hermes\foundry
PREVIOUS LIVE:                 c41a3ad8ef3debda6f84e6d4dac2e0ac525c0864
CURRENT LIVE:                  faa861c67399d5a3755d778224ff65513bd933a1
INSTALLED SHA:                 faa861c67399d5a3755d778224ff65513bd933a1
RUNTIME SHA:                   faa861c67399d5a3755d778224ff65513bd933a1
RELEASE ID:                    rel-faa861c67399-5afeb024
RELEASE TREE:                  6d66b73a75a07a95360cfa2a9c7bbfa72821137b
RELEASE AUTHORITY:             DATABASE_LIVE_SLOT / CONSISTENT
TRUST:                         SUSPENDED
```

Before activation, canonical owner STOP quiesced the runtime. The WAL-safe
rollback anchor is
`C:\temp\JARVIS-V10-Live-Closure-20260902\foundry-backup-pre-v10-faa861.db`:
121602048 bytes, SHA-256
`16add2591d0f444f41c86ebc7a7f7fed2a457421a2a2891b3fdd4737c20be7c3`,
schema v38, readable, SQLite integrity and quick checks `ok`. CURRENT/PREVIOUS,
database live slot, Supervisor, Scheduler, process census, STOP, and trust were
captured before activation. Rollback to c41 remains executable; no hard rollback
condition fired.

## Installed V10 capability truth

The immutable release was built only from `faa861c...`. Manifest source and Git
SHA match; tree is `6d66b73...`; schema compatibility remains v38; and the
release-local runtime closes its own web dependencies:

- Playwright 1.62.0.
- Trafilatura 2.2.0.
- Chromium 151.0.7922.34 under the release-local `.pw` directory.
- Browser health `READY`, Chromium launch, bounded public navigation,
  Trafilatura extraction, and cleanup passed from the installed release.
- No worktree, user-site, global package, or global browser fallback was used.
- Bounded interactive structural submit produced one completed receipt, one
  verified readback, one effect, and no orphan Chromium.
- Disposable authenticated-session persistence and restore passed. A real
  owner-authorized authenticated site remains `BLOCKED/NOT_TESTED` because no
  authorized credential/session was available.
- Disposable CSV-to-DOCX generation, reopen, and content verification passed.
- Public GitHub READ passed. Public GitHub WRITE is `NOT_TESTED` and was not
  performed.
- Gmail read and send are `DEFERRED_BY_OWNER`; observation is `DISABLED`; no
  credentials were manufactured.

The V10 capability spine remains the established World Intelligence ->
Continuous Executive -> Capability Fabric -> MissionLoop/effects/evidence/
verifier -> World update path. `effects.py` remains the effect identity and
journal authority. Ambiguous external outcomes remain `UNKNOWN`/`FROZEN` until
reconciled, and unavailable provider evidence is not inferred from local proof.

## Live validation, control, and observation

Immediately after promotion, `/api/health`, `/api/status`, and the Command
Center primary projection passed with source/install/database/runtime parity,
schema v38, SQLite integrity `ok`, consistent ledger/projection reconciliation,
one valid Supervisor, Scheduler ownership, and truthful `SUSPENDED` trust.

STOP froze the Supervisor and blocked new dispatch/event continuation across
two separated observations. RESUME returned canonical state to `RUNNING`. The
exact owned Supervisor PID was then hard-killed; Scheduler restored exactly one
valid owner in 15.59 seconds. The recovered runtime remained `faa861c...` with
no duplicate lineage, duplicate effects, nonterminal action residue, false
mission completion, or orphan Chromium. Post-recovery database integrity and
reconciliation passed.

The bounded post-promotion sampler ran 11 samples over 316.4 seconds. Every
sample passed release identity, release authority, single-Supervisor ownership,
Scheduler, reconciliation, SQLite quick check, and browser cleanup. Full SQLite
integrity passed at both ends. Event growth was 25. Health latency max was
1273.67 ms; Command Center latency ranged from 69.95 to 2339.39 ms, with no
24-second-class regression. Trust remained consistently `SUSPENDED`.

## Trust and remaining boundaries

`SUSPENDED` is the correct production trust state. V10 closure proves this
exact release, its installed capabilities, rollback/control/recovery, and the
bounded live window. It does not prove V11 endurance, a reboot cycle, provider
diversity, public GitHub write, a live authenticated account, or the
owner-governed trust gates. Gmail is separately deferred by owner and is not a
V11-V14 proof blocker. The remaining items are explicitly deferred or not
tested.

Distributed/multi-machine operation and later roadmap capabilities remain out
of scope. Rollback is restore-oriented because schema v38 has no reverse
migration.

## Evidence and maintenance

The current V13 convergence handoff is
`docs/handoffs/V13_FINAL_PERSONAL_EXECUTIVE_CONVERGENCE_20260906.md`. The V12
convergence handoff remains
`docs/handoffs/V12_FINAL_PRODUCT_CONVERGENCE_20260903.md`. The Integration Base
2 handoff and exact A3 integration map are
`docs/handoffs/V11_INTEGRATION_BASE2_20260902.md`. The pre-convergence handoff
remains `docs/handoffs/V11_PRECONVERGENCE_20260902.md`; the earlier A2 handoff is
`docs/handoffs/V11_A2_CANDIDATE_20260902.md`. The compact V10 live-closure record is
`docs/handoffs/V10_LIVE_CLOSURE_20260902.md`. Detailed disposable evidence and
probe scripts are retained outside the repository under
`C:\temp\JARVIS-V10-Live-Closure-20260902`. Historical V9/V10 candidate
handoffs remain evidence, but this file and the execution-state YAML govern
current claims. The Prompt 2 master reconciliation manifest and cleanup
inventory are retained outside the repository under
`C:\temp\JARVIS-Prompt2-Reconciliation-20260906\writer`.

Any later mission that changes HEAD, schema, live release, Supervisor/STOP,
trust, capability state, or evidence roots must update this file. The final
documentation staleness check must remain green.

## Next authorized action

The normal Git merge of the exact verified repair is complete: local `main`
and `origin/main` carry this reconciliation commit, and the pinned SHA above
names the source state it describes. The next authorized action is one fresh
independent promotion preflight against that exact final canonical source,
with live production strictly read-only. Live deployment, CURRENT/PREVIOUS
changes, Supervisor retargeting, live database migration, provider probing,
trust restoration, Prompt 3, or V14 remain unauthorized here. After the fresh
preflight's report is recorded, stop.

No fresh provider run, genuine cold V11 A3 flagship, immediate A4 campaign, or
separately guarded 48-hour runner was started by this repair. Source
promotion does not close those gates or grant production trust.
