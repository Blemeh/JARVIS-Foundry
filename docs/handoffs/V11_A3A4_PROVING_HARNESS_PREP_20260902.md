# V11 A3/A4 proving harness preparation

Date: 2026-09-02

Base: `00ee2e148c12151699a796649a1cb87ed892a69b` (A2, schema v39)

Branch: `candidate/v11-a3a4-proving-harness-20260902`

Scope: tests, scripts, and evidence contract only. No product architecture,
live runtime, live database, Scheduler, release pointer, main, or origin change.

## Purpose

`scripts/v11_a3a4_proving.py` prepares and later runs the final V11 A3/A4
unattended proof. It deliberately contains no winning candidate, target
improvement, product selection policy, or alternate executive. The final
integrated V11 candidate supplies a small driver command that exposes the
existing authority spine through the generated JSON contract.

The harness independently derives metrics from stable record identities. It
does not accept aggregate PASS claims from the driver.

## Covered flow

```text
cold unattended cycle
  -> real observations
  -> candidates / reasoned rejections
  -> selection
  -> objective
  -> mission / isolated worktree
  -> build / test
  -> independent acceptance
  -> accepted outcome
  -> learning
  -> WAIT
```

Required record identities are candidate, objective, mission, builder run,
effect, acceptance, outcome, and learning. Observations and repair attempts
also carry stable IDs. Every record supplies an `id` and a semantic
`identity_key`; the harness detects multiple IDs for one semantic identity
across samples and restarts.

## Fault plan

Each point is injected once, then the driver is called again with the same
`run_id` and `cycle_id` and `resume=true`:

1. `post-candidate`
2. `post-selection-objective`
3. `mid-build`
4. `post-effect-pre-acceptance`
5. `stop-resume`
6. `process-restart`

The driver must acknowledge `INJECTED` and then `RECOVERED`. The STOP case must
also return `stop_new_dispatch_blocked=true`. Changed cycle identity or changed
record identity fails closed.

## Metrics and hard assertions

The evidence includes observations, candidates, rejected, eligible,
objectives, missions, builder runs, effects, acceptances, verified outcomes,
learning updates, repair attempts, cycle duration, and recovery durations.

The hard zero assertions are:

- duplicate candidates, objectives, missions, effects, and learning;
- false successes;
- unverified completions.

A successful outcome is counted only when it links to an independent accepted
verdict whose builder and verifier run IDs differ. A completed mission without
that acceptance is an unverified completion. Learning cannot repair either
failure into a PASS.

## Prepare the frozen 48-hour bundle

Run this only after the final integrated V11 candidate SHA is known. Preparation
writes a checkpoint, contract, fault plan, and manifest but starts no cycle:

```powershell
$env:PYTHONPATH = (Get-Location).Path
python scripts/v11_a3a4_proving.py prepare `
  --expected-sha <FINAL_INTEGRATED_V11_SHA> `
  --duration-hours 48 `
  --output C:\temp\JARVIS-V11-A3A4-48H-<FINAL_SHA>
```

Create a driver-spec JSON outside the repository:

```json
{
  "argv": [
    "python",
    "C:\\path\\to\\final-v11-driver.py"
  ]
}
```

The generated `HARNESS_CONTRACT.json` is authoritative for driver request and
response fields. The driver receives `--request <path>` and prints exactly one
JSON snapshot to stdout. Product logs belong on stderr or in product-owned
evidence files.

## Start or resume the 48-hour window

The actual runner is blocked unless all of these are true:

- the worktree is clean;
- `HEAD == --expected-sha`;
- `--final-integrated-sha == --expected-sha`;
- the exact tree contains integrated V11 product changes beyond A2;
- the prepared duration is at least 48 hours;
- the owner/operator explicitly passes `--allow-48h`.

The wall-clock start and deadline are written only by the first authorized
`run`, not by `prepare`.

```powershell
$env:PYTHONPATH = (Get-Location).Path
python scripts/v11_a3a4_proving.py run `
  --expected-sha <FINAL_INTEGRATED_V11_SHA> `
  --final-integrated-sha <FINAL_INTEGRATED_V11_SHA> `
  --driver-spec C:\temp\v11-final-driver.json `
  --output C:\temp\JARVIS-V11-A3A4-48H-<FINAL_SHA> `
  --interval-seconds 300 `
  --allow-48h
```

`--once` performs one checkpointed cycle and exits, which supports a durable
external scheduler or process restart. Running the same command without
`--once` reloads the checkpoint and continues until the recorded wall-clock
deadline. The 48-hour gate is not started by this preparation lane.

## A2 execution-barrier dry run

The A2-only command uses a disposable schema-v39 database and a real read-only
project observation of the exact current worktree. It does not use the live
database or runtime:

```powershell
$env:PYTHONPATH = (Get-Location).Path
python scripts/v11_a3a4_proving.py a2-barrier `
  --output C:\temp\JARVIS-V11-A3A4-Harness-A2-Dry-Run
```

The command passes only if the cycle returns to `WAIT`/`IDLE`, SQLite quick
check is `ok`, the Git worktree inventory is unchanged, and all three execution
counts are exactly zero:

```text
objectives = 0
missions   = 0
effects    = 0
```

## Evidence bundle

Prepared/final runs use atomic checkpoints and append-only, fsync-backed JSONL
calls, snapshots, and fault records. Final artifacts are hashed by
`RUN_MANIFEST.json`:

- `HARNESS_CONTRACT.json`
- `FAULT_PLAN.json`
- `CHECKPOINT.json`
- `DRIVER_CALLS.jsonl`
- `SNAPSHOTS.jsonl`
- `SAMPLES.jsonl`
- `FAULTS.jsonl`
- `FINAL_METRICS.json`
- `FINAL_VERDICT.json`
- `FINAL_REPORT.md`

The A2 dry run writes `A2_BARRIER_RESULT.json` plus its hash manifest.
