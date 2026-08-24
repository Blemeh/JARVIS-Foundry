# V7 CANDIDATE MATRIX

Base used by all lanes: 6db467a2b0e6bdc37adbc4cbfac309418e33afec
Canonical target: main = origin/main = 38753a825bc02cba0acb829d03c20b41a2d7cb0a

| Field | V7A Executive Portfolio | V7B Broad Operations | V7C Resilience/Continuity |
|---|---|---|---|
| branch | candidate/v7-executive-portfolio-20260823 | candidate/v7-broad-operations-20260823 | candidate/v7-resilience-continuity-20260823 |
| local SHA | 9accb24d8f6e0a9bbc1aa948e325d5d05301bc5c | 8662fae7c7018a55c1a6c1870605fa946f8da417 | 224220594fb94e14091f422e7ede0adba2d825b0 |
| remote SHA | same (origin exists, equal) | same (origin exists, equal) | same (origin exists, equal) |
| merge-base with main | 6db467a2 | 6db467a2 | 6db467a2 |
| unique commits | 4 | 3 | 26 |
| behind main | 88 | 88 | 88 |
| unique files | 9 | 25 | 15 |
| file overlap w/ others | none | none | none |
| classification | UNIQUE_ACCEPTED_WORK | UNIQUE_ACCEPTED_WORK | UNIQUE_ACCEPTED_WORK |

## Lane contents

V7A:
- src/jarvis_foundry/evolution/executive_portfolio.py (new)
- scripts/v7_portfolio_lab.py (new)
- tests/foundry/test_v7_executive_portfolio.py (new)
- evidence/parallel-20260823/v7-executive/* (docs)

V7B:
- src/jarvis_foundry/operations/* — 11 new modules: procedure, targets,
  project, deployment, services, composition, reuse, planning, adversarial, lab, __init__
- tests/foundry/test_v7b_*.py — 8 new test modules
- evidence/parallel-20260823/v7-operations/* (docs)

V7C:
- src/jarvis_foundry/resilience/{__init__,core}.py (new)
- src/jarvis_foundry/control/capabilities.py (new)
- src/jarvis_foundry/supervisor/service.py (MODIFIED — resilience hook into heartbeat)
- scripts/v7_resilience_fault_matrix.py (new)
- tests/foundry/test_v7_resilience.py (new)
- evidence/parallel-20260823/v7-resilience/* (docs)

## Semantic risks

1. V7A portfolio selector must consume canonical V3/V4/V5/V6 sources, not a
   second scheduler authority. Verify adapter wiring against current main's
   supervisor/worker-loop and executive-intelligence code.
2. V7B operations must reuse V5 capability/effect machinery. Verify imports of
   connector/effect/idempotency APIs against their current main signatures
   (main advanced 88 commits; APIs may have evolved).
3. V7C modifies supervisor/service.py — the single highest-risk merge because
   main also evolved supervisor service since base. Must resolve semantics so
   resilience hooks compose with newer supervisor behavior.
4. V7A→V7B→V7C cross-lane contracts (mission→procedure→incident) must be wired;
   lanes were built in parallel against handoff docs only.

## Integration actions

- V7A: forward-merge onto integration/v7-full-autonomy-20260824 (from main).
- V7B: merge next; resolve semantic drift vs V5/current main if any.
- V7C: merge last; careful resolution inside supervisor/service.py.
- Then cross-lane wiring per Phase 3 contract.

No UNKNOWN remains: all three lanes are UNIQUE_ACCEPTED_WORK.
No ALREADY_CANONICAL / FULLY_SUPERSEDED / PARTIALLY_ABSORBED lanes exist.
