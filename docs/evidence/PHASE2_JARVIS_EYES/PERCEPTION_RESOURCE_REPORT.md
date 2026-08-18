# PERCEPTION RESOURCE REPORT — JARVIS EYES PHASE 2

## Method

Disposable home; deterministic fake source (500 records per cycle);
20 cycles of the full driver (health -> register -> read -> privacy ->
normalize -> dedup -> aggregate -> significance -> ledger append ->
cursor advance -> quota). Screenpipe sidecar overhead is NOT included
(that was measured in Phase 1); this is the ADDITIONAL JARVIS Eyes cost.

## Numbers (raw: `var/phase2-eyes/resource_report.json`)

| Metric | Value |
|---|---|
| cycles | 20 |
| source records (total) | 10,000 |
| semantic observations (ledger) | 4 |
| source-to-semantic reduction ratio | 2500:1 |
| avg cycle wall time | 0.0366 s |
| ledger events delta | 29 |
| Foundry DB growth | 675,840 bytes |
| observation payload bytes (all observed) | 3,509 |
| probe process CPU user | 0.188 s |
| probe process CPU system | 0.125 s |
| probe process RSS | ~62.9 MB |

The 2500:1 reduction is dominated by the cursor: cycles 2-20 read zero
new records and append only health/quota/cursor events. Eyes does not
materially degrade Supervisor/runtime responsiveness (sub-40ms per cycle
at lab scale, 29 ledger events over 20 cycles, ~676 KB DB growth).

Screenpipe raw storage policy: raw history stays in the external sidecar
with its own retention (`--retention-days`, media compaction 2.5x
observed in Phase 1); Foundry never stores raw history.
