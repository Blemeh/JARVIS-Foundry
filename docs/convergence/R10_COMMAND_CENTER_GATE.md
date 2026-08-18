# R10 Command Center Gate (Canonical Forward-Port)

Program: JARVIS Remaster to Canonical Convergence
Canonical base: `2b3573606570f910e5569cf3306223354f93e4ac` (main, schema v33)
Gate file: `tests/convergence/test_r10_command_center_gate.py`
Method: semantic forward-port onto canonical `command_center.py`,
`briefings.py`, `owner_controls.py`, `voice.py`, and the durable owner
directive ledger. No remaster `command_center` package or parallel UI
architecture is introduced.

## Proven contract

The focused gate proves, against a fresh canonical ledger:

- `build_snapshot` exposes the Command Center surfaces through the durable
  append-only ledger projection: goals/constraints/vetoes/pause under
  `controls`, missions with `next_action`, workers
  (`agent_run.*`), verification verdicts with findings, evidence bundles,
  health (supervisor, stale leases, crashed workers, degraded posture),
  resources, systems, candidates/rejected alternatives, releases/rollbacks
  via ledger events, recovery/blocked/waiting material, costs with honest
  `UNKNOWN` totals, briefings, voice, and the decision/effect timeline with
  per-record evidence refs and drill-down routes.
- authenticated owner controls STOP/PAUSE/RESUME/VETO are recorded through
  the SAME durable `owner_directive.*` ledger path used by the Supervisor;
  the UI/voice surface cannot bypass authentication, nonce replay guards, or
  the deterministic policy broker. UI POST controls hit `/api/controls` and
  are rejected without a valid HMAC signature; a UI process failure cannot
  remove STOP.
- briefing planning is idempotent (`briefing_outbox` has one row for an
  identical period), planning/rendering is read-only apart from the briefing
  event itself, and the EN/SQ semantic category briefings are exercised
  against the same snapshot payloads.
- voice is honest: fixture STT/TTS runs fail closed into auditable
  `voice_interactions` rows with `REJECTED`/`UNAUTHENTICATED`/`REPLAYED`
  outcomes, and no mock/provider receipt is presented as real voice proof.
  Real provider or interface status remains `NOT_TESTED` when no real
  receipt exists.
- the local Command Center server is a read-only projection; GET routes do
  not mutate the ledger, and the control endpoint is authenticated and
  fails closed.

## Surface mapping (remaster R10 list -> canonical main)

| Remaster surface | Canonical main evidence |
|---|---|
| goals | `directives` with `command_type=GOAL` -> `controls.goals` |
| projects | `evolution/portfolio.py` project registry (not a snapshot key) |
| missions | `missions` projection, `mission.created/state_changed` |
| candidate decisions / rejected alternatives | `candidates` projection and timeline `candidate*` events |
| current workers | `agent_runs` projection, `agent_run.*` |
| verification | `verification_verdicts`, `verification.*` |
| findings | `verdict.findings_json` (verification findings) |
| releases | `release.manifest_recorded` and related timeline events |
| capabilities | `resources.capabilities_json` + `systems` readiness; no snapshot key |
| costs | `cost_measurements`; measured totals are `UNKNOWN` when absent |
| resources | `resources` projection |
| evidence | `evidence_bundles` projection |
| health | `supervisor_state`, leases, crashed workers |
| incidents | durable `recovery`/blocked/unknown surfaces; no snapshot key |
| waiting/blockers | `missions` BLOCKED/WAITING + `recovery.blocked_missions` |
| next autonomous actions | `missions.next_action` + briefing `next_actions` |
| evidence drill-down | `/api/record/{mission,event}/<id>` routes with record refs |
| decision/effect timeline | `decision_timeline` with `evidence_refs` per event |

The keys that exist only in the remaster snapshot (projects, incidents,
capabilities, waiting) intentionally do NOT become canonical `build_snapshot`
keys: canonical main models those concepts through its own durable surfaces,
and the gate asserts absence of fabricated status instead.

## Honest voice boundary

The gate does NOT claim real provider proof. It proves:

- a fixture adapter record is distinguishable from a real provider receipt
  and is never presented as real;
- a provider/interface that is not configured is reported as
  `NOT_TESTED`/`BLOCKED` in the projection, not as working.

## Focused verification

`pytest tests/convergence/test_r10_command_center_gate.py` passes 6/6 on the
R3 candidate using the canonical venv. The canonical V4 command-center and
briefing suites (`tests/foundry/test_v4_command_center.py`,
`tests/foundry/test_v7_briefing.py`) remain the authoritative regression
surface and are deliberately not duplicated here.

## Known gaps / uncertainty

- Real Windows SAPI voice probe and real Chrome visual inspection are not
  part of this focused gate; they remain `NOT_TESTED` here and belong to the
  integration/visual slice.
- The R10 list's remaster-only snapshot keys (projects/incidents/capabilities
  as standalone sections) are mapped to canonical surfaces and are not
  fabricated as separate keys.
