# Lane 6 continuity demo

Run from the Lane 6 worktree with Python 3.13 plus the repository dev/web
dependencies installed:

```powershell
$env:PYTHONPATH = (Join-Path (Get-Location) 'src')
python var/lane6-demo/continuity_runner.py
```

The runner uses disposable state only. It executes the canonical V12
self-expansion, endurance/campaign, and MCP proofs plus the Lane 6 callable
entrypoint, shared-resource capacity wait/wake, and terminal child repair
behavior tests. The compact result is written to:

```text
var/lane6-demo/run/LANE6_CONTINUITY_PROOF.json
```

Child receipts remain under the same ignored `run/` directory. They are local
proof artifacts, not production state and not Catalog registrations for live
JARVIS.

Demonstrated continuity:

```text
objective/campaign
-> real Fabric capability gap
-> reuse-first discovery
-> failed candidate fallback
-> isolated adapter or MCP trial
-> independent verification
-> disposable READY/PROBATION
-> exact objective wake/resume
-> multiple campaigns with shared-resource WAIT/wake
-> child failure/repair/accepted continuation
-> checkpoint and fresh-process rebuild
-> verified completion or honest WAIT
```

The demo never installs or activates a release, changes the live database,
Supervisor, Scheduler, trust, provider configuration, `main`, or origin.
