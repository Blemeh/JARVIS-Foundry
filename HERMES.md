# Hermes Repository Boundary

Hermes is JARVIS's agent execution substrate. It is not JARVIS's durable
supervisor, autonomous action control plane, emergency stop, or final source
of project state.

## Current State

- Live Hermes home: `C:\Users\b\AppData\Local\hermes`.
- Live source commit: `244d296646909aca1dd16c9759491da0ef4cd163`.
- Canonical isolated development checkout:
  `C:\Users\b\Documents\ChatGPT\Autonomous AI powered by Hoowen\JARVIS-Foundry\.h`.
- Durable shorthand: `/.h = isolated Hermes development checkout`.
- `.h` is not automatically the V1 seven-day proving project, resource, or
  mission. Acceptance selection remains governed by
  `docs/architecture/04_V1_ACCEPTANCE_GATE.md`.
- The short name is intentional. Its longest current file path is 238
  characters; `.hermes-dev` would raise that to 247 before build tools add
  temporary suffixes. Keep `.h` unless a later migration proves all path and
  reference behavior without churn.
- Nous upstream is fetch-only; its configured push URL is `DISABLED`.
- The live checkout and running gateway were not modified during WP0.
- JARVIS now adapts and evolves Hermes only through isolated candidate work and
  evidence gates.

## Required Integration Shape

During V1 integration, the implementation must preserve:

```text
Independent JARVIS Supervisor
        |
        +-- durable state and event ledger
        +-- scheduler and wake policy
        +-- autonomous action and capability decisions
        +-- STOP, watchdog, and recovery
        `-- Hermes adapter
                 |
                 `-- bounded model-driven workers
```

Hermes may be unavailable, killed, upgraded, or rolled back without disabling
the Supervisor's ability to freeze work and preserve state.

## Before Editing Hermes

The autonomous build program establishes and records:

- exact checkout path and commit;
- upstream and push-remote policy;
- dirty-worktree state;
- repository-local instructions;
- Python and Node runtime versions;
- existing test commands and their baseline results;
- gateway start, stop, and health behavior;
- `HERMES_HOME` resolution;
- safe isolated worktree path;
- adapter boundary that avoids normal-session privilege leakage.

The first Hermes-related change occurs in an isolated worktree selected after
real-state inspection. The live installation is never the initial development
target, and candidates cannot overwrite the only recoverable live copy.
