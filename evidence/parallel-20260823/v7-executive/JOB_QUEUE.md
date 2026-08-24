# V7 Executive and Portfolio Job Queue

Queue owner: `candidate/v7-executive-portfolio-20260823`
Frozen base: `6db467a2b0e6bdc37adbc4cbfac309418e33afec`

Each job ends with focused tests, an adversarial case, a local commit, and an evidence update. A green factory or regression suite closes only that job. The next justified job remains queued.

| Job | Scope | Initial state |
| --- | --- | --- |
| V7A-1 | Truth map and duplicate-authority seam | COMPLETED |
| V7A-2 | Durable multi-project portfolio records | COMPLETED |
| V7A-3 | Capacity, budgets, project share, leases | COMPLETED |
| V7A-4 | Global next-useful-work selector | COMPLETED |
| V7A-5 | Honest WAIT and deterministic wake conditions | COMPLETED |
| V7A-6 | Maintenance due and bounded revisit | COMPLETED |
| V7A-7 | Research and unknown queue | COMPLETED |
| V7A-8 | Temporary role teams and replacement workers | COMPLETED |
| V7A-9 | Fairness and starvation prevention | COMPLETED |
| V7A-10 | Bounded outcome calibration | COMPLETED |
| V7A-11 | Owner management-by-exception packet | COMPLETED |
| V7A-12 | Restart, replay, and concurrency hardening | COMPLETED |
| V7A-13 | Disposable three-project autonomous lab | COMPLETED |

## Throughput checkpoint

- Blocking first steps. Freeze the base and map existing authorities before writing code.
- Independent workstreams. The domain records and proving-lab scenarios can be designed independently, but they share one event contract.
- Shared mutable state. All portfolio mutations go through the new event-sourced owner and atomic database append path.
- Smallest safe decomposition. Selection, capacity, WAIT, leases, fairness, and replay stay together because splitting them would create competing priority authorities.

## Closure evidence

- `src/jarvis_foundry/evolution/executive_portfolio.py` is the additive event-sourced adapter. Existing candidate scoring, mission materialization, resource, STOP/veto, team, and outcome authorities remain the authorities.
- `tests/foundry/test_v7_executive_portfolio.py` is the focused adversarial gate: 13 tests passed on 2026-08-23.
- `scripts/v7_portfolio_lab.py` is a disposable three-project replay lab. Its result is recorded in `LAB_RESULT.json`.
- Candidate-only status is intentional. Runtime/V4 consumers still require semantic reconciliation against the later integrated canonical head.
