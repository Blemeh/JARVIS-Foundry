# Canonical Hybrid Worker Integration Map (Phase 0 deliverable)

Date: 2026-08-16. Authoritative state was reconciled from Git and the live
machine BEFORE any mutation; nothing in this map is copied from the
experimental candidate's narrative.

## Frozen/reconciled state

```text
CANONICAL JARVIS (C:\Users\b\Documents\ChatGPT\Autonomous AI powered by Hoowen\JARVIS-Foundry)
  HEAD:            e715b812dc49a04e1a0294b8dbd18c48abacb9f8 (main)
  dirty:           6 pre-existing untracked owner docs only
  post-baseline:   v8 hybrid-memory (hybrid_memory package, schema 33,
                   worker_context Tier C extension, supervisor STOP fix,
                   pyyaml runtime dep, .gitignore restore) + promotion fixes

HYBRID CANDIDATE (C:\Users\b\Documents\JARVIS-Foundry-HybridWorkers)
  HEAD:            491910c0dc48a66653f7fb88f7aeb034b587548b (integration/hybrid-worker)
  gate evidence:   HYBRID_WORKER_INTEGRATION_GATE PASS (H1-H22) + verifier PASS

EXTERNAL FUSION (C:\Users\b\Documents\JARVIS-External-Candidate)
  HEAD:            5e90a5dc0c6373da7e0f95f85e979b62b9f9f070 (frozen, READ ONLY)

INTEGRATION WORKTREE: C:\temp\JARVIS-Canonical-HybridWorkers
  branch:          integration/canonical-hybrid-worker (from e715b812)
```

## Semantic delta 93a3ddc -> e715b812 (canonical post-baseline work)

- `src/jarvis_foundry/hybrid_memory/*` — new V8 hybrid-memory package
  (compiler, blocks, lifecycle, recall, versioning, curation, defense,
  procedures) — unrelated to the worker layer, must be preserved.
- `migrations.py` schema 33 (tier_c tables), `projections.py` Tier C
  projections, `reconcile.py` Tier C table keys — additive schema work.
- `worker_context.py` — V8 Tier C context extension (additive; the classic
  `deliver_worker_context` contract is untouched).
- `supervisor/service.py` — STOP freeze keyed per (stop_id, instance_id)
  (live crash-loop fix).
- `pyproject.toml` — pyyaml runtime dependency.
- `.gitignore` — restored to the canonical 55-rule set.
- v8 evidence/scripts/tests.

## Candidate delta 93a3ddc -> 491910c (26 new files, all additions)

- `src/jarvis_foundry/workers/` — contracts, envelope, router, evidence,
  isolation, lab, orchestrator, adapters (hermes/openhands/fakes/registry).
- `tests/foundry/test_worker_*.py` + `worker_test_helpers.py` (71 tests).
- `docs/architecture/06_HYBRID_WORKER_INTEGRATION.md`, gate + verifier
  evidence, `config/templates/worker_routing.example.yaml`.

## Change classification

| candidate change | classification | canonical action |
|---|---|---|
| workers package (contracts/envelope/router/evidence/isolation) | directly reusable | copy as-is; no canonical file overlap |
| adapters/hermes.py | directly reusable | copy as-is (same safety invariants as canonical roles.adapter) |
| adapters/openhands.py | needs adaptation | containment upgraded: OH_PERSISTENCE_DIR + disposable USERPROFILE/HOME/TMP/TEMP + fail-closed owner diff (Phase 3) |
| workers/orchestrator.py | needs adaptation | added worker.crashed partial-state capture (Path D); STOP/fallback already present |
| workers/mission_bridge.py | NEW (canonical adaptation) | RoleAdapter bridge so MissionLoop dispatches through workers |
| worker tests | directly reusable | copied (71) + containment tests (4) + bridge tests (2) + canonical gate (18) |
| docs/config | needs adaptation | rewritten for canonical placement + containment evidence |
| experiment-only bits (benchmark harness, lab scripts in var/) | must not enter canonical | excluded (var/ is gitignored anyway) |

No file in the candidate delta overlaps a file changed by canonical v8
work; the integration is purely additive to current canonical main.
