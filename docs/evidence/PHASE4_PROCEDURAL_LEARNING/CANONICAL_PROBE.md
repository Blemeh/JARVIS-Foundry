# Phase 4 Canonical Bounded Probe

**Checkout:** canonical `main` at `6c5eb05` (2026-08-18)

The probe used the harmless local synthetic application on `127.0.0.1:8787`,
the qualified compiled bundle with JARVIS artifact-byte binding,
`0d9a3e227315314fed4980739cf9d95cb803f636b3da1af62980c113180686e0`,
OpenAdapt `1.12.1`, OpenAdapt-Flow `1.31.0`, and the independent oracle
endpoint `/state`. It used temporary Foundry databases and did not touch the
live Hermes/Foundry database.

## Results

- **Fresh replay:** request `canonical-remediation-final-1`, task
  `PH4-CANONICAL-REMEDIATION`, option `CR1` — `COMPLETED`, effect
  `COMPLETED`, recovery
  `COMPLETED_VERIFIED`, `model_calls=0`, exit `0`. Report:
  `C:\temp\phase4-canonical-remediation-final-0818\openadapt-run\report.json`.
- **Duplicate replay:** the same request and parameters returned the recorded
  terminal effect with `report_path=null`; the provider record count stayed at
  `1`, proving no second launch or backend row.
- **STOP before dispatch:** an external STOP marker returned `NOT_READY`, exit
  `2`; no OpenAdapt process launched and no provider row was created.
- **Resume:** after clearing the external marker, request
  `canonical-remediation-resume-1` reached `COMPLETED`, `COMPLETED_VERIFIED`,
  `model_calls=0`, exit `0`; the provider count became `2`.
- **Cleanup:** the synthetic server was stopped, port `8787` was free, and no
  process command line referencing the remediation probe or OpenAdapt flow
  remained.

The earlier `canonical-final-1` and candidate remediation probes remain
historical evidence; this record is the post-promotion canonical Phase-4
probe.
