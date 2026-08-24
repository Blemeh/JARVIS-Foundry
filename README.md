# JARVIS Foundry

**A durable autonomy kernel and independent Supervisor for a local autonomous AI organization.**

JARVIS Foundry is a Windows-first research platform for planning, executing, verifying, and recovering work with durable evidence. It combines a deterministic control plane, scoped worker capabilities, long-term memory, and a local command center.

## What it demonstrates

- **Durable truth** - an append-only SQLite event ledger with event-derived projections and reconciliation.
- **Independent control** - Supervisor heartbeats, leases, recovery, and an emergency STOP that do not depend on an LLM, UI, provider, or network.
- **Brokered workers** - role-based workers receive scoped capabilities rather than raw credentials; consequential work is independently verified.
- **Evidence-driven delivery** - fault injection, acceptance gates, candidate releases, canaries, rollback, and explicit `UNKNOWN` / `NOT_TESTED` outcomes.
- **Long-term memory and visibility** - governed Obsidian integration plus a command center that projects ledger state without becoming a second source of truth.

## Architecture

```text
Mission
  -> Supervisor + policy
  -> capability broker
  -> scoped worker
  -> ledger + evidence
  -> independent verifier
  -> projections / command center
```

## Tech stack

`Python 3.13+` · `SQLite` · `PyYAML` · `pytest` · `Windows Task Scheduler` · `Obsidian` · local web UI

## Quick start

```powershell
git clone https://github.com/Blemeh/JARVIS-Foundry.git
cd JARVIS-Foundry
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
jarvis --help
pytest
```

Running the daemon changes local host state. Read [`docs/governance/AUTONOMY_MANDATE.md`](docs/governance/AUTONOMY_MANDATE.md) and [`docs/operations/EMERGENCY_STOP_AND_RECOVERY.md`](docs/operations/EMERGENCY_STOP_AND_RECOVERY.md) before enabling it.

## Repository map

- `src/jarvis_foundry/` - autonomy kernel, Supervisor, ledger, workers, and CLI
- `tests/` - unit, fault-injection, and acceptance coverage
- `schemas/` - machine-readable contracts
- `scripts/` - deterministic tooling and checks
- `ui/` - local command-center surface
- `docs/` - architecture, governance, operations, and evidence

## Current boundary

JARVIS is active local research and development. The authoritative status, evidence, schema, runtime, and deployment boundaries are maintained in [`docs/CURRENT_STATE.md`](docs/CURRENT_STATE.md). Experimental or disabled-by-default capabilities are documented there; this summary intentionally avoids turning source or test evidence into a production claim.

## Start here

- [`docs/architecture/00_READ_FIRST.md`](docs/architecture/00_READ_FIRST.md) - architecture entry point
- [`docs/CURRENT_STATE.md`](docs/CURRENT_STATE.md) - current status and evidence
- [`docs/governance/`](docs/governance/) - autonomy and capability rules
- [`docs/evidence/`](docs/evidence/) - gate reports and verification records

