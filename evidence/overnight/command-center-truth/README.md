# Command Center Truth Lane

Overnight work package enforcing honest operational numbers across non-Discovery Command Center routes.

## Scope

- Live snapshot mapping via `snapshotMapper.ts`
- Simulated fixture rendering with explicit `SIMULATED` labels
- Route resilience (loading, partial, error, empty)
- Accessibility for telemetry and resource meters

## Truth rules

1. Every operational number requires a source field or explicit `UNKNOWN` / `NOT REPORTED` / `SIMULATED` label.
2. Never use truthiness where zero is valid.
3. Live mode never silently falls back to fixtures.
4. Partial snapshots keep usable sections; missing sections stay explicit.

## Gates

```bash
cd ui/command-center
npm run lint
npm run typecheck
npm run test
npm run build
```

Visual captures (port 4523):

```bash
npm run build
npx vite preview --port 4523 --strictPort
node scripts/capture-truth-visuals.mjs
```

## Artifacts

| File | Purpose |
|------|---------|
| `INITIAL_STATE.json` | Entry SHA, packages, known defects |
| `SOURCE_TO_DISPLAY_MATRIX.md` | Field → display mapping |
| `BACKEND_HANDOFF.md` | Missing backend fields |
| `HANDOFF.json` | Final lane handoff |
| `screenshots/` | Visual evidence at 3 viewports |
