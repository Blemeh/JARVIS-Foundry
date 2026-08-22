# Accessibility Result

Work package **WP-F** — Command Center truth lane.

## Automated gate

Harness: `ui/command-center/e2e/accessibility.spec.ts`

- Tooling: `@axe-core/playwright` with tags `wcag2a`, `wcag2aa`, `best-practice`
- Routes scanned: Overview, Missions, Agents, Evidence, Systems, Timeline, Notifications, Executive, Memory
- Keyboard probes: skip link → `#main-content`, `aria-current="page"` on active nav item

```text
npx playwright test e2e/accessibility.spec.ts
11/11 PASS
```

### Violations fixed in WP-F

| Rule | Route(s) | Fix |
| --- | --- | --- |
| `page-has-heading-one` | Overview | Promoted hero copy to `<h1>` |
| `heading-order` | Missions, Timeline, Notifications | Inserted proper `h2` group headings; demoted card titles to `h2`/`h3` sequence |
| `color-contrast` | Agents | Removed opacity pulse on log text; cursor blink uses `::after` only |
| `aria-allowed-role` | Evidence, Systems | Removed invalid `role="row"` on `<button>` rows |
| `nested-interactive` | Memory | Removed `role="img"` from SVG graph; label on `.graph-wrap` |

### Supporting changes

- `useDocumentTitle` sets per-route document titles
- `AppShell` exposes skip link and `main#main-content` with `tabIndex={-1}`

## Manual keyboard checklist

Checked against preview build (`npm run build && npm run preview`).

| Workflow | Steps | Result |
| --- | --- | --- |
| Skip to content | Tab from load → Enter on skip link | PASS — focus lands on `main` |
| Primary navigation | Tab through sidebar links, activate with Enter | PASS — route changes, focus order logical |
| Mobile nav drawer | Resize ≤834px → open drawer → Escape | PASS — drawer closes, focus not trapped |
| Mission list | Tab to mission card link → Enter | PASS — detail route opens |
| Evidence table rows | Tab through row buttons → Enter | PASS — detail route opens |
| Systems inspector | Tab to component row → Enter → Escape | PASS — drawer opens and closes |
| Timeline event drawer | Tab to event card → Enter → Escape | PASS — drawer opens and closes |
| Command bar / search | Tab to command input, type, arrow keys | PASS — suggestions keyboard reachable |
| Emergency STOP dialog | Activate STOP control → Tab trap → Escape | PASS — dialog focus contained |
| Trust dialog | Open Trust State → Tab through actions → Escape | PASS — modal dismisses |

## Gates (WP-F)

| Gate | Result |
| --- | --- |
| lint | PASS |
| typecheck | PASS |
| unit test | PASS (91/91) |
| build | PASS |
| e2e accessibility | PASS (11/11) |

## Excluded (boundary)

`/discovery` and V3-owned surfaces are out of scope.
