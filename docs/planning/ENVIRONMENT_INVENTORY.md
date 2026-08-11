# Environment Inventory

Status: WP0 VERIFIED
Verified: 2026-08-11T17:51:28+02:00
Detailed evidence: `docs/evidence/WP0_BASELINE.md`

No secrets, tokens, cookies, raw credentials, or environment values are
recorded here.

## Host

| Item | Observed value |
|---|---|
| Windows | Windows 11 Pro 10.0.26200 |
| Architecture | x64 |
| CPU | Intel Core i5-10400, 12 logical processors |
| Memory | 31.9 GB total; 15.2 GB free at audit |
| Disk | C: 711.3 GB free; D: 75.9 GB free |
| Time zone | Central Europe Standard Time; product contract Europe/Budapest |
| Startup | Windows Task Scheduler is available; Hermes uses a logon task |

## Toolchain

| Tool | Path or surface | Version |
|---|---|---|
| Git | `C:\Program Files\Git\cmd\git.exe` | 2.50.1.windows.1 |
| Python | `C:\Python313\python.exe` | 3.13.5 |
| Python alternate | Windows Python launcher | 3.14.2 |
| Node | `C:\Program Files\nodejs\node.exe` | 24.19.0 |
| npm | system install | 11.17.0 |
| pnpm | user npm bin | installed; version not captured |
| SQLite | Python standard library | WAL and backup APIs verified |

## Hermes

| Item | Observed value |
|---|---|
| Home | `C:\Users\b\AppData\Local\hermes` |
| Live checkout | `C:\Users\b\AppData\Local\hermes\hermes-agent` |
| Commit | `244d296646909aca1dd16c9759491da0ef4cd163` |
| Branch / state | `main`, clean at audit |
| Development checkout | `.h`, clean `main`, push disabled; not the V1 proving resource |
| Repository rules | root `AGENTS.md`; desktop-specific `apps/desktop/AGENTS.md` |
| Baseline | 49 passed, 8 skipped across selected STOP/state/health tests |
| Gateway | scheduled logon task calling `python -m hermes_cli.main gateway run` |
| Live databases | four of four returned `quick_check=ok` read-only |

## Obsidian

| Item | Observed value |
|---|---|
| Active vault | `C:\Users\b\Blendi` |
| V1 write root | `C:\Users\b\Blendi\JARVIS\V1` (not created; WP6) |
| Write recovery | specified, not yet implemented |
| Protected content | existing human notes remain unchanged during V1 write gate |

## Providers

| Surface | Verified state | Remaining probe |
|---|---|---|
| Hermes | Installed, active, local databases healthy | structured worker adapter in WP4 |
| Codex | CLI installed and current task operational | app-server adapter and role mapping |
| OpenCode Go | Desktop data present; no shell command found | callable interface and models |
| ChatGPT Plus | Owner-reported subscription | not treated as API quota |
| Google Cloud / Vertex | `gcloud` installed | credential-safe auth, models, quota, billing |

## Network And External Systems

```yaml
network_policy: capability_and_consequence_controlled
public_actions: NOT_READY_UNTIL_CAPABILITY_TESTED
purchases: NOT_READY_UNTIL_CAPABILITY_TESTED
credential_values_recorded: false
```

## Audit Verdict

```yaml
status: PASS
auditor: Codex high-reasoning foundation pass
started_at: 2026-08-11T17:33:05+02:00
completed_at: 2026-08-11T17:51:28+02:00
live_system_mutations: 0
project_local_mutations:
  - isolated Hermes clone
  - test environments
  - staging Foundry databases
unknowns:
  - provider role/model availability
  - provider billing and quotas
  - full Hermes suite baseline
blockers: []
```
