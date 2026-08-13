# V4 Command Center Integration Manifest (pre-merge)

Status: **CANDIDATE — OWNER INTEGRATION REQUIRED**

- Base commit: `d07327eab983ae6d86db9e11c60733ab6320e124`
- V5 checkpoint: `NOT_OBTAINED — active V5 checkout contains untracked work`
- V4 branch: `codex/v4-command-center`
- V4 implementation commit: see the clean branch `HEAD` recorded at handoff
  (the manifest intentionally avoids a self-referential commit hash)
- Worktree: `JARVIS-Foundry-V4-command-center`
- Live runtime/database: untouched; no live migration, connector, sender,
  voice listener, or scheduler started

## Candidate files

- `src/jarvis_foundry/v4_schema.py` — additive V4 tables
- `src/jarvis_foundry/command_center.py` — durable snapshot, timeline, local
  read-only HTTP/HTML surface
- `src/jarvis_foundry/owner_controls.py` — HMAC + nonce authenticated owner
  directive path
- `src/jarvis_foundry/briefings.py` — idempotent local briefing planner/outbox
- `src/jarvis_foundry/voice.py` — provider-neutral English STT/TTS boundary
- projection/database/migration/reconcile wiring and V4 tests
- status/evidence/decision-log clarification documents

## Verification run

- `pytest -q tests/foundry/test_migrations.py tests/foundry/test_event_store.py
  tests/foundry/test_reconcile.py tests/foundry/test_cli.py
  tests/foundry/test_action_policy.py tests/foundry/test_action_broker.py
  tests/foundry/test_v4_command_center.py` — **PASS**
- `pytest -q tests/foundry/test_v4_command_center.py` — **PASS (5/5)**
- `python -m compileall -q src` — **PASS**
- smoke: briefing duplicate, authenticated STOP, voice fixture, snapshot —
  **PASS**
- full suite from this branch — **NOT_TESTED to completion** (bounded run
  exceeded the available execution window; rerun from a frozen checkpoint)

## Known limitations and rollback

- Real STT/TTS provider capability is `BLOCKED/NOT_TESTED`; fixtures do not
  prove live voice completion.
- No frozen V5 checkpoint is available yet; do not rebase or merge until the
  owner freezes the active V5 tree.
- Rollback is worktree-local: remove this branch/worktree or revert only V4
  commits after preserving the manifest. Do not reset or clean the active V5
  checkout.

## Required next integration actions

1. Obtain an exact clean/frozen V5 HEAD from the owner-controlled checkpoint.
2. Rebase this branch onto that commit without touching active untracked V5
   files.
3. Rerun the complete V4 and V5 suites, migration/restart/reconcile/STOP
   checks, and independent adversarial verification from frozen artifacts.
4. Stop before merge; candidate/canary promotion requires explicit owner
   authorization.
