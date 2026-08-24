# Unattended Harness Job Queue

The queue is ordered. Each job ends with focused tests, negative tests where
applicable, a disposable proof, an evidence checkpoint, and a verified commit.
Long profiles are prepared but are not run in this lane.

| Job | Scope | Status |
| --- | --- | --- |
| H1 | Inventory existing soak, fault, restart, effect, DB, release, and resource assets | COMPLETE in `INITIAL_STATE.md` |
| H2 | Declarative scenario and profile contract | PASS |
| H3 | Real-time and accelerated disposable clocks | PASS |
| H4 | Durable checkpoint and resume | PASS |
| H5 | Invariant sampler | PASS |
| H6 | Autonomous continuation metric | PASS |
| H7 | Busywork detector | PASS with negative control |
| H8 | Owner-spam metric | PASS with negative control |
| H9 | Owned process crash injector | PASS |
| H10 | Network/provider fault injector | PASS |
| H11 | Effect ambiguity and reconciliation | PASS |
| H12 | Disposable database fault scenarios | PASS |
| H13 | Resource and cost envelope | PASS with negative control |
| H14 | Release degradation and rollback | PASS |
| H15 | Process restart contract | PASS |
| H16 | Deterministic fault schedule | PASS |
| H17 | Machine-readable verdict engine | PASS |
| H18 | Complete evidence bundle and hashes | PASS |
| H19 | Accelerated 12-hour-equivalent proof | PASS, accelerated label only |
| H20 | Short real-time disposable proof | PASS, 900.018 seconds |
| H21 | Harness interruption and resume proof | PASS |
| H22 | False-PASS adversarial factory | PASS, 8/8 attacks rejected |
| H23 | QUICK_30M through REBOOT_ASSISTED_24H profiles | READY, not run |

## Completion rule

No job is closed by a green unit suite alone. A job is closed only when its
machine-readable evidence is durable, its disposable target is cleaned, and the
next engineer can rerun the same command from a clean candidate checkout.
