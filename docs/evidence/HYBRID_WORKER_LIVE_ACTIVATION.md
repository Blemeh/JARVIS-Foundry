# HYBRID WORKER LIVE ACTIVATION

Status: live runtime activation proven and independently reviewed;
`config/worker_routing.yaml` is ACTIVE (the driver only runs cycles when
explicitly launched — nothing auto-starts it).

## Scope

This phase connects the already-canonical Hybrid Worker package to the real
JARVIS Supervisor / mission / verification runtime so JARVIS autonomously
selects, dispatches, observes, verifies, repairs, recovers and continues
worker-driven mission steps. It does not redesign the worker layer.

## Canonical runtime path (one path, no parallel truth)

```text
MissionStep READY
  -> Supervisor/STOP sentinel (driver refuses while frozen)
  -> MissionLoop (canonical state machine)
  -> RoleDispatcher (durable leases, replay, exactly-once)
  -> HybridWorkerRoleAdapter (single worker bridge)
  -> WorkerRouter (deterministic routing; OpenHands only when READY)
  -> worker adapters (Hermes / OpenHands, disposable state)
  -> EvidenceReconciler (JARVIS-owned truth; worker claim never truth)
  -> fresh verifier -> verdict -> accept / repair / block
  -> ACTION_SELECTION -> next action
```

## Components changed

- `src/jarvis_foundry/runtime/` (new) — canonical live driver (`worker_loop`),
  explicit auditable config (`config`), adapter stack builder (`_stack`).
- `roles/manifest.py` + `roles/dispatcher.py` + `mission/loop.py` — durable
  mission truth (acceptance criteria, expected artifacts, forbidden scope,
  attempt) carried into the worker contract; manifest hashes stay
  byte-identical for default steps (historical replay semantics).
- `workers/mission_bridge.py` — verifier receives durable acceptance
  criteria; verifier evidence reconciliation is delta-scoped (the builder's
  uncommitted work is not a verifier claim mismatch).
- `workers/evidence.py` — deterministic tool caches (`__pycache__`,
  `.pytest_cache`, `.pyc`) are not unrecorded mutations; optional delta
  baseline for verifier role.
- `workers/adapters/hermes.py` — Hermes sessions run with cwd = contract
  working root and the checkout resolved via `PYTHONPATH` (live finding: the
  CLI previously wrote into `.h` instead of the worktree).
- `workers/adapters/openhands.py` — structured `VERDICT:` line parsing
  (fresh verifier verdicts), heartbeats during long sessions, secret
  redaction at intake.
- `workers/adapters/base.py` — `run_subprocess_with_heartbeat`: real worker
  sessions renew the role lease periodically so the supervisor reaper never
  reclaims a live session mid-flight (live finding 2026-08-16);
  `redact_secrets` / `configured_api_key` — every captured worker output is
  scrubbed of secret-shaped tokens before it enters durable envelopes, and
  disposable OpenHands run-state files are scrubbed after every run (live
  finding 2026-08-16: a worker dumped the credential env into a session
  event file; the artifact was deleted and the mechanism hardened).
- `config/worker_routing.yaml` (new) — explicit activation configuration,
  INACTIVE until review.
- `scripts/hybrid_live_probe.py` (new) — reproducible live probe.
- tests: `test_runtime_worker_loop.py` (new) + evidence/adapter/bridge
  regression coverage.

## Live proof (real OpenHands CLI + direct OpenCode Go, deepseek-v4-flash)

All sections ran against the actual canonical runtime and the live Foundry
database, in disposable registered worktrees under
`var/hybrid-live-proof/<stamp>/` (gitignored, reports retained as evidence):

| section | result |
|---|---|
| bounded mission A (fix `rows.load_rows`) | PASSED (automatic continuation to B) |
| bounded mission B (add regression test) | PASSED |
| false success (worker claims done, no work) | BLOCKED — never accepted |
| worker crash mid-run (killed child) | partial mutation visible; rerun PASSED |
| STOP sentinel | FROZEN — no worker dispatch |
| restart / durability (fresh process) | PASSED — first step preserved, next action proceeded |

Representative reports: `20260816-111228/report-mission.json`,
`20260816-112748/report-false_success.json`,
`20260816-115709/report-crash.json`, `20260816-115517/report-stop.json`,
`20260816-121257/report-restart.json`.

Model identity was verified from the actual OpenHands event stream:
`model_actual=deepseek-v4-flash`, `provider_actual=opencode_go` (owner
mandate; no substitutions, no fallbacks).

## Owner-state containment

- `~/.openhands` stayed at the 6 pre-existing conversation dirs throughout
  (fail-closed before/after diff; completed + forced-termination variants).
- The `.h` checkout stayed clean; probe artifact outside the worktree was
  removed and a regression test prevents recurrence.
- No credential value appears in probe reports, the live database, or any
  committed file (`sk-[A-Za-z0-9]{16,}` scans clean across the repo and the
  retained probe evidence). Two prior independent reviews each found and
  the hardening closed a real leak class:
  1. a retained disposable OpenHands session event held the value (deleted;
     adapters now redact all captured output and scrub disposable run-state
     on every exit path);
  2. 12 pre-existing gitignored wp8-gate staging `.env` files held the value
     (value redacted in place to `<REDACTED>` on 2026-08-16; the files are
     retained as historical evidence with the secret removed).

## Tests

- Runtime layer: 8 new tests (config fail-closed, directive routing,
  disabled-framework exclusion, contract truth, manifest-hash stability).
- Focused worker + runtime suite: 105 passed.
- Full canonical suite: 1138 passed, EXIT=0 (1124 baseline + 14 new).

## Configuration

`config/worker_routing.yaml` is the single activation surface: fail-closed
`enabled: false` until the independent review passes, then `ACTIVE`. The
credential is never stored there — adapters consume
`JARVIS_OPENCODE_API_KEY` / `JARVIS_OPENCODE_BASE_URL` from the process
environment.
