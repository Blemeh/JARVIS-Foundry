# JARVIS Foundry

JARVIS Foundry is the project workspace for a persistent autonomous digital
organization built around Hermes, Obsidian, specialist AI workers, durable
state, independent verification, continuous building, and self-evolution.

## Current State

```text
PROJECT PHASE: V1 AUTONOMY KERNEL
IMPLEMENTATION: STARTED
RUNTIME: PROJECT-LOCAL STAGING
ACTIVE VERSION: V1 / WP1
TRUST: LAB
PERMISSION MODEL: STANDING_AUTONOMY
OWNER CONTROL MODEL: VETO_AND_STOP
```

The owner issued `START BUILD` on 2026-08-11. The repository now contains the
durable event store, projections, resource registration, action policy,
leases, sentinel STOP, local CLI, and an independent Supervisor. The runtime
is being proven in project-local staging before automatic background install.

## Start Here

Every human or agent reads these files in order:

1. `PROJECT_STATE.md`
2. `IDEA.md`
3. `docs/governance/AUTONOMY_MANDATE.md`
4. `AGENTS.md`
5. `docs/architecture/00_READ_FIRST.md`
6. `docs/architecture/02_ARCHITECTURE_CONTRACT.md`
7. `docs/architecture/03_V1_AUTONOMY_KERNEL_SPEC.md`
8. `docs/architecture/04_V1_ACCEPTANCE_GATE.md`
9. `docs/planning/PREBUILD_CHECKLIST.md`
10. `docs/handoffs/LOW_COST_CONTINUATION.md`

The full destination is in `docs/architecture/01_NORTH_STAR.md`. The staged
roadmap describes automatic capability growth, not permission checkpoints.

## Repository Map

```text
JARVIS-Foundry/
|-- AGENTS.md                 Binding instructions for coding agents
|-- HERMES.md                 Hermes integration boundary
|-- PROJECT_STATE.md          Current project truth
|-- docs/
|   |-- architecture/        Canonical architecture and staged capabilities
|   |-- governance/          Standing mandate and integrity rules
|   |-- planning/            Evidence gates, risks, and build sequence
|   `-- operations/          STOP, recovery, incident, and restore contracts
|-- config/                  Runtime configuration and templates
|-- schemas/                 Machine-readable data contracts
|-- src/jarvis_foundry/      Executable autonomy kernel
|-- scripts/                 Reserved for deterministic tooling
`-- tests/                   Unit, fault, and acceptance tests
```

## Build Launch

The owner has given the one launch instruction: `START BUILD`. It is not
repeated for every phase. The build program now:

1. audits the real environment;
2. resolves Hermes, Obsidian, providers, projects, and available resources;
3. builds WP1 through WP8 in order;
4. verifies each gate independently;
5. repairs failures and retries;
6. activates the accepted kernel;
7. continues automatically through the capability roadmap;
8. reports progress without asking permission;
9. stops or redirects immediately when the owner vetoes.

## Design Principle

JARVIS acts by default. Evidence determines readiness. The owner manages by
exception through goals, constraints, vetoes, and STOP, not through routine
approval requests.
