# Phase 4 Adversarial Fault Matrix

The focused suite is `tests/foundry/test_procedural_learning.py` (20 tests).
The local bounded probe used the harmless synthetic app and an independent
`GET /state` system-of-record oracle. `PASS` means the required refusal or
effect truth was observed; `NOT_TESTED` would be used for a missing gate.

| Gate | Expected | Actual evidence | Verdict |
|---|---|---|---|
| F1 valid bundle | deterministic replay PASS | real local replay `COMPLETED`, 5/5 OpenAdapt steps, JARVIS effect `COMPLETED` | PASS |
| F2 same bundle repeated | same semantics, one effect | repeat returned the same action key and no second adapter launch | PASS |
| F3 safe parameter substitution | correct backend result | `PH4-A/B`, `PH4-SCRIPT/A`, and `PH4-RESUME/R` landed as distinct local records | PASS |
| F4 bundle tamper | reject before execution | focused test raises `BundleIntegrityError` after `workflow.json` mutation | PASS |
| F5 unknown version | fail closed / `NOT_READY` | focused `governed_replay` test returns `NOT_READY` from the invalid Tier-C reference before `procedure.execution.requested`; direct verification still raises `BundleVersionError`/`ActionAuthorityError` | PASS |
| F6 UI success/backend unchanged | FAIL/FROZEN | fake UI claim plus false oracle produced `FROZEN` | PASS |
| F7 backend truth wins | backend success despite UI claim | local `/state` oracle matched after OpenAdapt UI success; JARVIS, not UI, decided `COMPLETED` | PASS |
| F8 changed label/layout | only proven recovery rungs | Phase-1 drift report records 7 bounded heals; irreversible below identity halted | PASS (bounded) |
| F9 ambiguous target/control | HALT | `ambiguous` structural target rejected at bundle boundary | PASS |
| F10 irreversible geometry-only | refuse | irreversible step without selector/identity anchor rejected | PASS |
| F11 crash before effect | safe recovery/no duplicate | Phase-1 hard-kill before Submit recorded 0 backend writes; existing effect journal reap freezes | PASS |
| F12 crash after real effect | reconcile, no replay | existing effect recovery/effects tests plus terminal idempotency path | PASS |
| F13 partial effect | FROZEN/repair | focused mutable-oracle test classified a changed provider after adapter STOP as `PARTIAL` and froze the journal | PASS |
| F14 parent kill | owned tree clean | focused Windows process-tree test passed; first real replay failed closed on a transient descendant, then the bounded polling repair produced a fresh clean replay with no owned descendants | PASS |
| F15 STOP before replay | no execution | bounded STOP probe returned `NOT_READY`; no ActionEnvelope/effect was launched | PASS |
| F16 STOP during replay | terminate/freeze/reconcile | focused adapter STOP result reconciled to `FROZEN`; resume did not launch again | PASS |
| F17 restart after interruption | durable reconciliation | existing pipeline/effect resume regressions plus frozen request replay path | PASS |
| F18 duplicate request | one external effect | repeat probe left backend count unchanged and adapter call count at one | PASS |
| F19 fake secret | no leak | secret-bearing page text rejected; source and bundle scan found no secret assignment | PASS |
| F20 prompt injection | zero authority | injection text becomes `[UNTRUSTED_PAGE_TEXT]`; it cannot enter control fields | PASS |
| F21 Tier-C/bundle mismatch | reject | focused Tier-C tamper test raises `ActionAuthorityError` | PASS |
| F22 OpenAdapt unavailable | unrelated JARVIS healthy | missing executable returned `NOT_READY`; database remained initialized and Supervisor state unchanged | PASS |
| F23 desktop requested | NOT_READY/PROBING | desktop surface returns `NOT_READY`; registry remains `PROBING` | PASS |
| F24 healthy replay model calls | zero where qualified | actual OpenAdapt report and bounded result record `model_calls=0` | PASS |

Additional Phase-4 shadow gate: the new focused `shadow_procedure` test runs the
bundle reference through the existing V6 shadow machinery, records a proposal
with `zero_live_effects=1`, leaves the external marker unchanged, and emits no
`system.action_started` event before local replay. PASS.

Known limitation: Phase-1's native Windows replay still requires a WAA agent;
Phase 4 deliberately does not promote it.
