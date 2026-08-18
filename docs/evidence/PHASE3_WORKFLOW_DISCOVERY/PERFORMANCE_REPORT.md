# PHASE 3 PERFORMANCE REPORT

Measured by `scripts/phase3_workflow_discovery_probe.py` on the isolated
candidate worktree. Inputs are semantic observations only. The configured
near-variant comparison budget is 256 per endpoint bucket; exact fingerprint
groups are collapsed first.

| Observations | Sessions | Patterns | Repeated occurrences | Wall seconds | CPU seconds | Peak traced memory |
|---:|---:|---:|---:|---:|---:|---:|
| 100 | 25 | 1 | 25 | 0.029695 | 0.031250 | 104,084 bytes |
| 1,000 | 250 | 1 | 250 | 0.316414 | 0.312500 | 851,212 bytes |
| 10,000 | 2,500 | 1 | 2,500 | 3.032676 | 3.031250 | 8,030,078 bytes |

These are point-in-time bounded-run measurements, not a capacity guarantee.
The final probe output is `var/phase3/probe-final.txt` (generated, not
committed). It found a controlled repeated pattern with three occurrences and
did not access raw Screenpipe data or execute a computer action.
