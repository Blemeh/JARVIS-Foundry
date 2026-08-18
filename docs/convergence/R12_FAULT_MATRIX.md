# R12 Canonical Fault Campaign

The historical remaster campaign remains evidence-only. This candidate runs a
bounded integrated campaign against a fresh canonical `FoundryDatabase` for
each of the 47 required fault rows. Each row records a durable incident,
truthful before/after state, bounded blast radius, unrelated-work
continuation, reconciliation, suspension/compensation, recovery, and a
truthful briefing. The executable gate is
[`test_r12_fault_restore_gate.py`](../../tests/convergence/test_r12_fault_restore_gate.py).

The machine-readable result contract and per-fault outcome record are frozen
in [`R12_FAULT_CAMPAIGN.json`](R12_FAULT_CAMPAIGN.json). The test writes only
under pytest's temporary directory; the checked-in JSON is the durable
campaign evidence summary and does not claim live provider effects.

## Result Semantics

| Status | Meaning |
|---|---|
| `PASS` | The canonical durable semantic fault/recovery boundary executed and reconciled in isolation. |
| `NOT_TESTED` | A physical host or unavailable interface was not induced. The safe boundary was probed and no effect was attempted. |
| `BLOCKED` | An external provider was unavailable. The system recorded an honest block and no provider effect was attempted. |

The campaign contains 37 direct semantic probes and 10 explicit physical or
external boundary probes. `accepted=true` in the executable result means the
probe was honest, bounded, incident-recorded, reconciled, and restart-readable;
it does not turn `NOT_TESTED` or `BLOCKED` into real provider proof.

## Obligations And Durable Probes

| Fault family | Rows covered | Durable campaign probe | Boundary truth |
|---|---|---|---|
| Hermes, Supervisor, worker lifecycle | crash, duplicate, restart, malformed result, termination refusal | `agent_run.*` lifecycle, idempotency, reopen/reconcile | hang and host reboot are `NOT_TESTED` |
| Network and provider responses | outage, timeout, rate, partial, late | `system.action_*` terminal monotonicity and freeze | provider outage is `BLOCKED`; no provider effect |
| UI, browser, voice, credentials | UI/browser/voice failure, expiry, revocation | capability expiry/revocation and durable boundary record | unavailable interfaces are `NOT_TESTED` |
| Events and storage | duplicate/lost/late/order/corrupt, stale/conflict, migration, projection, WAL | atomic append, integrity rejection, rebuild, verified backup, reopen | live DB lock is `NOT_TESTED` |
| Cost and evidence pressure | cost, quota, disk, evidence/log growth | frozen effect and bounded failure | physical pressure is `NOT_TESTED` |
| Retrieved content and authority | malicious content, prompt injection, forged directive | inert untrusted ledger data and authenticated owner control | no retrieved text becomes authority |
| STOP and veto | queued, in-flight, veto during work | authenticated durable STOP/RESUME, capability denial, veto lifecycle | STOP remains the canonical control path |
| Evolution and release | bad candidate/promotion, freeze, rollback, restore | validation refusal, durable frozen state, restore-point rehearsal/restore | Supervisor owns restore authority |

The JSON artifact contains all 47 named rows, their `direct`/`boundary`
classification, status, probe description, and the shared outcome contract.
For every row the executable result additionally proves:

- incident event present after a fresh database reopen;
- final `reconcile(...).consistent == true` and `PRAGMA quick_check == "ok"`;
- unrelated ledger work continued in the isolated store;
- no path outside that row's temporary database was touched;
- `truthful_briefing` distinguishes semantic proof from external-effect proof.

Every row is run in its own temporary store. The campaign has no authority to
touch the canonical checkout, remaster checkout, Eyes worktree, provider
accounts, browser sessions, voice devices, or host power state.
