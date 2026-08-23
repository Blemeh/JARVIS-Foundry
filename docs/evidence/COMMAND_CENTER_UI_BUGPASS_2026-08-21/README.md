# Command Center UI bug-pass evidence

CLASSIFICATION: CANONICAL_EVIDENCE

The read-only Playwright spec and five screenshots record the 2026-08-21 live
Command Center inspection. The checks covered the overview, missions,
navigation, responsive widths from 390 to 1120 pixels, and the STOP dialog
presentation without activation.

The spec is intentionally stored outside `ui/command-center/e2e` because it
asserts the live runtime (`DATA SOURCE: LIVE`), while the normal Playwright
suite runs against the simulated preview. Reproduce it only against an
explicitly live Command Center server.
