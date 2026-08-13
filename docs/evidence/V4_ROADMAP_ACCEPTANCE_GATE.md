# Roadmap V4 Command Center Acceptance Gate

Status: **IN PROGRESS — NOT_TESTED for live provider delivery**  
Branch: `codex/v4-command-center`  
Base commit: `d07327eab983ae6d86db9e11c60733ab6320e124`

This is separate from the directive-defined continuous-autonomy/reliability
gate in `V4_ACCEPTANCE_GATE.md`. It must not be used to relabel that historical
verdict, and that verdict must not be used to claim this product surface is
complete.

| Criterion | Isolated evidence | Status |
|---|---|---|
| Durable Command Center projection | `src/jarvis_foundry/command_center.py`; `test_snapshot_is_ledger_projection_and_timeline_has_evidence`; rebuild assertion | PASS (isolated) |
| Decision timeline is traceable | Event sequence, event/correlation/causation IDs, evidence/artifact refs | PASS (isolated) |
| Costs/resources measured or explicit unknown | `resource.usage_recorded`; `UNKNOWN`/`NOT_AVAILABLE`/`NOT_TESTED` preserved; no zero fabrication | PASS (isolated) |
| Owner controls use authenticated directive path | HMAC owner command + nonce table + `owner_directive.*` events; policy broker remains unchanged | PASS (isolated) |
| STOP without Hermes/UI/models/network | Existing sentinel plus durable STOP_EVERYTHING directive; isolated test | PASS (isolated) |
| Recovery/degraded-mode visibility | stale leases, failed runs, blocked missions, provider unknowns and recovery events in snapshot | PASS (projection contract; live fault proof NOT_TESTED) |
| Briefing planner/outbox | unique briefing key, idempotent planning, claim/deliver/fail lifecycle, local sink only | PASS (isolated) |
| English voice authentication/anti-replay | provider-neutral STT/TTS contracts, fixture adapters, HMAC command, replay/unauthenticated/ambiguous tests | PASS (contract + fixtures) |
| Real tested STT/TTS provider | No live provider or external listener was started | **BLOCKED / NOT_TESTED** |
| UI restart reconciliation | deterministic rebuild test exists; live UI restart not run against live state | NOT_TESTED (live) |
| Prior V4/V5 suites and frozen integration | Existing focused suite green; full suite/integration rebase must run after V5 checkpoint freeze | NOT_TESTED |
| Live runtime/database mutation | none; worktree-only changes | PASS (safety boundary) |

Roadmap V4 must not be declared complete until the `BLOCKED`/`NOT_TESTED`
items are independently closed in an isolated candidate and a frozen artifact
review confirms no V5 regression. External WhatsApp/email/voice delivery is
disabled by design.

