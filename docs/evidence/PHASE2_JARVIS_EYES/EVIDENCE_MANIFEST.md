# JARVIS EYES PHASE 2 — EVIDENCE MANIFEST

Evidence root: `docs/evidence/PHASE2_JARVIS_EYES/`
Raw probe outputs: `var/phase2-eyes/` (not committed; hashes below)

## Committed evidence docs

| Artifact | Purpose |
|---|---|
| CANONICAL_RECONCILIATION.md | dynamic canonical truth reconstruction |
| PHASE2_BASELINE.md | candidate baseline + boundary declaration |
| SCREENPIPE_CONNECTOR_GATE.md | connector registration + architecture reuse |
| PERCEPTION_PRIVACY_GATE.md | privacy firewall evidence |
| PERCEPTION_EVENT_GATE.md | event types + observation contract + epistemic |
| PERCEPTION_RESTART_GATE.md | cursor/restart evidence |
| PERCEPTION_STOP_GATE.md | STOP/resume evidence |
| PERCEPTION_RESOURCE_REPORT.md | overhead measurement |
| PERCEPTION_FAULT_MATRIX.md | P2-F1..F20 results |
| SECRET_SCAN.md | synthetic-secret scan |
| FULL_TEST_REPORT.md | focused + affected + full suite |
| INDEPENDENT_REVIEW.md | verifier verdict |
| PHASE2_GATE_REPORT.md | acceptance-gate mapping |
| PHASE2_FINAL_VERDICT.md | terminal verdict |

## Raw probes (var/, hash-recorded in manifest.json)

| File | SHA-256 |
|---|---|
| fault_matrix.json | `5c4a7f4111705368bb344cf11f6d5dfbcbe06d8a222a7a9b318f2418acafc04b` |
| secret_scan.json | `e7f462eb3b220a4567595f999f097430e1dc3887ccb91db3d1d8abd12c75562f` |
| resource_report.json | `b16a4104cc3b61e83a99dfb152d19465a00037540e60b4836927c429cbb1c9c6` |
| independent_verifier.json | (see manifest.json after final run) |
| full-suite-run.txt | (see manifest.json after final run) |

Final raw hashes (2026-08-18 10:40 UTC):

| File | SHA-256 |
|---|---|
| fault_matrix.json | `a0bf045608f4f64987ebad7d5c9360f6cfa71a23a8310f11f42a6790d0a73c0a` |
| secret_scan.json | `e7f462eb3b220a4567595f999f097430e1dc3887ccb91db3d1d8abd12c75562f` |
| resource_report.json | `ae20dfb85a372a88d7c428219c14c8323a4dc5e2ac5eb6cb4f6f9104f79987c1` |
| independent_verifier.json | `59882bbe5dd40586f4880caf123ed0b0b0616e3043a78e40632dc90c4badd930` |
| full-suite-run.txt | `28bd10e3879e5b28794cbae626812956784215c6236551622258a397971a7391` |

## Source artifacts

- `src/jarvis_foundry/perception/` (implementation)
- `config/perception.yaml` (fail-closed config)
- `tests/foundry/test_perception.py` (23 tests)
- `scripts/perception_evidence_probe.py` (fault matrix + secret scan + resource)
- `scripts/phase2_independent_verifier.py` (25-item attack plan)

## Frozen Phase-1 import

- Lab manifest verified 50/51; `PHASE2_ENTRY_CONTRACT.md` drift recorded.
- Screenpipe 0.4.40 / upstream `c005dad4` / Commercial License posture kept.
