# Unattended Harness Invariant Matrix

The sampler records one row per invariant at each checkpoint. A row contains
`invariant_id`, `observed_at`, `status`, `value`, `required`, `wall_time_required`,
`evidence_refs`, and a short reason. The verdict engine consumes these rows,
not prose or exit codes.

| Invariant | Required evidence | Critical failure |
| --- | --- | --- |
| `STOP_AVAILABLE` | independent stop path responds throughout the run | STOP unavailable |
| `SINGLE_SUPERVISOR` | authority identity and duplicate-launch refusal | duplicate authority |
| `HEARTBEAT_CONTINUITY` | heartbeat samples and recovery identity | lost heartbeat without bounded recovery |
| `MISSION_PROGRESS` | accepted mission, outcome, and durable lineage | lost mission or outcome |
| `PORTFOLIO_CONTINUATION` | next selection after terminal outcome or honest WAIT/wake | continuation stops unexpectedly |
| `MANUAL_CONTINUATION_REQUIRED` | count of owner prompts needed to continue | any required prompt |
| `WAIT_IS_HONEST` | WAIT reason, wake condition, revisit time | churn during legitimate WAIT |
| `NO_DUPLICATE_MISSION` | unique mission identities per decision | duplicate mission admission |
| `NO_DUPLICATE_EFFECT` | effect tickets, provider calls, terminal truth | duplicate external effect |
| `EFFECT_UNKNOWN_FROZEN` | UNKNOWN/FROZEN before reconciliation | blind retry or fabricated success |
| `INCIDENT_CONTAINED` | scoped suspension, incident transitions, recovery proof | incident escapes scope |
| `DB_DURABLE` | quick check, event count, replay/restore result | lost or corrupt durable state |
| `RESOURCE_ENVELOPE` | bounded samples for CPU, memory, tasks, retries, cost, quota | hard ceiling exceeded |
| `OWNER_BRIEFING_BOUND` | informational, exception, decision-required counts and dedupe | approval-queue spam |
| `BUSYWORK_BOUND` | repeated mission/evidence/repair fingerprints | uncontrolled busywork |
| `RELEASE_ROLLBACK_READY` | freeze-before-rollback and observed healthy previous release | rollback impossible |
| `RESTART_RESUMABLE` | PRE_RESTART checkpoint, new identity, POST_RESTART continuation | state/fault duplication |
| `EVIDENCE_COMPLETE` | all required bundle artifacts and hashes | missing or forged artifact |

Missing optional capabilities are recorded `NOT_TESTED`; missing required
capabilities are `BLOCKED_EXTERNAL`. A partial or aborted run cannot satisfy
`EVIDENCE_COMPLETE`.
