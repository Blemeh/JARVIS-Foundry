# PHASE 3 FULL TEST REPORT

## Command

```text
$env:PYTHONPATH=(Resolve-Path src).Path
& C:\Users\b\Documents\ChatGPT\Autonomous AI powered by Hoowen\JARVIS-Foundry\.venv\Scripts\python.exe -m pytest -q
```

The absolute candidate `PYTHONPATH` is intentional: Windows child-process
tests embed `sys.executable` in temporary scheduled tasks, and the runtime
must import the exact candidate checkout rather than the canonical editable
install. The live JARVIS task itself uses the same canonical venv family.

## Result

- Collection: **1,202 tests** (baseline 1,166 plus 36 Phase-3 tests).
- Exit code: **0**.
- Progress: **100%**, no failures or errors.
- Post-promotion canonical `main` rerun: **1,202 collected, EXIT 0** in the
  clean scheduler window (`var/phase3/full-suite-post-promotion-clean2.txt`).
- Focused Phase 3: **36 passed**.
- Affected Phase-2 perception slice: **23 passed**.
- Compileall: **PASS**.
- Documentation staleness check: **PASS**.

Raw runner output is retained as generated `var/phase3/full-suite-run-final.txt`
and `var/phase3/full-suite-post-promotion-clean2.txt`; these are intentionally
not committed. An earlier post-promotion attempt overlapped an unrelated
convergence pytest and is classified as environmental interference, not a
product failure.
