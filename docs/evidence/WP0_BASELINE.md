# WP0 Reality And Baseline Evidence

Verdict: PASS
Started: 2026-08-11T17:33:05+02:00
Completed: 2026-08-11T17:51:28+02:00
Revalidated: 2026-08-11T18:07:27+02:00
Mutation scope: project-local state and isolated clone only

## Observed Host

| Surface | Observed fact |
|---|---|
| OS | Windows 11 Pro 10.0.26200, x64 |
| CPU | Intel Core i5-10400, 12 logical processors |
| Memory | 31.9 GB total; 15.2 GB free at audit |
| Disk | C: 711.3 GB free; D: 75.9 GB free |
| Time zone | Central Europe Standard Time / Europe-Budapest contract |
| Python | 3.13.5 default; 3.14.2 also installed |
| Node/npm | Node 24.19.0; npm 11.17.0 |
| Git | 2.50.1.windows.1 |

## Hermes

- Home: `C:\Users\b\AppData\Local\hermes`.
- Live checkout: `C:\Users\b\AppData\Local\hermes\hermes-agent`.
- Live branch and commit: `main`, `244d296646909aca1dd16c9759491da0ef4cd163`.
- Live checkout was clean at audit.
- Gateway task: `Hermes_Gateway`, logon trigger, hidden VBS launcher.
- Gateway command: live virtual-environment Python running `-m hermes_cli.main gateway run`.
- `state.db`, `kanban.db`, `projects.db`, and `verification_evidence.db` all returned `quick_check=ok` read-only.
- Development checkout: `.h`, same commit, clean `main`, `core.longpaths=true`.
- Upstream fetch: Nous Research GitHub. Push URL: `DISABLED`.
- A longer `.worktrees/hermes-foundry` clone failed checkout on a Windows long
  filename. It is ignored and preserved as failure evidence; `.h` is canonical.

## Hermes Baseline Verification

Using an isolated `HERMES_HOME` and a project-local pytest overlay:

| Suite | Result |
|---|---|
| `tests/test_estop.py` | 24 passed |
| state read-only, WAL fallback, gateway health export | 25 passed, 8 skipped |
| Live Hermes databases | 4 of 4 quick checks passed |

The first combined state-suite attempt exceeded 120 seconds and produced no
valid verdict. It is not counted as evidence. Missing async/runtime packages
in initial test environments were corrected without changing live Hermes.

## Obsidian And Providers

- Active Obsidian vault: `C:\Users\b\Blendi`.
- V1 write root is planned as `C:\Users\b\Blendi\JARVIS\V1`; it was not created in WP0.
- Hermes and Obsidian desktop processes were active during audit.
- Codex CLI and Google Cloud SDK are installed.
- OpenCode desktop data is present, but no `opencode` shell command was found.
- Only environment-variable names were inspected; values and credentials were not exposed.
- Provider authentication, model availability, and billable quota remain to be probed through credential-safe adapters in WP4.

## Persistent START BUILD Mandate

- `config/start_record.yaml` records `status: ACTIVE`, received at
  `2026-08-11T17:33:05+02:00`, with automatic continuation enabled and all
  routine approval fields false.
- `var/staging-foundation/foundry.db` revalidated at schema 2 with
  `quick_check=ok`, `start_active=1`, and received timestamp
  `2026-08-11T15:33:05Z`.
- The durable mandate advances work after verified PASS. It does not turn
  technical readiness into an owner-approval ladder.

## Development Checkout Selection

The clean `.h` checkout is only the isolated Hermes development checkout used
for adapter and candidate work. It is not automatically the V1 seven-day
proving project, resource, or mission. Those remain `NOT_SELECTED` until the
system applies `04_V1_ACCEPTANCE_GATE.md` and records the scoring evidence.

LeadGen, OperatorOS, Queuetwin, and this uncommitted bootstrap repository were
observed but were neither mutated nor selected as the acceptance resource.

## Gate Rationale

Observed paths replace planning assumptions, the clean source is preserved,
the isolated clone is reproducible, selected baseline tests pass, and no live
Hermes, Obsidian, provider, startup, or unrelated repository mutation occurred.

WP0 PASS proves the baseline and isolated development boundary. It does not
prove WP1+, select the seven-day mission, or activate any later capability.
