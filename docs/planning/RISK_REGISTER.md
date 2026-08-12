# Risk Register

Status: OPEN

| ID | Risk | Impact | Autonomous treatment |
|---|---|---|---|
| RK-01 | Hermes failure kills supervision | Critical | Separate Supervisor and kill tests. |
| RK-02 | Retrieved text forges owner control | Critical | Authenticated directives outside content. |
| RK-03 | Whole-vault edits corrupt meaning | High | Provenance, snapshots, conflicts, semantic rollback. |
| RK-04 | Summaries convert uncertainty into fact | High | Epistemic record separation. |
| RK-05 | Retry duplicates local or external effect | Critical | Idempotency and external reconciliation. |
| RK-06 | Crash loses or repeats work | High | Durable leases and recovery state machine. |
| RK-07 | Builder reports false success | High | Independent artifact verification. |
| RK-08 | Secrets leak through prompts or logs | Critical | Broker and redaction tests. |
| RK-09 | Ambiguous paths change unintended data | Critical | Canonical target identity and before state. |
| RK-10 | External action causes unexpected consequence | Critical | C3 multi-review, staged execution, monitoring, compensation. |
| RK-11 | Financial action exceeds useful value | High | Cost accounting, anomaly stop, outcome review. |
| RK-12 | Candidate rollback cannot read new schema | Critical | Forward/backward migration and restore rehearsal. |
| RK-13 | Provider outage stops progress | Medium | Fallback, local work, bounded retry. |
| RK-14 | Disk exhaustion corrupts state | High | Thresholds, autonomous safe cleanup, restore test. |
| RK-15 | Event history grows without bound | Medium | Verified archival and rebuild. |
| RK-16 | Heartbeat wastes inference | Medium | Event-first wakeups. |
| RK-17 | Permission-first language reappears | High | Repository-wide drift audit and schema checks. |
| RK-18 | Acceptance is unrealistic | High | Fault injection and seven-day run. |
| RK-19 | Subscription is mistaken for API access | Medium | Capability discovery from actual interface. |
| RK-20 | STOP or veto is weakened by self-evolution | Critical | Immutable external control and R3 fault tests. |
| RK-21 | Automatic rollback increases damage | Critical | Freeze, reconcile, then targeted rollback. |
| RK-22 | Owner cannot understand autonomous action | High | Evidence-linked reporting and command center. |
| RK-23 | Version labels overstate operational reality | High | Two-gate model + reclassification record (D-026..D-030); promotion requires both gates. |
| RK-24 | V2 operational integration mutates live vault/DB beyond the governed area | Critical | Governed-area-only writes, expected-hash conflicts, snapshot/rollback, documented initialize() steps, live-DB replacement forbidden while daemon active. |
| RK-25 | Vault content (untrusted) influences control decisions | Critical | STOP/veto/directives remain outside note content; ingestion is data-only (gate criterion 13). |

Any credible route to ignored owner veto, STOP failure, unrecoverable data loss,
secret disclosure, unlawful action, hidden consequence, or audit destruction
is severity one and blocks the affected capability until repaired.
| RK-26 | Repeated polls / duplicate records inflate selectable candidates and create duplicate missions | High | Poll feed filtered (OPEN/non-empty/uncovered), poll key = loop_id, duplicates land REJECTED (projection), UNIQUE monitors index (v18). Verified: FAIL round (deleg_71efdc9e) → repair → PASS round (deleg_9decc685). |
| RK-27 | Queue-REJECTED low-value items resurrect through alternate generation paths | Medium | Poll feed status filter (OPEN only); regression test proves only genuinely-OPEN signals surface. |
| RK-28 | Projection drift accumulates when projection rules change (rows projected under older rules contradict the current rebuild) | Medium | Atomic rebuilds at every rule change (S1: 780 rows, S5-R1: 45 rows); reconcile-consistent verified after each close; rebuilds rehearsed on copies first. |
| RK-29 | Case-insensitive filesystems / repo-name collisions misresolve claim sources | Low | Support requires content-hash + line-hash match (never fabricated support); collisions resolve false-negative (safe direction), documented. |
