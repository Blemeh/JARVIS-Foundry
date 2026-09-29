"""Portfolio work-discovery bridge: broad goal -> concrete candidates.

Covers the repair that connects the orphaned observe -> detect -> reject ->
feed chain to production: starvation trigger, bounded fail-closed discovery,
value-based selection, executable-contract synthesis, and lineage/duplication
safety. Uses real git checkouts + the real registry; only the GitHub network
edge is faked (reader/connector seams the bridge itself provides).
"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.discovery_sources import (
    SourceObservation,
    SourceReadResult,
    content_hash,
)
from jarvis_foundry.evolution.executive_portfolio import (
    PortfolioPolicy,
    configure_portfolio_policy,
    register_work_item,
    select_next_work,
)
from jarvis_foundry.executive.autonomy import step_has_executable_contract
from jarvis_foundry.executive.portfolio_bridge import (
    materialize_portfolio_selection,
)
from jarvis_foundry.mission.contracts import contract_rows
from jarvis_foundry.project_discovery import (
    discover_portfolio_opportunities,
    eligible_discovery_repositories,
)


BROAD_GOAL = (
    "Autonomously improve the quality, reliability, usefulness, and "
    "maintainability of the owner's active registered software projects."
)


@pytest.fixture()
def db(tmp_path: Path) -> FoundryDatabase:
    database = FoundryDatabase(tmp_path / "discovery-bridge.db")
    database.initialize()
    configure_portfolio_policy(database, PortfolioPolicy())
    return database


def _make_repo(root: Path, slug: str, *, with_ci: bool = False) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-b", "main"], cwd=root, check=True,
                   capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"],
                   cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=root,
                   check=True, capture_output=True)
    (root / "README.md").write_text("# test\n", encoding="utf-8")
    if with_ci:
        workflows = root / ".github" / "workflows"
        workflows.mkdir(parents=True)
        (workflows / "ci.yml").write_text("on: [push]\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True,
                   capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=root, check=True,
                   capture_output=True)
    subprocess.run(
        ["git", "remote", "add", "origin",
         f"https://github.com/{slug}.git"],
        cwd=root, check=True, capture_output=True)
    return root


def _register(db: FoundryDatabase, root: Path) -> dict:
    from jarvis_foundry.software_intelligence.project_identity import (
        ProjectIdentityResolver,
    )
    record = ProjectIdentityResolver(db).register_repository(root)
    row = db.row("resources", "resource_id", record["resource_id"])
    assert row is not None
    mapping = dict(row)
    # Normalize capabilities to the flat production shape
    # ({capability: state}) so contract validation behaves as live.
    flat = {"read_registered_files": "READY",
            "write_registered_worktree_files": "READY"}
    with db.connect() as conn:
        conn.execute(
            "UPDATE resources SET capabilities_json = ? WHERE resource_id = ?",
            (json.dumps(flat), mapping["resource_id"]))
        conn.commit()
    bindings_dir = Path(db.path).parent / "project_profiles"
    bindings_dir.mkdir(exist_ok=True)
    bindings = {}
    path = bindings_dir / "bindings.json"
    if path.exists():
        bindings = json.loads(path.read_text(encoding="utf-8"))
    bindings[str(mapping["resolved_root"]).replace("\\", "/").lower()] = "profile-test"
    path.write_text(json.dumps(bindings), encoding="utf-8")
    return mapping


def _obs(identity: str, payload: dict) -> SourceObservation:
    semantic = {k: v for k, v in payload.items() if k != "updated_at"}
    return SourceObservation(
        source_kind="github",
        source_identity=identity,
        source_ref=f"https://github.com/x/{identity}",
        payload=payload,
        content_hash=content_hash(semantic),
        candidate_on="never",
        raw_content_hash=content_hash(payload),
    )


def _read_result(slug: str, *, pushed_at: str, head: str,
                 open_issues: int = 0, with_release: bool = False,
                 with_failure: bool = False) -> SourceReadResult:
    observations = [
        _obs(f"github:{slug}:repository", {
            "kind": "repository", "repo": slug, "default_branch": "main",
            "archived": False, "visibility": "public",
            "open_issues_count": open_issues, "updated_at": pushed_at,
        }),
        _obs(f"github:{slug}:default-branch", {
            "kind": "default_branch_state", "repo": slug, "branch": "main",
            "head_sha": head, "head_message": "x",
            "committed_at": pushed_at,
        }),
    ]
    if open_issues:
        observations.append(_obs(f"github:{slug}:issue:1", {
            "kind": "issue", "repo": slug, "number": 1,
            "title": "completely unrelated docs question",
            "state": "open",
        }))
    if with_release:
        observations.append(_obs(f"github:{slug}:release:v1", {
            "kind": "release", "repo": slug, "tag": "v1",
            "published_at": pushed_at, "draft": False,
        }))
    if with_failure:
        observations.append(_obs(f"github:{slug}:workflow:9", {
            "kind": "workflow_run", "repo": slug, "run_id": 9,
            "name": "CI", "conclusion": "failure",
            "head_sha": head, "created_at": pushed_at,
        }))
    return SourceReadResult(status="READY",
                            observations=tuple(observations))


def _connector(*, pushed_at: str, open_issues: int = 0,
               runs: list[dict] | None = None):
    items = runs if runs is not None else []

    def read_api(action: str, endpoint: str):
        attempt = SimpleNamespace(outcome="COMPLETED", error_code=None)
        if "actions/runs" in endpoint:
            return attempt, {"total_count": len(items),
                             "workflow_runs": items}
        return attempt, {
            "pushed_at": pushed_at, "updated_at": pushed_at,
            "open_issues_count": open_issues, "default_branch": "main",
            "archived": False,
        }
    return SimpleNamespace(read_api=read_api)


def _reader_for(result: SourceReadResult):
    def reader(spec) -> SourceReadResult:
        assert "github" in spec.source_kind
        return result
    return reader


NOW = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)


# 1. Broad goals are NOT directly executable (invariant preserved).
def test_broad_owner_goal_is_not_directly_executable(db) -> None:
    from jarvis_foundry.executive.autonomy import ObjectiveRecord, PlanStep

    objective = ObjectiveRecord(
        objective_id="obj-broad", objective=BROAD_GOAL,
        provenance={"directive_id": "owner-test-goal"})
    broad = PlanStep(step_id="execute", objective_id="obj-broad",
                     description="execute the improvement goal")
    ok, missing = step_has_executable_contract(objective, broad)
    assert ok is False and missing
    # Contrast: an explicit non-generic contract IS executable.
    concrete = PlanStep(
        step_id="execute", objective_id="obj-broad",
        description="Add default-branch CI running pytest",
        output_contract=("Evt: ci workflow runs green (criterion 7)",))
    ok, _ = step_has_executable_contract(objective, concrete)
    assert ok is True


# 2. Starved portfolio + broad goal -> discovery feeds a concrete candidate.
def test_starvation_triggers_discovery_and_feeds(db, tmp_path) -> None:
    root = _make_repo(tmp_path / "repo", "Owner/Alpha")
    mapping = _register(db, root)
    pushed = "2026-09-29T10:00:00Z"
    report = discover_portfolio_opportunities(
        db, objective_id="owner-test-goal",
        reader=_reader_for(_read_result(
            "owner/alpha", pushed_at=pushed, head="a" * 40)),
        connector=_connector(pushed_at=pushed),
        now=NOW)
    assert len(report["repos"]) == 1
    entry = report["repos"][0]
    assert entry["outcome"] == "completed", entry
    assert any("MISSING_TEST" in key for key in entry["accepted"]), entry
    assert entry["fed"] and entry["fed"][0]["portfolio_item_id"]
    with db.connect(readonly=True) as conn:
        assert conn.execute("SELECT COUNT(*) FROM missions").fetchone()[0] == 0


# 3. Multiple opportunities from real evidence shapes.
def test_multiple_opportunities_from_evidence(db, tmp_path) -> None:
    root = _make_repo(tmp_path / "repo", "Owner/Beta")
    _register(db, root)
    pushed = "2026-06-01T00:00:00Z"  # stale AND failing
    report = discover_portfolio_opportunities(
        db, objective_id="owner-test-goal",
        reader=_reader_for(_read_result(
            "owner/beta", pushed_at=pushed, head="b" * 40,
            with_failure=True)),
        connector=_connector(
            pushed_at=pushed,
            runs=[{"name": "CI", "status": "completed",
                   "conclusion": "failure", "created_at": pushed}]),
        now=NOW)
    kinds = {key.split("-")[2] for key in report["repos"][0]["accepted"]}
    assert "CI_FAILURE" in kinds and "MAINTENANCE" in kinds, report


# 4. Duplicate discovery does not duplicate candidates.
def test_rerun_with_same_evidence_dedupes(db, tmp_path) -> None:
    root = _make_repo(tmp_path / "repo", "Owner/Gamma")
    _register(db, root)
    pushed = "2026-09-29T10:00:00Z"
    kwargs = dict(
        objective_id="owner-test-goal",
        reader=_reader_for(_read_result(
            "owner/gamma", pushed_at=pushed, head="c" * 40)),
        connector=_connector(pushed_at=pushed))
    first = discover_portfolio_opportunities(db, now=NOW, **kwargs)
    assert first["repos"][0]["outcome"] == "completed"
    with db.connect(readonly=True) as conn:
        candidates = conn.execute("SELECT COUNT(*) FROM candidates").fetchone()[0]
    second = discover_portfolio_opportunities(
        db, now=NOW + timedelta(hours=7), **kwargs)
    assert second["repos"][0]["outcome"] == "completed"
    assert second["repos"][0]["accepted"] == []
    assert any("already covered" in rej["reason"]
               for rej in second["repos"][0]["rejected"])
    with db.connect(readonly=True) as conn:
        assert conn.execute("SELECT COUNT(*) FROM candidates").fetchone()[0] == candidates


# 5. Fed items carry the exact registered resource binding.
def test_fed_item_binds_exact_resource(db, tmp_path) -> None:
    root = _make_repo(tmp_path / "repo", "Owner/Delta")
    mapping = _register(db, root)
    pushed = "2026-09-29T10:00:00Z"
    report = discover_portfolio_opportunities(
        db, objective_id="owner-test-goal",
        reader=_reader_for(_read_result(
            "owner/delta", pushed_at=pushed, head="d" * 40)),
        connector=_connector(pushed_at=pushed),
        now=NOW)
    item_id = report["repos"][0]["fed"][0]["portfolio_item_id"]
    from jarvis_foundry.evolution.executive_portfolio import (
        rebuild_portfolio_state,
    )
    item = rebuild_portfolio_state(db).work_items[item_id]
    assert item.resource_id == mapping["resource_id"]
    assert item.provenance["repository"] == "owner/delta"
    assert item.provenance["opportunity_kind"] == "MISSING_TEST"
    assert item.provenance["problem_statement"]


# 6. Selection scores value/readiness/risk, not registration order.
def test_selection_prefers_value_over_order(db, tmp_path) -> None:
    # Both repos eligible in ONE bridge run; registration order still loses.
    # The low-value stale repo is registered FIRST on purpose.
    _register(db, _make_repo(tmp_path / "low", "Owner/Slow"))
    _register(db, _make_repo(tmp_path / "high", "Owner/Shigh"))
    stale, fresh = "2026-01-01T00:00:00Z", "2026-09-29T10:00:00Z"
    report = _per_repo_bridge(db, {
        "owner/slow": {
            "result": _read_result(
                "owner/slow", pushed_at=stale, head="e" * 40),
            "connector": _connector(pushed_at=stale),
        },
        "owner/shigh": {
            "result": _read_result(
                "owner/shigh", pushed_at=fresh, head="f" * 40),
            "connector": _connector(pushed_at=fresh),
        },
    }, NOW)
    assert report["repos"][0]["outcome"] == "completed"
    assert report["repos"][1]["outcome"] == "completed"
    decision = select_next_work(db, loop_id="loop-value-multi")
    assert decision.kind == "SELECT", decision.reason
    assert "MISSING_TEST" in (decision.entity_id or ""), decision.entity_id


def _per_repo_bridge(db, specs: dict, now) -> dict:
    """Run the bridge with per-repository fake evidence keyed by slug."""
    import jarvis_foundry.project_discovery as bridge

    results = {slug: data["result"] for slug, data in specs.items()}
    connectors = {slug: data["connector"] for slug, data in specs.items()}

    def reader(spec) -> SourceReadResult:
        return results[spec.config["repo"]]

    real_connector = SimpleNamespace(read_api=lambda *a, **k: None)
    original_cheap = bridge._bridge_cheap_fingerprint
    original_runs = bridge._bridge_ci_runs

    def cheap(connector, slug):
        return original_cheap(connectors[slug], slug)

    def runs(connector, slug):
        return original_runs(connectors[slug], slug)

    bridge._bridge_cheap_fingerprint = cheap
    bridge._bridge_ci_runs = runs
    try:
        return bridge.discover_portfolio_opportunities(
            db, objective_id="owner-test-goal", reader=reader,
            connector=real_connector, now=now, max_repos=len(specs))
    finally:
        bridge._bridge_cheap_fingerprint = original_cheap
        bridge._bridge_ci_runs = original_runs


# 7. Research structurally precedes commitment in synthesized contracts.
def test_research_precedes_commitment_in_contract(db, tmp_path) -> None:
    root = _make_repo(tmp_path / "repo", "Owner/Eta")
    mapping = _register(db, root)
    pushed = "2026-09-29T10:00:00Z"
    report = discover_portfolio_opportunities(
        db, objective_id="owner-test-goal",
        reader=_reader_for(_read_result(
            "owner/eta", pushed_at=pushed, head="0" * 40)),
        connector=_connector(pushed_at=pushed),
        now=NOW)
    item_id = report["repos"][0]["fed"][0]["portfolio_item_id"]
    decision = select_next_work(db, loop_id="loop-research-first")
    assert decision.entity_id == item_id
    result = materialize_portfolio_selection(
        db, decision=decision.to_payload(),
        resource_id=mapping["resource_id"], source_id="test-research")
    assert result["materialized"] is True, result
    from jarvis_foundry.mission.contracts import latest_contract
    plan = latest_contract(db, result["mission_id"], step_id="discovery-plan")
    execute = latest_contract(
        db, result["mission_id"], step_id="discovery-execute")
    assert plan is not None and execute is not None
    assert "INVESTIGATE FIRST" in plan.instructions
    assert "uncertaint" in plan.instructions
    assert "investigation-" in execute.instructions
    assert "follow its conclusion" in execute.instructions


# 8. Selected concrete candidate becomes an executable contract.
def test_selected_candidate_materializes_real_contract(db, tmp_path) -> None:
    root = _make_repo(tmp_path / "repo", "Owner/Theta")
    mapping = _register(db, root)
    pushed = "2026-09-29T10:00:00Z"
    report = discover_portfolio_opportunities(
        db, objective_id="owner-test-goal",
        reader=_reader_for(_read_result(
            "owner/theta", pushed_at=pushed, head="1" * 40)),
        connector=_connector(pushed_at=pushed),
        now=NOW)
    item_id = report["repos"][0]["fed"][0]["portfolio_item_id"]
    decision = select_next_work(db, loop_id="loop-materialize")
    assert decision.entity_id == item_id
    result = materialize_portfolio_selection(
        db, decision=decision.to_payload(),
        resource_id=mapping["resource_id"], source_id="test-materialize")
    assert result["materialized"] is True, result
    mission = db.row("missions", "mission_id", result["mission_id"])
    assert mission is not None
    rows = list(contract_rows(db, result["mission_id"]))
    assert len(rows) == 2
    from jarvis_foundry.mission.contracts import latest_contract
    execute = latest_contract(
        db, result["mission_id"], step_id="discovery-execute")
    assert execute is not None
    assert execute.resource_id == mapping["resource_id"]
    assert "ci.yml" in execute.target_relative_path
    assert len(execute.verification_criteria) >= 3
    assert any("no file outside" in line
               for line in execute.verification_criteria)
    # Scope discipline lives in the synthesis template itself.
    from jarvis_foundry.executive.portfolio_bridge import (
        _DISCOVERY_CONTRACT_TEMPLATES,
    )
    template = _DISCOVERY_CONTRACT_TEMPLATES["MISSING_TEST"]
    assert "src/" in template["forbidden"]
    assert "tests/" in template["forbidden"]


# 9. Unselected candidates remain durable.
def test_unselected_candidate_survives_materialization(db, tmp_path) -> None:
    from jarvis_foundry.evolution.executive_portfolio import (
        configure_portfolio_policy as configure,
    )
    configure(db, PortfolioPolicy(max_concurrent_missions=2))
    _register(db, _make_repo(tmp_path / "low", "Owner/Ilow"))
    high_mapping = _register(db, _make_repo(tmp_path / "high", "Owner/Ihigh"))
    stale, fresh = "2026-01-01T00:00:00Z", "2026-09-29T10:00:00Z"
    _per_repo_bridge(db, {
        "owner/ilow": {
            "result": _read_result(
                "owner/ilow", pushed_at=stale, head="2" * 40),
            "connector": _connector(pushed_at=stale),
        },
        "owner/ihigh": {
            "result": _read_result(
                "owner/ihigh", pushed_at=fresh, head="3" * 40),
            "connector": _connector(pushed_at=fresh),
        },
    }, NOW)
    decision = select_next_work(db, loop_id="loop-durable")
    assert "MISSING_TEST" in (decision.entity_id or "")
    first_item = decision.entity_id
    materialize_portfolio_selection(
        db, decision=decision.to_payload(),
        resource_id=high_mapping["resource_id"], source_id="test-durable")
    # The materialized item is consumed with mission linkage: it can never
    # be re-selected into a duplicate mission.
    from jarvis_foundry.evolution.executive_portfolio import (
        rebuild_portfolio_state as rebuild,
    )
    consumed = rebuild(db).work_items[first_item]
    assert consumed.status == "CONSUMED"
    assert (consumed.mission_id or "").startswith("mission-")
    # The unselected stale-repo work stays actionable: the next decision
    # selects the surviving item, never the consumed one, and the lower
    # value opportunity remains durable behind it.
    waiting = select_next_work(db, loop_id="loop-durable-next")
    assert waiting.kind == "SELECT", waiting.reason
    assert waiting.entity_id != first_item
    assert "ilow" in (waiting.entity_id or ""), waiting.entity_id
    survivors = [item for item in rebuild(db).work_items.values()
                 if item.status == "OPEN"]
    assert any("MAINTENANCE" in item.item_id for item in survivors)


# 10. Unsafe/unscoped candidates fail closed (no mission, exact reason).
def test_untemplated_kind_fails_closed(db, tmp_path) -> None:
    mapping = _register(db, _make_repo(tmp_path / "repo", "Owner/Kappa"))
    register_work_item(
        db, item_id="pw-opaque", kind="MAINTENANCE",
        objective="opaque discovery item without a synthesis template",
        resource_id=mapping["resource_id"],
        expected_value=0.9, urgency=0.9, confidence=0.9, effort=0.1,
        risk=0.1, reversibility=1.0,
        provenance={"source_lane": "live-github-research-activation",
                    "repository": "owner/kappa",
                    "opportunity_kind": "UX_FRICTION",
                    "problem_statement": "vague friction with no safe scope",
                    "duplicate_key": "dupkey-owner/kappa-UX_FRICTION-x",
                    "why_now": "test"},
        metadata={"repository": "owner/kappa"},
    )
    decision = select_next_work(db, loop_id="loop-fail-closed")
    assert decision.kind == "SELECT"
    result = materialize_portfolio_selection(
        db, decision=decision.to_payload(),
        resource_id=mapping["resource_id"], source_id="test-fail-closed")
    assert result["materialized"] is False
    assert result["mission_id"] is None
    assert "cannot be safely scoped" in (result["blocked_reason"] or "")
    with db.connect(readonly=True) as conn:
        assert conn.execute("SELECT COUNT(*) FROM missions").fetchone()[0] == 0


# 11. No executable candidate -> truthful WAIT on current evidence.
def test_healthy_repo_yields_truthful_wait(db, tmp_path) -> None:
    _register(db, _make_repo(tmp_path / "repo", "Owner/Lambda"))
    pushed = "2026-09-29T10:00:00Z"
    report = discover_portfolio_opportunities(
        db, objective_id="owner-test-goal",
        reader=_reader_for(_read_result(
            "owner/lambda", pushed_at=pushed, head="4" * 40)),
        connector=_connector(
            pushed_at=pushed,
            runs=[{"name": "CI", "status": "completed",
                   "conclusion": "success", "created_at": pushed}]),
        now=NOW)
    assert report["repos"][0]["outcome"] == "completed"
    assert report["repos"][0]["accepted"] == []
    decision = select_next_work(db, loop_id="loop-truthful-wait")
    assert decision.kind == "WAIT"
    assert decision.reason == "no positive-value work is currently executable"


# 12. New project evidence wakes/re-evaluates the standing goal.
def test_new_evidence_reruns_after_quiet_window(db, tmp_path) -> None:
    root = _make_repo(tmp_path / "repo", "Owner/Mu")
    _register(db, root)
    pushed = "2026-09-29T10:00:00Z"
    kwargs = dict(
        objective_id="owner-test-goal",
        reader=_reader_for(_read_result(
            "owner/mu", pushed_at=pushed, head="5" * 40)),
        connector=_connector(pushed_at=pushed))
    assert discover_portfolio_opportunities(
        db, now=NOW, **kwargs)["repos"][0]["outcome"] == "completed"
    quiet = discover_portfolio_opportunities(
        db, now=NOW + timedelta(hours=1), **kwargs)
    assert quiet["repos"][0]["outcome"] == "skipped"
    assert "no new evidence" in (quiet["repos"][0]["reason"] or "")
    # New push upstream -> the cheap fingerprint changes -> full re-run.
    moved_kwargs = dict(
        objective_id="owner-test-goal",
        reader=_reader_for(_read_result(
            "owner/mu", pushed_at="2026-09-29T11:00:00Z", head="6" * 40)),
        connector=_connector(pushed_at="2026-09-29T11:00:00Z"))
    moved = discover_portfolio_opportunities(
        db, now=NOW + timedelta(hours=1), **moved_kwargs)
    assert moved["repos"][0]["outcome"] == "completed"


# 13. Solved opportunities never become busywork.
def test_solved_opportunity_rejected_with_evidence(db, tmp_path) -> None:
    root = _make_repo(tmp_path / "repo", "Owner/Nu", with_ci=True)
    _register(db, root)
    pushed = "2026-09-29T10:00:00Z"
    report = discover_portfolio_opportunities(
        db, objective_id="owner-test-goal",
        reader=_reader_for(_read_result(
            "owner/nu", pushed_at=pushed, head="7" * 40)),
        connector=_connector(pushed_at=pushed),
        now=NOW)
    entry = report["repos"][0]
    assert entry["outcome"] == "completed"
    assert entry["accepted"] == []
    assert any("already present on the default branch" in rej["reason"]
               for rej in entry["rejected"]), entry


# 14. Restart/replay preserves candidate and run lineage (no duplicates).
def test_restart_replay_preserves_lineage(db, tmp_path) -> None:
    root = _make_repo(tmp_path / "repo", "Owner/Xi")
    _register(db, root)
    pushed = "2026-09-29T10:00:00Z"
    kwargs = dict(
        objective_id="owner-test-goal",
        reader=_reader_for(_read_result(
            "owner/xi", pushed_at=pushed, head="8" * 40)),
        connector=_connector(pushed_at=pushed))
    first = discover_portfolio_opportunities(db, now=NOW, **kwargs)
    assert first["repos"][0]["outcome"] == "completed"
    reopened = FoundryDatabase(db.path)
    second = discover_portfolio_opportunities(
        reopened, now=NOW + timedelta(hours=7), **kwargs)
    assert second["repos"][0]["accepted"] == []
    with reopened.connect(readonly=True) as conn:
        runs = conn.execute(
            "SELECT COUNT(*) FROM events WHERE event_type = ?",
            ("portfolio.discovery_completed",)).fetchone()[0]
        candidates = conn.execute(
            "SELECT COUNT(*) FROM candidates").fetchone()[0]
    # The interval rerun honestly records its (empty) result; the candidate
    # itself is never duplicated.
    assert runs == 2
    assert candidates == 1


# 15. No duplicate mission materialization for one decision.
def test_materialization_is_idempotent_per_decision(db, tmp_path) -> None:
    mapping = _register(db, _make_repo(tmp_path / "repo", "Owner/Omicron"))
    register_work_item(
        db, item_id="pw-idem", kind="MAINTENANCE",
        objective="idempotent discovery item",
        resource_id=mapping["resource_id"],
        expected_value=0.8, urgency=0.5, confidence=0.8, effort=0.2,
        risk=0.1, reversibility=1.0,
        provenance={"source_lane": "live-github-research-activation",
                    "repository": "owner/omicron",
                    "opportunity_kind": "MAINTENANCE",
                    "problem_statement": "stale checkout needs a decision",
                    "duplicate_key": "dupkey-owner/omicron-MAINTENANCE-x",
                    "why_now": "test"},
        metadata={"repository": "owner/omicron"},
    )
    decision = select_next_work(db, loop_id="loop-idem")
    assert decision.kind == "SELECT"
    payload = decision.to_payload()
    first = materialize_portfolio_selection(
        db, decision=payload, resource_id=mapping["resource_id"],
        source_id="test-idem")
    second = materialize_portfolio_selection(
        db, decision=payload, resource_id=mapping["resource_id"],
        source_id="test-idem")
    assert first["materialized"] is True
    assert second["mission_id"] == first["mission_id"]
    with db.connect(readonly=True) as conn:
        assert conn.execute("SELECT COUNT(*) FROM missions").fetchone()[0] == 1


def test_unprofiled_fixture_repo_is_not_eligible(db, tmp_path) -> None:
    # A checkout with a GitHub remote but NO project-profile binding (probe
    # worktrees, fixtures) must never be discovered against.
    from jarvis_foundry.software_intelligence.project_identity import (
        ProjectIdentityResolver,
    )
    root = _make_repo(tmp_path / "fixture", "Owner/Fixture")
    ProjectIdentityResolver(db).register_repository(root)
    assert eligible_discovery_repositories(db) == []


# 17. Cross-resource materialization binds the mission to the item's exact
# resource (contract validation rejects caller/bound mismatch).
def test_cross_resource_materialization_binds_exact_resource(
    db, tmp_path,
) -> None:
    bound_mapping = _register(db, _make_repo(tmp_path / "bound", "Owner/Bound"))
    pushed = "2026-09-29T10:00:00Z"
    report = discover_portfolio_opportunities(
        db, objective_id="owner-test-goal",
        reader=_reader_for(_read_result(
            "owner/bound", pushed_at=pushed, head="a" * 40)),
        connector=_connector(pushed_at=pushed),
        now=NOW)
    assert report["repos"][0]["outcome"] == "completed"
    decision = select_next_work(db, loop_id="loop-cross-resource")
    assert decision.kind == "SELECT", decision.reason
    # Caller resource differs from the item's bound resource on purpose.
    result = materialize_portfolio_selection(
        db, decision=decision.to_payload(),
        resource_id="resource-caller-unrelated", source_id="test-cross")
    assert result["materialized"] is True, result
    mission = db.row("missions", "mission_id", result["mission_id"])
    assert mission is not None
    assert mission["resource_id"] == bound_mapping["resource_id"]
    from jarvis_foundry.mission.contracts import latest_contract
    for step in ("discovery-plan", "discovery-execute"):
        contract = latest_contract(
            db, result["mission_id"], step_id=step)
        assert contract is not None, step
        assert contract.resource_id == bound_mapping["resource_id"], step


# 18. The investigate step is scope-bounded too (never open-ended).
def test_investigate_step_carries_scope_bounds(db, tmp_path) -> None:
    mapping = _register(db, _make_repo(tmp_path / "repo", "Owner/Pi"))
    pushed = "2026-09-29T10:00:00Z"
    report = discover_portfolio_opportunities(
        db, objective_id="owner-test-goal",
        reader=_reader_for(_read_result(
            "owner/pi", pushed_at=pushed, head="b" * 40)),
        connector=_connector(pushed_at=pushed),
        now=NOW)
    assert report["repos"][0]["fed"]
    decision = select_next_work(db, loop_id="loop-plan-scope")
    result = materialize_portfolio_selection(
        db, decision=decision.to_payload(),
        resource_id=mapping["resource_id"], source_id="test-plan-scope")
    assert result["materialized"] is True, result
    from jarvis_foundry.mission.contracts import latest_contract
    plan = latest_contract(
        db, result["mission_id"], step_id="discovery-plan")
    assert plan is not None
    assert len(plan.forbidden_paths) > 0
    assert "src/" in plan.forbidden_paths


# 19. Stale-repo objectives are day-stable: reruns dedupe, not duplicate.
def test_stale_objective_stable_across_days(db, tmp_path) -> None:
    _register(db, _make_repo(tmp_path / "repo", "Owner/Rho"))
    pushed = "2026-01-01T00:00:00Z"
    kwargs = dict(
        objective_id="owner-test-goal",
        reader=_reader_for(_read_result(
            "owner/rho", pushed_at=pushed, head="c" * 40)),
        connector=_connector(pushed_at=pushed))
    first = discover_portfolio_opportunities(db, now=NOW, **kwargs)
    assert first["repos"][0]["outcome"] == "completed"
    assert any("MAINTENANCE" in key
               for key in first["repos"][0]["accepted"])
    with db.connect(readonly=True) as conn:
        candidates = conn.execute(
            "SELECT COUNT(*) FROM candidates").fetchone()[0]
    second = discover_portfolio_opportunities(
        db, now=NOW + timedelta(days=2), **kwargs)
    assert second["repos"][0]["outcome"] == "completed"
    assert second["repos"][0]["accepted"] == []
    with db.connect(readonly=True) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM candidates").fetchone()[0] == candidates


# 20. A runs page claiming runs but listing none is unknown, not NO_CI.
def test_truncated_runs_page_fails_closed(db, tmp_path) -> None:
    _register(db, _make_repo(tmp_path / "repo", "Owner/Sigma"))
    pushed = "2026-09-29T10:00:00Z"

    def read_api(action: str, endpoint: str):
        attempt = SimpleNamespace(outcome="COMPLETED", error_code=None)
        if "actions/runs" in endpoint:
            return attempt, {"total_count": 3, "workflow_runs": []}
        return attempt, {
            "pushed_at": pushed, "updated_at": pushed,
            "open_issues_count": 0, "default_branch": "main",
            "archived": False,
        }

    report = discover_portfolio_opportunities(
        db, objective_id="owner-test-goal",
        reader=_reader_for(_read_result(
            "owner/sigma", pushed_at=pushed, head="d" * 40)),
        connector=SimpleNamespace(read_api=read_api),
        now=NOW)
    entry = report["repos"][0]
    assert entry["outcome"] == "skipped"
    assert "CI state unreadable" in (entry["reason"] or "")


# 21. REPAIR-class lane items synthesize fresh missions (never the repair
# track, which requires a pre-existing mission_id).
def test_repair_lane_item_synthesizes_fresh_mission(db, tmp_path) -> None:
    mapping = _register(db, _make_repo(tmp_path / "repo", "Owner/Tau"))
    pushed = "2026-09-29T10:00:00Z"
    report = discover_portfolio_opportunities(
        db, objective_id="owner-test-goal",
        reader=_reader_for(_read_result(
            "owner/tau", pushed_at=pushed, head="e" * 40,
            with_failure=True)),
        connector=_connector(
            pushed_at=pushed,
            runs=[{"name": "CI", "status": "completed",
                   "conclusion": "failure", "created_at": pushed}]),
        now=NOW)
    entry = report["repos"][0]
    assert entry["outcome"] == "completed", entry
    assert any("CI_FAILURE" in key for key in entry["accepted"]), entry
    decision = select_next_work(db, loop_id="loop-repair-lane")
    assert decision.kind == "SELECT", decision.reason
    assert decision.selected_class == "REPAIR"
    result = materialize_portfolio_selection(
        db, decision=decision.to_payload(),
        resource_id=mapping["resource_id"], source_id="test-repair-lane")
    assert result["materialized"] is True, result
    mission = db.row("missions", "mission_id", result["mission_id"])
    assert mission is not None
    assert mission["resource_id"] == mapping["resource_id"]
    from jarvis_foundry.mission.contracts import latest_contract
    assert latest_contract(
        db, result["mission_id"],
        step_id="discovery-execute") is not None


# 22. A runs page without total_count is unknown, not NO_CI.
def test_missing_total_count_fails_closed(db, tmp_path) -> None:
    _register(db, _make_repo(tmp_path / "repo", "Owner/Upsilon"))
    pushed = "2026-09-29T10:00:00Z"

    def read_api(action: str, endpoint: str):
        attempt = SimpleNamespace(outcome="COMPLETED", error_code=None)
        if "actions/runs" in endpoint:
            return attempt, {"workflow_runs": []}
        return attempt, {
            "pushed_at": pushed, "updated_at": pushed,
            "open_issues_count": 0, "default_branch": "main",
            "archived": False,
        }

    report = discover_portfolio_opportunities(
        db, objective_id="owner-test-goal",
        reader=_reader_for(_read_result(
            "owner/upsilon", pushed_at=pushed, head="f" * 40)),
        connector=SimpleNamespace(read_api=read_api),
        now=NOW)
    entry = report["repos"][0]
    assert entry["outcome"] == "skipped"
    assert "CI state unreadable" in (entry["reason"] or "")
