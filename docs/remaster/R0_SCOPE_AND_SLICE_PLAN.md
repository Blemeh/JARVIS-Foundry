# Remaster Scope, Risks, Budgets, and Slice Plan

## Frozen scope

The remaster preserves the whole `IDEA.md` end state. It is not a cleanup of the
watchdog or a rebranding of V7. The final product must perform all ten executable
purpose journeys through real integrated paths, with the owner managing by
exception.

Allowed operation is DIGITAL WORK ONLY. The product may use governed local
files/applications, owner-controlled repositories/services, research sources,
and independently accepted communication/deployment targets. It must deny
purchasing, payments, trading, banking, cryptocurrency transfer,
financial-account change, and all movement of money.

## Environment isolation

- immutable baseline: main checkout at `93a3ddc` remains the rollback source;
- construction: sibling `JARVIS-Foundry-Remaster`, branch
  `remaster/post-v7-purpose`;
- build environment: a remaster-local locked virtual environment, never the
  live editable `.venv`;
- staging runtime: `var/remaster/runtime`, with its own DB, STOP, logs, evidence,
  caches, candidate roots, connector roots, and release registry;
- staging ports: `8780`–`8784`, subject to a fresh collision check before bind;
- browser/provider targets: separate owner-authorized test profile/targets;
- secrets: metadata/stubs only until a bounded adapter gate; no secret copied
  into Git, worker context, logs, or evidence;
- live V7: unchanged and observe-only until R12 live adoption.

## Frozen preliminary performance/resource budgets

These thresholds precede remaster results. R2 measurement may make them stricter
but may not lower them merely to obtain PASS.

| Measure | Entry observation | Initial release threshold |
|---|---:|---:|
| STOP recognition | V7 polling target 5s | p99 <= 10s; queued work refused immediately |
| in-flight cancellation/containment | NOT_TESTED | <= 30s or capability suspended with incident |
| healthy supervisor heartbeat gap | observed max 7.448s in current process | no gap > 15s without degraded incident |
| restart/recovery to truthful health | NOT_TESTED | <= 60s local host, including lease/reconcile |
| local event append | NOT_MEASURED | p95 <= 50ms, p99 <= 250ms at entry-scale ledger |
| full replay/reconcile | NOT_MEASURED | <= 30s for 50k events, <= 512MiB peak RSS |
| owner control acknowledgement | NOT_TESTED | <= 2s local UI/API, independent of provider |
| Command Center first useful render | stale preview only | <= 2s local p95 |
| idle Supervisor CPU/RSS | NOT_MEASURED | <= 5% one core / <= 512MiB |
| retained logs/evidence growth | NOT_MEASURED | <= 250MiB/day excluding explicit frozen assets |
| provider calls/tokens/cost | unpriced, no Foundry accounting | per-mission hard call/token/time caps; unknown price blocks monetary PASS; no new paid service or purchase |
| external effects | zero live | one intent per idempotency key; every attempt inspected and reconciled |

## Initial risks and stop conditions

| Risk | Severity | Required control |
|---|---|---|
| editable live source loads partial changes | S1 | immutable versioned release + atomic pointer + restore |
| duplicated authority/effect paths | S1 | one typed kernel port; old paths unreachable |
| target substitution/arbitrary performer | S1 | canonical target binding and adapter-owned execution |
| forged directive from notes/model/web | S1 | authenticated owner-control boundary outside content |
| history loss/corrupt migration | S1 | additive compatibility, WAL-safe rehearsal, verified restore |
| financial effect despite new boundary | S1 | absent/deny policy plus mutation/fault tests |
| secrets in context/log/evidence | S1 | brokered handles, redaction, scanners, canaries |
| fake PASS from mocks/tests/isolated DB | S2 | evidence dimensions + real after-state receipts |
| stale monitor/knowledge produces work | S2 | freshness policy, contradiction handling, WAITING |
| provider outage stalls unrelated work | S2 | per-capability isolation, cooldown/backpressure |
| candidate self-certifies/promotes | S1 | external verifier and Supervisor-owned release controller |
| UI/voice failure disables control | S1 | control plane independent from owner surfaces/providers |

An authenticated owner STOP/veto/redirect, unrepaired S1 integrity/control/data
loss risk requiring owner action, or a blocker preventing all safe progress stops
work. A subsystem blocker does not stop independent slices.

## R0–R12 dependency plan

| Stage | Frozen outcome | Gate evidence |
|---|---|---|
| R0 | truthful baseline, restore unit, findings, disposition and purpose scope | independent structured R0 PASS |
| R1 | implementation-independent ten-journey executable specification | old observe-only V7 fails; compliant reference harness passes |
| R2 | every module/test/migration/adapter/live path classified | complete machine-readable inventory + independent review |
| R3 | locked engineering foundation and automatic quality blockers | reproducible Windows/local CI; type/lint/security/coverage/mutation/property gates |
| R4 | small typed trusted kernel and one authority/effect/release path | state, mutation, concurrency, crash, security, replay and restore PASS |
| R5 | lossless V1–V7 compatibility/migration | sequence-one replay, invariant compare, interrupted migration and restore PASS |
| R6 | governed evidence-backed institutional memory | real vault index/retrieve/conflict/write/rollback with semantic integrity |
| R7 | durable goals, portfolio, research, monitoring and useful selection | real-source changes generate traceable nonduplicate missions |
| R8 | build/verify/repair/maintain/deliver organization | real useful asset plus real maintenance repair independently accepted |
| R9 | governed adapters and real digital effects | every ACTIVE system/action has bounded real after-state proof |
| R10 | truthful Command Center, briefings, English/Albanian text and voice | real owner use and control under component/provider failures |
| R11 | controlled self-evolution | three live cycles, two measured promotions, rejection, canary and restore |
| R12 | differential shadow, fault campaign, immutable live adoption | exact deployed hashes, restoration, then unwaived purpose run |

Every consequential slice follows: frozen objective and tests; separate builder;
focused/full/quality/fault checks; actual artifact/system inspection; immutable
hash bundle; fresh adversarial verifier; preserved FAIL; exact repair and
regression; fresh re-verification; scoped local commit only after PASS.

## Final wait condition

After R12 engineering and canary PASS, the task must remain active for seven
consecutive unwaived days on the exact deployed release. The historical waiver
cannot count. Completion still requires ten useful independently verified
missions, all purpose journeys, fault/restart/briefing/evolution evidence, zero
known reproducible defects, and fresh final engineering, security,
data/recovery, product-purpose, and live-operational PASS verdicts.
