# Production Proving and Hardening — 2026-08-29

Status: `FAIL` for unrestricted production trust; engineering hardening and
the bounded proving slice passed. The isolated lane was intentionally not
promoted into the live installation.

## Scope and identity

- Authoritative live base at entry: `ba652babf91e7faa06053864b965ff5f42abeb4b`.
- Isolated lane: `candidate/final-production-convergence-20260828`.
- Candidate proving head after the hardening milestones:
  `7e222d8b983c5f55c9576cbb0a01576d6c3578b1` (tree
  `e8b0e87e348cf445df2c08532feead7360bbb5c4`).
- Canonical checkout remained dirty/preserved and was not modified.
- Live install, database, Scheduler task, and live processes were not
  retargeted by this lane.

## Iterations and landed changes

| Iteration | Commit | Change | Evidence |
|---|---|---|---|
| 1 | `5837924` | Added the canonical `LIMITED_AUTONOMY` trust stage, explicit bounded/full evidence gates, strict staged promotion, shared state vocabulary, fail-closed malformed heartbeat projection, and a read-only `scripts/trust_gate_probe.py`. | Trust/projection/runtime slice: 135/135 PASS. |
| 2 | `66e5e06` | Bounded memoization for deterministic knowledge normalization, removing repeated regex work during full ledger reconciliation. | Knowledge/reconciliation slice: 47/47 PASS; profiled `production_status` fell from 27.5 s to ~4.3–4.6 s on the live-sized ledger with the same consistent result. |
| 3 | `7e222d8` | Added restart persistence coverage for `LIMITED_AUTONOMY`. | Trust lifecycle: 12/12 PASS. |

No change introduced a second mission engine, ledger, STOP plane, owner path,
or runtime authority.

## Verification and failure matrix

| Area | Result | Evidence / boundary |
|---|---|---|
| Full repository functional suite | PASS | 2,310 collected; 0 functional failures, 1 explicit skip. The only two red rows were documentation freshness checks while the code commits intentionally preceded the final docs-only pin. |
| Focused post-hardening reliability slice | PASS | 188/188, 0 failures/errors/skips, 106.625 s (`C:\\temp\\jarvis-production-proving-post-hardening-20260829.xml`). |
| Trust state/projection/replay/forgery | PASS | Limited/full gate, Supervisor-only transition, idempotent evidence, malformed heartbeat, projection rebuild, and restart persistence tests. |
| Exactly-once and duplicate ownership | PASS | `runtime_fault_repro.py`: one provider call, outcomes `DELIVERED` + `UNKNOWN`, duplicate Supervisor blocked. |
| Owner controls and restart lifecycle | PASS (disposable) | `runtime_control_repro.py`: STOP/RESTART, duplicate launch refusal, reused instance generation, final STOPPED; 22 events. |
| Diagnostic STOP/freeze | PASS (disposable) | `runtime_diagnostic_repro.py`: running correlation and BLOCKED health under STOP. |
| Release identity/rollback | PASS (disposable) | `release_identity_repro.py`: two immutable identities, three starts, exact rollback pointer and schema 37. |
| SQLite backup/rebuild/reconcile | PASS | Lane 2 read-only probe and focused reconciliation suite; integrity `ok`, no mismatches, no live mutation. |
| Live source/install/DB/runtime parity at entry | PASS | `C:\\temp\\jarvis-production-proving-lane2-entry-20260828.json`: all identities `ba652…`, exactly one valid owner, Scheduler PASS, schema 37, reconciliation consistent. |
| Live endurance observation | PASS (bounded) | ~5m13s across 15 `/api/health` samples: all `OK/ready`, stable PID 35252, `MATCH`, schema 37/37, normal ledger growth, RSS ~52.9–55.5 MB. |
| Live health latency attack | PASS (bounded) | 12 samples: 673–971 ms, average 740.3 ms, p95 971 ms; no errors. One earlier 5.0 s read under transient SQLite contention remained `OK`. |
| Live trust evidence | FAIL / WAITING | State `SUSPENDED`, recommendation `SUSPENDED`, no recorded evidence; both limited and full gates are `WAITING`. This is the truthful safety state, not a test failure. |
| Live owner STOP/PAUSE/RESUME/REVOKE mutation | NOT_RUN in this lane | Live mutation was kept out of the isolated candidate lane; disposable controls passed. |
| Live hard-kill/restart | NOT_RUN in this lane | Prior historical evidence exists, but no new destructive live action was issued here. |
| Physical reboot | NOT_TESTED | Requires an approved maintenance window and physical host interruption. |
| Real provider/network effects | NOT_TESTED / BLOCKED | Disposable fake-provider faults passed; no external provider credentials/effects were invoked. |
| 12-hour / 24-hour real soak | NOT_RUN | The bounded live observation is not a long-duration soak. |
| Live candidate promotion/rollback | NOT_RUN | Candidate remains isolated; no live pointer, DB, Scheduler, or process mutation. |

## Trust gate truth

The live probe (`C:\\temp\\jarvis-trust-gate-probe-20260829.json`) reports:

- `state`: `SUSPENDED`;
- `recommended_state`: `SUSPENDED`;
- `LIMITED_AUTONOMY`: `WAITING`, missing all ten bounded-safety dimensions;
- `TRUSTED_AUTONOMOUS`: `WAITING`, missing all eleven full dimensions;
- `production_mutation`: `false`.

The candidate now provides the explicit safe progression
`SUSPENDED → PROVING → LIMITED_AUTONOMY → TRUSTED_AUTONOMOUS`. Limited
autonomy requires local runtime/recovery, verifier, release, schema, and
owner-control evidence; `provider_routing` remains a full-autonomy gate.
No live evidence was manufactured from tests or elapsed health polls.

## Exit assessment

The engineering predicate for the isolated hardening work advanced: the trust
model is staged and fail-closed, the large-ledger reconciliation cost is
bounded, the focused and full functional suites are green apart from the
expected pre-pin documentation rows, and the live base remained healthy during
the bounded observation. The production-trust predicate is not met because
long soak, physical reboot, real provider/network proof, and approved live
destructive recovery are still absent. Recommended live trust state remains
`SUSPENDED`.

Next reliability work requires an approved maintenance window: promote the
candidate through the guarded release procedure, collect signed trust evidence
for each gate, run the exact live hard-kill/restart and reboot drills, and keep
real provider/network rows separate from disposable evidence.
