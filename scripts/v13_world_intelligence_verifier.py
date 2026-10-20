"""Independently verify the frozen V13 World Intelligence evidence.

The verifier intentionally uses only the Python standard library. It does not
import the research or decision implementation that produced the proof.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


PROOF_CONTRACT = "jarvis.v13.world-intelligence-proof.v1"
VERIFIER_CONTRACT = "jarvis.v13.world-intelligence-verifier.v1"
DOSSIER_CONTRACT = "jarvis.v13.research-dossier.v2"
FROZEN_BASE = "51d2cfcce1be847fd252d1c857105f09f5f2c0e4"
#: The schema this verifier expects the proof to have been produced at.
#: Bumped with the product schema; kept as a local literal because this
#: verifier deliberately imports nothing from the product it verifies.
EXPECTED_SCHEMA = 41
EXPECTED_MAIN = "2905c206fed0461bf3085bc0cb43773be913300a"
EXPECTED_BRANCH = (
    "candidate/v13-final-personal-executive-convergence-20260905"
)
OWNER_CONTEXT_FINAL = "ca3e9adb6a559f507253bdf7b70854424bc8b35d"
OWNER_CONTEXT_SOURCE = "41dfcd285e3fb407237a43b9c2caba47d9d6ab1b"

REQUIRED_DIMENSIONS = frozenset({
    "owner_goals",
    "project_context",
    "expected_value",
    "evidence_strength",
    "confidence",
    "risk",
    "cost",
    "time",
    "quality",
    "maintainability",
    "reversibility",
    "dependencies",
    "opportunity_cost",
    "compatibility",
})
SOURCE_CLASSES = frozenset({
    "PRIMARY_OFFICIAL",
    "UPSTREAM_TECHNICAL",
    "HIGH_QUALITY_SECONDARY",
    "COMMUNITY_EXPERIENCE",
    "WEAK_UNKNOWN",
})
ALLOWED_SOURCE_PATHS = frozenset({
    "docs/CURRENT_STATE.md",
    "docs/handoffs/V13_WORLD_INTELLIGENCE_CHECKPOINT_20260903.md",
    "docs/handoffs/V13_WORLD_INTELLIGENCE_DECISION_TRAIL_20260903.md",
    "docs/handoffs/V13_WORLD_INTELLIGENCE_FINAL_20260904.md",
    "scripts/v13_world_intelligence_proof.py",
    "scripts/v13_world_intelligence_verifier.py",
    "src/jarvis_foundry/autonomy_projection.py",
    "src/jarvis_foundry/research_decision.py",
    "src/jarvis_foundry/research_dossier.py",
    "src/jarvis_foundry/world_model/research_handoff.py",
    "tests/foundry/test_v13_research_decision.py",
    "tests/foundry/test_v13_research_dossier.py",
    "tests/foundry/test_v13_world_intelligence_proof.py",
    "tests/foundry/test_v13_world_intelligence_verifier.py",
    "tests/foundry/test_v13_world_research_handoff.py",
    "tests/foundry/test_v11_a2_initiative.py",
})
ALLOWED_SOURCE_PREFIXES = (
    "evidence/v13-world-intelligence/",
)
FORBIDDEN_SOURCE_PREFIXES = (
    "src/jarvis_foundry/migrations",
    "src/jarvis_foundry/provider",
    "src/jarvis_foundry/release",
    "src/jarvis_foundry/runtime",
    "src/jarvis_foundry/scheduler",
    "src/jarvis_foundry/supervisor",
    "src/jarvis_foundry/ui",
    "ui/",
)


def _iso_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} does not contain a JSON object")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _run(
    command: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    timeout: int = 180,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )


def _git(
    repo_root: Path,
    *args: str,
    timeout: int = 60,
) -> subprocess.CompletedProcess[str]:
    return _run(
        ["git", "-C", str(repo_root), *args],
        timeout=timeout,
    )


def _git_text(repo_root: Path, *args: str) -> str | None:
    result = _git(repo_root, *args)
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def _at(value: Any, *path: str | int) -> Any:
    current = value
    for part in path:
        if isinstance(part, int):
            if not isinstance(current, list) or part >= len(current):
                return None
            current = current[part]
        else:
            if not isinstance(current, dict) or part not in current:
                return None
            current = current[part]
    return current


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {
            str(key): _jsonable(item)
            for key, item in value.items()
        }
    return repr(value)


class CheckRecorder:
    def __init__(self) -> None:
        self.checks: list[dict[str, Any]] = []

    def add(
        self,
        check_id: str,
        passed: bool,
        *,
        detail: str,
        actual: Any = None,
        expected: Any = None,
    ) -> None:
        record: dict[str, Any] = {
            "check_id": check_id,
            "status": "PASS" if passed else "FAIL",
            "detail": detail,
        }
        if actual is not None:
            record["actual"] = _jsonable(actual)
        if expected is not None:
            record["expected"] = _jsonable(expected)
        self.checks.append(record)

    @property
    def passed(self) -> bool:
        return all(row["status"] == "PASS" for row in self.checks)

    @property
    def failures(self) -> list[dict[str, Any]]:
        return [
            row for row in self.checks
            if row["status"] != "PASS"
        ]


def _contains_forbidden_score(value: Any) -> list[str]:
    findings: list[str] = []

    def visit(item: Any, path: str) -> None:
        if isinstance(item, dict):
            for key, nested in item.items():
                child = f"{path}.{key}" if path else str(key)
                if str(key).lower() in {
                    "score",
                    "total_score",
                    "weighted_score",
                    "numeric_score",
                }:
                    findings.append(child)
                visit(nested, child)
        elif isinstance(item, list):
            for index, nested in enumerate(item):
                visit(nested, f"{path}[{index}]")

    visit(value, "")
    return findings


def _semantic_summary(proof: dict[str, Any]) -> dict[str, Any]:
    return {
        "contract": proof.get("contract"),
        "source_sha": proof.get("source_sha"),
        "base_sha": proof.get("base_sha"),
        "schema": proof.get("schema"),
        "A": {
            "preliminary_selection": _at(
                proof,
                "proofs",
                "A",
                "preliminary",
                "recommendation",
                "selected_option_id",
            ),
            "preliminary_stop": _at(
                proof,
                "proofs",
                "A",
                "preliminary",
                "stop_assessment",
                "disposition",
            ),
            "final_selection": _at(
                proof,
                "proofs",
                "A",
                "final",
                "recommendation",
                "selected_option_id",
            ),
            "changed_mind": _at(
                proof,
                "proofs",
                "A",
                "final",
                "recommendation",
                "changed_mind",
            ),
            "changed_evidence": _at(
                proof, "proofs", "A", "changed_evidence"
            ),
            "final_stop": _at(
                proof,
                "proofs",
                "A",
                "final",
                "stop_assessment",
                "disposition",
            ),
            "executive_consumed": _at(
                proof,
                "proofs",
                "A",
                "executive_bridge",
                "consumed_by_executive",
            ),
            "a3_status": _at(
                proof, "proofs", "A", "a3_admission", "status"
            ),
            "bounded_mission": bool(
                _at(
                    proof,
                    "proofs",
                    "A",
                    "bounded_mission",
                    "mission_id",
                )
            ),
        },
        "B": {
            "selection": _at(
                proof,
                "proofs",
                "B",
                "recommendation",
                "selected_option_id",
            ),
            "actual_validation": _at(
                proof, "proofs", "B", "actual_validation"
            ),
            "syndicated_inputs": _at(
                proof, "proofs", "B", "syndicated_inputs"
            ),
            "syndicated_records_retained": _at(
                proof,
                "proofs",
                "B",
                "syndicated_records_retained",
            ),
        },
        "C": {
            "selection": _at(
                proof,
                "proofs",
                "C",
                "recommendation",
                "selected_option_id",
            ),
            "semantic_consumption_only": _at(
                proof,
                "proofs",
                "C",
                "ancestry",
                "semantic_consumption_only",
            ),
            "base_is_owner_ancestor": _at(
                proof,
                "proofs",
                "C",
                "ancestry",
                "base_is_ancestor_of_owner_context_source",
            ),
            "owner_is_base_ancestor": _at(
                proof,
                "proofs",
                "C",
                "ancestry",
                "owner_context_source_is_ancestor_of_base",
            ),
        },
        "D": {
            "status": _at(
                proof, "proofs", "D", "recommendation", "status"
            ),
            "selection": _at(
                proof,
                "proofs",
                "D",
                "recommendation",
                "selected_option_id",
            ),
            "stop": _at(
                proof,
                "proofs",
                "D",
                "stop_assessment",
                "disposition",
            ),
            "action": _at(proof, "proofs", "D", "action"),
            "gmail": _at(proof, "proofs", "D", "gmail_policy"),
        },
        "adversarial": proof.get("adversarial"),
        "projection": {
            "contract": _at(proof, "projection", "contract"),
            "count": _at(proof, "projection", "count"),
            "source_quality_present": all(
                bool(row.get("source_quality"))
                for row in (
                    _at(proof, "projection", "dossiers") or []
                )
                if isinstance(row, dict)
            ),
        },
        "calibration": {
            "observations": _at(
                proof, "calibration", "observations"
            ),
            "self_training": _at(
                proof, "calibration", "self_training"
            ),
        },
        "decision_state_counts": proof.get("decision_state_counts"),
        "event_counts": proof.get("event_counts"),
        "boundaries": proof.get("boundaries"),
    }


def _parse_junit(path: Path) -> dict[str, Any]:
    root = ET.parse(path).getroot()
    suites = (
        [root]
        if root.tag.rsplit("}", 1)[-1] == "testsuite"
        else [
            child for child in root
            if child.tag.rsplit("}", 1)[-1] == "testsuite"
        ]
    )

    def count(name: str) -> int:
        root_value = root.attrib.get(name)
        if root_value is not None:
            return int(float(root_value))
        return sum(
            int(float(suite.attrib.get(name, "0")))
            for suite in suites
        )

    result = {
        "path": str(path),
        "tests": count("tests"),
        "failures": count("failures"),
        "errors": count("errors"),
        "skipped": count("skipped"),
        "time_seconds": float(root.attrib.get(
            "time",
            sum(float(suite.attrib.get("time", "0")) for suite in suites),
        )),
    }
    result["status"] = (
        "PASS"
        if result["tests"] > 0
        and result["failures"] == 0
        and result["errors"] == 0
        else "FAIL"
    )
    return result


def _fresh_replay(
    repo_root: Path,
    source_sha: str,
    expected_summary: dict[str, Any],
    *,
    timeout: int,
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(
        prefix="jarvis-v13-verifier-"
    ) as raw:
        temp_root = Path(raw).resolve()
        source_root = temp_root / "source"
        output = temp_root / "fresh-proof.json"
        add = _git(
            repo_root,
            "worktree",
            "add",
            "--detach",
            str(source_root),
            source_sha,
            timeout=timeout,
        )
        if add.returncode != 0:
            return {
                "status": "FAIL",
                "stage": "git-worktree-add",
                "stderr": add.stderr.strip(),
            }
        try:
            project_python = (
                repo_root / ".venv" / "Scripts" / "python.exe"
            )
            python = (
                project_python
                if project_python.is_file()
                else Path(sys.executable)
            )
            env = os.environ.copy()
            pythonpath = str(source_root / "src")
            if env.get("PYTHONPATH"):
                pythonpath = (
                    pythonpath + os.pathsep + env["PYTHONPATH"]
                )
            env["PYTHONPATH"] = pythonpath
            run = _run(
                [
                    str(python),
                    str(
                        source_root
                        / "scripts"
                        / "v13_world_intelligence_proof.py"
                    ),
                    "--repo-root",
                    str(source_root),
                    "--output",
                    str(output),
                ],
                cwd=source_root,
                env=env,
                timeout=timeout,
            )
            if run.returncode != 0 or not output.is_file():
                return {
                    "status": "FAIL",
                    "stage": "proof-replay",
                    "returncode": run.returncode,
                    "stdout": run.stdout.strip(),
                    "stderr": run.stderr.strip(),
                }
            replay = _load_json(output)
            replay_summary = _semantic_summary(replay)
            return {
                "status": (
                    "PASS"
                    if replay_summary == expected_summary
                    else "FAIL"
                ),
                "stage": "semantic-comparison",
                "source_sha": replay.get("source_sha"),
                "proof_sha256": _sha256(output),
                "semantic_match": replay_summary == expected_summary,
                "summary": replay_summary,
            }
        finally:
            remove = _git(
                repo_root,
                "worktree",
                "remove",
                "--force",
                str(source_root),
                timeout=timeout,
            )
            if remove.returncode != 0:
                _git(repo_root, "worktree", "prune", timeout=timeout)


def verify(
    *,
    repo_root: Path,
    source_sha: str,
    expected_main: str,
    expected_branch: str,
    proof_path: Path,
    focused_junit: Path | None,
    full_junit: Path | None,
    require_junit: bool,
    fresh_replay: bool,
    enforce_lane_scope: bool,
    timeout: int,
) -> dict[str, Any]:
    recorder = CheckRecorder()
    proof = _load_json(proof_path)
    source_object = _git(
        repo_root, "rev-parse", "--verify", f"{source_sha}^{{commit}}"
    )
    recorder.add(
        "git.source-commit-exists",
        source_object.returncode == 0,
        detail="The declared source SHA resolves to a Git commit.",
        actual=source_object.stdout.strip() or source_object.stderr.strip(),
        expected=source_sha,
    )
    ancestry = _git(
        repo_root,
        "merge-base",
        "--is-ancestor",
        FROZEN_BASE,
        source_sha,
    )
    recorder.add(
        "git.frozen-base-is-ancestor",
        ancestry.returncode == 0,
        detail="The frozen V12 base is an ancestor of the source commit.",
        actual=ancestry.returncode,
        expected=0,
    )
    main_sha = _git_text(repo_root, "rev-parse", "main")
    origin_main_sha = _git_text(repo_root, "rev-parse", "origin/main")
    recorder.add(
        "git.main-preserved",
        main_sha == expected_main,
        detail="Local main remains at the captured canonical SHA.",
        actual=main_sha,
        expected=expected_main,
    )
    recorder.add(
        "git.origin-main-preserved",
        origin_main_sha == expected_main,
        detail="origin/main remains at the captured canonical SHA.",
        actual=origin_main_sha,
        expected=expected_main,
    )
    changed_result = _git(
        repo_root,
        "diff",
        "--name-only",
        f"{FROZEN_BASE}..{source_sha}",
    )
    changed_paths = sorted({
        row.strip().replace("\\", "/")
        for row in changed_result.stdout.splitlines()
        if row.strip()
    })
    unexpected_paths = [
        path for path in changed_paths
        if (
            path not in ALLOWED_SOURCE_PATHS
            and not path.startswith(ALLOWED_SOURCE_PREFIXES)
        )
    ]
    forbidden_paths = [
        path for path in changed_paths
        if path.lower().startswith(FORBIDDEN_SOURCE_PREFIXES)
    ]
    recorder.add(
        "git.source-path-allowlist",
        (
            changed_result.returncode == 0
            and (not enforce_lane_scope or not unexpected_paths)
        ),
        detail=(
            "Lane-only source paths are enforced when requested; integrated "
            "convergence sources are recorded without rejecting other "
            "accepted V13 lanes."
        ),
        actual={
            "enforced": enforce_lane_scope,
            "unexpected_paths": unexpected_paths,
        },
        expected=[],
    )
    recorder.add(
        "git.forbidden-surfaces-untouched",
        not enforce_lane_scope or not forbidden_paths,
        detail=(
            "Lane-only forbidden surfaces are enforced when requested; the "
            "integrated convergence candidate is verified by its final "
            "cross-lane authority and source-boundary gates."
        ),
        actual={
            "enforced": enforce_lane_scope,
            "forbidden_paths": forbidden_paths,
        },
        expected=[],
    )

    recorder.add(
        "proof.contract",
        proof.get("contract") == PROOF_CONTRACT,
        detail="The proof uses the frozen V13 evidence contract.",
        actual=proof.get("contract"),
        expected=PROOF_CONTRACT,
    )
    recorder.add(
        "proof.branch",
        proof.get("branch") == expected_branch,
        detail=(
            "The evidence was generated on the externally declared isolated "
            "candidate branch."
        ),
        actual=proof.get("branch"),
        expected=expected_branch,
    )
    recorder.add(
        "proof.source-sha",
        proof.get("source_sha") == source_sha,
        detail="The proof is pinned to the declared source commit.",
        actual=proof.get("source_sha"),
        expected=source_sha,
    )
    recorder.add(
        "proof.base-and-schema",
        (
            proof.get("base_sha") == FROZEN_BASE
            and proof.get("schema") == EXPECTED_SCHEMA
        ),
        detail=(
            "The proof preserves the frozen base and schema "
            f"v{EXPECTED_SCHEMA}."
        ),
        actual={
            "base_sha": proof.get("base_sha"),
            "schema": proof.get("schema"),
        },
        expected={"base_sha": FROZEN_BASE, "schema": EXPECTED_SCHEMA},
    )
    recorder.add(
        "proof.owner-context-input",
        (
            _at(
                proof, "owner_context_input", "final"
            ) == OWNER_CONTEXT_FINAL
            and _at(
                proof, "owner_context_input", "source"
            ) == OWNER_CONTEXT_SOURCE
        ),
        detail="The exact divergent owner-context input refs are preserved.",
        actual=proof.get("owner_context_input"),
        expected={
            "final": OWNER_CONTEXT_FINAL,
            "source": OWNER_CONTEXT_SOURCE,
        },
    )

    category_a = {
        "preliminary_selection": _at(
            proof,
            "proofs",
            "A",
            "preliminary",
            "recommendation",
            "selected_option_id",
        ),
        "preliminary_stop": _at(
            proof,
            "proofs",
            "A",
            "preliminary",
            "stop_assessment",
            "disposition",
        ),
        "final_selection": _at(
            proof,
            "proofs",
            "A",
            "final",
            "recommendation",
            "selected_option_id",
        ),
        "changed_mind": _at(
            proof,
            "proofs",
            "A",
            "final",
            "recommendation",
            "changed_mind",
        ),
        "changed_evidence": _at(
            proof, "proofs", "A", "changed_evidence"
        ),
        "final_stop": _at(
            proof,
            "proofs",
            "A",
            "final",
            "stop_assessment",
            "disposition",
        ),
    }
    recorder.add(
        "proof.A.changed-mind",
        category_a == {
            "preliminary_selection": "transaction-begin-immediate",
            "preliminary_stop": "CONTINUE",
            "final_selection": "read-snapshot-mode-ro",
            "changed_mind": True,
            "changed_evidence": True,
            "final_stop": "STOP_ENOUGH_EVIDENCE",
        },
        detail=(
            "Stronger evidence changes the technical recommendation before "
            "the bounded stopping rule fires."
        ),
        actual=category_a,
    )
    bridge = _at(proof, "proofs", "A", "executive_bridge") or {}
    recorder.add(
        "proof.A.canonical-executive-bridge",
        (
            bridge.get("authority") == "EXECUTIVE_REFERENCE_ONLY"
            and bridge.get("candidate_created") is True
            and bridge.get("evaluated_by_executive") is True
            and bridge.get("consumed_by_executive") is True
            and bridge.get("objective_id") is None
            and bridge.get("mission_id") is None
            and _at(
                proof, "proofs", "A", "a3_admission", "status"
            ) == "ADMITTED"
            and bool(
                _at(
                    proof,
                    "proofs",
                    "A",
                    "bounded_mission",
                    "mission_id",
                )
            )
        ),
        detail=(
            "The advisory result crosses A2, A3, and the existing Executive "
            "before one bounded mission exists."
        ),
        actual={
            "bridge": bridge,
            "a3_status": _at(
                proof, "proofs", "A", "a3_admission", "status"
            ),
            "mission_id": _at(
                proof,
                "proofs",
                "A",
                "bounded_mission",
                "mission_id",
            ),
        },
    )

    category_b = {
        "selection": _at(
            proof,
            "proofs",
            "B",
            "recommendation",
            "selected_option_id",
        ),
        "actual_validation": _at(
            proof, "proofs", "B", "actual_validation"
        ),
        "syndicated_inputs": _at(
            proof, "proofs", "B", "syndicated_inputs"
        ),
        "retained": _at(
            proof,
            "proofs",
            "B",
            "syndicated_records_retained",
        ),
    }
    recorder.add(
        "proof.B.library-reuse-and-dedupe",
        category_b == {
            "selection": "reuse-jsonschema",
            "actual_validation": "PASSED",
            "syndicated_inputs": 3,
            "retained": 1,
        },
        detail=(
            "The mature validation library is exercised and syndicated "
            "weak sources do not overcount."
        ),
        actual=category_b,
    )

    category_c = {
        "selection": _at(
            proof,
            "proofs",
            "C",
            "recommendation",
            "selected_option_id",
        ),
        "semantic_only": _at(
            proof,
            "proofs",
            "C",
            "ancestry",
            "semantic_consumption_only",
        ),
        "base_to_owner": _at(
            proof,
            "proofs",
            "C",
            "ancestry",
            "base_is_ancestor_of_owner_context_source",
        ),
        "owner_to_base": _at(
            proof,
            "proofs",
            "C",
            "ancestry",
            "owner_context_source_is_ancestor_of_base",
        ),
        "owner_ref": _at(
            proof,
            "proofs",
            "C",
            "recommendation",
            "owner_context_advisory_refs",
            0,
        ),
    }
    owner_ref = category_c["owner_ref"] or {}
    recorder.add(
        "proof.C.semantic-owner-context-consumption",
        (
            category_c["selection"] == "bounded-decision-contracts"
            and category_c["semantic_only"] is True
            and category_c["base_to_owner"] is False
            and category_c["owner_to_base"] is False
            and owner_ref.get("context_id") == "v13-owner-context-input"
            and owner_ref.get("context_authority") == "ADVISORY_INPUT"
            and owner_ref.get("provenance_authority")
            == "OWNER_CONTEXT_CANDIDATE_INPUT"
        ),
        detail=(
            "The divergent owner-context candidate is used as advisory "
            "provenance, not merged as execution authority."
        ),
        actual=category_c,
    )

    category_d = {
        "status": _at(
            proof, "proofs", "D", "recommendation", "status"
        ),
        "selection": _at(
            proof,
            "proofs",
            "D",
            "recommendation",
            "selected_option_id",
        ),
        "stop": _at(
            proof,
            "proofs",
            "D",
            "stop_assessment",
            "disposition",
        ),
        "action": _at(proof, "proofs", "D", "action"),
        "freshness": _at(
            proof, "proofs", "D", "dossier", "freshness", "status"
        ),
        "gmail": _at(proof, "proofs", "D", "gmail_policy"),
    }
    recorder.add(
        "proof.D.honest-insufficient-evidence",
        (
            category_d["status"] == "INSUFFICIENT_EVIDENCE"
            and category_d["selection"] is None
            and category_d["stop"] == "STOP_INSUFFICIENT_EVIDENCE"
            and category_d["action"] == "WAIT_AND_RESEARCH_LATER"
            and category_d["freshness"] == "STALE"
        ),
        detail=(
            "Stale and unavailable provider evidence produces an honest "
            "wait decision instead of a production-trust claim."
        ),
        actual=category_d,
    )
    gmail = category_d["gmail"] or {}
    recorder.add(
        "proof.D.gmail-deferred",
        gmail == {
            "read": "DEFERRED_BY_OWNER",
            "send": "DEFERRED_BY_OWNER",
            "observation": "DISABLED",
            "authenticated": False,
            "probed": False,
        },
        detail="Gmail remains deferred, disabled, unauthenticated, and unprobed.",
        actual=gmail,
    )

    dossier_paths = (
        ("proofs", "A", "preliminary", "dossier"),
        ("proofs", "A", "final", "dossier"),
        ("proofs", "B", "dossier"),
        ("proofs", "C", "dossier"),
        ("proofs", "D", "dossier"),
    )
    dossiers = [_at(proof, *path) for path in dossier_paths]
    provenance_rows = [
        row
        for dossier in dossiers
        if isinstance(dossier, dict)
        for row in dossier.get("source_provenance", [])
        if isinstance(row, dict)
    ]
    recorder.add(
        "proof.dossiers-and-source-classes",
        (
            all(
                isinstance(dossier, dict)
                and dossier.get("contract") == DOSSIER_CONTRACT
                and bool(dossier.get("source_quality"))
                for dossier in dossiers
            )
            and bool(provenance_rows)
            and all(
                row.get("authority") == "DATA_ONLY"
                and row.get("source_class") in SOURCE_CLASSES
                and bool(row.get("source_group"))
                for row in provenance_rows
            )
        ),
        detail=(
            "Every dossier is v2 and every retrieved source remains "
            "classified DATA_ONLY with explicit source grouping."
        ),
    )
    contradictions = _at(
        proof, "proofs", "A", "final", "dossier", "contradictions"
    ) or []
    recorder.add(
        "proof.explicit-contradictions",
        bool(contradictions) and all(
            isinstance(row, dict)
            and bool(row.get("current_assessment"))
            and bool(row.get("what_would_resolve"))
            for row in contradictions
        ),
        detail="Contradictions remain explicit and carry resolution criteria.",
        actual=len(contradictions),
    )

    recommendations = [
        _at(proof, "proofs", "A", "preliminary", "recommendation"),
        _at(proof, "proofs", "A", "final", "recommendation"),
        _at(proof, "proofs", "B", "recommendation"),
        _at(proof, "proofs", "C", "recommendation"),
        _at(proof, "proofs", "D", "recommendation"),
    ]
    options = [
        option
        for recommendation in recommendations
        if isinstance(recommendation, dict)
        for option in recommendation.get("options", [])
        if isinstance(option, dict)
    ]
    dimension_failures = [
        option.get("option_id")
        for option in options
        if not isinstance(option.get("dimensions"), dict)
        or not REQUIRED_DIMENSIONS.issubset(option["dimensions"])
    ]
    score_findings = [
        finding
        for index, recommendation in enumerate(recommendations)
        if isinstance(recommendation, dict)
        for finding in (
            f"recommendation[{index}].{path}"
            for path in _contains_forbidden_score(recommendation)
        )
    ]
    recorder.add(
        "proof.decision-dimensions",
        bool(options) and not dimension_failures,
        detail="Every serious option evaluates all required decision dimensions.",
        actual=dimension_failures,
        expected=[],
    )
    recorder.add(
        "proof.no-numeric-decision-score",
        not score_findings,
        detail="Decision recommendations do not collapse judgment into a score.",
        actual=score_findings,
        expected=[],
    )

    adversarial = proof.get("adversarial") or {}
    recorder.add(
        "proof.adversarial-inputs",
        (
            adversarial.get("prompt_injection_records", 0) >= 1
            and adversarial.get("prompt_injection_authorities")
            == ["DATA_ONLY"]
            and bool(adversarial.get("stale_source_ids"))
            and adversarial.get("source_outage_disposition")
            == "STOP_INSUFFICIENT_EVIDENCE"
            and adversarial.get("syndicated_inputs") == 3
            and adversarial.get("syndicated_records_retained") == 1
        ),
        detail=(
            "Prompt injection, stale evidence, source outage, and syndication "
            "attacks remain bounded."
        ),
        actual=adversarial,
    )
    projection = proof.get("projection") or {}
    projection_rows = projection.get("dossiers") or []
    recorder.add(
        "proof.projection",
        (
            projection.get("contract") == DOSSIER_CONTRACT
            and projection.get("count") == 4
            and len(projection_rows) == 4
            and all(
                isinstance(row, dict) and bool(row.get("source_quality"))
                for row in projection_rows
            )
        ),
        detail="The read projection exposes four current V13 dossiers.",
        actual={
            "contract": projection.get("contract"),
            "count": projection.get("count"),
        },
    )
    calibration = proof.get("calibration") or {}
    recorder.add(
        "proof.read-only-calibration",
        (
            calibration.get("authority") == "READ_ONLY_CALIBRATION"
            and calibration.get("observations") == 1
            and calibration.get("self_training") is False
        ),
        detail="Calibration observes accepted outcomes without self-training.",
        actual={
            "authority": calibration.get("authority"),
            "observations": calibration.get("observations"),
            "self_training": calibration.get("self_training"),
        },
    )
    recorder.add(
        "proof.event-ledger-counts",
        (
            _at(proof, "decision_state_counts", "plans") == 4
            and _at(
                proof, "decision_state_counts", "recommendations"
            ) == 5
            and _at(proof, "decision_state_counts", "results") == 1
            and _at(proof, "decision_state_counts", "outcomes") == 1
            and _at(
                proof, "event_counts", "executive.objective_created"
            ) == 1
            and _at(proof, "event_counts", "mission.created") == 1
            and _at(
                proof,
                "event_counts",
                "research.decision_result_recorded",
            ) == 1
        ),
        detail=(
            "Canonical event-ledger rebuilds show bounded plans, decisions, "
            "one objective, and one mission."
        ),
        actual={
            "decision_state_counts": proof.get("decision_state_counts"),
            "event_counts": proof.get("event_counts"),
        },
    )
    boundaries = proof.get("boundaries") or {}
    recorder.add(
        "proof.boundaries",
        (
            boundaries.get("live_mutated") is False
            and boundaries.get("main_mutated") is False
            and boundaries.get("origin_main_mutated") is False
            and boundaries.get("main_sha") == expected_main
            and boundaries.get("origin_main_sha") == expected_main
            and boundaries.get("disposable_database") is True
            and boundaries.get("disposable_a3_repository") is True
            and boundaries.get("voice_albanian") == "DEFERRED"
        ),
        detail=(
            "The proof stayed on disposable state and did not claim live, "
            "main, connector, or voice authority."
        ),
        actual=boundaries,
    )

    junit: dict[str, Any] = {}
    for label, path in (
        ("focused", focused_junit),
        ("full", full_junit),
    ):
        if path is None:
            junit[label] = {"status": "NOT_SUPPLIED"}
            if require_junit:
                recorder.add(
                    f"junit.{label}",
                    False,
                    detail=f"The required {label} JUnit report was not supplied.",
                )
            continue
        try:
            report = _parse_junit(path)
        except (OSError, ET.ParseError, ValueError) as exc:
            report = {
                "path": str(path),
                "status": "FAIL",
                "error": str(exc),
            }
        junit[label] = report
        recorder.add(
            f"junit.{label}",
            report.get("status") == "PASS",
            detail=f"The {label} test report has no failures or errors.",
            actual=report,
        )

    expected_summary = _semantic_summary(proof)
    if fresh_replay:
        replay = _fresh_replay(
            repo_root,
            source_sha,
            expected_summary,
            timeout=timeout,
        )
        recorder.add(
            "replay.fresh-separate-process",
            replay.get("status") == "PASS",
            detail=(
                "A separate process replayed the proof from a detached "
                "worktree at the exact source commit."
            ),
            actual={
                key: value
                for key, value in replay.items()
                if key != "summary"
            },
        )
    else:
        replay = {"status": "SKIPPED"}

    return {
        "contract": VERIFIER_CONTRACT,
        "generated_at": _iso_now(),
        "verdict": "PASS" if recorder.passed else "FAIL",
        "assurance": {
            "kind": "DETERMINISTIC_SEPARATE_PROCESS",
            "fresh_source_checkout": fresh_replay,
            "implementation_imported": False,
            "human_or_model_independence_claimed": False,
        },
        "repo_root": str(repo_root),
        "source_sha": source_sha,
        "base_sha": FROZEN_BASE,
        "proof": {
            "path": str(proof_path),
            "sha256": _sha256(proof_path),
            "contract": proof.get("contract"),
        },
        "changed_source_paths": changed_paths,
        "junit": junit,
        "fresh_replay": replay,
        "checks": recorder.checks,
        "failures": recorder.failures,
        "limitations": [
            (
                "This is deterministic separate-process verification, not "
                "a separate human or model-authored review."
            ),
            (
                "Provider production trust, Gmail, voice, promotion, and "
                "live runtime behavior remain outside this isolated proof."
            ),
        ],
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
    )
    parser.add_argument("--source-sha")
    parser.add_argument("--expected-main", default=EXPECTED_MAIN)
    parser.add_argument("--expected-branch", default=EXPECTED_BRANCH)
    parser.add_argument("--proof", type=Path, required=True)
    parser.add_argument("--focused-junit", type=Path)
    parser.add_argument("--full-junit", type=Path)
    parser.add_argument("--require-junit", action="store_true")
    parser.add_argument("--skip-fresh-replay", action="store_true")
    parser.add_argument("--enforce-lane-scope", action="store_true")
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    repo_root = args.repo_root.expanduser().resolve()
    source_sha = args.source_sha or _git_text(
        repo_root, "rev-parse", "HEAD"
    )
    if not source_sha:
        print("unable to resolve source SHA", file=sys.stderr)
        return 2
    proof_path = args.proof.expanduser().resolve()
    report = verify(
        repo_root=repo_root,
        source_sha=source_sha,
        expected_main=args.expected_main,
        expected_branch=args.expected_branch,
        proof_path=proof_path,
        focused_junit=(
            args.focused_junit.expanduser().resolve()
            if args.focused_junit
            else None
        ),
        full_junit=(
            args.full_junit.expanduser().resolve()
            if args.full_junit
            else None
        ),
        require_junit=args.require_junit,
        fresh_replay=not args.skip_fresh_replay,
        enforce_lane_scope=args.enforce_lane_scope,
        timeout=args.timeout,
    )
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        output = args.output.expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding="utf-8")
        print(output)
    else:
        print(rendered, end="")
    return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
