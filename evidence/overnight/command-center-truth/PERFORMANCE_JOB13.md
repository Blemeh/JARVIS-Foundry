# Job 13 — Performance productization

## Before (eager route imports @ cca7003)

| Asset | Size | Gzip |
|-------|------|------|
| `index-*.js` (main) | 530.90 kB | 149.69 kB |
| `index-*.css` | 87.58 kB | 17.40 kB |

Vite warned: chunk > 500 kB.

## After (route-level `React.lazy` code splitting)

| Asset | Size | Gzip |
|-------|------|------|
| `index-*.js` (shell + shared) | 405.88 kB | 120.99 kB |
| Largest route chunk (`MissionDetail`) | 15.28 kB | 4.12 kB |
| `Overview` route chunk | 9.70 kB | 3.48 kB |

**Main chunk delta:** −125.02 kB (−23.5%) minified, −28.7 kB gzip.

## Product impact

- Initial parse/execute cost drops because route modules load on demand.
- Repeat navigation pays a small lazy-fetch cost (acceptable for an operator console).
- No Lighthouse vanity chase; change justified by measured monolith split.

## Verdict

`CHANGE_REQUIRED` — route lazy loading applied.
