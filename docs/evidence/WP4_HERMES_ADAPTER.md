# WP4 Evidence — Hermes Role Adapter

Status: **PASS** (gate closed 2026-08-11, continuation run 2)
Updated: 2026-08-11

## Gate Close Record

- Two independent adversarial verifiers: deleg_f2954000 (30/30 scratch probes,
  C1–C7 PASS) and deleg_221bd327 (30/30 scratch probes, C1–C7 PASS with 1
  MAJOR integrity condition — certify only against the frozen state — and 3
  MINOR findings). Both summaries integrated; all MINOR findings repaired
  before the final freeze.
- Executive re-verification: full suite **145 passed** fresh; Hermes estop
  baseline 24 passed; canonical DBs byte-unchanged (full recheck at every
  freeze via var/evidence/recheck_canonical_databases.py).
- Frozen snapshot: var/evidence/wp4-gate/ (snapshot sha256
  `5fbf1fb80eea1949c29333268ac7fb482dca73df414fa382228bcc0a19d9540f`).
- Full detail: `docs/evidence/WP4_GATE_REPORT.md`.

## Implemented

The Supervisor can dispatch isolated role sessions (executive, builder,
verifier) through a narrow adapter boundary, record exact context and
provider identity durably, and recover from provider failure — without
adding Foundry tools to normal Hermes sessions and without touching live
Hermes.

## Implementation

| Module | Responsibility |
|--------|----------------|
| `src/jarvis_foundry/roles/manifest.py` | `ContextManifest`: exact role/mission/step/resource/capability/provider/model/isolation/tool-policy/budget/duration context; content-addressed via deterministic sha256 (`content_hash`); roundtrips through `to_payload`/`from_payload`; never carries credential fields |
| `src/jarvis_foundry/roles/router.py` | `RoleRouter`: abstract role -> ordered provider chain (preferred -> fallbacks, deduplicated); built-in roles (executive/builder/verifier/critic/repair/researcher/premium_reviewer); YAML role config loading (rejects template placeholders `__*__`, malformed files); provider diversity preference (`prefer_diversity` — True when configuration makes a distinct verifier provider possible; `diversity_resolution(builder_provider)` — best-effort distinct provider) |
| `src/jarvis_foundry/roles/adapter.py` | `RoleAdapter` protocol; `FakeHermesAdapter` — deterministic parity adapter, records every received manifest, scripted provider failure + outcomes; `HermesOneshotAdapter` — real dispatch through `.h` only, DISABLED by default, isolated HERMES_HOME required, live-Hermes root rejected, bounded by timeout, usage-file cost accounting |
| `src/jarvis_foundry/roles/dispatcher.py` | `RoleDispatcher.dispatch`: lease acquire -> agent_run.started (exact manifest hash + provider identity) -> adapter dispatch with heartbeats -> agent_run.completed/failed -> lease release; fallback on ProviderUnavailable; BLOCKED on exhaustion |
| `src/jarvis_foundry/roles/results.py` | `RoleResult`: structured status/verdict/provider/model/evidence/error class/cost; validated enums; serializes into `agent_runs.result_json` |
| `src/jarvis_foundry/roles/reaper.py` | `reap_stale_role_runs`: deterministic heartbeat-timeout enforcement — RUNNING role session with missing/expired lease is marked FAILED (`heartbeat_timeout`) via durable event; expired `role:*` leases released; idempotent |

## Safety Invariants (adapter boundary, C7)

- `HermesOneshotAdapter` is **disabled by default**: dispatch raises
  `ProviderUnavailable` unless explicitly enabled with an isolated
  `HERMES_HOME`.
- `hermes_root` must resolve to the isolated `.h` checkout; the live Hermes
  installation root is rejected at construction (`AdapterConfigurationError`).
- The command surface matches the real CLI (verified against `.h`
  `hermes_cli/_parser.py` AND a live `--help` probe): entry module is
  `hermes_cli.main` (there is no importable `hermes` module), the prompt is
  the `-z` VALUE, `--provider` pairs with `--model`, no `--role` flag exists
  (role rides in the manifest/instructions), `--usage-file` returns the JSON
  cost report even on failure. Regression tests pin the exact argv shape so a
  CLI drift fails loudly in the suite, not in production.
- `hermes_home` must be isolated: the live Hermes profile root (and any path
  inside it) is rejected at construction, exactly like the live installation
  root.
- Sessions are bounded (`timeout_seconds`); the subprocess is killed on
  expiry and the run is recorded FAILED (`timeout`).
- Crashed-caller recovery: re-dispatching a COMPLETED run_id with the
  identical context replays from the ledger (no adapter re-run, no new
  events); a changed context under the same run_id fails loudly on the event
  idempotency key (first write stands).
- No Foundry tools/plugins are ever added to normal Hermes sessions; the
  adapter is the only boundary through which a role session launches.

## Durable Record

- `agent_runs` rows (schema v3) carry: run_id, mission_id, step_id, role,
  provider (actually used), model, context_manifest_hash (recomputable),
  tool_policy_json, status RUNNING/COMPLETED/FAILED, started_at/heartbeat_at/
  finished_at, result_json (full structured result), failure_class, cost.
- Events are append-only with idempotency keys: started per (run, provider
  attempt), heartbeat with per-run counter, completed/failed per run — all
  dedupe on identical re-delivery (EventConflict on changed payload under the
  same key, per WP3 B6 semantics).
- Leases (`role:{role}:{mission}:{step}`) bound every session; the reaper
  turns an expired/missing lease into a durable `heartbeat_timeout` failure.

## Test Inventory (145-test suite)

- `test_role_manifest.py` (10): exact context fields, deterministic
  content-addressing, tamper rejection, roundtrip, no-credentials policy.
- `test_role_routing.py` (14): chains, dedupe, unknown-role rejection,
  diversity resolution semantics, YAML loading + placeholder rejection
  (provider AND model).
- `test_role_dispatch.py` (19): gate fake mission (three isolated sessions,
  exact context + provider identity durably recorded), leases acquired and
  released, concurrent same-step lease BLOCKED, heartbeat events, provider
  fallback (failure_class cleared on completion), exhaustion -> BLOCKED,
  adapter crash -> FAILED, structured verdicts, real-Hermes safety boundary
  (disabled by default, live root + subdirs rejected, live profile home
  rejected, isolated home required), command-surface regression (real CLI
  argv shape, isolated HERMES_HOME, bounded), usage-cost parsing, ledger
  replay + fail-loud conflict, diversity recorded in ledger,
  provider_override.
- `test_role_reaper.py` (6): expired-lease run marked FAILED heartbeat_timeout,
  orphan run (no lease) marked FAILED, idempotency, live-lease skip, expired
  role-lease release scope, completed/non-role leases untouched.

## Verification

- Independent verifiers deleg_f2954000 + deleg_221bd327 (fresh isolated
  adversarial sessions): C1–C7 all PASS; 8 findings repaired with regression
  tests (isolation gap, replay conflict, command surface x2, reaper missing,
  diversity semantics, failure_class staleness, placeholder models, live-root
  containment). Full detail: `docs/evidence/WP4_GATE_REPORT.md`.
- Full suite: **145 passed** (fresh reproduction, frozen state).

## Remaining Work

- WP4 gate closed (PASS). Next: WP5 (mission and verification loop) —
  next-action proposal validation, step dispatch, result intake, independent
  verification, repair, rejection, blocking, local commit after PASS.
  Gate: the loop repairs a deliberately failed step and continues to the next
  useful action without owner prompting.
