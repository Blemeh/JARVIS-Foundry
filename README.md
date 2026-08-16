# JARVIS Foundry

**A persistent autonomous AI institution — an always-on digital organization that plans, builds, verifies, repairs, and improves itself on your own machine.**

JARVIS Foundry is the project workspace and runtime for JARVIS, an autonomous personal AI organization built around [Hermes Agent](https://hermes-agent.nousresearch.com), Obsidian, and specialist AI workers. JARVIS runs continuously — even while the owner is away — with institutional memory, durable evidence, independent verification, and the ability to keep selecting and executing the next valuable action without waiting for prompts or routine permission.

Part of the Hoowen platform family.

> The goal is the science-fiction JARVIS experience: an always-active digital company, research laboratory, builder, operator, and self-improving second brain running on a Windows computer.

## Status

```text
PHASE:        V4 CONTINUOUS AUTONOMY — ACTIVE
V5:           MULTI-SYSTEM INTELLIGENCE — ACCEPTED (final gate PASS)
V6:           CONTINUOUS SELF-EVOLUTION — ACTIVATED, in isolated construction
TRUST STATE:  TRUSTED_AUTONOMOUS (project-local staging runtime only)
PERMISSION:   STANDING AUTONOMY — owner manages by exception (veto + STOP)
SUITE:        710 tests passing (EXIT=0) at the V5 acceptance close
```

This is an early-stage, research-grade system. The runtime operates in **project-local staging only** — nothing is installed system-wide and no external service is connected. The owner started the build on 2026-08-11 with a single `START BUILD` instruction; since then the program has advanced through work packages and version gates, with every phase independently verified. Current project truth always lives in [`PROJECT_STATE.md`](PROJECT_STATE.md).

## How it works

- **Append-only event ledger (source of truth).** Every material action is an event in a durable, monotonically sequenced SQLite ledger. Projections are derived views, never trusted as truth, and are periodically reconciled against the ledger.
- **Deterministic Supervisor (source of control).** An independent supervisor runs outside the LLM stack: heartbeats, leases, and a sentinel **STOP** that works even with no LLM, UI, provider, or network — and freezes before any rollback.
- **Specialist workers with brokered capabilities.** Work is dispatched to role-based workers (builder, verifier, reaper, orchestrator, …) through a capability broker. Workers receive scoped capabilities, not raw credentials. Model roles stay abstract; providers are configurable.
- **Evidence gates, not claims.** Every phase ends in an acceptance gate: tests, fault injection (killed daemons, killed gateways, network and provider outages), adversarial independent verification, and reconciliation. Builders never certify their own consequential work. `UNKNOWN`, `NOT_AVAILABLE`, and `NOT_TESTED` are first-class results.
- **Obsidian as long-term memory.** The vault is indexed, retrieved, and written back through governed writes with snapshots, provenance, conflict handling, and integrated rollback — the institutional brain of the organization.
- **Continuous self-improvement.** Candidate releases, canaries, shadow runs, verification, and automatic rollback. The system improves its own agents, prompts, tools, and workflows after evidence, within owner constraints.
- **JARVIS Command Center (in progress).** A local web console that projects live state — missions, workers, decisions, evidence, costs, health — straight from the ledger. It is explicitly *not* a second source of truth.

## Repository layout

```text
JARVIS-Foundry/
├── AGENTS.md / HERMES.md        Binding instructions for coding agents; Hermes boundary
├── DESIGN.md / IDEA.md / PRODUCT.md
├── PROJECT_STATE.md             Current project truth
├── src/jarvis_foundry/          The autonomy kernel (Python)
├── tests/foundry/               Unit, fault-injection, and acceptance tests
├── config/                      Runtime configuration + templates
├── schemas/                     Machine-readable contracts
├── scripts/                     Deterministic tooling
└── docs/
    ├── architecture/            Canonical architecture (00_READ_FIRST → 05_POST_V1_ROADMAP)
    ├── governance/              Autonomy mandate, constitution, action policy, secrets policy
    ├── planning/                Work packages, checklists, risk register, decision log
    ├── operations/              Emergency STOP, backup/restore, incident response
    ├── evidence/                Gate reports, fault injections, verifier verdicts, directives
    └── handoffs/                Continuation checkpoints
```

## Getting started

Requires **Python 3.13+ on Windows** (the kernel is built and proven on Windows — console-control handling and Task Scheduler integration are part of it).

```bash
git clone https://github.com/Blemeh/JARVIS-Foundry.git
cd JARVIS-Foundry
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
jarvis --help        # CLI entry point
pytest               # full test suite
```

This is a personal autonomy system in staging. Running it wires up a local daemon, ledger, and scheduled supervisor on the host. Read [`docs/governance/AUTONOMY_MANDATE.md`](docs/governance/AUTONOMY_MANDATE.md) and [`docs/operations/EMERGENCY_STOP_AND_RECOVERY.md`](docs/operations/EMERGENCY_STOP_AND_RECOVERY.md) before running anything.

## Design principles

- **Acts by default.** One standing `START BUILD`; the owner manages by exception through goals, constraints, vetoes, and STOP — not approval queues.
- **Evidence determines readiness.** Nothing promotes without both engineering and operational gate evidence.
- **Durable truth before convenience.** Real measurements, honest unknowns, provenance for every decision.
- **Safety is deterministic.** The Supervisor and STOP are independent of the LLM, the UI, the provider, and the network.

## Documentation

Start with [`docs/architecture/00_READ_FIRST.md`](docs/architecture/00_READ_FIRST.md), then the north star in [`docs/architecture/01_NORTH_STAR.md`](docs/architecture/01_NORTH_STAR.md) and the architecture contract in [`docs/architecture/02_ARCHITECTURE_CONTRACT.md`](docs/architecture/02_ARCHITECTURE_CONTRACT.md). Governance lives in [`docs/governance/`](docs/governance/); the full evidence trail — gates, fault injections, verifier verdicts, owner directives — lives in [`docs/evidence/`](docs/evidence/).

## Version history

- **V1 — Autonomy kernel:** durable event store, projections, leases, sentinel STOP, local CLI, independent Supervisor. Accepted under a seven-day reliability waiver; observation window still open.
- **V2 — Knowledge brain:** Obsidian vault indexing, retrieval, ingestion, consolidation, integrated rollback. Engineering + operational gates PASS.
- **V3 — Discovery:** candidate pipeline from monitored sources, mission-execution wiring. Engineering + operational gates PASS.
- **V4 — Continuous autonomy:** reliability slices (model routing, verifier truth, lease monotonicity, exactly-once resume, support semantics, monitors, scoring) plus the Command Center surface. Active.
- **V5 — Multi-system intelligence:** systems, broker, capabilities, connectors, correlation, effects, activation loop. Accepted — final gate PASS.
- **V6 — Continuous self-evolution:** candidate releases, canaries, shadow runs, promotion, restore. Activated; in isolated construction.

## License

No license has been selected for this repository yet. Until one is added, standard copyright applies and all rights are reserved by the owner.
