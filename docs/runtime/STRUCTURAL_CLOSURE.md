# Runtime structural closure (Goal 3)

Focused production truth. Not a repository-wide remaster.

## Configuration authority

`config/foundry.yaml` is an **EXPLICIT TEMPLATE / HISTORICAL** file.
It is not loaded by the Supervisor, CLI, installer, or worker loop.

Production precedence (one chain):

1. CLI (`--home`, `--interval`, `--spawn-worker-loop`, `--worker-loop-config`)
2. Environment (`JARVIS_HOME`, `HERMES_HOME`, `JARVIS_WORKER_LOOP_CONFIG`,
   `JARVIS_PYTHON_EXE`, `JARVIS_SPAWN_WORKER_LOOP`, `JARVIS_HEARTBEAT_SECONDS`)
3. Installed `{foundry_home}/foundry-profile.yaml` (installer-owned)
4. Worker runtime YAML (enablement + Hermes/OpenHands executables)
5. Deterministic defaults (`%LOCALAPPDATA%/hermes/foundry`, spawn OFF)

See `jarvis_foundry.runtime.authority`.

## FAST vs FULL tests

- FAST (normal development): `pytest tests/foundry`
- FULL (canonical, includes convergence): `pytest tests/foundry tests/convergence`

Default `pytest` (`pyproject.toml` `testpaths`) is FAST. FULL is never
implied by an unqualified `pytest`.

## Production execution path

Count = 1:

`runtime.worker_loop` → `MissionLoop` → `HybridWorkerRoleAdapter` → worker adapters

Classifications:

- `HybridStepController`: TEST_ONLY
- `HermesOneshotAdapter`: LEGACY_DISABLED (roles-layer duplicate; not the live driver)
- `JARVIS-Foundry-Worker-Loop` scheduled task: LEGACY_DISABLED; cannot be enabled
  as production. Supervisor owns lifecycle; `spawn_worker_loop` defaults OFF.

## Supervisor deployment mechanism

`jarvis install --v1` writes:

- scheduled task with absolute python, `--home`, and `WorkingDirectory`
- `foundry-profile.yaml` including `source_sha` / `code_origin`

The live Supervisor is **not** retargeted by Goal 3. Live adoption is Goal 4.

## Trust

Allowed: LAB, PROVEN, TRUSTED_AUTONOMOUS, DEGRADED, SUSPENDED.

Trust changes only through named evidence-bearing events. Heartbeats preserve
the current trust state and never promote to TRUSTED_AUTONOMOUS. Goal 1 / Goal 2
are engineering proofs, not an unattended trust gate. Honest live state is LAB.
