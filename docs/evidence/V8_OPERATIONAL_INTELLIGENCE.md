# V8 Operational Intelligence Candidate

Status: CANDIDATE — implementation and evidence are isolated to
`candidate/v8-operational-intelligence-20260828` from baseline
`ba652babf91e7faa06053864b965ff5f42abeb4b`.

## Authority boundary

The V8 surface is a read-only projection over the append-only event ledger and
existing canonical registries. It does not create a mission engine, objective
prioritizer, owner-control path, scheduler, provider credential store, or
second memory authority. Execution remains with the existing action broker,
connector registry, role dispatcher, Supervisor, and owner directives.

## Implemented slice

- Capability records now accept provider/model, permissions, context,
  tool-support, limits, and required-authority metadata. Supervisor-only
  `capability.observed` events carry bounded health, auth, availability,
  latency, reliability, and evidence without secrets.
- The existing role router exposes a pure capability-aware route planner. It
  rejects unknown health/auth/capability/context/cost data, supports explicit
  observed fallback candidates, and preserves the mandatory internal
  provider/model/no-fallback directive.
- `operational_intelligence.py` derives stable semantic fingerprints from
  event and projection evidence, scores novelty/impact/urgency/confidence/
  relevance/recurrence/reversibility/coverage, classifies the seven salience
  levels, deduplicates repeated events, tracks resolution, and emits bounded
  follow-ups, recurring-work observations, cross-source context, provider
  status, capability status, route status, Tier C memory quality, Tier B
  knowledge freshness/evidence/contradiction quality, and existing V6/V7
  evolution evidence plus governed improvement recommendations.
- The existing Command Center `discovery` surface carries an additive
  `operational_intelligence` subprojection. Briefing packets and periodic
  briefing payloads include open follow-ups and uncertainty without granting
  authority. The cinematic UI adds a compact signal panel only when the live
  projection is present; simulated mode remains explicit.

## Reproducible proof

Run from this worktree:

```text
$env:PYTHONPATH = (Join-Path (Get-Location) 'src')
py -3.13 scripts/v8_operational_intelligence_probe.py \
  --output evidence/v8-operational-intelligence/proof.json
```

The disposable proof covers capability observation, repeated provider
degradation, repeated agent failure, semantic deduplication, CREATE_WORK
classification, memory/knowledge provenance and duplicate candidates,
read-only evolution recommendations, explicit capable fallback, provider
recovery, Command Center surface wiring, projection non-mutation, and STOP
non-authorization. The
machine-readable result is `evidence/v8-operational-intelligence/proof.json`.

## Truth limits

No provider health/auth/capability sample means `UNKNOWN`; the candidate does
not claim live provider readiness. Recurring-work detection recommends review
but does not register Scheduler work. Due dates are `UNKNOWN` unless a source
event records one. Memory and knowledge duplicate/contradiction candidates
remain advisory until the existing governed curation/ingestion authorities
accept them. Evolution recommendations remain advisory until the existing
Supervisor-gated V6 discovery/cycle machinery records and verifies an
improvement. Cross-source context is deterministic evidence synthesis, not
model-generated belief. Distributed/multi-machine operation and external
connector credentials remain outside this candidate.

## Verification record

Final candidate verification:

- Focused Python regression set: 101 passed.
- Disposable V8 workflow: 13/13 checks true in
  `evidence/v8-operational-intelligence/proof.json`.
- Relevant full Python run with the shared Scheduler restart probe excluded:
  2,310 passed, 1 skipped. The unqualified run had exactly one failure in
  `tests/foundry/test_installer.py::test_task_restarts_after_hard_kill_via_repeating_trigger`:
  `daemon was not restarted by the repeating trigger`. This remains an
  explicit `BLOCKED_EXTERNAL_SHARED_WINDOWS_SCHEDULER` boundary, not a V8
  product claim.
- Command Center UI: typecheck, ESLint, 26 Vitest files / 121 tests, and Vite
  production build passed. The Impeccable detector returned `[]`; a disposable
  LIVE routed-snapshot capture verified the panel at desktop and mobile sizes.
- `compileall` and `git diff --check` passed. The final commit and exact SHA
  are recorded in the task handoff.

No merge, push, live database write, provider mutation, or Scheduler mutation
is authorized by this candidate.
