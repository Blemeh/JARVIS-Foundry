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

## REQUIRED at four-lane semantic integration: NEW_PROJECT mapping

Lane 3 has independently added the canonical V7A work class `NEW_PROJECT`.
This lane was built from the older frozen base and currently maps:

```
NEW_PROJECT_OPPORTUNITY -> CANDIDATE      (_PORTFOLIO_CLASS, project_discovery.py)
```

After Lane-1 + Lane-3 semantic integration, the four-lane integration
session MUST change this single mapping line to the canonical class:

```
NEW_PROJECT_OPPORTUNITY -> NEW_PROJECT    (canonical V7A work class from Lane 3)
```

Do NOT recreate or duplicate NEW_PROJECT inside Lane-1 code before that
session; do not merge/cherry-pick Lane 3 here. The mapping is deliberately
isolated to one dictionary entry so the integration wiring is a one-line
change against the combined tree.

## Closure-repair notes (same branch, post-pass batch)

- Research claim identity is now CLOCK-FREE (`claim_identity`):
  proposition+source identity is stable across retrievals; later reads of
  an identical claim append `research.revalidation_recorded` events in the
  same ledger instead of minting duplicates.
- The selection bar is no longer copied into discovery:
  `feed_portfolio` reads the authoritative `v3_loop.DEFAULT_MIN_SCORE` at
  decision time via `canonical_min_candidate_score()`.
- `read_document` redirect contract implemented as documented: bounded
  hops, HTTPS-only, host-policy-checked per hop, loop-fail bounded.
- `read_document` now returns sanitized `DocumentContent` (+ raw_sha256
  provenance); raw secret material cannot reach returned/durable text.

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
