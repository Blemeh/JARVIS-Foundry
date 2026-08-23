# Fixture and Mapper Hardening Result

Work package **WP-D** — Command Center truth lane.

## Boundary classes covered

| Class | Fixture case id | Mapper assertion |
| --- | --- | --- |
| constraints | `constraints-present` | factor detail counts constraints |
| stale leases | `stale-leases` | factor detail counts leases |
| missing nested objects | `missing-factors-input` | factors still present, no throw |
| zero values | `zero-cost` | cost row `0` preserved |
| false values | `false-stop` | STOP clear, not coerced active |
| null values | `null-cost` | `NOT_REPORTED_NUMERIC` |
| malformed values | `malformed-cost` | `NOT_REPORTED_NUMERIC` |
| contradictory factors | `contradictory-trust` | STOP wins → `SUSPENDED` |
| negative values | `negative-cost` | negative preserved (not clamped to zero) |
| extremely large values | `large-cost` | large value preserved |
| UNKNOWN string tokens | `unknown-token` | `NOT_REPORTED_NUMERIC` |
| stale timestamps | `stale-timestamp` | maps without throw |
| future timestamps | `future-timestamp` | maps without throw |
| Unicode / long text | `unicode-objective` | mission title preserved |

## Artifacts

- `ui/command-center/src/lib/__tests__/snapshotTruthCases.ts` — controlled snapshot fixtures
- `ui/command-center/src/lib/__tests__/snapshotMapper.test.ts` — table-driven `snapshotTruthCases boundary matrix`

## Gate

```text
npm run test → snapshotMapper + snapshotTruthCases matrix green
```
