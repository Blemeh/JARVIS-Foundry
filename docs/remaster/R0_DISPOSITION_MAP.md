# R0 Preliminary Disposition Map

This is the first purpose-led disposition. R2 must classify every individual
module, test, migration, adapter, capability, and live path before repair.

| Subsystem/artifact | Disposition | R0 reason | R2/R4 ownership |
|---|---|---|---|
| Git baseline `93a3ddc` and V1–V7 history | KEEP | immutable rollback/history claim source | release/data compatibility |
| Six owner files in main | KEEP | protected owner material | root evidence custodian |
| Existing event rows | KEEP | historical facts/claims must remain byte-unchanged | data compatibility |
| Event ledger implementation | REWRITE | no header chain, DB enforcement, or narrow typed port | trusted kernel |
| 1,670-line projection dispatcher | REWRITE | monolithic and silently ignores unknown events | trusted kernel |
| Schema/migration history | REFACTOR | valuable compatibility base; weak constraints and no downgrade unit | data/recovery |
| Supervisor watchdog | REFACTOR | STOP/lease/heartbeat base works; no executive loop | trusted kernel/runtime |
| Owner STOP sentinel | KEEP + HARDEN | reachable and provider-independent | trusted kernel/control |
| Other owner directives/controls | REWRITE | incomplete commands, stale authority projection, no hosted path | trusted kernel/control |
| Scheduler | REWRITE | unreachable, disabled, and event-only firing | runtime executive |
| Portfolio | REWRITE | unreachable and incomplete decision model | runtime executive |
| Goal/mission state machines | REWRITE | duplicated/parallel transitions and no durable executive ownership | trusted kernel |
| Legacy mission loop | UNSAFE / REWRITE | bypasses newer effect authorization | trusted kernel/execution |
| ActionEnvelope/effect pipeline | UNSAFE / REWRITE | disconnected; performer and target are not bound | trusted kernel/execution |
| Capability broker | REWRITE | ledger readiness only; no credential delivery/revocation boundary | security |
| Local scratch filesystem connector | REFACTOR | useful proof base, not a general governed adapter | adapters |
| Browser/email/calendar/messaging/cloud/Git remote | NOT_IMPLEMENTED or BLOCKED_EXTERNAL | hard-coded blocked; no real after-state proof | adapters |
| Financial action families | DELETE / deterministic DENY | superseded and explicitly out of scope | policy/security |
| Hermes worker adapter | REFACTOR | isolated execution exists but is not runtime-wired | worker gateway |
| Discovery candidate heuristics | REWRITE | lexical scoring is not durable-goal reasoning | intelligence |
| Research/monitor interfaces | NOT_IMPLEMENTED / REWRITE | no real source polling | intelligence/adapters |
| Knowledge/claim primitives | REFACTOR | useful base, stale live data, weak continuous path | memory |
| Vault indexing/retrieval | REFACTOR | useful primitives need governed continuous runtime and adversarial QA | memory |
| Obsidian writer/consolidation | UNSAFE / REFACTOR | direct writes lack kernel STOP/veto/effect enforcement | memory/execution |
| Command Center | REFACTOR | preview/test surface; no auto-hosting; DB errors hidden as empties | owner experience |
| Briefings | NOT_IMPLEMENTED | planner/local-test-sink only | owner experience/adapters |
| Voice | NOT_IMPLEMENTED / BLOCKED_EXTERNAL | fixture-only, no Albanian integrated path/provider receipt | owner experience/adapters |
| Evolution records/lab | REFACTOR | useful test scaffolding exists | evolution |
| Evolution cycle/promotion controller | UNSAFE / REWRITE | disconnected and records manufactured hashes/evidence | trusted release controller |
| V7 isolated cycle DB | KEEP AS HISTORICAL EVIDENCE | proves isolated construction only | evidence custodian |
| V7 ignored/temp verdicts | KEEP AS HISTORICAL CLAIMS | now copied/hash-bound; cannot retroactively prove PASS | evidence custodian |
| Existing tests | KEEP + RECLASSIFY | regression substrate, not live capability proof | test architecture |
| Existing evidence scripts/artifacts | REFACTOR | useful probes mixed with mutable/ignored artifacts and false claims | evidence/release |
| `PROJECT_STATE.md` | REWRITE | stale, internally contradictory, conflates engineering and operation | root/product truth |
| Architecture/governance docs | REFACTOR | valuable invariants; financial scope and accepted status are superseded | root/security |

Disposition meanings:

- KEEP: preserve unchanged as accepted history or trusted input.
- REFACTOR: retain behavior/data but put it behind the new contract.
- REWRITE: replace the implementation while preserving required compatibility.
- DELETE: remove from the new reachable product after proving no required history
  or compatibility dependency.
- NOT_IMPLEMENTED: purpose requires the capability but current source does not.
- UNSAFE: code must remain unreachable until replaced and independently accepted.
- BLOCKED_EXTERNAL: implementation can proceed, but ACTIVE status requires a real
  owner-controlled system/provider and after-state proof.
