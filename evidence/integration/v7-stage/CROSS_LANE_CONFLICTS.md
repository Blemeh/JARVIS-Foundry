# V7 CROSS-LANE CONFLICTS

Mechanical conflicts: NONE (zero file overlap across V7A/V7B/V7C; each lane is
88 commits behind main, so only candidate-vs-main overlaps matter — none of the
three lanes touch files changed on main since base except V7C's
supervisor/service.py, to be resolved during its merge).

Semantic conflicts to reconcile during/after merges:

| ID | Conflict | Lanes | Resolution plan |
|---|---|---|---|
| S1 | Single scheduler authority: V7A portfolio selector must not become a second discovery/scheduler authority | V7A vs main(V3) | Portfolio consumes canonical candidates/scoring; verify no duplicate loop |
| S2 | V7B must reuse V5 effect machinery (connector registry, idempotency, attempt journal) not reimplement it | V7B vs main(V5) | Inspect operations/* imports; adapt to current signatures if drifted |
| S3 | V7C resilience hooks inside supervisor heartbeat must compose with newer supervisor service behavior | V7C vs main(supervisor) | Semantic merge in supervisor/service.py; keep both behaviors |
| S4 | Incident priority vs STOP: STOP outranks incident automation | V7C vs main(STOP) | Verify STOP gate precedes incident actions in hook order |
| S5 | V6 boundary: incident patterns → improvement evidence without recursive loops or self-certification | V7C vs V6 | Bounded evidence bridge only; verifier independence preserved |
| S6 | Owner truth channel: V4 briefings must stay material-exception-only | V7A vs V4 | Map portfolio outcomes onto existing briefing thresholds |

Each item must be closed with code + test evidence before Phase 6.
