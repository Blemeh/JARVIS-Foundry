# V4 Command Center + V5 Integration Manifest

Status: **PASS — VERIFIED SOURCE INTEGRATION; RUNTIME PROMOTION NOT PERFORMED**

- Base commit: `d07327eab983ae6d86db9e11c60733ab6320e124`
- Frozen V5 checkpoint: `598024d142803eb8f53d0e3a145419c5e991b282`
- V4 branch: `codex/v4-command-center`
- V4 implementation commit: `6270a21b30bd81f5068e8bf3910f947a38ee7fe8`
- Verified merge commit: `a09fee3b61e3bd4f6d445ab0bbc88b775eebe1f0`
- Integration branch: `codex/v4-v5-integrated`
- Isolated verification worktree: `%LOCALAPPDATA%\Temp\v4cc-merge-check`
- Live runtime/database: untouched; no live migration, connector, sender,
  voice listener, or scheduler started
- Active source-checkout untracked V5 planning/hardening documents: preserved
  unchanged through the merge

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

## Verification run on the exact merged artifact

- Complete combined V4 + V5 suite — **PASS (719/719)**.
- Windows scheduled hard-kill/restart regression — **PASS** from a disposable
  candidate-local virtual environment, proving the restarted child loaded the
  merged artifact rather than the main checkout's pre-merge editable package.
- Focused V4 Command Center and STOP suites — **PASS (16/16)**.
- `python -m compileall -q src` — **PASS**
- Direct mission/event record routes, unknown-record 404, briefing duplicate,
  authenticated STOP, voice fixture, and durable snapshot — **PASS**.
- `git diff --check` reports only pre-existing/documentation whitespace and the
  upstream Tektur license line; no merge conflict or runtime failure.

The first full-suite attempt exposed an environment mismatch: the scheduled
child used the main checkout's editable package and therefore loaded pre-merge
V5 code after restart. The same regression and the complete suite passed when
run from a candidate-local environment containing this exact merge. No product
code was changed to hide that diagnostic.

## Known limitations and rollback

- Real STT/TTS provider capability remains `BLOCKED/NOT_TESTED`; fixtures do
  not prove live voice completion, so the original roadmap-V4 live-voice
  criterion is not relabeled PASS.
- Online connector/provider reconciliation limitations recorded by V5 remain
  unchanged.
- Source rollback is two explicit reverts after preserving later work: revert
  this manifest-close commit, then revert merge `a09fee3`. Do not reset or
  clean the active checkout.
- Runtime rollback is not required because no runtime promotion occurred.

## Remaining runtime promotion actions

1. Keep source integration separate from deployment.
2. Rehearse any live-home migration and restart on a copied candidate home.
3. Promote through a candidate/canary slot with an explicit reversible runtime
   procedure; a source merge does not update the running Supervisor or Hermes.
4. Keep external delivery and live voice disabled until their independent
   provider gates pass.
