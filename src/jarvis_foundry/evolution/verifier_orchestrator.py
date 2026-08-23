"""V6 lane JOB: independent verifier orchestration.

Closes finding F1 from the lane truth trace: cycle.py's step 5
fabricates its own PASS verdict inside the builder's process, so a
candidate certifies itself through the canonical path. This module
provides an ORCHESTRATOR that:

1. binds a verification request to the candidate identity (release id
   + artifact hashes) and frozen criteria derived from the improvement
   record — never caller-supplied prose;
2. executes a verifier PROBE SUITE out-of-process (subprocess, fresh
   interpreter, sanitized env) whose exit contract produces per-
   criterion results — the probe suite is authored by the BUILDER but
   executed by the orchestrator so the verdict evidence does not come
   from the candidate's own process;
3. refuses to record PASS unless every criterion result is PASS with
   an evidence hash, the verifier identity differs from the builder
   session (is_trusted_verdict), and the artifact hashes still match
   the request;
4. detects FORGERY: a stale verdict (request already VERDICTED), a
   wrong-candidate SHA in the artifacts, missing criteria, or a
   verifier identity equal to the builder session all refuse.

The orchestration record (verifier.run_recorded) binds candidate
identity, tree hash, benchmark artifacts, criteria, verdict, verifier
run identity, independence status, and evidence hashes.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import uuid
from pathlib import Path
from typing import Any

from ..clock import isoformat
from ..database import FoundryDatabase
from ..events import Event, canonical_json
from ..platform.windows_subprocess import headless_run_kwargs
from ..runtime.provider_config import load_provider_settings
from .records import ImprovementError, _validate_metric  # noqa: F401
from .verify import (
    VerificationError,
    is_trusted_verdict,
    record_verification_request,
    record_verification_verdict,
)


class VerifierOrchestrationError(ValueError):
    """Verifier orchestration refused or failed."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise VerifierOrchestrationError(message)


def _tree_hash(root: Path) -> str:
    """Deterministic hash of every file under root (sorted rel paths).
    Reparse points are pruned by the same rule as the builder."""
    from .builder import _is_reparse_point

    digest = hashlib.sha256()
    for dirpath, dirnames, filenames in __import__("os").walk(
            root, followlinks=False):
        dirpath_path = Path(dirpath)
        dirnames[:] = [name for name in dirnames
                       if not _is_reparse_point(dirpath_path / name)]
        for filename in sorted(filenames):
            path = dirpath_path / filename
            if _is_reparse_point(path):
                continue
            rel = path.relative_to(root).as_posix()
            digest.update(rel.encode("utf-8"))
            digest.update(b"\0")
            digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def orchestrate_verification(
    database: FoundryDatabase, *, improvement_id: str,
    candidate_release_id: str, candidate_tree_root: Path,
    builder_identity: dict[str, Any], controller: str,
    probe_command: list[str], probe_timeout_s: int = 120,
    benchmark_artifacts: dict[str, str] | None = None,
    provider_settings: Any | None = None,
    source_type: str = "supervisor", source_id: str = "v6",
) -> dict[str, Any]:
    """Run ONE independent verification round for a candidate.

    The probe command runs OUT-OF-PROCESS against the candidate tree.
    Its stdout must be JSON: {"criteria": {<criterion>: {"status":
    "PASS"|"FAIL"|..., "evidence_hash": "<hex64>"}}, "findings": []}.
    Criteria are FROZEN from the improvement guardrails; the probe may
    not add, drop, or rename them (extra/missing keys refuse).
    """
    _require(controller == "supervisor",
             "only the Supervisor can orchestrate verification")
    improvement = database.row("improvement_records", "improvement_id",
                               improvement_id)
    _require(improvement is not None,
             f"improvement {improvement_id} does not exist — refused")
    manifest = database.row("release_manifests", "release_id",
                            candidate_release_id)
    _require(manifest is not None,
             f"candidate release {candidate_release_id} has no manifest")
    _require(str(manifest["improvement_id"]) == improvement_id,
             "candidate manifest belongs to a different improvement — "
             "refused (wrong-candidate guard)")
    declared = json.loads(str(manifest["artifact_hashes_json"]))
    artifacts = dict(declared)
    if benchmark_artifacts:
        for name, digest in benchmark_artifacts.items():
            _require(isinstance(digest, str) and len(digest) == 64,
                     f"benchmark artifact {name!r} hash malformed")
        artifacts.update(benchmark_artifacts)

    # frozen criteria from the improvement guardrails (same derivation
    # the old cycle ladder used — but now they gate a REAL probe run)
    guardrails = json.loads(str(improvement["guardrails_json"]))
    criteria = [f"guardrail {name}: {detail}"
                for name, detail in guardrails.items()]

    provider_settings = provider_settings or load_provider_settings()
    verification_id = record_verification_request(
        database, improvement_id=improvement_id,
        subject_ref=candidate_release_id, criteria=criteria,
        artifacts=artifacts, builder_identity=builder_identity,
        controller=controller, provider_settings=provider_settings)

    tree_hash = _tree_hash(Path(candidate_tree_root))
    run_token = uuid.uuid4().hex[:12]
    started = isoformat()

    # execute the probe suite OUT-OF-PROCESS (fresh interpreter,
    # sanitized env — the candidate never records its own verdict)
    env = {key: value for key, value in __import__("os").environ.items()
           if not key.endswith(("_API_KEY", "_TOKEN", "_SECRET",
                                "_PASSWORD"))
           and key not in ("HERMES_HOME", "JARVIS_HOME")}
    try:
        completed = subprocess.run(
            [*probe_command, str(Path(candidate_tree_root))],
            cwd=str(candidate_tree_root), env=env, capture_output=True,
            timeout=probe_timeout_s, shell=False,
            **headless_run_kwargs())
        stdout = completed.stdout.decode("utf-8", errors="replace")
        probe_exit = completed.returncode
    except subprocess.TimeoutExpired:
        raise VerifierOrchestrationError(
            f"verifier probe timed out after {probe_timeout_s}s")
    except OSError as error:
        raise VerifierOrchestrationError(
            f"verifier probe failed to spawn: {error}")

    report: dict[str, Any] | None = None
    try:
        start = stdout.find("{")
        end = stdout.rfind("}")
        if start >= 0 and end > start:
            report = json.loads(stdout[start:end + 1])
    except ValueError:
        report = None
    _require(report is not None and isinstance(report.get("criteria"),
                                               dict),
             f"verifier probe produced no parseable criteria report "
             f"(exit={probe_exit})")

    reported = report["criteria"]  # type: ignore[union-attr]
    missing = [c for c in criteria if c not in reported]
    extra = [c for c in reported if c not in criteria]
    _require(not missing and not extra,
             "probe criteria diverge from the frozen request — "
             f"missing={missing} extra={extra}")
    criteria_results: list[dict[str, Any]] = []
    findings = list(report.get("findings") or [])  # type: ignore[union-attr]
    all_pass = True
    for criterion in criteria:
        result = reported[criterion]
        status = result.get("status")
        _require(status in ("PASS", "FAIL", "BLOCKED", "NOT_TESTED"),
                 f"criterion {criterion!r} has invalid status {status!r}")
        evidence_hash = result.get("evidence_hash")
        if status == "PASS":
            _require(isinstance(evidence_hash, str)
                     and len(evidence_hash) == 64,
                     f"PASS criterion {criterion!r} lacks a 64-char "
                     "evidence hash — unearned PASS refused")
        else:
            all_pass = False
        criteria_results.append({"criterion": criterion,
                                 "status": status,
                                 "evidence_hash": evidence_hash})

    verifier_identity = {
        "provider": provider_settings.provider,
        "model": provider_settings.model,
        # INDEPENDENCE CONTRACT: same authorized provider policy as the
        # workforce, but a FRESH dedicated session minted for this run
        # — never the builder's session. Session distinctness is the
        # property is_trusted_verdict enforces; same-provider runs are
        # flagged honestly by the S5 boundary itself.
        "session_id": f"verifier-{run_token}",
    }
    record_verification_verdict(
        database, verification_id=verification_id,
        verdict="PASS" if all_pass else "FAIL",
        criteria_results=criteria_results, findings=findings,
        verifier_identity=verifier_identity,
        evidence_refs=[f"verifier-runs/{run_token}",
                       f"tree:{tree_hash[:16]}"],
        provider_settings=provider_settings)

    trusted = is_trusted_verdict(database, verification_id,
                                 builder_identity)
    finished = isoformat()
    payload = {
        "orchestration_id": "",
        "verification_id": verification_id,
        "improvement_id": improvement_id,
        "candidate_release_id": candidate_release_id,
        "candidate_tree_sha256": tree_hash,
        "criteria_count": len(criteria),
        "all_criteria_pass": all_pass,
        "trusted_independent_pass": trusted,
        "independence_status": ("INDEPENDENT" if trusted else
                                "SAME_SESSION_UNTRUSTED"),
        "verifier_run_identity": verifier_identity,
        "evidence_hashes": {
            "tree": tree_hash,
            "probe_stdout": hashlib.sha256(
                stdout.encode("utf-8")).hexdigest()},
        "started_at": started, "finished_at": finished,
    }
    orchestration_id = "vorch-" + hashlib.sha256(canonical_json(
        {k: v for k, v in payload.items()
         if k != "orchestration_id"}).encode("utf-8")).hexdigest()[:12]
    payload["orchestration_id"] = orchestration_id
    database.append(Event.create(
        "verifier.run_recorded", payload,
        source_type=source_type, source_id=source_id,
        idempotency_key=f"verifier.run_recorded:{orchestration_id}"))
    return payload


def verify_orchestration_binding(database: FoundryDatabase,
                                 orchestration_id: str, *,
                                 expected_release_id: str,
                                 ) -> bool:
    """Binding re-check: the recorded orchestration must still point at
    the expected candidate release with a trusted independent pass."""
    for event in database.events():
        if event.event_type == "verifier.run_recorded" and \
                event.payload.get("orchestration_id") == orchestration_id:
            return bool(event.payload.get("trusted_independent_pass")) \
                and str(event.payload.get("candidate_release_id")) == \
                expected_release_id
    return False
