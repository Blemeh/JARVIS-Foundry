# R11 micro evolution target

This is the real, bounded code target used by the R11 proving harness.  The
baseline contains genuine behavior defects; candidates are real code changes
evaluated against the real measured baseline in isolated git clones.

Files:

- `config/values.py` - configuration surface (R1 risk class).
- `core.py` - core parsing/scoring logic.
- `workflow.py` - pipeline orchestration (R2 risk class).
- `tests/test_micro_target.py` - real pytest behavior tests.
- `probe.py` - deterministic behavior probe used for before/after measurement.
