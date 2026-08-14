# build_r2_evidence.py is superseded

Status: RETIRED. Do not run.

`build_r2_evidence.py` was the original R2 evidence generator. It hardcodes
stale measurements that later verifier rounds disproved:

- `created_event_types = 113` (canonical frozen census: 112, method-dependent)
- `exact_handled = 95` (canonical AST census: 100 distinct)
- `rebuild_projections` at `database.py:130-141` (actual 104-112)
- `Supervisor.start/heartbeat` at `service.py:34-99` (actual 34-65 and 67-90)
- legacy tuple-form line ranges (the current contract requires classified
  `{start, end, kind}` node/line ranges)

The authoritative evidence files are maintained by the frozen deterministic
tools in this directory and the applied repair scripts:

- `audit_r2_spans.py` (AST-exact + prose + stale-token gate, currently
  `checked=161 mismatches=0`)
- `classify_r2_ranges.py` (161 = 81 node + 80 line)
- `measure_test_references.py` (88 files, word_boundary=311, substring=344)
- `measure_event_types.py` (created=112, handled=100, prefixes=3, unhandled=41)
- `repair_r2_evidence_round*.py` (verifier-driven repairs R2-F031..F035)

Running `build_r2_evidence.py` would regenerate stale evidence and is
explicitly forbidden by the frozen R2 work package stop conditions.
