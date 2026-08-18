# CANONICAL RECONCILIATION — JARVIS EYES PHASE 2

Status: FROZEN (fresh dynamic discovery, 2026-08-18)

## Method

Reconstructed from actual Git/runtime/DB/process evidence at Phase-2 start.
No historical SHA was used as a reset/checkout target. Truth hierarchy
honored: code/runtime/Git/evidence > CURRENT_STATE/capability matrix >
historical docs > remembered SHA values.

## Observed canonical state (start)

| Surface | Observed |
|---|---|
| origin/main | `765a725c941efc9762317741cdbcf1703e44e7be` |
| local main | `765a725c941efc9762317741cdbcf1703e44e7be` (== origin/main) |
| current branch | `main` |
| worktree | canonical checkout only (all other JARVIS branches in sibling worktrees, listed below) |
| dirty state | 6 untracked owner/handoff docs under `docs/handoffs/`, `docs/planning/` (preserved, untouched) |
| schema | v33 (live DB `schema_migrations` max version 33) |
| live DB | `C:\Users\b\AppData\Local\hermes\foundry\foundry.db` (events 81058 at start; 93979 by 2026-08-18) |
| supervisor | RUNNING, single lease owner, fresh heartbeats, STOP sentinel OFF |
| trust state | LAB |
| hybrid worker live state | ACTIVE (worker_routing.yaml `enabled: true`, provider `opencode_go` + model `deepseek-v4-flash`) |
| remaster/R12 state | remaster branch `6be9185` (R12 closed); canonical main merged remaster docs truth |
| test baseline | 1124 collected (per `docs/CURRENT_STATE.md`); full suite re-run in Phase 2 on candidate |

## Sibling worktrees (collision scan)

| Worktree | Branch | HEAD | Dirty |
|---|---|---|---|
| `C:\temp\JARVIS-Canonical-HybridWorkers` | integration/canonical-hybrid-worker | 37ab5cb | clean |
| `C:\temp\JARVIS-CommandCenter-UI` | candidate/command-center-ui | f38f6b1 | clean |
| `C:\Users\b\...\JARVIS-Foundry-Remaster` | remaster/post-v7-purpose | 6be9185 | untracked r6 work (owner/session) |
| `C:\Users\b\...\JARVIS-Foundry-V4-command-center` | codex/v4-command-center | 6270a21 | clean |
| `C:\Users\b\Documents\JARVIS-External-Candidate` | codex/v4-v5-integrated | fa4e65f | untracked evidence/ (owner) |
| `C:\Users\b\Documents\JARVIS-Foundry-HybridWorkers` | integration/hybrid-worker | 491910c | clean |

No active Git mutator: last canonical reflog write 2026-08-16 18:30
(README merge); no active codex/mission builder since live agent_run rows
ended 2026-08-16 12:16; supervisor processes only heartbeat.

## Phase-1 evidence import check

Lab: `C:\Users\b\Documents\JARVIS-Eyes-Qualification-Lab`

- `final/evidence_manifest.json` present; 50/51 artifacts hash-verified.
- `contracts/PHASE2_ENTRY_CONTRACT.md` hash MISMATCH: manifest pins
  `392a83c6...`, file hashes `e36914eb...` (file rewritten 2026-08-16
  16:15 local, content is the corrected post-review version; the manifest
  entry itself is stale). Recorded as honest drift; no repair of the lab
  archive without owner action. All other frozen contracts verify,
  including `PERCEPTION_INTEGRATION_CONTRACT.md` (`52d5e601...`) and
  `SCREENPIPE_QUALIFICATION.md` (`b59c0f42...`).
- Phase-1 verdict PASS; Screenpipe 0.4.40 qualified; OpenAdapt browser
  qualified, Windows PROBING (preserved for Phase 4, NOT integrated).

## Current canonical conclusions

- CURRENT_CANONICAL_HEAD = `765a725c941efc9762317741cdbcf1703e44e7be`
- CURRENT_CANONICAL_LINEAGE = main merges: 2a9a5de (hybrid workers) +
  ea0ff5a (README) on top of ddc5129 docs-truth remaster ancestry
- CURRENT_SCHEMA = 33
- CURRENT_RUNTIME_STATE = supervisor RUNNING / STOP OFF / trust LAB
- CURRENT_WORKER_RUNTIME_STATE = hybrid worker runtime ACTIVE (external
  to this phase's scope; untouched)
- CURRENT_REMSTER/R12_STATE = R12 closed on remaster branch; docs-truth
  contract canonical
- CURRENT_DIRTY_STATE = 6 owner untracked docs (preserved)
- CURRENT_COLLISION_RISK = LOW (no active mutator; candidate isolated)
