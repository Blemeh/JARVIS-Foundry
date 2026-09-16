# MASTER GOAL 01 converged product demo

Isolated candidate only. Do not run these against live JARVIS, `main`, origin,
the installed release, or owner applications that this worktree did not launch.

Worktree: `C:\temp\JARVIS\v14-v16-max-semantic-convergence-20260916`  
Branch: `candidate/v14-v16-max-semantic-convergence-20260916`  
Base: `4b2ae28da582b369982661c2a1d2b1792e2f48cb`

All four scenarios write non-secret `RESULT.json` next to the runner. Disposable
`run/` directories, cookies, and databases stay gitignored.

## V14 digital operator close

Authenticated localhost browser → artifact → owned Word native readback →
disposable Git commit/push. Does not adopt owner Notepad PID 29016.

```powershell
C:\Users\b\.local\bin\uv.exe run --extra dev --extra web python var/convergence-20260916/v14_real_scenario/run.py
```

Pass requires `workflow_success`, native readback, remote SHA equality, and
`owner_notepad_pid_untouched`.

## V15 owner interface

English GOAL plus Albanian STOP/PAUSE/RESUME/status/VETO through
`OwnerControlService`, STOP independent of STT/TTS/UI, toast/preferences, and
Command Center EN/SQ tests/build. Albanian microphone STT is `NOT_TESTED`
because Windows Speech has no Albanian recognizer.

```powershell
C:\Users\b\.local\bin\uv.exe run --extra dev --extra web python var/convergence-20260916/v15_real_scenario/run.py
cd ui/command-center
npm ci
npm test
npm run build
```

## V16 campaign continuity

Campaign child `workflow.run` of `v16.bounded_note`, resource WAIT/wake,
self-expansion resume of the exact original objective, builder failure →
repair → independent verifier PASS, process-loss rebuild continue-once, and
zero duplicate mission/effect/continuation/false success.

```powershell
python var/convergence-20260916/v16_real_scenario/run.py
```

## Owner-style high-level GOAL

One authenticated `GOAL` enters `OwnerControlService` → Executive
`sync_owner_goals` / `run_autonomy_cycle` → `cap.workflow.run` of
`v14.digital_operator.close` → MissionLoop. No manual subsystem prompt-shuffling.

```powershell
C:\Users\b\.local\bin\uv.exe run --extra dev --extra web python var/convergence-20260916/owner_style_scenario/run.py
```
