"""Build R2 forensic evidence artifacts for the JARVIS Post-V7 remaster.

Read-only on the primary checkout.  Reads the measurement snapshot produced
by measure_repo.py, adds the R2 disposition/classification and findings, and
writes the four R2 deliverables under docs/remaster/evidence/r2/ of the
remaster worktree.

Usage:
    python build_r2_evidence.py <checkout> <measure.json> <remaster-root>
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


SCHEMA_FINDINGS = "jarvis-r2-forensic-findings-v1"
SCHEMA_INVENTORY = "jarvis-r2-module-inventory-v1"
SCHEMA_METRICS = "jarvis-r2-metrics-v1"
BASELINE_COMMIT = "93a3ddc0965dfaf3e4cb9dc0952467bd5250520a"
BASELINE_TREE = "2e1df6190b0d032372ae46e6a59bef22ddac57e3"
GENERATED_BY = "R2_ARCHITECTURE_AND_CODE_FORENSICS_AUDITOR (deepseek-v4-flash via codex router)"


# Ordered first-match rules: (path-substring, disposition, basis).
MODULE_RULES: list[tuple[str, str, str]] = [
    ("src/jarvis_foundry/projections.py", "REWRITE", "R0 event ledger REWRITE; R2-F001/F012"),
    ("src/jarvis_foundry/events.py", "REWRITE", "R0 event ledger REWRITE; R2-F011/F021"),
    ("src/jarvis_foundry/database.py", "REWRITE", "R0 event ledger REWRITE; R2-F011/F012"),
    ("src/jarvis_foundry/reconcile.py", "REWRITE", "R0 ledger/replay REWRITE; R2-F023"),
    ("src/jarvis_foundry/migrations.py", "REFACTOR", "R0 schema/migration REFACTOR; R2-F023"),
    ("src/jarvis_foundry/v4_schema.py", "REFACTOR", "R0 schema compatibility REFACTOR"),
    ("src/jarvis_foundry/mission/", "REWRITE", "R0 goal/mission state machines REWRITE; R2-F007"),
    ("src/jarvis_foundry/domain.py", "REWRITE", "R0 mission state machines REWRITE; R2-F007"),
    ("src/jarvis_foundry/v3_loop.py", "REWRITE", "R0 legacy mission loop UNSAFE/REWRITE; R2-F006/F007"),
    ("src/jarvis_foundry/execution/", "REWRITE", "R0 effect pipeline UNSAFE/REWRITE; R2-F006/F007"),
    ("src/jarvis_foundry/effects.py", "REWRITE", "R0 effect pipeline UNSAFE/REWRITE; R2-F003/F022"),
    ("src/jarvis_foundry/control/action_envelope.py", "REWRITE", "R0 ActionEnvelope UNSAFE/REWRITE; R2-F007/F013"),
    ("src/jarvis_foundry/control/broker.py", "REWRITE", "R0 capability/authority REWRITE; R2-F007"),
    ("src/jarvis_foundry/control/capability_broker.py", "REWRITE", "R0 capability broker REWRITE; R2-F013"),
    ("src/jarvis_foundry/control/policy.py", "REWRITE", "R0 owner directive controls REWRITE; R2-F010"),
    ("src/jarvis_foundry/control/registration.py", "REWRITE", "R0 authority registration REWRITE"),
    ("src/jarvis_foundry/control/redaction.py", "KEEP", "R2: useful deterministic redaction boundary"),
    ("src/jarvis_foundry/owner_controls.py", "REWRITE", "R0 other owner directives REWRITE; R2-F013"),
    ("src/jarvis_foundry/capabilities/", "REWRITE", "R0 capability broker REWRITE; R2-F028"),
    ("src/jarvis_foundry/evolution/scheduler.py", "REWRITE", "R0 scheduler REWRITE; R2-F017"),
    ("src/jarvis_foundry/evolution/portfolio.py", "REWRITE", "R0 portfolio REWRITE; R2-F018"),
    ("src/jarvis_foundry/evolution/cycle.py", "REWRITE", "R0 evolution controller UNSAFE/REWRITE; R2-F019"),
    ("src/jarvis_foundry/evolution/promote.py", "REWRITE", "R0 evolution promotion UNSAFE/REWRITE; R2-F007"),
    ("src/jarvis_foundry/evolution/registry.py", "REWRITE", "R0 evolution registry REWRITE; R2-F007"),
    ("src/jarvis_foundry/evolution/shadow.py", "REWRITE", "R0 shadow/release REWRITE"),
    ("src/jarvis_foundry/evolution/canary.py", "REWRITE", "R0 release canary REWRITE"),
    ("src/jarvis_foundry/evolution/acceptance.py", "REWRITE", "R0 evolution acceptance REWRITE"),
    ("src/jarvis_foundry/evolution/records.py", "REFACTOR", "R0 evolution records REFACTOR; R2-F019"),
    ("src/jarvis_foundry/evolution/builder.py", "REFACTOR", "R0 builder substrate REFACTOR"),
    ("src/jarvis_foundry/evolution/lab.py", "REFACTOR", "R0 evolution lab REFACTOR"),
    ("src/jarvis_foundry/evolution/verify.py", "REFACTOR", "R0 verifier substrate REFACTOR"),
    ("src/jarvis_foundry/evolution/discover.py", "REFACTOR", "R0 discovery substrate REFACTOR"),
    ("src/jarvis_foundry/evolution/restore.py", "REFACTOR", "R0 restore/recovery primitives REFACTOR"),
    ("src/jarvis_foundry/supervisor/evolution_control.py", "REWRITE", "R0 evolution controller REWRITE; R2-F006"),
    ("src/jarvis_foundry/supervisor/estop.py", "KEEP", "R0 owner STOP sentinel KEEP + HARDEN"),
    ("src/jarvis_foundry/supervisor/", "REFACTOR", "R0 supervisor watchdog REFACTOR; R2-F014"),
    ("src/jarvis_foundry/connectors/fakes.py", "DELETE", "R2-F005/F015: dead fixture connector"),
    ("src/jarvis_foundry/connectors/", "REFACTOR", "R0 scratch connector REFACTOR; R2-F028"),
    ("src/jarvis_foundry/gates/owner_families.py", "DELETE", "R0 financial families DELETE/DENY; R2-F029"),
    ("src/jarvis_foundry/gates/", "REFACTOR", "R0 mini-gates REFACTOR"),
    ("src/jarvis_foundry/roles/", "REFACTOR", "R0 Hermes worker adapter REFACTOR; R2-F027"),
    ("src/jarvis_foundry/voice.py", "NOT_IMPLEMENTED", "R0 voice NOT_IMPLEMENTED/BLOCKED_EXTERNAL; R2-F005/F020"),
    ("src/jarvis_foundry/reporting/briefing.py", "NOT_IMPLEMENTED", "R0 briefings NOT_IMPLEMENTED; R2-F020"),
    ("src/jarvis_foundry/briefings.py", "NOT_IMPLEMENTED", "R0 briefings NOT_IMPLEMENTED; R2-F005/F020"),
    ("src/jarvis_foundry/command_center.py", "REFACTOR", "R0 Command Center REFACTOR; R2-F016/F018"),
    ("src/jarvis_foundry/discovery_candidates.py", "REWRITE", "R0 discovery heuristics REWRITE; R2-F025"),
    ("src/jarvis_foundry/discovery_monitors.py", "REWRITE", "R0 monitors REWRITE; R2-F025"),
    ("src/jarvis_foundry/research_queue.py", "NOT_IMPLEMENTED", "R0 research interfaces NOT_IMPLEMENTED/REWRITE; R2-F026"),
    ("src/jarvis_foundry/knowledge_ingestion.py", "REFACTOR", "R0 knowledge primitives REFACTOR; R2-F026"),
    ("src/jarvis_foundry/knowledge_retrieval.py", "REFACTOR", "R0 knowledge primitives REFACTOR"),
    ("src/jarvis_foundry/knowledge_snapshots.py", "REFACTOR", "R0 knowledge primitives REFACTOR"),
    ("src/jarvis_foundry/knowledge_entities.py", "REFACTOR", "R0 knowledge primitives REFACTOR"),
    ("src/jarvis_foundry/knowledge_extract.py", "REFACTOR", "R0 knowledge primitives REFACTOR"),
    ("src/jarvis_foundry/knowledge.py", "REFACTOR", "R0 knowledge primitives REFACTOR"),
    ("src/jarvis_foundry/live_ingestion.py", "REFACTOR", "R0 memory primitives REFACTOR; R2-F015"),
    ("src/jarvis_foundry/correlation/", "REFACTOR", "R0 memory/correlation REFACTOR"),
    ("src/jarvis_foundry/vault_indexer.py", "REFACTOR", "R0 vault indexing REFACTOR; R2-F026"),
    ("src/jarvis_foundry/vault_retrieval.py", "REFACTOR", "R0 vault retrieval REFACTOR"),
    ("src/jarvis_foundry/obsidian/writer.py", "UNSAFE", "R0 Obsidian writer UNSAFE/REFACTOR"),
    ("src/jarvis_foundry/obsidian/snapshots.py", "UNSAFE", "R0 Obsidian consolidation UNSAFE/REFACTOR"),
    ("src/jarvis_foundry/consolidation.py", "UNSAFE", "R0 Obsidian consolidation UNSAFE/REFACTOR"),
    ("src/jarvis_foundry/obsidian/", "REFACTOR", "R0 Obsidian policy REFACTOR"),
    ("src/jarvis_foundry/artifacts.py", "REFACTOR", "R0 evidence/artifact substrate REFACTOR"),
    ("src/jarvis_foundry/installer.py", "REFACTOR", "R0 runtime installer REFACTOR; R2-F009/F014"),
    ("src/jarvis_foundry/cli/", "REFACTOR", "R0 control CLI REFACTOR; R2-F014"),
    ("src/jarvis_foundry/paths.py", "REFACTOR", "R0 runtime identity REFACTOR; R2-F008/F009"),
    ("src/jarvis_foundry/clock.py", "KEEP", "R2: stable deterministic time primitive"),
    ("src/jarvis_foundry/systems.py", "REFACTOR", "R0 capability/system registry REFACTOR; R2-F028"),
    ("src/jarvis_foundry/integrated_rollback.py", "REFACTOR", "R0 rollback substrate REFACTOR; R2-F015"),
    ("src/jarvis_foundry/worker_context.py", "REFACTOR", "R0 worker context REFACTOR; R2-F013"),
    ("__init__.py", "KEEP", "R2: package markers only"),
]


def classify(path: str) -> tuple[str, str]:
    for needle, disposition, basis in MODULE_RULES:
        if needle in path:
            return disposition, basis
    return "REFACTOR", "R2 default: retain behavior behind new contract"


def module_typing(mod: dict) -> dict:
    funcs = mod["functions"]
    with_params = [f for f in funcs if f["params"]]
    return {
        "functions": len(funcs),
        "with_params": len(with_params),
        "all_params_and_return_annotated": sum(
            1 for f in with_params if f["params_annotated"] == f["params"]
        ),
        "untyped_params": sum(
            1 for f in with_params if f["params_annotated"] == 0
        ),
    }


FINDINGS: list[dict] = [
    {
        "id": "R2-F001",
        "severity": "S2",
        "subsystem": "trusted kernel / event projections",
        "title": "Monolithic 1,740-line projection dispatcher silently ignores unknown events",
        "paths": [
            {"path": "src/jarvis_foundry/projections.py", "line_ranges": [[58, 1726], [1729, 1739]]},
            {"path": "src/jarvis_foundry/database.py", "line_ranges": [[130, 141]]},
        ],
        "measured_evidence": "projections.py total 1740 lines; apply_event spans 58-1726 (1669 lines); reset spans 1729-1739; 45 PROJECTION_TABLES; 95 exact event_type handlers plus 3 startswith families; the function ends with no else/raise after the last elif (tail 1715-1726), so unknown event types exit silently; rebuild_projections (database.py:130-141) replays every event, and unhandled types are silently dropped from projected state.",
        "affected_invariants": ["replay determinism", "fail closed", "audit truth"],
        "disposition": "REWRITE",
        "r0_cross_ref": "R0-F016",
    },
    {
        "id": "R2-F002",
        "severity": "S3",
        "subsystem": "repository-wide",
        "title": "Oversized functions concentrate complexity in 24 functions over 80 lines",
        "paths": [
            {"path": "src/jarvis_foundry/projections.py", "line_ranges": [[58, 1726]]},
            {"path": "src/jarvis_foundry/mission/loop.py", "line_ranges": [[236, 453]]},
            {"path": "src/jarvis_foundry/v3_loop.py", "line_ranges": [[175, 382]]},
            {"path": "src/jarvis_foundry/evolution/cycle.py", "line_ranges": [[76, 254]]},
            {"path": "src/jarvis_foundry/command_center.py", "line_ranges": [[100, 233]]},
            {"path": "src/jarvis_foundry/reconcile.py", "line_ranges": [[121, 220]]},
            {"path": "src/jarvis_foundry/effects.py", "line_ranges": [[181, 279]]},
            {"path": "src/jarvis_foundry/control/broker.py", "line_ranges": [[206, 299]]},
            {"path": "src/jarvis_foundry/evolution/scheduler.py", "line_ranges": [[156, 246]]},
        ],
        "measured_evidence": "Measured top-20 by AST body lines: apply_event 1669; run_step 218; run_one_loop 208; shadow_run 199; _html 197; run_cycle 179; build_snapshot 134; build_candidate 122; select_connector 114; dispatch 114 (roles/dispatcher.py:155-268); _one_candidate 110; run_evaluation 102; reconcile 100; execute_effect 99; decide 94; register_release 93; dispatch 92 (roles/adapter.py:216-307); tick_scheduler 91; two tests at 90. Distribution: 18 functions 81-160 lines, 6 functions 161+.",
        "affected_invariants": ["module cohesion", "reviewability", "bounded change surface"],
        "disposition": "REFACTOR",
        "r0_cross_ref": None,
    },
    {
        "id": "R2-F003",
        "severity": "S2",
        "subsystem": "repository-wide exception handling",
        "title": "Broad Exception/BaseException handlers swallow failure detail across 30+ sites",
        "paths": [
            {"path": "src/jarvis_foundry/effects.py", "line_ranges": [[261, 265]]},
            {"path": "src/jarvis_foundry/database.py", "line_ranges": [[36, 37], [47, 48]]},
            {"path": "src/jarvis_foundry/live_ingestion.py", "line_ranges": [[105, 110], [244, 246], [287, 289]]},
            {"path": "src/jarvis_foundry/execution/pipeline.py", "line_ranges": [[145, 146], [166, 168], [399, 400], [414, 415]]},
            {"path": "src/jarvis_foundry/command_center.py", "line_ranges": [[91, 99]]},
            {"path": "src/jarvis_foundry/supervisor/service.py", "line_ranges": [[119, 136]]},
            {"path": "src/jarvis_foundry/supervisor/run.py", "line_ranges": [[65, 67], [151, 157], [180, 184]]},
            {"path": "src/jarvis_foundry/connectors/activation.py", "line_ranges": [[223, 225], [236, 241], [292, 294]]},
            {"path": "src/jarvis_foundry/artifacts.py", "line_ranges": [[93, 95]]},
            {"path": "src/jarvis_foundry/obsidian/snapshots.py", "line_ranges": [[41, 43]]},
            {"path": "src/jarvis_foundry/supervisor/controls.py", "line_ranges": [[219, 221]]},
            {"path": "src/jarvis_foundry/supervisor/worker.py", "line_ranges": [[62, 64]]},
        ],
        "measured_evidence": "123 except handlers in src modules; 9 BaseException, 29 Exception, 0 bare except. BaseException sites: artifacts.py:93, database.py:36/47, live_ingestion.py:108, obsidian/snapshots.py:41, effects.py:261, supervisor/controls.py:219, supervisor/worker.py:62. Broad Exception sites include command_center.py:94, evolution/acceptance.py:57/71, roles/dispatcher.py:202/241, reporting/briefing.py:45/93, voice.py:119/127, evolution/restore.py:153, supervisor/console.py:65, supervisor/service.py:123/135, supervisor/run.py:65/153/180, connectors/activation.py:223/236/240/292.",
        "affected_invariants": ["fail loud", "no silent state loss", "failure provenance"],
        "disposition": "REFACTOR",
        "r0_cross_ref": None,
    },
    {
        "id": "R2-F004",
        "severity": "S2",
        "subsystem": "engineering foundations",
        "title": "No typing, lint, coverage, mutation, or CI gate exists",
        "paths": [
            {"path": "pyproject.toml", "line_ranges": [[1, 27]]},
        ],
        "measured_evidence": "pyproject.toml declares setuptools + pytest only; dev dependency is pytest>=9,<10; no [tool.mypy], [tool.ruff], [tool.coverage], or [tool.mutmut] sections; no CI workflow exists in the repository (no .github, zero workflow yaml). Measured typing: 1440/1701 functions with parameters fully annotated (84.7%), 401 functions with untyped parameters, 11 typed module variables. Mutation evidence in R1 used a custom mutant harness (docs/remaster/evidence/r1/mutant_results*) rather than a configured runner.",
        "affected_invariants": ["regression control", "deterministic quality gates", "verifier independence"],
        "disposition": "REFACTOR",
        "r0_cross_ref": None,
    },
    {
        "id": "R2-F005",
        "severity": "S2",
        "subsystem": "owner experience / adapters",
        "title": "Fixture providers and local sinks are the only voice/briefing delivery substrate",
        "paths": [
            {"path": "src/jarvis_foundry/voice.py", "line_ranges": [[40, 59], [61, 150]]},
            {"path": "src/jarvis_foundry/briefings.py", "line_ranges": [[75, 79]]},
            {"path": "src/jarvis_foundry/connectors/fakes.py", "line_ranges": [[18, 60]]},
        ],
        "measured_evidence": "voice.py:40-46 FixtureSTT declares provider 'fixture-stt'; voice.py:48-59 FixtureTTS declares 'fixture-tts'; VoiceGateway:61-150 has no runtime caller. briefings.py:75-79 deliver_local records briefing.delivered with sink 'local-test-sink'. connectors/fakes.py FakeConnector:18-60 is never referenced by source or tests. Executive entry points have 343 test-side references and zero runtime callers, so fixture-only behavior cannot be presented as live capability proof.",
        "affected_invariants": ["live capability separation", "real after-state proof"],
        "disposition": "NOT_IMPLEMENTED",
        "r0_cross_ref": "R0-F012 / R1-F006",
    },
    {
        "id": "R2-F006",
        "severity": "S1",
        "subsystem": "runtime executive",
        "title": "Every non-watchdog executive path is implemented but never scheduled or invoked",
        "paths": [
            {"path": "src/jarvis_foundry/evolution/scheduler.py", "line_ranges": [[117, 124], [156, 246]]},
            {"path": "src/jarvis_foundry/evolution/portfolio.py", "line_ranges": [[170, 199]]},
            {"path": "src/jarvis_foundry/v3_loop.py", "line_ranges": [[175, 382]]},
            {"path": "src/jarvis_foundry/mission/loop.py", "line_ranges": [[236, 453]]},
            {"path": "src/jarvis_foundry/execution/pipeline.py", "line_ranges": [[373, 427], [430, 515]]},
            {"path": "src/jarvis_foundry/evolution/cycle.py", "line_ranges": [[76, 254]]},
            {"path": "src/jarvis_foundry/evolution/promote.py", "line_ranges": [[168, 222]]},
            {"path": "src/jarvis_foundry/evolution/shadow.py", "line_ranges": [[93, 291]]},
            {"path": "src/jarvis_foundry/evolution/lab.py", "line_ranges": [[120, 221]]},
            {"path": "src/jarvis_foundry/vault_indexer.py", "line_ranges": [[51, 110]]},
            {"path": "src/jarvis_foundry/knowledge_ingestion.py", "line_ranges": [[44, 99]]},
            {"path": "src/jarvis_foundry/research_queue.py", "line_ranges": [[48, 119]]},
            {"path": "src/jarvis_foundry/reporting/briefing.py", "line_ranges": [[62, 75]]},
            {"path": "src/jarvis_foundry/command_center.py", "line_ranges": [[531, 566]]},
            {"path": "src/jarvis_foundry/supervisor/evolution_control.py", "line_ranges": [[48, 167]]},
        ],
        "measured_evidence": "Definitions exist for tick_scheduler/enable_scheduler, portfolio select_projects, run_one_loop, MissionLoop.run_step, pipeline run_mission/run_candidate, run_cycle, promote_release, shadow_run, run_evaluation, sync_vault, ingest_documents, detect_research_items, generate_briefing, VoiceGateway, CommandCenterServer, and EvolutionControl. Source-tree call-site scan finds zero runtime callers; the only references are tests (343 matches) and docstrings. poll_monitor (discovery_monitors.py:37-80) is called only by the unreachable v3_loop.run_one_loop.",
        "affected_invariants": ["persistent autonomy", "purpose journeys", "operational reachability"],
        "disposition": "REWRITE",
        "r0_cross_ref": "R0-F004",
    },
    {
        "id": "R2-F007",
        "severity": "S1",
        "subsystem": "trusted kernel / authority",
        "title": "Parallel mission, execution, capability, and promotion controllers write the same state",
        "paths": [
            {"path": "src/jarvis_foundry/domain.py", "line_ranges": [[28, 47]]},
            {"path": "src/jarvis_foundry/projections.py", "line_ranges": [[79, 141]]},
            {"path": "src/jarvis_foundry/mission/loop.py", "line_ranges": [[160, 190], [286, 287]]},
            {"path": "src/jarvis_foundry/v3_loop.py", "line_ranges": [[310, 321], [365, 369]]},
            {"path": "src/jarvis_foundry/execution/pipeline.py", "line_ranges": [[430, 515]]},
            {"path": "src/jarvis_foundry/evolution/registry.py", "line_ranges": [[214, 277]]},
            {"path": "src/jarvis_foundry/evolution/promote.py", "line_ranges": [[168, 222]]},
            {"path": "src/jarvis_foundry/control/broker.py", "line_ranges": [[195, 299]]},
            {"path": "src/jarvis_foundry/control/capability_broker.py", "line_ranges": [[68, 318]]},
            {"path": "src/jarvis_foundry/capabilities/registry.py", "line_ranges": [[66, 148]]},
        ],
        "measured_evidence": "Mission state is transition-validated in domain.py:28-47, projected from mission.state_changed at projections.py:131-141, appended by MissionLoop._advance at mission/loop.py:160-190, and appended directly by v3_loop.py:310-321 and 365-369. Three execution authorities exist: MissionLoop.run_step (mission/loop.py:236-453), pipeline.run_mission (execution/pipeline.py:430-515), and v3_loop.run_one_loop (v3_loop.py:175-382); MissionLoop dispatches roles directly (mission/loop.py:286) without the ActionEnvelope/effect gate. Promotion is duplicated between evolution/registry.py:214-277 promote_candidate and evolution/promote.py:168-222 promote_release, both writing release.slot_changed/release.promoted. Capability authority is split across control/broker.py PolicyBroker, control/capability_broker.py, and capabilities/registry.py.",
        "affected_invariants": ["single authority path", "state ownership", "effect safety"],
        "disposition": "REWRITE",
        "r0_cross_ref": "R0-F007",
    },
    {
        "id": "R2-F008",
        "severity": "S1",
        "subsystem": "runtime identity / truth",
        "title": "Runtime home switch has no release/commit identity pin and trusts caller-supplied state labels",
        "paths": [
            {"path": "src/jarvis_foundry/paths.py", "line_ranges": [[13, 23]]},
            {"path": "src/jarvis_foundry/supervisor/service.py", "line_ranges": [[28, 32]]},
            {"path": "src/jarvis_foundry/projections.py", "line_ranges": [[1715, 1726]]},
            {"path": "config/foundry.yaml", "line_ranges": [[2, 14]]},
        ],
        "measured_evidence": "FoundryPaths.discover (paths.py:13-23) selects JARVIS_HOME, else HERMES_HOME/foundry, else LOCALAPPDATA/hermes/foundry with no baseline-commit identity stored or validated. Supervisor generates a self-assigned instance_id (supervisor/service.py:28-32). supervisor_state trust_state is written from event payload with default 'LAB' (projections.py:1721-1726), so any caller can label itself. config/foundry.yaml:14 declares LAB while PROJECT_STATE.md:94 declares TRUSTED_AUTONOMOUS; live truth at R0 was LAB (R0-F018). No code binds the loaded source tree hash to the ledger or supervisor_state.",
        "affected_invariants": ["release identity", "truthful state", "exact artifact binding"],
        "disposition": "REWRITE",
        "r0_cross_ref": "R0-F018",
    },
    {
        "id": "R2-F009",
        "severity": "S1",
        "subsystem": "release integrity",
        "title": "Live scheduled task imports mutable editable main source",
        "paths": [
            {"path": ".venv/Lib/site-packages/__editable__.jarvis_foundry-0.1.0.pth", "line_ranges": [[1, 1]]},
            {"path": "src/jarvis_foundry/installer.py", "line_ranges": [[73, 74], [116, 124]]},
        ],
        "measured_evidence": "The main .venv editable .pth contains exactly 'C:\\Users\\b\\Documents\\ChatGPT\\Autonomous AI powered by Hoowen\\JARVIS-Foundry\\src', so the scheduled process imports the mutable main checkout. installer.py:73-74 schedules 'python -m jarvis_foundry.supervisor.run --home \"<home>\"' and installer.py:124 resolves python_exe from sys.executable; no immutable release pointer or hash-bound deployment exists. R0-F005 observed the live process started before commit 93a3ddc and loaded this editable source.",
        "affected_invariants": ["release integrity", "rollback", "byte-exact deployment"],
        "disposition": "REWRITE",
        "r0_cross_ref": "R0-F005",
    },
    {
        "id": "R2-F010",
        "severity": "S2",
        "subsystem": "canonical truth / planning",
        "title": "Committed planning and status docs contradict live source truth",
        "paths": [
            {"path": "PROJECT_STATE.md", "line_ranges": [[13, 37], [94, 94], [135, 150]]},
            {"path": "docs/evidence/V7_S13_FINAL_GATE.md", "line_ranges": [[4, 4], [93, 93], [115, 147]]},
            {"path": "config/foundry.yaml", "line_ranges": [[2, 2], [14, 14]]},
            {"path": "docs/planning/V5_READ_FIRST.md", "line_ranges": [[100, 114]]},
        ],
        "measured_evidence": "PROJECT_STATE.md:13-37 reports an active 12-hour duty window, PROJECT_STATE.md:94 reports trust_state TRUSTED_AUTONOMOUS, and PROJECT_STATE.md:135-150 reports operational gate and waiver completion; config/foundry.yaml:2 has foundry.enabled:false and :14 trust_state LAB. V7_S13_FINAL_GATE.md:4/93/115-147 declares 13/13 slices PASS, suite 985 EXIT=0, and perpetual operation, but the preserved verifier audited HEAD c589d3b (R0-F001) and the live ledger has zero scheduler/cycle/action/briefing rows (R0 claim register). V5_READ_FIRST.md:100-114 still describes external/financial reconciliation authority superseded by the Post-V7 directive.",
        "affected_invariants": ["canonical truth", "owner comprehension", "scope containment"],
        "disposition": "REWRITE",
        "r0_cross_ref": "R0-F001/F003/F018",
    },
    {
        "id": "R2-F011",
        "severity": "S1",
        "subsystem": "trusted kernel / ledger integrity",
        "title": "Ledger schema has no foreign keys, triggers, or event-header hash chain",
        "paths": [
            {"path": "src/jarvis_foundry/migrations.py", "line_ranges": [[13, 31]]},
            {"path": "src/jarvis_foundry/events.py", "line_ranges": [[73, 81]]},
            {"path": "src/jarvis_foundry/database.py", "line_ranges": [[86, 115]]},
        ],
        "measured_evidence": "events table (migrations.py:13-31) stores payload_hash (CHECK length 64) but no previous_hash/header chain; source-wide scan finds zero FOREIGN KEY, REFERENCES, or CREATE TRIGGER statements; migrations.py contains only PRIMARY KEY and CHECK constraints. Event.validate (events.py:73-81) checks only non-empty id/type with '.' and payload hash. database.py:86-115 inserts rows without chaining or deletion protection. PRAGMA foreign_keys=ON (database.py:28) cannot enforce constraints that do not exist.",
        "affected_invariants": ["history integrity", "referential integrity", "append-only immutability"],
        "disposition": "REWRITE",
        "r0_cross_ref": "R0-F015",
    },
    {
        "id": "R2-F012",
        "severity": "S1",
        "subsystem": "trusted kernel / replay",
        "title": "41 created event types have no projection, so replay silently loses their state",
        "paths": [
            {"path": "src/jarvis_foundry/projections.py", "line_ranges": [[58, 1726]]},
            {"path": "src/jarvis_foundry/database.py", "line_ranges": [[130, 141]]},
        ],
        "measured_evidence": "Source creates 113 distinct event_type values; projections.py handles 95 exact types plus 3 startswith families (consolidation., emergency_stop., supervisor.). 41 created types are unhandled, including scheduler.enabled/disabled/tick/item_fired/item_registered/item_deferred/item_missed/suspended, portfolio.* (8), capability.* (4), connector.* (4), brokerage.* (2), shadow.* (3), evaluation.* (2), gate.decision, owner.command_recorded, mission.outcome.recorded, action.decided, briefing.recorded, candidate_root.registered, correlation.* (2), effect.reconcile_not_tested, evolution.mode_changed, pipeline.verification.recorded. rebuild_projections (database.py:130-141) replays and apply_event falls through silently, making rebuild non-equivalent to incremental appends.",
        "affected_invariants": ["replay determinism", "state equivalence", "fail closed"],
        "disposition": "REWRITE",
        "r0_cross_ref": "R0-F015/F016",
    },
    {
        "id": "R2-F013",
        "severity": "S1",
        "subsystem": "security / target identity",
        "title": "Credential flow stops at identifiers and caller-supplied performer/worktree is not bound",
        "paths": [
            {"path": "src/jarvis_foundry/control/capability_broker.py", "line_ranges": [[281, 318]]},
            {"path": "src/jarvis_foundry/command_center.py", "line_ranges": [[548, 552]]},
            {"path": "src/jarvis_foundry/worker_context.py", "line_ranges": [[78, 118]]},
            {"path": "src/jarvis_foundry/mission/loop.py", "line_ranges": [[403, 408]]},
            {"path": "src/jarvis_foundry/roles/adapter.py", "line_ranges": [[230, 246]]},
        ],
        "measured_evidence": "resolve_provider_auth (control/capability_broker.py:281-318) derives an auth_id from provider+mission+run and issues a capability when a credentials system row declares READY; no code path retrieves or hands a credential VALUE to an adapter, and no READY credentials system exists. CommandCenterServer.configure_owner (command_center.py:548-552) keeps the owner secret in process memory and is never hosted. deliver_worker_context accepts a caller-supplied worker string (worker_context.py:78-118). MissionLoop commit_after_pass uses resource['resolved_root'] from an event-projected row (mission/loop.py:403-408) without cryptographically binding the performer or canonical target. roles/adapter.py:230-246 passes os.environ wholesale to subprocesses.",
        "affected_invariants": ["credential confinement", "canonical target identity", "capability scope", "no secret in context"],
        "disposition": "REWRITE",
        "r0_cross_ref": "R0-F008",
    },
    {
        "id": "R2-F014",
        "severity": "S1",
        "subsystem": "operational reachability",
        "title": "Installed runtime path dead-ends at Supervisor heartbeat watchdog",
        "paths": [
            {"path": "pyproject.toml", "line_ranges": [[23, 23]]},
            {"path": "src/jarvis_foundry/cli/main.py", "line_ranges": [[48, 113]]},
            {"path": "src/jarvis_foundry/installer.py", "line_ranges": [[73, 74]]},
            {"path": "src/jarvis_foundry/supervisor/service.py", "line_ranges": [[34, 99]]},
            {"path": "src/jarvis_foundry/supervisor/run.py", "line_ranges": [[121, 186]]},
        ],
        "measured_evidence": "Only console entry point is jarvis -> cli.main:main (pyproject.toml:23). cli/main.py:48-113 exposes init/status/doctor/stop/resume/events/install/uninstall/activate/deactivate only. The scheduled task runs python -m jarvis_foundry.supervisor.run (installer.py:73-74); Supervisor.start (service.py:34-65) initializes, reconciles, reaps workers, acquires lease, and appends supervisor.started; heartbeat (service.py:67-99) reaps, checks STOP, and appends supervisor.heartbeat; run.py:121-186 loops heartbeat only. No scheduler, portfolio, mission, effect, command-center, briefing, voice, knowledge, vault, research, or evolution call exists downstream of the entry points.",
        "affected_invariants": ["persistent autonomy", "purpose journeys", "live operational truth"],
        "disposition": "REWRITE",
        "r0_cross_ref": "R0-F004",
    },
    {
        "id": "R2-F015",
        "severity": "S3",
        "subsystem": "repository-wide dead code",
        "title": "Five defined source symbols have zero references anywhere",
        "paths": [
            {"path": "src/jarvis_foundry/roles/dispatcher.py", "line_ranges": [[40, 42]]},
            {"path": "src/jarvis_foundry/connectors/fakes.py", "line_ranges": [[18, 60]]},
            {"path": "src/jarvis_foundry/live_ingestion.py", "line_ranges": [[58, 64]]},
            {"path": "src/jarvis_foundry/integrated_rollback.py", "line_ranges": [[42, 50]]},
            {"path": "src/jarvis_foundry/roles/adapter.py", "line_ranges": [[356, 364]]},
        ],
        "measured_evidence": "AST cross-reference scan over all src and tests: DispatchError (roles/dispatcher.py:40), FakeConnector (connectors/fakes.py:18), _last_file_hash (live_ingestion.py:58), _latest (integrated_rollback.py:42), and make_hermes_adapter (roles/adapter.py:356) each occur only at their definition site (src_refs_after_def=0, test_refs=0).",
        "affected_invariants": ["maintainability", "honest capability inventory"],
        "disposition": "DELETE",
        "r0_cross_ref": None,
    },
    {
        "id": "R2-F016",
        "severity": "S2",
        "subsystem": "owner experience",
        "title": "Command Center converts database failures into empty arrays",
        "paths": [
            {"path": "src/jarvis_foundry/command_center.py", "line_ranges": [[91, 99], [153, 154]]},
        ],
        "measured_evidence": "_safe_table (command_center.py:91-99) catches Exception and returns [] for every table read, so missing/corrupt tables or locked DB render as empty state; build_snapshot (100-233) uses it for briefings, voice, candidates, and other tables (e.g., :153-154).",
        "affected_invariants": ["truthful health", "no silent failure"],
        "disposition": "REFACTOR",
        "r0_cross_ref": "R0-F017",
    },
    {
        "id": "R2-F017",
        "severity": "S2",
        "subsystem": "runtime executive / scheduler",
        "title": "Scheduler is disabled by default and its tick only emits events",
        "paths": [
            {"path": "src/jarvis_foundry/evolution/scheduler.py", "line_ranges": [[1, 5], [117, 124], [156, 246]]},
        ],
        "measured_evidence": "scheduler.py:1-5 documents DISABLED by default (observe-only); enable_scheduler (117-124) requires controller and appends scheduler.enabled; tick_scheduler (156-246) raises when disabled, then appends scheduler.tick and item_fired/deferred/missed events with fired=[] and no dispatch callback or mission trigger. No source caller exists.",
        "affected_invariants": ["persistent autonomy", "scheduled execution", "no silent no-op"],
        "disposition": "REWRITE",
        "r0_cross_ref": None,
    },
    {
        "id": "R2-F018",
        "severity": "S3",
        "subsystem": "runtime executive / portfolio",
        "title": "Portfolio decision model is unreachable and its events are unprojected",
        "paths": [
            {"path": "src/jarvis_foundry/evolution/portfolio.py", "line_ranges": [[1, 271], [170, 199]]},
        ],
        "measured_evidence": "portfolio.py defines register_project:98-129, score_projects:161-167, select_projects:170-199, pause/resume/reprioritize, record_unknown:248-258, record_conflict:261-271; select_projects has no runtime caller. All portfolio.* event types (project_registered, status_changed, reprioritized, decision, deferred, unknown, conflict) are in the 41 unhandled projection types.",
        "affected_invariants": ["purposeful next-action selection", "state visibility"],
        "disposition": "REWRITE",
        "r0_cross_ref": None,
    },
    {
        "id": "R2-F019",
        "severity": "S1",
        "subsystem": "evolution / release integrity",
        "title": "Evolution cycle records manufactured hashes and a fixed evidence path",
        "paths": [
            {"path": "src/jarvis_foundry/evolution/cycle.py", "line_ranges": [[135, 136], [206, 209]]},
        ],
        "measured_evidence": "cycle.py:135-136 builds the baseline with source_commit='a'*40 and artifact_hashes={'baseline.txt': 'a'*64}; cycle.py:206-209 records evidence_hash='c'*64 and evidence_refs=['var/evidence/v6-s11/cycle-verdict.txt'], a fixed path. These manufactured values make promotion/release proof untrustworthy.",
        "affected_invariants": ["release integrity", "independent evidence", "no self-certification"],
        "disposition": "REWRITE",
        "r0_cross_ref": "R0-F009",
    },
    {
        "id": "R2-F020",
        "severity": "S2",
        "subsystem": "owner experience / adapters",
        "title": "Voice and briefing delivery remain fixture/local-sink only",
        "paths": [
            {"path": "src/jarvis_foundry/voice.py", "line_ranges": [[40, 59], [61, 150]]},
            {"path": "src/jarvis_foundry/briefings.py", "line_ranges": [[75, 79]]},
            {"path": "src/jarvis_foundry/reporting/briefing.py", "line_ranges": [[62, 75]]},
        ],
        "measured_evidence": "VoiceGateway:61-150 uses FixtureSTT/FixtureTTS only and has no runtime caller; briefing planner generate_briefing:62-75 has no runtime caller; the only delivery implementation writes briefing.delivered with sink 'local-test-sink' (briefings.py:75-79). No provider receipt, owner channel, or English/Albanian integrated path exists.",
        "affected_invariants": ["owner experience", "real after-state proof"],
        "disposition": "NOT_IMPLEMENTED",
        "r0_cross_ref": "R0-F012",
    },
    {
        "id": "R2-F021",
        "severity": "S2",
        "subsystem": "types / schemas",
        "title": "Event validation is minimal and JSON schemas are not enforced at runtime",
        "paths": [
            {"path": "src/jarvis_foundry/events.py", "line_ranges": [[44, 68], [73, 81]]},
            {"path": "schemas/event.schema.json", "line_ranges": [[1, 20]]},
            {"path": "schemas/mission.schema.json", "line_ranges": [[1, 20]]},
            {"path": "schemas/action-envelope.schema.json", "line_ranges": [[1, 20]]},
        ],
        "measured_evidence": "Event.create (events.py:44-68) always stamps schema_version=1 and validate (73-81) only requires non-empty id/type containing '.' plus payload hash. Seven schema files exist (schemas/*.json) but no source module references them or uses a schema validator; pyproject dependencies are empty.",
        "affected_invariants": ["typed contracts", "schema enforcement", "payload compatibility"],
        "disposition": "REFACTOR",
        "r0_cross_ref": None,
    },
    {
        "id": "R2-F022",
        "severity": "S3",
        "subsystem": "reliability / retries",
        "title": "Retry/timeout/idempotency controls exist but are partial and never exercised on a live path",
        "paths": [
            {"path": "src/jarvis_foundry/database.py", "line_ranges": [[28, 31]]},
            {"path": "src/jarvis_foundry/supervisor/run.py", "line_ranges": [[72, 89]]},
            {"path": "src/jarvis_foundry/effects.py", "line_ranges": [[181, 279]]},
            {"path": "src/jarvis_foundry/migrations.py", "line_ranges": [[574, 585]]},
        ],
        "measured_evidence": "Positives: busy_timeout=10000 (database.py:28-31); heartbeat lock retry budget 15.5s (run.py:72-89); execute_effect exactly-once semantics with crash reap/freeze and terminal-state refusal (effects.py:181-279); system_actions UNIQUE(system_id,target_ref,payload_hash) (migrations.py:574-585). Gaps: scheduler has no dispatch timeout/backpressure; CommandCenterServer has no request budget; no live path exercises any of these controls; no provider timeout or cost receipt exists.",
        "affected_invariants": ["exactly-once effects", "bounded retries", "crash containment"],
        "disposition": "REFACTOR",
        "r0_cross_ref": None,
    },
    {
        "id": "R2-F023",
        "severity": "S2",
        "subsystem": "migrations / replay / reconcile",
        "title": "Migration history is additive-only with destructive table rebuilds and no downgrade unit",
        "paths": [
            {"path": "src/jarvis_foundry/migrations.py", "line_ranges": [[8, 8], [498, 520], [573, 585]]},
            {"path": "src/jarvis_foundry/reconcile.py", "line_ranges": [[121, 220]]},
            {"path": "src/jarvis_foundry/database.py", "line_ranges": [[130, 141]]},
        ],
        "measured_evidence": "SCHEMA_VERSION=32 with MIGRATIONS dict 1..32; CHECK-constraint changes are applied by CREATE-copy-DROP-RENAME rebuilds (e.g., candidates v17 at migrations.py:498-520, system_actions v20 at 573-585); no downgrade migration or interrupted-migration unit exists. reconcile (reconcile.py:121-220) rebuilds projections plus runtime state; rebuild_projections (database.py:130-141) replays events and silently skips the 41 unhandled event types.",
        "affected_invariants": ["history preservation", "replay equivalence", "restore reproducibility"],
        "disposition": "REFACTOR",
        "r0_cross_ref": "R0-F015",
    },
    {
        "id": "R2-F024",
        "severity": "S3",
        "subsystem": "test architecture",
        "title": "Tests are tmp_path-heavy but touch environment, subprocesses, sleeps, and real Task Scheduler",
        "paths": [
            {"path": "tests/foundry/conftest.py", "line_ranges": [[5, 9]]},
            {"path": "tests/foundry/test_installer.py", "line_ranges": [[4, 43], [306, 354]]},
            {"path": "tests/foundry/test_role_gateway_kill.py", "line_ranges": [[13, 22], [269, 302]]},
            {"path": "tests/foundry/test_f5_network_outage.py", "line_ranges": [[20, 22]]},
        ],
        "measured_evidence": "920 test functions across 88 files; 84 files use tmp_path; 28 sleep calls; 68 subprocess hits; 44 env accesses; 19 network-import hits; 7 files use monkeypatch/capsys; 15 parametrize decorations; 0 fixed-port hits. conftest.py:5-9 inserts src into sys.path. test_installer.py creates/deletes real Windows scheduled tasks with staging names (4-43) and hard-kills spawned daemons (306-354); test_role_gateway_kill drives a staged gateway via HERMES_HOME (13-22); R1-F003/F004 documented host-suite failures from subprocess import identity and in-repo basetemp.",
        "affected_invariants": ["reproducible tests", "no host mutation", "order independence"],
        "disposition": "KEEP",
        "r0_cross_ref": "R1-F003 / R1-F004",
    },
    {
        "id": "R2-F025",
        "severity": "S3",
        "subsystem": "intelligence / monitoring",
        "title": "Monitor polling consumes supplied items and never reads a real source",
        "paths": [
            {"path": "src/jarvis_foundry/discovery_monitors.py", "line_ranges": [[37, 80]]},
            {"path": "src/jarvis_foundry/v3_loop.py", "line_ranges": [[250, 251]]},
        ],
        "measured_evidence": "poll_monitor (discovery_monitors.py:37-80) requires caller-supplied poll_items and records them; v3_loop.py:250 calls it only inside the unreachable run_one_loop. Live enabled monitors were stale since 2026-08-13 at R0 (R0-F006).",
        "affected_invariants": ["freshness", "real monitoring", "no fabricated observation"],
        "disposition": "REWRITE",
        "r0_cross_ref": "R0-F006",
    },
    {
        "id": "R2-F026",
        "severity": "S3",
        "subsystem": "memory / knowledge",
        "title": "Knowledge ingestion, vault sync, and research detection are unreachable",
        "paths": [
            {"path": "src/jarvis_foundry/knowledge_ingestion.py", "line_ranges": [[44, 99]]},
            {"path": "src/jarvis_foundry/vault_indexer.py", "line_ranges": [[51, 110]]},
            {"path": "src/jarvis_foundry/research_queue.py", "line_ranges": [[48, 119]]},
            {"path": "src/jarvis_foundry/v3_loop.py", "line_ranges": [[208, 209]]},
        ],
        "measured_evidence": "ingest_documents (knowledge_ingestion.py:44-99), sync_vault (vault_indexer.py:51-110), and detect_research_items (research_queue.py:48-119) have no runtime callers; detect_research_items is only called from the unreachable v3_loop.run_one_loop (v3_loop.py:208). Live knowledge/vault data was stale at R0.",
        "affected_invariants": ["memory freshness", "continuous knowledge path"],
        "disposition": "REFACTOR",
        "r0_cross_ref": None,
    },
    {
        "id": "R2-F027",
        "severity": "S3",
        "subsystem": "worker gateway",
        "title": "Hermes adapter and worker spawner are not wired to runtime",
        "paths": [
            {"path": "src/jarvis_foundry/roles/adapter.py", "line_ranges": [[216, 307], [356, 364]]},
            {"path": "src/jarvis_foundry/supervisor/worker.py", "line_ranges": [[46, 66]]},
        ],
        "measured_evidence": "HermesOneshotAdapter.dispatch (roles/adapter.py:216-307) exists and make_hermes_adapter (356-364) is dead (R2-F015); WorkerController.spawn (supervisor/worker.py:46-66) has no runtime caller. The isolated Hermes execution substrate is not part of the live path.",
        "affected_invariants": ["worker capability", "isolated execution"],
        "disposition": "REFACTOR",
        "r0_cross_ref": None,
    },
    {
        "id": "R2-F028",
        "severity": "S2",
        "subsystem": "capability truth",
        "title": "Capability readiness is fragmented across three registries with inconsistent live labels",
        "paths": [
            {"path": "src/jarvis_foundry/connectors/activation.py", "line_ranges": [[180, 196]]},
            {"path": "src/jarvis_foundry/capabilities/registry.py", "line_ranges": [[143, 148]]},
            {"path": "src/jarvis_foundry/systems.py", "line_ranges": [[53, 56]]},
        ],
        "measured_evidence": "connectors/activation.py:180-196 records READY/NOT_READY/BLOCKED/NOT_TESTED activation rows; capabilities/registry.py:143-148 builds a reconciliation matrix; systems.py:53-56 registers probes. R0 observed the local filesystem connector labeled READY while its system projection said NOT_TESTED and no credential/capability delivery exists.",
        "affected_invariants": ["truthful capability state", "single capability catalog"],
        "disposition": "REWRITE",
        "r0_cross_ref": None,
    },
    {
        "id": "R2-F029",
        "severity": "S2",
        "subsystem": "policy / financial scope",
        "title": "Historical financial action families remain in source and planning",
        "paths": [
            {"path": "src/jarvis_foundry/gates/owner_families.py", "line_ranges": [[46, 52]]},
            {"path": "config/foundry.yaml", "line_ranges": [[34, 38]]},
            {"path": "docs/planning/V5_READ_FIRST.md", "line_ranges": [[100, 114]]},
        ],
        "measured_evidence": "gates/owner_families.py:46 lists effectful classes including transfer and pay_invoice; config/foundry.yaml:34-38 still carries purchases/git readiness flags; V5_READ_FIRST.md:100-114 retains external/financial action language. The authenticated Post-V7 directive supersedes this and requires deterministic denial of money movement (R0-F014).",
        "affected_invariants": ["scope containment", "deterministic financial denial"],
        "disposition": "DELETE",
        "r0_cross_ref": "R0-F014",
    },
    {
        "id": "R2-F030",
        "severity": "S3",
        "subsystem": "schemas / contracts",
        "title": "Seven JSON contract schemas are unused by runtime code",
        "paths": [
            {"path": "schemas/event.schema.json", "line_ranges": [[1, 20]]},
            {"path": "schemas/action-envelope.schema.json", "line_ranges": [[1, 20]]},
            {"path": "schemas/mission.schema.json", "line_ranges": [[1, 20]]},
            {"path": "schemas/start-record.schema.json", "line_ranges": [[1, 20]]},
            {"path": "schemas/resource-record.schema.json", "line_ranges": [[1, 20]]},
            {"path": "schemas/epistemic-record.schema.json", "line_ranges": [[1, 20]]},
            {"path": "schemas/verification-verdict.schema.json", "line_ranges": [[1, 20]]},
        ],
        "measured_evidence": "Source-wide scan finds no import/read of any schemas/*.json file and no jsonschema usage; pyproject.toml declares no validation dependency. The contracts exist as documentation only and cannot gate event or action payloads.",
        "affected_invariants": ["typed contracts", "schema enforcement"],
        "disposition": "REFACTOR",
        "r0_cross_ref": None,
    },
]


def main() -> int:
    if len(sys.argv) != 4:
        print("usage: python build_r2_evidence.py <checkout> <measure.json> <remaster-root>", file=sys.stderr)
        return 2
    checkout = Path(sys.argv[1]).resolve()
    measure_path = Path(sys.argv[2]).resolve()
    remaster = Path(sys.argv[3]).resolve()
    measure = json.loads(measure_path.read_text(encoding="utf-8"))
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    tracked = subprocess.run(
        ["git", "-C", str(checkout), "ls-tree", "-r", "--name-only", "HEAD"],
        check=True, capture_output=True, text=True,
    ).stdout.splitlines()
    by_dir: dict[str, int] = {}
    for name in tracked:
        top = name.split("/")[0]
        by_dir[top] = by_dir.get(top, 0) + 1

    inventory: list[dict] = []
    for mod in measure["modules"] + measure["tests"]:
        disposition, basis = classify(mod["path"])
        kind = "test_support" if mod.get("test_support") else (
            "test" if "/tests/" in mod["path"] else "source"
        )
        inventory.append(
            {
                "path": mod["path"],
                "kind": kind,
                "bytes": mod["bytes"],
                "total_lines": mod["total_lines"],
                "code_lines": mod["code_lines"],
                "top_level": mod["top_level"],
                "functions_count": len(mod["functions"]),
                "typing": module_typing(mod),
                "except_handlers": mod["except_handlers"],
                "markers": mod["markers"],
                "test_functions": mod["test_functions"],
                "disposition": disposition,
                "disposition_basis": basis,
            }
        )

    src_mods = [m for m in measure["modules"]]
    test_mods = [m for m in measure["tests"]]
    sloc_by_dir: dict[str, dict] = {}
    for mod in src_mods:
        rel = mod["path"].replace(checkout.as_posix() + "/", "")
        parts = rel.split("/")
        key = "/".join(parts[:3]) if len(parts) > 3 else "/".join(parts[:2])
        entry = sloc_by_dir.setdefault(key, {"files": 0, "total_lines": 0, "code_lines": 0})
        entry["files"] += 1
        entry["total_lines"] += mod["total_lines"]
        entry["code_lines"] += mod["code_lines"]
    for mod in test_mods:
        rel = mod["path"].replace(checkout.as_posix() + "/", "")
        parts = rel.split("/")
        key = "/".join(parts[:2])
        entry = sloc_by_dir.setdefault(key, {"files": 0, "total_lines": 0, "code_lines": 0})
        entry["files"] += 1
        entry["total_lines"] += mod["total_lines"]
        entry["code_lines"] += mod["code_lines"]

    funcs = []
    for mod in src_mods + test_mods:
        funcs.extend(mod["functions"])
    sized = sorted(funcs, key=lambda f: f["lines"], reverse=True)
    top20 = [
        {
            "lines": f["lines"],
            "module": f.get("module", mod_path_of(f, measure)),
            "name": f["name"],
            "lineno": f["lineno"],
            "end_lineno": f["end_lineno"],
        }
        for f in sized[:20]
    ]

    metrics = {
        "schema": SCHEMA_METRICS,
        "baseline_commit": BASELINE_COMMIT,
        "baseline_tree": BASELINE_TREE,
        "generated_at": now,
        "generated_by": GENERATED_BY,
        "repo_scale": {
            "tracked_files": len(tracked),
            "tracked_by_top_dir": by_dir,
            "src_modules": len(src_mods),
            "test_files": len(test_mods),
        },
        "sloc_by_dir": sloc_by_dir,
        "src_totals": {
            "total_lines": sum(m["total_lines"] for m in src_mods),
            "code_lines": sum(m["code_lines"] for m in src_mods),
        },
        "test_totals": {
            "total_lines": sum(m["total_lines"] for m in test_mods),
            "code_lines": sum(m["code_lines"] for m in test_mods),
        },
        "function_metrics": measure["metrics"]["function_size_buckets"],
        "top_20_functions_by_lines": top20,
        "exception_handlers": measure["metrics"]["except_handlers_by_kind"],
        "exception_handlers_total": measure["metrics"]["except_handlers_total"],
        "typing_metrics": measure["metrics"]["typing"],
        "marker_counts": {"todo_fixme_xxx_hack": measure["metrics"]["markers_total"]},
        "test_metrics": measure["metrics"]["test_metrics"],
        "event_coverage": {
            "created_event_types": 113,
            "exact_handled": 95,
            "prefix_families": ["consolidation.", "emergency_stop.", "supervisor."],
            "unhandled_count": 41,
            "unhandled_types": [
                "action.decided", "briefing.recorded", "brokerage.decision",
                "brokerage.waiting", "candidate_root.registered",
                "capability.issued", "capability.registered", "capability.revoked",
                "capability.used", "connector.activated", "connector.activation_blocked",
                "connector.discovered", "correlation.entity_ref_added",
                "correlation.entity_registered", "correlation.manifest_generated",
                "effect.reconcile_not_tested", "evaluation.comparison_recorded",
                "evaluation.workload_registered", "evolution.mode_changed",
                "gate.decision", "mission.outcome.recorded", "owner.command_recorded",
                "pipeline.verification.recorded", "portfolio.conflict",
                "portfolio.decision", "portfolio.deferred",
                "portfolio.project_registered", "portfolio.reprioritized",
                "portfolio.status_changed", "portfolio.unknown", "scheduler.disabled",
                "scheduler.enabled", "scheduler.item_deferred", "scheduler.item_fired",
                "scheduler.item_missed", "scheduler.item_registered",
                "scheduler.suspended", "scheduler.tick",
                "shadow.blocked_connector_attempted", "shadow.decision_proposed",
                "shadow.live_effect_attempted",
            ],
        },
        "migrations": {
            "schema_version": 32,
            "migration_count": 32,
            "foreign_keys": 0,
            "triggers": 0,
            "event_header_hash_chain": False,
        },
        "quality_gates": {
            "ci_configs": [],
            "mypy_config": False,
            "ruff_config": False,
            "coverage_config": False,
            "mutmut_config": False,
            "pytest_addopts": "-q",
        },
        "dead_code_candidates": measure["metrics"]["dead_code_candidates"],
        "live_source_drift": {
            "editable_pth": "C:/Users/b/Documents/ChatGPT/Autonomous AI powered by Hoowen/JARVIS-Foundry/src",
            "scheduled_command": "python -m jarvis_foundry.supervisor.run --home <foundry_home>",
            "console_script": "jarvis = jarvis_foundry.cli.main:main",
        },
        "unreachable_executive_entries": [
            "evolution/scheduler.py tick_scheduler",
            "evolution/portfolio.py select_projects",
            "v3_loop.py run_one_loop",
            "mission/loop.py MissionLoop.run_step",
            "execution/pipeline.py run_mission",
            "execution/pipeline.py run_candidate",
            "evolution/cycle.py run_cycle",
            "evolution/promote.py promote_release",
            "evolution/shadow.py shadow_run",
            "evolution/lab.py run_evaluation",
            "vault_indexer.py sync_vault",
            "knowledge_ingestion.py ingest_documents",
            "research_queue.py detect_research_items",
            "reporting/briefing.py generate_briefing",
            "voice.py VoiceGateway",
            "command_center.py CommandCenterServer",
            "supervisor/evolution_control.py EvolutionControl",
        ],
    }

    findings_doc = {
        "schema": SCHEMA_FINDINGS,
        "baseline_commit": BASELINE_COMMIT,
        "baseline_tree": BASELINE_TREE,
        "generated_at": now,
        "generated_by": GENERATED_BY,
        "method": "read-only git/AST/source scans; no repo mutation",
        "findings": FINDINGS,
        "totals": {
            "total": len(FINDINGS),
            "S1": sum(1 for f in FINDINGS if f["severity"] == "S1"),
            "S2": sum(1 for f in FINDINGS if f["severity"] == "S2"),
            "S3": sum(1 for f in FINDINGS if f["severity"] == "S3"),
        },
    }

    inventory_doc = {
        "schema": SCHEMA_INVENTORY,
        "baseline_commit": BASELINE_COMMIT,
        "baseline_tree": BASELINE_TREE,
        "generated_at": now,
        "generated_by": GENERATED_BY,
        "primary_repo": checkout.as_posix(),
        "modules": inventory,
        "counts": {
            "source_modules": len(src_mods),
            "test_files": len(test_mods),
            "test_support_files": sum(1 for m in test_mods if m.get("test_support")),
            "total": len(inventory),
        },
    }

    out_dir = remaster / "docs" / "remaster" / "evidence" / "r2"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "R2_FORENSIC_FINDINGS.json").write_text(
        json.dumps(findings_doc, indent=2) + "\n", encoding="utf-8"
    )
    (out_dir / "R2_MODULE_INVENTORY.json").write_text(
        json.dumps(inventory_doc, indent=2) + "\n", encoding="utf-8"
    )
    (out_dir / "R2_METRICS.json").write_text(
        json.dumps(metrics, indent=2) + "\n", encoding="utf-8"
    )

    s1 = findings_doc["totals"]["S1"]
    s2 = findings_doc["totals"]["S2"]
    s3 = findings_doc["totals"]["S3"]
    summary = f"""# R2 Forensic Summary - Post-V7 JARVIS Remaster

Baseline: `{BASELINE_COMMIT}` (tree `{BASELINE_TREE}`), main checkout, read-only audit.
Scope: {len(src_mods)} source modules, {len(test_mods)} test files, {len(tracked)} tracked files.
Findings: {len(FINDINGS)} total ({s1} S1, {s2} S2, {s3} S3). Status: forensic inventory only; no repair performed.

## Repository shape

- Source: {sum(m['total_lines'] for m in src_mods)} lines ({sum(m['code_lines'] for m in src_mods)} code) in {len(src_mods)} modules.
- Tests: {sum(m['total_lines'] for m in test_mods)} lines ({sum(m['code_lines'] for m in test_mods)} code) in {len(test_mods)} files; 920 test functions.
- Functions: 1,860 total; 18 functions are 81-160 lines and 6 exceed 160 lines. The largest is `projections.apply_event` at 1,669 lines.
- Exception handling: 123 handlers; 29 `except Exception`, 9 `except BaseException`, 0 bare `except`.
- Typing: 1,440 of 1,701 parameterized functions are fully annotated; 401 are untyped; no mypy/lint/coverage/mutation gate exists.
- Markers: 0 TODO/FIXME/XXX/HACK occurrences.

## Top structural risks

1. **Unreachable executive** (`R2-F014`, S1): the installed path ends at the Supervisor heartbeat watchdog
   (`cli/main.py:48-113`, `installer.py:73-74`, `supervisor/service.py:34-99`, `supervisor/run.py:121-186`).
2. **Parallel authority/state writers** (`R2-F007`, S1): three mission/execution paths, two promotion controllers
   (`evolution/registry.py:214-277` vs `evolution/promote.py:168-222`), and three capability registries.
3. **Replay loses state** (`R2-F012`, S1): 41 of 113 created event types have no projection, and
   `rebuild_projections` (`database.py:130-141`) silently skips them.
4. **No immutable ledger** (`R2-F011`, S1): zero foreign keys/triggers and no event-header hash chain
   (`migrations.py:13-31`, `events.py:73-81`).
5. **Editable live source** (`R2-F009`, S1): the scheduled task imports mutable main `src` through the editable
   `.pth`; no immutable release pointer exists.

## Mandated resolutions

- Projection dispatcher: `src/jarvis_foundry/projections.py` is 1,740 lines; `apply_event` spans 58-1726;
  unknown events fall through without error (`R2-F001`).
- Top-20 functions by size: recorded in `R2_METRICS.json` and `R2_FORENSIC_FINDINGS.json` (`R2-F002`).
- Broad exception handling: all sites with paths/lines in `R2-F003`.
- Quality gates: absent; `pyproject.toml` has pytest only and no CI config exists (`R2-F004`).
- Fixture-as-live surface: voice fixture providers, `local-test-sink` delivery, and dead `FakeConnector` (`R2-F005`, `R2-F020`).
- Never-invoked code: 17 executive entry points with zero runtime callers; 343 test-only references (`R2-F006`).
- Duplicated transitions: mission, promotion, and capability authority enumerated (`R2-F007`).
- Runtime/live switch: env-ordered `FoundryPaths` with no identity pin; LAB-vs-TRUSTED contradictions (`R2-F008`, `R2-F010`).
- Editable source drift: `.venv` `.pth` -> mutable main `src` (`R2-F009`).
- Planning contradictions: `PROJECT_STATE.md`/V7-S13 claims vs live LAB truth (`R2-F010`).
- State/schema/migration/replay: no FK/triggers/header chain; 32 additive migrations with destructive rebuilds (`R2-F011`, `R2-F023`).
- Secrets/target identity: credential flow ends at identifiers; caller-supplied worker/worktree not bound (`R2-F013`).
- Test independence: tmp_path-heavy but env/subprocess/sleep/Task-Scheduler-coupled; R1-F003/F004 regressions preserved (`R2-F024`).
- Operational reachability: every point where the live path dead-ends is listed in `R2-F006`/`R2-F014`.

## Disposition summary

The R0 disposition map was applied per module in `R2_MODULE_INVENTORY.json` (KEEP/REFACTOR/REWRITE/DELETE/
NOT_IMPLEMENTED/UNSAFE). Ledger, projections, mission/execution, capability, scheduler, portfolio, and evolution
release controllers are REWRITE/UNSAFE; watchdog STOP and control primitives are KEEP+HARDEN or REFACTOR; tests are
KEEP with reclassification; fixture adapters are NOT_IMPLEMENTED/BLOCKED_EXTERNAL.

## Outputs

- `docs/remaster/evidence/r2/R2_FORENSIC_FINDINGS.json`
- `docs/remaster/evidence/r2/R2_MODULE_INVENTORY.json`
- `docs/remaster/evidence/r2/R2_METRICS.json`
- `docs/remaster/evidence/r2/R2_SUMMARY.md`
"""
    (out_dir / "R2_SUMMARY.md").write_text(summary, encoding="utf-8")
    return 0


def mod_path_of(fn: dict, measure: dict) -> str:
    for mod in measure["modules"] + measure["tests"]:
        for f in mod["functions"]:
            if f is fn:
                return mod["path"]
    return ""


if __name__ == "__main__":
    raise SystemExit(main())
