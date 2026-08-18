# Phase 1 OpenAdapt Revalidation

The frozen qualification lab at
`C:\Users\b\Documents\JARVIS-Eyes-Qualification-Lab` was inspected before
integration. The isolated lab interpreter reported:

```text
openadapt 1.12.1
openadapt-flow 1.31.0
```

The frozen contract and evidence files are:

- `contracts\OPENADAPT_QUALIFICATION.md`
- `contracts\OPENADAPT_INTEGRATION_CONTRACT.md`
- `evidence\openadapt_bundle_integrity_evidence.txt`
- `evidence\openadapt_crash_partial_action.txt`
- `evidence\openadapt_stop_process_control.txt`
- `evidence\openadapt_windows_probe.txt`
- `openadapt\bundle-synthetic18\manifest.json`

The Phase-1 `final\evidence_manifest.json` was rehashed: 51/51 entries were
present; every OpenAdapt artifact listed above matched its recorded SHA-256.
The lab's separately recorded historical Phase-2 entry-contract drift remains
unchanged and was not repaired as part of this Phase-4 mission.

Revalidated Phase-1 posture:

| Surface | Result | Phase-4 treatment |
|---|---|---|
| Browser record/compile/replay | QUALIFIED | governed candidate for READY |
| Browser deterministic replay | PASS; healthy runs recorded 0 model calls | adapter rejects grounding and records the claim only |
| Parameter substitution | PASS in the lab oracle | input schema and request hash are frozen |
| Bundle integrity | PASS; tamper produced `BundleIntegrityError` | JARVIS verifies file hashes before launch |
| False visual success | PASS; screen success with effect 0/1 halted | independent oracle is mandatory |
| Ambiguity halt | PASS | irreversible geometry-only and ambiguous controls are refused |
| Windows desktop | PROBING; replay NOT_TESTED without WAA | registered separately as `openadapt.desktop: PROBING` |

The two unresolved Phase-1 findings were treated as hard Phase-4 requirements:

1. Killing only the OpenAdapt parent left 11 browser descendants in the lab.
   Phase 4 adds scoped descendant tracking and cleanup verification.
2. A hard kill did not provide a durable OpenAdapt checkpoint and partial
   effect state was not sufficient to assume safety. Phase 4 routes every
   replay through JARVIS's existing effect journal and reconciliation path;
   ambiguous outcomes freeze and never blindly retry.
