# MASTER GOAL 02 — integrated product acceptance matrix

Worktree: `C:\temp\JARVIS\jarvis-integrated-acceptance-20260916`  
Branch: `candidate/jarvis-integrated-acceptance-20260916`  
Base: `15a25536348d87be53ff115033c5012192d988be`  
Live JARVIS: not touched. `main`: not merged. Origin: not pushed.

Classification key: `ACCEPTED` | `ACCEPTED_WITH_LIVE_PROOF_REMAINING` | `BLOCKED` | `REJECTED`

| Surface | Classification | Evidence |
| --- | --- | --- |
| V14 Browser operator | `ACCEPTED` | Scenario A: authenticated localhost Playwright, injected `/expire` session expiry, canonical `browser_reauth_provider` recovery, verified receipts. |
| V14 Windows operator | `ACCEPTED` | Scenario A native Word `_WwG` readback; owner Notepad PID 29016 untouched. |
| V14 Git/GitHub effects | `ACCEPTED_WITH_LIVE_PROOF_REMAINING` | Disposable local bare remote push + SHA equality proved. Real GitHub API writes remain live-only. |
| V14 Cross-app composition | `ACCEPTED` | Recipe `v14.digital_operator.close` through Fabric: browser → artifact → Word → git commit/push. Owner-style Scenario D uses the same recipe via MissionLoop `cap.workflow.run`. |
| V15 Voice/input | `ACCEPTED_WITH_LIVE_PROOF_REMAINING` | Typed/fixture EN/SQ owner commands proved. Albanian microphone STT `NOT_TESTED` (no Windows recognizer). |
| V15 Albanian/English | `ACCEPTED` | Typed EN/SQ GOAL, SQ PAUSE/RESUME/STOP/VETO, `çka je tu bo?` / `what are you doing?`, same-language confirmations, CC locale EN↔SQ persistence. |
| V15 Command Center | `ACCEPTED` | Disposable CC server locale/prefs + voice-text status; vitest/build recorded in results. |
| V15 Notifications | `ACCEPTED_WITH_LIVE_PROOF_REMAINING` | In-app toast emit then dedupe proved. Native `winotify` toast `NOT_TESTED`. |
| V16 Campaign continuity | `ACCEPTED` | Resource WAIT/wake, process-loss continue-once, duplicate counters 0. After repair, children `SUCCEEDED` once verifier evidence exists. |
| V16 Self-expansion | `ACCEPTED` | Isolated expansion resumes the exact original objective; replay is identical. |
| V16 MCP capability acquisition | `ACCEPTED_WITH_LIVE_PROOF_REMAINING` | Self-expansion/local trial path proved in Scenario C. Live Catalog registration remains live-only. |
| V16 Repair/resume | `ACCEPTED` | Builder FAIL → repair → independent verifier PASS; startup resume continues once. |
| STOP/restart | `ACCEPTED` | Scenario E: PAUSE blocks new effects, RESUME completes work, STOP sentinel survives reopen, canonical REVOKE clears it, objective lineage intact. |
| Owner-style no-prompt-shuffling | `ACCEPTED` | Scenario D: one GOAL through OwnerControlService → Executive → MissionLoop → `cap.workflow.run` of `v14.digital_operator.close`. No manual subsystem prompt shuffling. |

## Known-limitation verdicts

| Item | Verdict |
| --- | --- |
| Campaign child remaining RUNNING after MissionLoop PASS | **Defect, repaired.** Transitional RUNNING is only valid until verifier/acceptance evidence exists. Canonical reconcile now promotes `SUCCEEDED` without owner intervention. |
| `cap.workflow.run` Catalog UNKNOWN | **Intentional until first verified outcome.** After verified success, readiness is `READY` and a second eligible workflow runs. Not a deadlock. |
| Albanian microphone STT | `ACCEPTED_WITH_LIVE_PROOF_REMAINING` / hardware-model unavailable. |

## Product decision

`ACCEPTED_FOR_LIVE_PROMOTION`

No remaining Critical/High source defect on this candidate. Remaining work is genuinely live, external, hardware, or owner-in-loop proof. Production trust `SUSPENDED` is not this decision.
