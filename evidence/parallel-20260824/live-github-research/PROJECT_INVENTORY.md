# Project Inventory — Real Accessible Estate (G2/G10)

Discovered live via `GitHubResearchConnector` (gh-brokered auth) on 2026-08-24.
Exact remote identity from API reads; local checkouts deduplicated by remote
URL/identity.

| Field | Blemeh/JARVIS-Foundry | Blemeh/LeadGen | Blemeth→Blemeh/Hoowen |
|---|---|---|---|
| resource_key | github:Blemeh/JARVIS-Foundry | github:Blemeh/LeadGen | github:Blemeh/Hoowen |
| provider | github | github | github |
| visibility | PRIVATE | PRIVATE | PRIVATE |
| default branch | main | main | main |
| latest head SHA | 079245aec0eed9e1... | a7ccc12cc056d3a0... | b467fd74a7164c43... |
| last activity | 2026-08-24T18:08:28Z | 2026-08-24T18:08:25Z | 2026-08-24T18:08:24Z |
| language/stack | Python (+UI) | Python, TypeScript, CSS, Dockerfile | Python, TypeScript, JS, PLpgSQL |
| issues enabled | true (open: 0) | true (open: 0) | true (open: 0) |
| PR/check state | windows-ci failed run on side branch `remaster/post-v7-purpose`; no CI on default | zero Actions runs ever | CI workflow present; 3 recent runs ALL failing |
| release state | none | none | none |
| local checkout | YES — canonical worktree + ~100 registered worktrees under C:\temp (this lane's worktree is one of them) | YES — C:\Users\b\Documents\LeadGen on main, DIRTY (66 entries; owner work preserved, untouched by this lane) | not found in common locations |
| build/test commands | pytest tests/foundry (+ convergence for FULL) | not probed (no CI config to copy) | vitest + backend unit/integration per CI workflow names |
| risk/reversibility | high-value production repo — read-only this lane | low risk | low-medium risk |
| JARVIS registration status | registered as git_remote/local_repository resources in disposable campaign DB via `record_attempt` evidence (resource_id=github:...) | same | same |

## Dedup notes

- Local LeadGen checkout and GitHub remote identified as the SAME project
  (remote URL + default branch); dirty local state recorded, never mutated.
- Hoowen has no known local clone; treated as remote-only inventory entry.
- No blind clones were made; metadata-first discovery kept to bounded calls
  (21 total including research tasks).

## Honest estate summary

All three repos are private, active within the last day (owner pushed a
README/portfolio commit set across all three at 2026-08-24T18:08Z). The
actionable signal is in CI health, not issue backlog (zero open issues).
