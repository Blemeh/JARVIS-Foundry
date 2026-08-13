# Recovery Notes — JARVIS Foundry (CHECKPOINT 2026-08-13 ~10:57)

**Session state: V5 MASTER AUTONOMY SESSION ACTIVE** (owner's
V5_MASTER_PROMPT.md). Resume point: this file + git log + the V5 slice
manifest (`docs/planning/V5_SLICE_MANIFEST.md`).

## Repository state

- HEAD `28156f0` (feat(v5-s2) — ActionEnvelope + policy broker).
  Previous: `8497cc2` (V5-S1 CLOSED), `8426558` (S1 repairs),
  `213fb06` (V5-S0), `7e54bbc` (handoff).
- Working tree: CLEAN except the OWNER'S two untracked brief files —
  `docs/planning/V5_MASTER_PROMPT.md` + `V5_READ_FIRST.md`. NEVER
  commit/move them (owner's files).
- V5-S1 CLOSED PASS: verifier deleg_d7639766 (24 probes); repairs
  `8426558`; live close seq 22079-22080; live schema v18→v19
  (rehearsed on WAL-safe copy); suite 505→507.
- V5-S2 (this commit): ActionEnvelope contract fields + BrokerDecision
  (7 codes) + control/broker.py (15-step PolicyBroker.decide,
  normalize_action_kind, resolve_canonical_target, record_decision,
  require_allowance) + 54 tests; suite 561 EXIT=0; rehearsal
  (live-copy reconcile consistent, 22,255 events). **Verifier
  deleg_6c96be6d RUNNING** at frozen HEAD 28156f0 — verdict pending;
  NO src/tests edits until it lands.
  - On PASS: extract verdict → update docs/evidence/V5_S2_BROKER.md →
    close script (close_v5_s1.py is the model; verdict id
    `verdict-v5-s2-broker:round1`; live events only, NO migration —
    live stays v19) → archive V5_S2_WORK_PACKAGE.yaml →
    V5_S2_WORK_PACKAGE.closed.yaml → commit → S3.
  - On FAIL: repair exact findings → regression → re-verify from a
    fresh frozen state.

## Live system (verified 10:37–10:53)

- Daemon RUNNING (supervisor-6d250614…), 5s heartbeats, STOP false,
  no stop marker, no active STOP_EVERYTHING/VETO; live schema **v19**
  (systems + system_actions tables live, 0 rows — registry not yet
  used); ~22,255 events; 1,052 claims; 4 monitors polled; reconcile
  consistent (re-checked on a live snapshot at 10:53).
- Cadence keeper (old session's subagent deleg_4ea0c87e): loops at
  10:05 + 10:45 observed — honest WAITING (top 0.25 < 0.3), 8 scored,
  0 candidates; loops +80m/+120m (~11:20, ~12:00) still to come;
  artifacts in var/evidence/v3-loops/live-loops-*.json.

## Next steps (V5 phase)

1. S2 verdict → close → commit → S3 (scoped capability + credential
   broker: short-lived capabilities bound to mission/run/target/expiry,
   redaction, revocation on STOP/veto, honest NOT_READY).
2. S4 connectors → S5 entities/correlation → S6 effect execution/
   reconciliation → S7 loop wiring → S8 real-connector activation →
   S9 final gate + V6 handoff. Full plan: docs/planning/
   V5_SLICE_MANIFEST.md.
3. Checkpoints: var/evidence/checkpoints/; keep this file current at
   every slice boundary.
