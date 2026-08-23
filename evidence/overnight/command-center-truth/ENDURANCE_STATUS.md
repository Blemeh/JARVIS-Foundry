# Endurance status

```text
ENDURANCE:
COMPLETE
PASS
6h gate satisfied (2026-08-23T00:46:55Z → 2026-08-23T06:46:56Z)
```

## Final run (task 107532)

| Field | Value |
|-------|-------|
| Duration | **21,600,417 ms** (~6.00 h) |
| Cycles | **41,289** |
| Errors | **0** |
| Throttle | `ENDURANCE_REQUEST_DELAY_MS=500` |
| Preview | `http://localhost:4523` |
| Checkpoints | start + every 15m (t+15m … t+360m) + end |
| `pass` | **true** |

## Prior attempts (historical)

Earlier runs failed when `vite preview` on 4523 crashed (~31–48 min) or produced HTTP 500/404 storms under unthrottled load. Recovery required dedicated preview restart and 500ms request delay.

## Owner note

`NIGHT_SHIFT_STATE.json` may still record `DEFERRED_BY_OWNER` for the development-continuation phase; this artifact documents that the **6-hour gate was subsequently completed** with zero errors regardless.

## Artifacts

| File | Role |
|------|------|
| `ENDURANCE_RESULT.json` | Authoritative pass/fail record |
| `endurance-live.log` | Process stdout/stderr |
