# Documentation map

About 450 files live here. Most are *evidence or history*; only a few are the
current truth. Start with the first block and ignore the rest until you need it.

## Read these (current truth)

| File | What it is |
| --- | --- |
| [`STATUS.md`](STATUS.md) | Short, reconciled "where are we" — which docs to believe, what was verified, open decisions. |
| [`CURRENT_STATE.md`](CURRENT_STATE.md) | The authoritative long-form state record (pin enforced by `scripts/doc_staleness_check.py`). Newest section is on top. |
| [`../AGENTS.md`](../AGENTS.md) | Control tower for every coding agent: authority order, boundaries, truth/Git discipline. |
| [`architecture/00_READ_FIRST.md`](architecture/00_READ_FIRST.md) | Architecture entry point (north star, contract, kernel spec, acceptance gate). |
| [`governance/`](governance/) | Constitution, autonomy mandate, action policy, secrets policy. |
| [`agent/README.md`](agent/README.md) | Which workflow to use (build / convergence / promotion / proving). |
| [`operations/`](operations/) | Emergency STOP and recovery, backup/restore, incident response. |

## Plans and decisions

| Path | Contents | Status |
| --- | --- | --- |
| [`roadmap/JARVIS_1_0_EXECUTION_STATE.yaml`](roadmap/JARVIS_1_0_EXECUTION_STATE.yaml) | Pointer to the only authorized next gate. | **Stale** (last reconciled 2026-09-03); see `STATUS.md`. |
| [`handoffs/`](handoffs/) | The frozen master roadmap to 1.0, version handoffs and checkpoints. | Roadmap is binding; checkpoints are history. |
| [`planning/`](planning/) | Work packages, decision log, integration maps (mostly August). | History; `DECISION_LOG.md` is still useful context. |
| [`superpowers/`](superpowers/) | Spec and plan for the owner-experience Command Center redesign (2026-10-08). | Implemented. |
| [`v3/`](v3/) | V3 discovery/building spec. | Historical spec, implemented. |

## Evidence (large, append-only — do not read linearly)

| Path | Contents |
| --- | --- |
| [`evidence/`](evidence/) | Gate reports, proofs, integration runs, and the test baselines (`test-baseline-*.json`). |
| [`convergence/`](convergence/) | Convergence reports and JUnit/JSON suite results from the August convergence work. |
| [`runtime/`](runtime/) | Structural-closure notes. |

## Checking the repository

```powershell
python scripts/verify.py            # tests (python), command center (ui), docs pin
python scripts/doctor.py            # read-only machine/project drift report
python scripts/prune_releases.py    # dry run: old installed releases that could be removed
```

If two documents disagree, trust in this order: the latest owner instruction, then
`CURRENT_STATE.md` (re-verify drift-prone live facts), then `STATUS.md`, then
everything else as history.
