# Owner Workflow Result

Work package **WP-C** — Command Center truth lane (`candidate/overnight-command-center-truth`).

Recorded after owner-workflow e2e and disabled-control audit gates.

## Navigation and identity

| Check | Result | Evidence |
| --- | --- | --- |
| Primary nav `aria-current` on all major routes | PASS | `e2e/owner-workflow.spec.ts` |
| Data source pill shows `SIMULATED` on every route | PASS | `e2e/owner-workflow.spec.ts`, `e2e/truth-lane.spec.ts` |
| Trust pill shows trust **state** (not data source mode) | PASS | `TopStatusBar.tsx`, `e2e/owner-workflow.spec.ts` |
| Deep link `/missions/:id` resolves | PASS | `e2e/owner-workflow.spec.ts` |
| Browser back from mission detail | PASS | `e2e/owner-workflow.spec.ts` |
| Sidebar OWNER-01 labeled `SIMULATED` | PASS | `Sidebar.tsx`, `sidebar.test.tsx` |
| Systems release line labeled `projection` | PASS | `Systems.tsx` |
| Evidence integrity badge `Integrity SIMULATED` | PASS | `Evidence.tsx`, `e2e/owner-workflow.spec.ts` |

## Disabled control audit (non-Discovery)

| Surface | Control | Simulated behavior | Honesty mechanism |
| --- | --- | --- | --- |
| Overview | Owner channel | Disabled | `aria-label="Owner channel locked"` |
| Evidence | Export ledger | Enabled (fixture export only) | `Integrity SIMULATED` badge + title |
| Agents | Deploy worker | Enabled when not STOP | title explains simulated-only |
| Missions | New / Import | Enabled when mutations allowed | title when frozen |
| Mission detail | Halt / Re-evaluate gate | Enabled (simulated contract) | aria-label names simulated path |
| Memory | Re-evaluate node | Simulated-only | title when unavailable |
| Notifications | Simulate next event | Disabled during STOP | title explains freeze |
| Emergency STOP | Activate / Resume | Simulated path explicit | dialog copy + labels |

Unit audit: `src/routes/__tests__/disabledControlsAudit.test.tsx`

## STOP / trust visibility

- Trust summary loaded in context (`trustSummary`, `trustSummaryStatus`) and surfaced in top bar.
- STOP chip and Emergency STOP dialog distinguish `SIMULATED` vs `LIVE` vs unavailable sources.

## Backend handoffs (not invented)

Unsupported owner channel STT/TTS, live ledger export, and live worker deployment remain disabled or simulated-only. See `BACKEND_HANDOFF.md`.

## Automated gates (WP-C)

```text
npm run test       → includes topStatusBar, sidebar, disabledControlsAudit
npx playwright test e2e/owner-workflow.spec.ts
```
