# V4-7 — Command Center management by exception

Status: PASS — commit `4cd6e20`

## Behavior delivered

The live Command Center now has an `/owner` route titled **Owner control /
management by exception**. It maps the canonical snapshot through
`RealJarvisRuntimeAdapter.getOwnerControlSurface()` and displays:

- runtime state, reason, source, pause/STOP/resume truth;
- open decision queue and consequential options/evidence;
- authenticated goals, constraints, vetoes, redirects, reprioritizations,
  and directive history;
- briefing history and result-lineage references.

The route is explicitly read-only and says it is not a second authority. A
simulated client without a canonical owner-ledger method renders an honest
empty state rather than fixture authority. Existing design-system components,
navigation, route titles, and responsive panel patterns are preserved.

## Proof

In `ui/command-center`, all of the following passed:

- `npm.cmd run typecheck`
- `npm.cmd run lint`
- `npm.cmd run test` — 22 files / 108 tests
- `npm.cmd run build`

The backend snapshot and owner-management regressions also passed. This proves
the isolated source build; it is not live-runtime or provider evidence.

