# V4 MR-1 — Mandatory Internal Model Routing (DIR-2026-08-12)

**Slice:** V4_CONTINUOUS_AUTONOMY / truth-critical reliability — mandatory
model routing
**Commit:** `bab5830` (fix(routing): DIR-2026-08-12 mandatory internal model
routing)
**Date:** 2026-08-12
**Status:** CLOSED — independent verifier **PASS** (deleg_debc2612,
2026-08-12T23:39+02:00, P1–P10 probe matrix executed; 2 info-severity
findings with no routing impact — verdict JSON archived at
`var/evidence/v4-mr1-routing/verdict-deleg_debc2612.json`, sha256
5ff10149…). Ledger: `verification.passed:verdict-v4-mr1-routing:round1` +
`evidence.created:v4-mr1-routing-close` (seq 14050–14051, schema 16).

## Problem (observed, not assumed)

The live ledger showed real JARVIS builder dispatches recorded with the
WRONG model:

```
agent_runs:
  run-13ec6f846520       builder COMPLETED opencode_go kimi-k2.7-code 2026-08-12T20:11:48Z
  run-13ec6f846520-verify verifier COMPLETED opencode_go deepseek-v4-flash 2026-08-12T20:15:15Z
  run-446d64ab721b       builder COMPLETED opencode_go kimi-k2.7-code 2026-08-12T20:17:12Z
  run-446d64ab721b-verify verifier COMPLETED opencode_go deepseek-v4-flash 2026-08-12T20:21:41Z
```

Root cause: `RoleRouter.BUILTIN_ROLES` (the `model_roles: default`
configuration used by the live profile `foundry-profile.yaml`) routed:

- `builder` → kimi-k2.7-code
- `repair` → kimi-k2.7-code
- `critic` → deepseek-v4-pro
- `researcher` → deepseek-v4-pro
- `premium_reviewer` → vertex_ai / gemini-2.5-pro

DIR-2026-08-12 (Mandatory JARVIS Internal Model Routing) requires EVERY
real JARVIS internal dispatch to resolve to `provider = opencode_go`,
`model = deepseek-v4-flash`, with silent fallbacks to Kimi/GLM/other models
disabled, and any unavailability recorded BLOCKED — never substituted.

## Repair

1. `src/jarvis_foundry/roles/router.py`
   - `BUILTIN_ROLES`: every role now resolves to `DIRECTIVE_PROVIDER`
     (`opencode_go`) + `DIRECTIVE_MODEL` (`deepseek-v4-flash`),
     `fallbacks: []`; constants `DIRECTIVE_PROVIDER`/`DIRECTIVE_MODEL`
     added so the policy is a single named source of truth.
   - `RoleRouter.enforce_directive_routing()`: raises
     `RoleConfigurationError` naming every role whose provider, model or
     fallback list deviates — a non-compliant role must be recorded
     BLOCKED, never substituted.
   - `prefer_diversity()` docstring records the directive override:
     the DeepSeek-only constraint overrides provider-diversity
     preferences this window; verifier independence is delivered by
     separate verifier sessions + read-only verification of frozen
     artifacts + deterministic adversarial probes (limitation visible).
2. `src/jarvis_foundry/v3_loop.py` — `run_one_loop` calls
   `router.enforce_directive_routing()` as step 0, BEFORE any observation,
   mutation or dispatch. (Incidental fix found by the new tests: the
   honest-WAITING path crashed with `IndexError` on an empty candidate
   table — `top[0]` in the reason; now reports `top: none`.)
3. `var/evidence/v3_loops/live_loop_driver.py` and
   `resume_blocked_mission.py` — enforcement after router construction
   (local evidence tooling, untracked under `var/`).
4. `config/templates/model_roles.example.yaml` — rewritten to the
   directive policy with the constraint documented in the header;
   `builder_verifier_provider_diversity_preferred: false`.

## Tests (deterministic, every role + fallback path)

`tests/foundry/test_directive_model_routing.py` (26 tests):

- every BUILTIN_ROLES entry is opencode_go + deepseek-v4-flash, no
  fallbacks; default router passes enforcement; every role resolves.
- enforcement rejects each forbidden model (`kimi-k2.7-code`,
  `kimi-k2.5`, `glm-5`, `glm-4.5`, `gemini-2.5-pro`, `deepseek-v4-pro`,
  `claude-sonnet`, `qwen3.8-max`), each forbidden provider (`vertex_ai`,
  `anthropic`, `vertex`), and any fallback on ANY role.
- A/B: kimi builder rejected, directive model passes; all offending roles
  named in the error.
- YAML: compliant config passes; kimi+fallback config rejected.
- `run_one_loop` refuses a non-compliant router with ZERO events/missions/
  candidates mutated; compliant router runs (honest WAITING on empty
  candidate table — regression for the IndexError).
- dispatcher: single-provider outage with the directive router → BLOCKED,
  `agent_run.failed:provider_unavailable`, provider/model recorded as the
  directive pair, lease released, no substitution.
- ledger truth: a completed dispatch records opencode_go/deepseek-v4-flash
  in `agent_run.started`.
- diversity limitation: `prefer_diversity()` is False on the default
  config.

## Evidence

- Full suite artifact: `var/evidence/v4-mr1-routing/suite-v4-mr1.txt`
  (expected 414 passed, EXIT=0; regenerated at 2026-08-12T23:32+02:00
  against HEAD `bab5830`).
- Live ledger historical violation rows (before repair): `agent_runs`
  run-13ec6f846520 / run-446d64ab721b (kimi builders) — preserved as
  history; superseded by this repair.
- Next: independent adversarial verifier (freeze discipline), then a REAL
  bounded dispatch confirming the first post-repair builder, verifier and
  recovery worker all record deepseek-v4-flash on the ledger.
