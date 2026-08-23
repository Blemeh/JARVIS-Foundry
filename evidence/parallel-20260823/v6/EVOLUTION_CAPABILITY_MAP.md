# V6 Evolution Capability Map (2026-08-23)

Classification of existing self-evolution machinery per the lane mandate.
ACTIVE = wired into a governed path that runs today; PROVEN_BUT_NOT_CONNECTED =
tests prove it, no production/continuous caller; PARTIAL = exists with gaps;
SUPERSEDED = older equivalent retained for audit; BROKEN = fails today;
ABSENT = must be built.

| Capability | Module(s) | Status | Notes |
|---|---|---|---|
| Improvement records (frozen hypothesis, MMI, guardrails, risk) | evolution/records.py | ACTIVE | used by discover + cycle + registry |
| Deterministic risk classification (unknown→R3 fail-closed) | records.classify_risk | ACTIVE | monotonic raises enforced |
| Release registry + immutable manifests | evolution/registry.py | ACTIVE | verify_manifest re-hashes artifacts |
| Slot pointers (live/candidate/previous/quarantine) | registry.set_slot/slot_pointers | ACTIVE | supervisor-owned |
| Registry-level promote/rollback (S2) | registry.promote_candidate/rollback_live/quarantine_release | SUPERSEDED | only own tests call them; governed path is promote.py |
| Isolated candidate builder (sanitized env, locks, reaping, outside-write refusal) | evolution/builder.py | ACTIVE | Windows process liveness via OpenProcess |
| Reproducible evaluation lab (hash-verified workloads) | evolution/lab.py | ACTIVE | comparison fails closed on window mismatch |
| Independent verifier boundary (forgery guards) | evolution/verify.py | PARTIAL | boundary itself is strong; see next row |
| Cycle-ladder verification | evolution/cycle.py step 5 | BROKEN (by design gap) | fabricates its own PASS verdict inside run_cycle — self-certification through the canonical path; JOB V6-9 repairs this lane-side |
| Zero-effect shadow mode | evolution/shadow.py (+ ShadowSink) | PARTIAL | proven; not yet a first-class experiment stage in cycle ladder |
| Canary controller (bounded, supervisor-owned) | evolution/canary.py | ACTIVE | R2/R3 required by promote gate |
| Atomic promotion + crash recovery | evolution/promote.py | ACTIVE | gate chain incl. trusted verdict + measured improvement |
| Freeze-first rollback + reconcile check | promote.rollback_release | ACTIVE | triggers vocabulary enforced |
| Restore points (verified backups, data-loss analysis, rehearsals) | evolution/restore.py | ACTIVE | disposable-copy rehearsals |
| Observation-driven discovery (4 observation types) | evolution/discover.py | PROVEN_BUT_NOT_CONNECTED | no producer anywhere in src/ derives observations from durable outcomes — nothing calls candidate_improvements outside tests |
| End-to-end cycle ladder | evolution/cycle.py | PARTIAL | runs real stages but verdict is fabricated (above) and canary ticks are caller-supplied rather than measured |
| Engineering/operational acceptance | evolution/acceptance.py | ACTIVE | operational minimums currently unmet until harness runs |
| Event-first scheduler | evolution/scheduler.py | PARTIAL | fires ScheduleItems but has NO evolution integration (no discovery/cycle driving) |
| Portfolio executive | evolution/portfolio.py | ACTIVE | consumed by V7 convergence surfaces |
| Supervisor control plane (observe default, STOP gate) | supervisor/evolution_control.py | ACTIVE | all effectful ops pinned to controller=='supervisor' |
| Weakness detector over durable outcomes | — | ABSENT | agent_runs failures / eval error rates / costs exist but are never aggregated into evidence-backed weaknesses |
| Baseline/counterfactual measurement layer w/ explicit UNKNOWN | lab.py compare_evaluations | PARTIAL | comparable-window checks exist; no distribution/confounder capture; no INSUFFICIENT_SAMPLE state |
| Experiment plan compiler (hypothesis → bounded plan) | — | ABSENT | cycle takes raw commands; no forbidden-surface/budget compilation from hypothesis+risk |
| Typed benchmark engine (multi-metric, no single scalar) | lab.py | PARTIAL | error_rate/duration only via _metric_key mapping |
| Verifier orchestration (independence enforcement at cycle level) | verify.py + cycle.py | PARTIAL | identity binding strong; orchestration absent inside cycles (fabricated verdict) |
| Disposable canary execution against release slots | canary.py | PARTIAL | ledger canaries exist; executable traffic allocation against disposable slots ABSENT |
| Promotion eligibility decision function (ELIGIBLE/REJECTED/BLOCKED/UNKNOWN) | promote._gate_promotion | PARTIAL | raises on refusal; no typed decision record distinguishing BLOCKED vs REJECTED vs UNKNOWN |
| Bad-candidate factory (attack suite) | — | ABSENT | tests attack pieces individually; no reusable factory producing deliberately bad disposable candidates end-to-end |
| Disposable promotion + rollback loop proof | promote.py + restore.py | PARTIAL | ledger-proven; full loop with disposable release ROOTS needs the harness |
| Outcome feedback (predicted vs actual, calibration) | — | ABSENT | cycles table ends at outcome; nothing closes the learning loop |
| Next-improvement selector (impact/confidence/cost/risk; WAITING) | portfolio.py scoring | PARTIAL | project scoring exists; improvement-specific selector with rejected-alternatives + reasons ABSENT |
| Multi-cycle autonomous evolution harness | — | ABSENT | acceptance.py counts minimums nobody produces |

## Producer/consumer truth table (discovery inputs)

| Observation type | Consumed by | Produced by |
|---|---|---|
| metric_exceedances | discover.candidate_improvements | NOTHING in src/ (tests only) |
| guardrail_breaches | discover.candidate_improvements | NOTHING in src/ |
| verifier_findings | discover.candidate_improvements | NOTHING in src/ |
| task_failures | discover.candidate_improvements | NOTHING in src/ |

Durable outcome sources available to a future detector: agent_runs
(status/failure_class/cost_json), evaluation_runs (error_rate/duration_s/
cost_usd), verifications (criteria_results/findings), canaries (raw health
metrics), cost_measurements, missions/steps. This is the V6-2 surface.
