# Unattended Harness Evidence Index

Frozen base: `107827d81eaee514edc66eab98390438b63c1650`
Candidate branch: `candidate/postv7-unattended-harness-20260824`
Implementation commit: `e843c04` (pushed and verified on the candidate branch)

## Fresh disposable proofs

| Run | Clock | Verdict | Evidence |
| --- | --- | --- | --- |
| Accelerated 12-hour-equivalent | `ACCELERATED_DISPOSABLE` | PASS | `runs/ACCELERATED_12H_EQUIVALENT/FINAL_VERDICT.json` |
| Short real-time | 900.018 seconds wall time | PASS | `runs/SHORT_REAL_TIME/FINAL_VERDICT.json` |

Both bundles contain the required manifest, scenario, timeline, invariant,
fault, resource, autonomy, incident, effect, briefing, verdict, and report
artifacts. Manifest hashes were independently rechecked after consolidation.

The accelerated bundle also records the fresh 21-row V7 resilience matrix,
owned process identity replacement, harness interruption/resume, and eight
false-PASS attacks. The short real-time bundle records the same process,
interruption, and false-PASS proofs without claiming a long soak.

## Profile readiness

`QUICK_30M`, `STANDARD_6H`, `FULL_12H`, `FULL_24H`, and
`REBOOT_ASSISTED_24H` are declared in `config/unattended_profiles.yaml` and
have scenario files. The CLI refuses long real-time profiles unless an owner
explicitly supplies `--allow-long-profile`. No long profile ran in this lane.

## Boundary

All runs used fake providers, temporary release state, disposable process
fixtures, and isolated evidence directories. Production, live DB, Scheduler,
credentials, deployments, and main were not modified.
