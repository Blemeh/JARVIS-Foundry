# Autonomy recovery: upstream selection repaired and proven live; next edge pinned

Date: 2026-10-04. Author: Hermes Agent. Status: **live vertical PROVEN; next
defect IDENTIFIED, NOT yet repaired.**

## Identity at checkpoint

| role | value |
|---|---|
| canonical `main` | `99661a863d67a0855b73c6476c5cf525d76a03ba` |
| `origin/main` | `99661a863d67a0855b73c6476c5cf525d76a03ba` |
| installed `CURRENT` | `99661a863d67a0855b73c6476c5cf525d76a03ba` |
| live release | `rel-99661a863d67-fa132ae4` (tree `36934ecce0581c24de7d04c3eda40dfa63b179b1`, schema 41) |
| `PREVIOUS` (rollback) | `639ec5771554510b853865c36aaec254291223cd` / `rel-639ec5771554-b5627b54` |
| release authority | `CONSISTENT`; `database_authority_mode=DATABASE_LIVE_SLOT`; runtime identity match `true` |
| Supervisor | PID 34052 (child 33288), `supervisor.started` release `99661a8`, singleton lease reclaimed from the 639ec57 instance |
| source repo | `C:\temp\JARVIS\wave2-main-promotion-20260924` (branch `main`) |

Canonical source, `origin/main`, installed release, database release authority
and the running runtime all identify `99661a8`. Schema unchanged at 41.

## The first broken edge that was repaired

`run_autonomy_cycle` -> `_run_discovery_starvation_fallback`
(`src/jarvis_foundry/executive/autonomy.py`) gated the whole discovery lane on

```
fed_any = any(entry.get("portfolio_item_id") and not entry.get("portfolio_error") ...)
if not fed_any:
    return None, None
```

evaluated **before** `select_next_work` ran. `fed_any` asked "did *this pass*
register something NEW?", not "is there an admissible item in the lane scope?".

The only live opportunity, `pw-dupkey-blemeh/jarvis-foundry-CI_FAILURE-f2757e1b6f92`
(REPAIR, OPEN, `resource-jarvis-canonical`, bound to the governing owner goal
`owner-c4dbe49e2dd7b49e11583655`), was already registered. Re-deriving it
raised the honest idempotency conflict; `_reopen_terminal_item_for_recovery`
then correctly refused because the item was OPEN, not terminal. The feed entry
therefore carried `portfolio_error` and no `portfolio_item_id`, `fed_any` was
False, and the lane returned `(None, None)` **without ever calling the
selector**.

Live proof of that shape is in the ledger at 345445-345453: candidate generated
-> scored -> reopen -> `portfolio.selection_recorded` rejecting the item on its
own recovery narrative -> `project.registered` (PROBING) ->
`executive.autonomy_cycle_recorded` WAITING -> `worker_loop.waiting_heartbeat`
"no dispatchable mission has an authoritative executable contract".

### Repair (6 commits, `e63083d`..`99661a8`)

Admission is now the canonical selector's verdict alone. The refusal path is
bounded by a budget, not a veto, and owner control is never charged to it:

- `_LANE_REFUSAL_BUDGET = 2` in `project_discovery.py`; a refusal is recorded as
  `executive.discovery_lane_not_materialized` and retires the item `BLOCKED`.
- `materialize_portfolio_selection` returns `owner_control_blocked: True` for a
  STOP/veto refusal; the lane records the receipt but does **not** retire the
  item and does **not** spend budget.
- The retirement reason carries a refusal ordinal, because `set_work_status`
  dedupes an identical payload *silently*.
- Lineage is claimed only when the lane actually materialized.

Three independent adversarial reviews were run; each REJECTed the previous
round and each finding was fixed. Final review: **PROMOTE-WITH-FOLLOWUPS**, no
blocking finding, vertical VERIFIED end to end through the real
`run_continuous_cycle` and REFUTED on untouched `639ec57`.

## Live proof (no operator tick anywhere in this chain)

Ledger `358887`-`359023`, release `99661a8`, unattended Continuous Executive:

```
358887 portfolio.selection_recorded        loop executive:<cycle>:discovery-fed
358888 mission.created                     mission-4646c7023b05
                                            created_from portfolio:pw-dupkey-...f2757e1b6f92
358892 portfolio.work_updated
358893 portfolio.work_status_changed       -> CONSUMED
358895 mission.step_contract_recorded x2   discovery-plan, discovery-execute
358896 portfolio.selection_materialized
358897 executive.plan_updated
358899 executive-scope:owner-goal:owner-c4dbe49e2dd7b49e11583655:execute.materialized
358900 portfolio.selection_claimed
358901 portfolio.capacity_lease_acquired
358902 portfolio.selection_released
358903 executive.objective_progress_recorded
358904 executive.continuation_decided      CONTINUE
358905 executive.autonomy_cycle_recorded
358906 worker_loop.waiting_ended           "actionable mission selected"
358907 mission.state_changed  x4           -> EXECUTING  run-ae47e5e40fd9
358908 agent_run.started                   provider opencode_go / muse-spark-1.3-contributor, role builder
358909 provider.dispatch_attempted
358910 worker.manifest_delivered           wm-0c9e628916b0
358911 worker.route_decided                hermes READY, bounded_implementation
358913-358920 provider.compute_reserved / compute_settled   (15510, 1889, 16021 tokens)
358992 verification.started  x3
358993 verification.failed  x3
358995 mission.outcome_finalized           FAILED, repair_count 3
358996 executive.calibration_recorded
358997 executive.outcome_measured
358998-359005 world.entity/observation/signal/pattern, memory.observed
359006 portfolio.outcome_recorded
359010 executive.plan_updated x2
359011 executive.objective_progress_recorded x2
359012 executive.continuation_decided
359014 executive.next_action_scheduled
359019 executive.wake_created x3
359022 executive.next_action_consumed
359023 executive.wake_triggered x4
```

Real worker artifact on disk, in the registered resource, produced by the
worker and not by an operator:

```
C:\temp\JARVIS\wave2-main-promotion-20260924\.jarvis\discovery\investigation-7d2d5178b6.md
6341 bytes, 2026-10-04 02:03:19
sections: "## Evidence" (l.8), "## Findings" (l.55), "## Uncertainties (explicit)" (l.77)
.jarvis-result.json -> {"files_changed": [".jarvis/discovery/investigation-7d2d5178b6.md"]}
```

Content is genuine: real `gh run list` / `gh run view` / `gh api` commands with
observed exit codes, run `32213129329`, conclusion `failure`, `jobs: []`,
`total_count: 0`, and the actual root cause (13 `run: & "..."` lines where a
bare `&` opens a YAML anchor).

Bounded retries and recovery semantics held: three attempts, then an honest
terminal FAILED, learning recorded, next-action wake created and consumed, and
a fresh Executive decision. No duplicate mission, no false completion.

## The NEXT first broken edge (identified, NOT repaired)

The worker **did** satisfy its contract. The contract criteria were
`.jarvis/discovery/investigation-7d2d5178b6.md exists` and `record states
evidence, findings, and uncertainties`, and the artifact on disk satisfies both.
The adapter also recovered a **valid** result claim
(`ingest_result_claim` -> `files_changed=['.jarvis/discovery/investigation-7d2d5178b6.md']`).

It still failed, with `error_class=evidence_mismatch`, `verdict=NOT_TESTED`,
`verifier_run_id=run-...-crash`, three times.

Exact edge:

1. `src/jarvis_foundry/workers/adapters/hermes.py:442` — `self._structured_verdict(stdout)`
   returns `None`, because the builder emitted free-text prose rather than the
   adapter's structured verdict grammar.
2. `hermes.py:454-457` — `verdict = VERDICT_NOT_TESTED`, note
   `"[adapter] no structured verdict found in the session output"`.
3. `hermes.py:461` — `ingest_result_claim` nevertheless returns a **valid** claim.
   The absent structured verdict is allowed to override the present, valid,
   contract-declared artifact evidence.
4. `src/jarvis_foundry/workers/mission_bridge.py:785` —
   `agree = report.agree and envelope.worker_status == COMPLETED`; with verdict
   `NOT_TESTED`, `report.agree` is False.
5. `mission_bridge.py:812-820` — `status = FAILED`,
   `error_class = "evidence_mismatch"`, `error_detail` empty.
6. Because the builder result is not COMPLETED/agreeing, `mission/loop.py` sends
   the step to the `-crash` verifier rather than an independent verification, so
   an independent verdict is never actually obtained.

Repair shape to evaluate next (NOT started): at `hermes.py:454-457`, a missing
structured verdict must not by itself force `NOT_TESTED` when the contract's
own declared artifacts are present, valid and inside scope. Precedence should be
artifact-evidence-first, verdict-grammar-second, and the honest `NOT_TESTED` must
remain for the case where neither is present. Do NOT relax
`mission_bridge.py:785` — "a mismatched or incomplete claim must not stay
COMPLETED or a later verifier PASS would accept unproven work" is a real
invariant.

## Findings that need no code change (do not "repair" these)

- **Owner authority is legitimate.** `owner-c4dbe49e2dd7b49e11583655` is
  `active=1`, `GOAL`, `GLOBAL`, updated `2026-09-29T14:11:29Z`, and is NOT in the
  revoked/superseded set. `owner_authority_revoked` applies to exactly six
  **superseded** objectives (`owner-09fd3f82156b3d4b1e2fbdc7`,
  `owner-4aed1870aab3d969b135d4ff`, `owner-8648cf218d664bc95a4f3a80`,
  `owner-8b7c02c43ea6f1b150297bae`, `owner-a94874ae6cca67a108336e92`,
  `owner-d3560e48bd22c0aaa07e6575`). This is NOT an owner-only blocker and no
  replacement owner goal is needed.
- **`resource-jarvis-canonical` is PROBING with CURRENT, correct evidence.** The
  live `project.registered` baseline at ledger 345451 is byte-identical to the
  worktree right now: `?? .github/`, `?? .jarvis-result.json`, `?? .jarvis/`,
  `?? docs/evidence/windows-ci-fix-20260930.md`. `.jarvis*` is prescribed
  exhaust; `.github/` and `docs/evidence/windows-ci-fix-20260930.md` are the
  previous FAILED mission's own unverified output (verifier FAIL,
  `evidence_mismatch`, repair_count 3). `refresh_git_resource_readiness` is
  correctly a no-op. Do NOT stamp READY and do NOT weaken containment. Adopting
  `.github/workflows/windows-ci.yml` onto `main` would activate a workflow whose
  `scripts/remaster/r3/*` harness does not exist on `main` — known-broken code.
  Also note this resource does **not** gate the discovery lane's dispatch:
  `dispatchable_contract_missions` / `select_dispatchable_mission` never consult
  capability readiness, which is why `mission-4646c7023b05` dispatched while the
  resource was PROBING.
- **The governing objective's `missing-executable-contract` refusal is
  CORRECT.** `owner-goal:owner-c4dbe49e2dd7b49e11583655` is a reprioritization
  directive: `output_contract=('durable outcome evidence',)` is generic, and it
  has no explicit success criteria, no `recipe_id`, and no validated intent
  block. `step_has_executable_contract` refuses it truthfully. Do not remove that
  plan blocker.
- `owner-goal:owner-c4cd1c69e3e7c75cc87a956f` and
  `owner-goal:owner-c50d8699981e579a5e559fdf` still show
  `resource_unconfigured` + `WAIT_FOR_RESOURCE`, but their portfolio items are
  already `COMPLETED` (missions PASSED). Those are stale objective projections,
  not live work.

## Deployment truth that must not be lost

The live worker-loop configuration is an **uncommitted, machine-local** file:

```
C:\Users\b\Documents\ChatGPT\Autonomous AI powered by Hoowen\JARVIS-Foundry\config\worker_routing.yaml
```

That checkout is **detached at `ddec34e`**, is not canonical, and is the
`--worker-loop-config` the scheduled task `JARVIS-Foundry-Supervisor` passes.
It is the only place `executive.continuous_enabled: true`,
`executive.continuous_discovery_enabled: true`, `enabled: true`, the hermes
worker root/python and `readiness.hermes.state: READY` exist. The canonical
`main` copy of that file still reads `enabled: false` / `status: UNCONFIGURED`
by design (fail-closed template).

Consequence: the Continuous Executive is effective **only** because the
Supervisor was pointed at that untracked file. This is a real deployment-truth
debt, not a defect of this repair. If that checkout is ever cleaned, the live
Executive silently reverts to fail-closed.

## Known follow-ups (recorded, not fixed)

- Orphan contract-less `READY` missions hold the single mission slot
  (`max_concurrent_missions=1`) with no reaper. Pre-existing, reachable from the
  main materialization path.
- `_bind_resolution_capabilities` (`portfolio_bridge.py:1030`) is unwrapped; it is
  provably unreachable for discovery items today because
  `_declared_fabric_capabilities` returns `()` for them, but if it ever fires the
  bound is incidental (the orphan mission) rather than durable.
- With the production 6-hour cheap-evidence throttle, the observable refusal
  budget is 1 unless discovery evidence drifts. The bound errs toward
  under-work, never busywork.
- The LIKE predicate in `_discovery_lane_refusals` is built by concatenation, so
  it assumes an item id never contains `"` or `\` (fails closed to a wrong count).
- `docs/CURRENT_STATE.md` is stale (reconciled 2026-09-28 to `01fa151b`) and
  `docs/roadmap/JARVIS_1_0_EXECUTION_STATE.yaml` is stale (2026-09-03, V10/V11
  era). Neither reflects `639ec57` or `99661a8`.

## Test evidence

- New suite: `tests/foundry/test_autonomy_recovery_open_item_consumption.py`, 14
  tests. Attribution: `639ec57` -> **11 fail / 3 pass**; `6043a58` -> 3 fail /
  11 pass; `99661a8` -> **14 pass / 0 fail**. The 3 that pass on `639ec57` are
  the guard-preservation tests, correctly not pinning this campaign.
- Green: the 6 autonomy-recovery suites, `test_project_discovery`,
  `test_discovery_candidates`, `test_registered_github_discovery`,
  `test_v7_executive_portfolio`, `test_v7_portfolio`,
  `test_portfolio_discovery_bridge`, `test_v8_executive_autonomy`,
  `test_v7_cross_lane_wiring`, `test_v7_stage_adversarial`,
  `test_post4_integration`, `test_v7_busywork_retirement`,
  `test_supervisor_v6_discovery`, `test_crash_recovery`,
  `test_worker_availability_recovery`.
- Pre-existing failures, each reproduced identically on untouched `639ec57`
  (not caused by this campaign):
  `test_v3_discovery_cycle::test_flag_defaults_off_and_config_roundtrip`,
  `test_goal2_supervisor_autonomy::test_heartbeat_stays_nonblocking_when_spawning_worker_loop`,
  `test_executive_campaign_planning::test_actionable_owner_outcomes_cross_into_campaign_and_canonical_mission`,
  `test_executive_campaign_planning::test_normal_worker_reaches_campaign_child_execution_boundary`.
  The first two are the machine-local owner training-consent policy at
  `runtime/config.py:273`.

## Provider authentication

`GET https://opencode.ai/zen/go/v1/models` with the configured Bearer credential
returned **HTTP 200** (key present, `sk-***`, 67 chars). The previous reviewer's
`HTTP 401: Invalid API key` was not this route's credential. Confirmed
independently live: `provider.compute_settled` with 15510 / 1889 / 16021 tokens
on `opencode_go / muse-spark-1.3-contributor`, `overrun: false`.