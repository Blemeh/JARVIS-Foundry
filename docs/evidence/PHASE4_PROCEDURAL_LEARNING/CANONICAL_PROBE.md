# Phase 4 Canonical Bounded Probe

**Checkout:** canonical `main` at `11161a2` (2026-08-18)

The probe used the harmless local synthetic application on `127.0.0.1:8787`,
the qualified compiled bundle
`6e84ebc3d938cbff463e1e0da5e2b9094b8b9256880c23772eedf927f06c2377`,
OpenAdapt `1.12.1`, OpenAdapt-Flow `1.31.0`, and the independent oracle
endpoint `/state`. It used a temporary Foundry database and did not touch the
live Hermes/Foundry database.

## Results

- **Fresh replay:** request `canonical-final-1`, task
  `PH4-CANONICAL-FINAL`, option `CF1` — `COMPLETED`, effect
  `COMPLETED`, recovery `COMPLETED_VERIFIED`, `model_calls=0`, exit `0`.
  Report: `C:\temp\phase4-canonical-probe-0818-final\openadapt-run\report.json`.
- **Duplicate replay:** the same request and parameters returned the recorded
  terminal effect with `report_path=null`; the provider record count stayed at
  `1`, proving no second launch or backend row.
- **STOP before dispatch:** an external STOP marker returned `NOT_READY`, exit
  `2`, and the provider count stayed at `1`.
- **Resume:** after removing the marker, request `canonical-final-resume`
  reached `COMPLETED`, `COMPLETED_VERIFIED`, `model_calls=0`, exit `0`; the
  provider count became `2`.
- **Cleanup:** the synthetic server was stopped, port `8787` was free, and no
  process command line referencing the canonical probe or bundle remained.

The first attempted invocation used the app root instead of `/state`; the
oracle correctly froze that run as `AMBIGUOUS` on `JSONDecodeError`. It was a
probe-input error, not a code-path result, and it was not used as acceptance
evidence.
