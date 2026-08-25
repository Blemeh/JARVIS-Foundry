# Lane Orientation — Live GitHub + Internet Research Activation (Lane 1/3)

Date: 2026-08-24
Branch: `candidate/live-github-research-activation-20260824`
Worktree: `C:\temp\JARVIS-Live-GitHub-Research`

## FROZEN_BASE

Reference in the lane order was `f7dfd7c1b370d8ab203b2e568f6e6d14e489a90b`.
Reality at lane start: `origin/main` had legitimately advanced to
`079245aec0eed9e1026df87548817a1baf3be036` (`docs: rewrite README for portfolio
presentation`). Per the mission rule ("Do NOT reset to the reference if
canonical legitimately advanced"):

```
FROZEN_BASE = 079245aec0eed9e1026df87548817a1baf3be036 (actual origin/main)
```

Verified via `git fetch --all --prune`, `git rev-parse main`, `git rev-parse
origin/main`, `git status --short --branch`, `git worktree list --porcelain`,
`git log --oneline --decorate -n 30`.

## Mission shape

PRODUCT ACTIVATION of the existing JARVIS machinery against REAL GitHub and
REAL web sources. Stop at DISCOVERY / RESEARCH / CANDIDATE FEEDING plus the
minimum remote GitHub proof required to establish readiness. No new research
framework, no second candidate engine, no second Git abstraction, no main
mutation, no production touch.

## Canonical machinery reused (inspected before adding code)

| Need | Existing canonical implementation |
|---|---|
| Git push + provider truth | `src/jarvis_foundry/v3_git.py` (`GitEffectAdapter`: exactly-once effect journal, STOP-gated, ls-remote reconciliation) bound by `connectors/git.py` (`GitConnector`) |
| Connector contract | `connectors/contract.py` (`ConnectorSpec` / `ConnectorRequest` / `ConnectorAttempt`, semantics check) |
| Connector registry/routing/quota | `connectors/registry.py` |
| Attempt evidence | `connectors/evidence.py` (`record_attempt`) |
| Account activation (GitHub scopes model) | `connectors/account_activation.py` (`FakeGitHubProvider`; capability scopes incl. `github.repo.read`, `github.push`) |
| Resource discovery | `connectors/resources.py` (incl. `git_remote` kind) |
| Candidate pipeline + dedupe | `discovery_candidates.py` (`generate_candidate`, `score_candidate`, `covered_objective`, reject/supersede/outcome) |
| V7A portfolio | `evolution/executive_portfolio.py` (`register_work_item`, SELECT/WAIT, wake conditions, outcomes), `executive/scoring.py`, `executive/portfolio_bridge.py` |
| Knowledge claims + contradictions | `knowledge_ingestion.py`, `projections.py` (`knowledge.claim_recorded`, deterministic negation-contradiction detection), `knowledge.evidence_linked` |

## New code this lane adds (additive only)

1. `src/jarvis_foundry/connectors/github_research.py` — read-only GitHub REST +
   bounded HTTPS docs reader following the EXISTING connector contract; auth
   stays brokered (gh CLI/keyring); no raw tokens in process.
2. `src/jarvis_foundry/research_contract.py` — OBSERVATION/EVIDENCE/CLAIM/
   UNKNOWN/CONFLICT recording over the EXISTING knowledge events; DATA_ONLY
   handling of untrusted content.
3. `src/jarvis_foundry/project_discovery.py` — inventory, change observations,
   typed opportunity detection, duplicate rejection, bridge into the EXISTING
   candidate generator/scorer/V7A portfolio.

## Boundaries

- This lane pushes ONLY its own candidate branch.
- No merge to main, no production/runtime/live-DB mutation, no Task Scheduler,
  no branch protections, no repo deletion, no public posting.
- GitHub reads are bounded; rate-limit state is observed and recorded.
- Untrusted web/GitHub content is data; it cannot widen scope or become an
  owner directive.

## Job queue

See `JOB_QUEUE.md` (serial G1..G13 with live status).
