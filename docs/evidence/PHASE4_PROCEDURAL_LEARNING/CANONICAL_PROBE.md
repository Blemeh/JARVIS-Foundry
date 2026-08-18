# Phase 4 Bounded Remediation Probe

**Checkout:** `candidate/procedural-learning-phase4-remediation` at
`992317e8c1bac8ef556f05ac82a884bff3a91c36`, applied to convergence base
`657519c` (2026-08-18)

The probe used the harmless local synthetic application on `127.0.0.1:8787`,
the qualified compiled bundle with JARVIS artifact-byte binding,
`0d9a3e227315314fed4980739cf9d95cb803f636b3da1af62980c113180686e0`,
OpenAdapt `1.12.1`, OpenAdapt-Flow `1.31.0`, and the independent oracle
endpoint `/state`. It used temporary Foundry databases and did not touch the
live Hermes/Foundry database.

## Results

- **Fresh replay:** request `remediation-final-1`, task `PH4-REMEDIATION`,
  option `R1` — `COMPLETED`, effect `COMPLETED`, recovery
  `COMPLETED_VERIFIED`, `model_calls=0`, exit `0`. Report:
  `C:\temp\phase4-remediation-probe-final-0818-b\openadapt-run\report.json`.
- **Duplicate replay:** the same request and parameters returned the recorded
  terminal effect with `report_path=null`; the provider record count stayed at
  `1`, proving no second launch or backend row.
- **STOP before dispatch:** an external STOP marker returned `NOT_READY`, exit
  `2`; no OpenAdapt process launched and no provider row was created.
- **Resume:** after clearing the external marker, request
  `remediation-resume-1` reached `COMPLETED`, `COMPLETED_VERIFIED`,
  `model_calls=0`, exit `0`; the provider count became `2`.
- **Cleanup:** the synthetic server was stopped, port `8787` was free, and no
  process command line referencing the remediation probe or OpenAdapt flow
  remained.

The previous `canonical-final-1` probe and its earlier bundle hash remain
historical evidence for the pre-remediation wrapper; this record is the
current Phase-4 remediation probe and will be followed by a fresh canonical
`main` probe after promotion.
