# R7 Research, Discovery and Lineage Gate

Program: JARVIS remaster forward-port onto canonical main (`2b3573606570f910e5569cf3306223354f93e4ac` base).
Stage: R7 (beyond portfolio: discovery, monitored research, provenance,
uncertainty/contradiction, injection isolation, dedup, rate/cost controls,
restart-safe monitoring, candidate->mission lineage, canonical identity,
retrieved content is data). Owner redirect for R6 is preserved: R6 is skipped
and never reopened.

Method: SEMANTIC FORWARD-PORT. Reuse canonical `main` surfaces instead of
porting remaster package code wholesale. R7 is PROVEN here by
`tests/convergence/test_r7_research_lineage_gate.py` against the real durable
ledger/projection machinery; no parallel remaster architecture is added, and
existing canonical discovery/monitor/research/knowledge tests are preserved.

## Canonical surfaces exercised

- Goals/projects/dependencies: `evolution/portfolio.py` (goals, dependencies,
  EV/urgency/cost, capacity, pause/resume/reprioritize, unknowns/conflicts).
- Evidence-based discovery: `discovery_candidates.py` (canonical objective,
  source trust, score, approve/reject, `created_from` lineage).
- Monitored research: `discovery_monitors.py` (durable registry, polls,
  enabled/disabled), `research_queue.py` (STALE/CONTRADICTED/UNSUPPORTED/WEAK
  items; low-value rejection), `vault_indexer.py` (file hashes/mtime identity),
  `knowledge_ingestion.py` (deterministic content hashing, idempotent
  re-ingestion).
- Provenance/snapshots: `knowledge_claims` (`source_run_id`,
  `source_event_id`), `knowledge_snapshots.py` (sha256 + read-only restore).
- Uncertainty/contradiction: `knowledge_contradictions` + deterministic
  negation detection (`knowledge.py`), `portfolio.unknown/conflict`.
- Injection isolation: unparsed file content is inert data; event validation
  and deterministic transitions (`f13_injection_data` semantics).
- Deduplication/restart safety: `monitors` UNIQUE(source_kind, source_ref);
  canonicalized objective dedup; idempotency-keyed polls; rebuild/reconcile
  reproducibility.
- Rate/cost: `PolicyBroker` budget fail-closed (`budget_estimate` vs policy,
  `BUDGET_EXCEEDED`, no budget policy => `NOT_READY`), connector quota
  accounting (`record_quota_use`, `select_connector` refuses exhausted
  connectors).
- Canonical identity: `resolve_canonical_target` (absolute, no wildcards/env),
  registered system/resource checks, `normalize_action_kind`.
- Retrieved content is never authority: unknown/fabricated event types are
  recorded but inert; stop/veto gates remain; hostile files change no
  supervisor/mission state and create no events by themselves.

## Gate assertions (focused, no duplicate architecture)

1. Registered project persists goals/dependencies and rejects a non-Supervisor
   controller.
2. Discovery requires evidence: a candidate without a source is not selectable
   through the real pipeline; scoring and approval are recorded against the
   actual candidate row.
3. Monitored research: a durable monitor is registered, polled only while
   enabled, dedupes repeat polls via canonicalized objectives, and its `OPEN`
   research item converts into a candidate that carries the poll/evidence
   provenance.
4. Provenance and sources: knowledge claims persist `source_run_id` +
   `source_event_id`; source content identity is durable across a restart-safe
   rebuild; a snapshot records head/schema/claims and verifies unchanged until
   tampered.
5. Uncertainty/contradiction: statements projecting into
   `knowledge_contradictions` are represented (not destroyed), and
   portfolio.unknown/conflict events are recorded rather than resolved.
6. Injection isolation: hostile file bytes are inert data and a fabricated
   event type is recorded but performs no projection mutation; reconcile
   remains consistent.
7. Dedup: repeated campus/candidate generation with a canonicalized duplicate
   objective does not increase selectable candidates.
8. Rate/cost controls: broker refuses a budget estimate with no policy
   (fail closed), refuses a `BUDGET_EXCEEDED` request, and connector routing
   refuses an exhausted quota while a healthy unrelated connector can still be
   selected.
9. Canonical identity: `normalize_action_kind` canonicalizes variants and
   `resolve_canonical_target` rejects fuzzy/wildcard/root targets.
10. Lineage: an approved candidate's mission is recorded with
    `created_from: candidate:<id>`; a completed mission marks the candidate
    EXECUTED with the outcome wired into the candidate evidence.

## Proof contract

This gate passes when every assertion in the focused test file passes on the
candidate worktree (candidate `src` prefixed via the test conftest). It does
not claim live provider research, live voice, or elapsed proving time; those
remain honest `NOT_TESTED`/`WAITING` areas owned by later stages and the real
proving window. R6 remains `OWNER_REDIRECT_R6_DEFERRED`, never reopened.
