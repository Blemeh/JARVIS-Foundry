# Cross-Lane Handoff — Live GitHub + Internet Research Activation

For Lane 2 and Lane 3 (later semantic integration). Everything below exists
on branch `candidate/live-github-research-activation-20260824`
(FROZEN_BASE `079245a`, lane head at handoff time: see git log).

## What lanes 2/3 can consume directly

1. **Read-only GitHub research connector**
   `jarvis_foundry.connectors.github_research.GitHubResearchConnector`
   - Actions (all semantic `read`): `github.repo.read`, `github.commit.read`,
     `github.issue.read`, `github.pull_request.read`, `github.check.read`,
     `github.release.read`, `github.search.read`, `http.doc.read`.
   - Returns `(ConnectorAttempt, document|None)`; documents are
     redaction-normalized; attempts are persistable via the canonical
     `connectors.evidence.record_attempt`.
   - Auth stays brokered by an existing gh CLI session. No token handling.
   - Extension points: inject `api_transport`/`doc_transport` for tests or a
     future brokered-token transport WITHOUT changing callers.

2. **Research evidence contract**
   `jarvis_foundry.research_contract`: `record_research_item`,
   `record_unknown`, `record_conflict`, `link_attempt_evidence`,
   `classify_content`. Claims land in the EXISTING knowledge_claims machinery
   with provenance in immutable event payloads (`provenance.provider`,
   `canonical_location`, `retrieved_at`, `title`, `content_sha256_16`,
   `data_only`). Clean statements keep canonical contradiction detection
   functional.

3. **Project discovery pipeline**
   `jarvis_foundry.project_discovery`: `RepositoryRecord`,
   `observe_repository`, `detect_opportunities`, `reject_duplicates`,
   `feed_portfolio`.
   - `feed_portfolio(db, mission_id, opportunities)` returns machine-readable
     `{fed, passing_min_score, decision: SELECT_CANDIDATES|WAIT,
     wait_reason}` and writes through EXISTING generate_candidate /
     score_candidate / register_work_item only. No second scorer was created.

4. **Evidence package** (this directory)
   - `E2E_PROOF.json`: full machine-readable campaign truth (inventory,
     observations, research tasks T1-T5, adversarial verdict, portfolio feed).
   - `PROJECT_INVENTORY.md`: estate snapshot incl. local checkout dedupe
     (LeadGen local checkout is DIRTY owner work: preserve, do not mutate).

## Known real-world findings worth consuming downstream

- Hoowen CI recurring red (vitest + PostgreSQL integration + backend unit);
  untracked in issues; strongest current candidate (score 0.487, REPAIR).
- JARVIS-Foundry default branch has NO windows-ci workflow (the historic red
  run targeted side branch `remaster/post-v7-purpose`): maintenance gap.
- LeadGen has never had CI; low current value: honest WAIT.
- GitHub custom property literally named `token` exists on these repos:
  ANY consumer of repo metadata must go through the redaction-normalizing
  connector, not raw payloads.

## Boundaries respected (verify on integration)

- No main mutation, no production/live-DB/scheduler touch, no merges from
  this lane; candidate branch pushed with governed reconciliation proof.
- Campaign ran against disposable DBs; nothing wrote to live Foundry state.

## Suggested integration order for Lane 2/3

1. Merge this branch behind existing V7A semantics; run fast suite.
2. Wire `observe_repository` into an existing bounded cycle/wake mechanism
   (do NOT add aggressive polling): snapshot head SHAs per repo, diff on wake.
3. Route `feed_portfolio` output into the live executive's existing
   portfolio selection path (`executive/portfolio_bridge.py`) so candidates
   become executable missions under normal V7A control.
4. Extend `_CONTROL_MARKERS`/corpus as new adversarial shapes appear; keep
    the structural DATA_ONLY guarantee (no retrieval path emits control
    events) intact.
