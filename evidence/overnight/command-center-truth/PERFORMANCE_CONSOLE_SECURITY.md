# Performance, Console, and Security Result

Work package **WP-H** — Command Center truth lane.

## Production build

```bash
cd ui/command-center
npm run build
```

| Asset | Raw | Gzip |
| --- | ---: | ---: |
| `index.html` | 2.07 kB | 1.13 kB |
| `index-B5RNTn1a.css` | 85.88 kB | 17.12 kB |
| `index-DUJ2x52P.js` | 522.81 kB | 147.52 kB |
| Font payloads (woff2) | ~354 kB total | — |

Build completed in ~4.0s. Vite warns the JS chunk exceeds 500 kB; acceptable for this candidate lane (no code-splitting scope change in WP-H).

## Console check

Script: `ui/command-center/scripts/console-check.mjs`

- Base URL: `http://localhost:4523`
- Routes exercised: 12 non-Discovery surfaces including detail and not-found
- Captures `console.error` and `pageerror`

```text
node scripts/console-check.mjs
PASS — 12 routes, 0 errors
```

## Frontend security scan

Script: `ui/command-center/scripts/frontend-security-scan.mjs`

| Check | Result |
| --- | --- |
| `dangerouslySetInnerHTML` | 0 occurrences |
| `target="_blank"` without `noopener` | 0 occurrences |
| Hardcoded secret patterns | 0 matches |

```text
node scripts/frontend-security-scan.mjs
PASS
```

## Gates (WP-H)

| Gate | Result |
| --- | --- |
| build | PASS |
| console check | PASS |
| security scan | PASS |
