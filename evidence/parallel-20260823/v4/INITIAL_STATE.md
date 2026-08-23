# JARVIS V4 Owner Briefings — Initial State

Recorded: 2026-08-23

## Frozen lane

- Frozen base: `01db32cbcaa409dfe26386ce66110429c2c0b0e0`
- Candidate branch: `candidate/v4-owner-briefings-20260823`
- Candidate worktree: `C:\\temp\\JARVIS-V4-Owner-Briefings`
- Candidate HEAD at entry: `01db32cbcaa409dfe26386ce66110429c2c0b0e0`
- Candidate status at entry: clean, no staged/unstaged/untracked files before this evidence package
- Remote candidate branch at entry: absent (`git ls-remote` returned no row)

## Read-only baseline observations

- Canonical checkout: `C:\\Users\\b\\Documents\\ChatGPT\\Autonomous AI powered by Hoowen\\JARVIS-Foundry`
- Local `main`: `01db32cbcaa409dfe26386ce66110429c2c0b0e0`
- `origin/main`: `01db32cbcaa409dfe26386ce66110429c2c0b0e0`
- Main status at entry: clean and aligned with `origin/main`
- Git worktrees observed: 112 entries (including prunable historical entries); no existing target worktree or candidate branch
- Production/live state: not inspected for mutation and not touched by this lane

## Source orientation

Read current versions of:

- `docs/architecture/00_READ_FIRST.md`
- `docs/architecture/01_NORTH_STAR.md`
- `docs/architecture/02_ARCHITECTURE_CONTRACT.md`
- `docs/architecture/05_POST_V1_ROADMAP.md`
- `docs/CURRENT_STATE.md`
- `docs/CAPABILITY_MATRIX.md`

Relevant existing owner/control sources:

- `src/jarvis_foundry/owner_controls.py` — HMAC + nonce owner admission; GOAL, CONSTRAINT, VETO, STOP, PAUSE/RESUME, reprioritize/redirect commands
- `src/jarvis_foundry/mission/directives.py` — authenticated R10 reprioritize/redirect consumer
- `src/jarvis_foundry/executive/goals.py` — event-derived GOAL projection and lexical goal scoring; constraints currently read from the directives projection
- `src/jarvis_foundry/control/broker.py` and `src/jarvis_foundry/control/policy.py` — deterministic STOP/PAUSE/veto enforcement
- `src/jarvis_foundry/supervisor/control.py`, `supervisor/estop.py`, `supervisor/controls.py` — runtime control state and deterministic STOP surfaces
- `src/jarvis_foundry/projections.py`, `migrations.py`, `events.py`, `database.py` — append-only event ledger and projections

Relevant briefing/Command Center sources:

- `src/jarvis_foundry/briefings.py` — idempotent period-keyed snapshot outbox; raw snapshot payload, no event-item selection/checkpoint
- `src/jarvis_foundry/reporting/briefing.py` — older count-based localized briefing record
- `src/jarvis_foundry/command_center.py` — read-only snapshot/timeline and authenticated local owner endpoint
- `ui/command-center/src/lib/realClient.ts` and `snapshotMapper.ts` — live adapter; notifications are derived only from STOP/blocked/failed rows and acknowledgement is UI-local
- `ui/command-center/src/routes/Executive.tsx` and `Notifications.tsx` — decision spine and exception UI, with no durable owner-decision/briefing history surface

## Known gaps entering V4-1

1. `directives` projection stores only command/target/text/active and last event; authenticated source, nonce/hash, causation/correlation, supersession, revocation reason/time, and explicit UNKNOWN state are not product-projected.
2. GOAL reconstruction does not validate the nonce/hash pair like the R10 consumer, and active constraints are read from the projection rather than one shared event-derived directive model. Goal history/status/conflict semantics are incomplete.
3. PAUSE is treated as an active blocking directive and `RESUME` revokes all global STOP/PAUSE rows, but there is no durable pause/resume state projection distinguishing PAUSED, STOPPED, BLOCKED, DEGRADED, WAITING, UNKNOWN, and RUNNING.
4. Briefings are raw `build_snapshot()` dumps keyed by a period; there is no deterministic authoritative-event item selection, per-item evidence/importance/freshness/dedupe identity, bounded lookback cursor, cross-window dedupe, zero-change suppression, or restart-safe checkpoint.
5. Live notifications do not project authoritative exception/owner-decision records; acknowledgements/resolution are explicitly UI-local.
6. Command Center exposes active control arrays and the executive decision spine, but not durable goal/constraint/veto history, briefing packets/history, exception/owner-decision queues, or complete decision -> evidence -> outcome -> next-action traceability.

## Lane ownership

This lane intends to own only V4 product semantics, event-derived projections,
briefing/exception/owner-decision packets, Command Center management surfaces,
tests, and lane evidence. Supervisor/release/V5/V6 internals, shared schema
migrations, production files, live DB, scheduler, provider credentials,
voice, and localization remain outside this lane. Existing localization code
is read-only reference; owner explicitly deferred new voice/localization work.

