# Phase 4 Test Report

Candidate branch: `candidate/procedural-learning-phase4-remediation`
Candidate HEAD: `992317e8c1bac8ef556f05ac82a884bff3a91c36`
Integration base: `47adab97b64ab992280d3a329fd0ca3d03c2d45f`

Commands and results:

```text
pytest tests/foundry/test_procedural_learning.py -q
23 passed

pytest tests/foundry/test_perception.py -q
23 passed

pytest tests/foundry/test_workflow_discovery.py -q
36 passed

pytest Tier-C, control/effects/connectors, shadow/evolution slices
all passed

pytest --collect-only
1225 tests collected

pytest -q
EXIT 0

python -m compileall -q src scripts
PASS

scripts/doc_staleness_check.py
DOCUMENTATION STALENESS CHECK: PASS
```

The full suite uses the canonical repository virtual environment with the
isolated candidate `src` directory first on `PYTHONPATH`. No canonical live DB,
Supervisor task, STOP sentinel, or unrelated process was changed.

The remediation adds an explicit zero-effect shadow bridge proof, an invalid
Tier-C readiness refusal, and complete JARVIS artifact-byte binding (including
generated `workflow.py` files omitted by the native OpenAdapt manifest). The
full suite was run with the project `.venv` and an absolute integration `src`
path so scheduled child processes resolve the same editable checkout; it
completed with `EXIT 0` and 1,225 collected tests. An earlier system-Python
attempt is recorded as a harness-only installer failure and is not acceptance
evidence.

Fresh local evidence used the harmless synthetic browser app at
`127.0.0.1:8787`, bundle hash
`0d9a3e227315314fed4980739cf9d95cb803f636b3da1af62980c113180686e0`, and
OpenAdapt `1.12.1` / Flow `1.31.0`: fresh replay and resume were
`COMPLETED_VERIFIED` with `model_calls=0`; duplicate replay had
`report_path=null` and no second provider row; external STOP returned
`NOT_READY` with exit `2`; the runner was stopped and port `8787` was free.
