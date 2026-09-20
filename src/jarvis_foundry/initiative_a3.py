"""V11 A3 autonomous candidate admission through the existing authority spine.

The A2 Executive decision remains the only selector.  This module is the one
admission authority that rechecks mutable safety policy, prepares an isolated
Git worktree, creates an Executive objective/plan, and hands it to the normal
Continuous Executive/portfolio/MissionLoop path.  It does not execute a
worker, verify output, or write outcome-learning state itself.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .control.broker import emergency_stop_reason, matching_veto_reason
from .control.capabilities import set_capability_state
from .control.registration import inspect_git_resource, registration_event
from .database import FoundryDatabase
from .events import Event, canonical_json
from .executive.autonomy import (
    PlanStep,
    build_plan,
    create_objective,
    rebuild_executive_state,
    run_autonomy_cycle,
)
from .executive.decisions import ExecutiveDecision
from .fabric.catalog import get_spec, observed_readiness
from .mission.intent import (
    CANDIDATE_BUILD_CLASSES,
    CANDIDATE_NEEDS,
    MissionIntentError,
    intent_from_observation,
    needs_for_candidate_class,
)
from .mission.verification import accepted_verifications


_TERMINAL_OBJECTIVE = frozenset({"COMPLETED", "FAILED", "CANCELLED", "SUSPENDED"})
# The admitted set is the binder's own declared class table: a class that has
# no deterministic need binding cannot be admitted, and a class the binder can
# bind is not refused merely because it is a different kind of useful work.
# The previous hand-written four-class list refused discovered project-health
# work (project_staleness) while Lane A discovery emitted it - the observed
# stall this convergence repairs.
_ALLOWED_ACTION_CLASSES = frozenset(CANDIDATE_NEEDS) | CANDIDATE_BUILD_CLASSES


@dataclass(frozen=True, slots=True)
class A3AdmissionPolicy:
    """Fixed small/reversible autonomous admission bounds."""

    autonomous_wip_cap: int = 1
    build_budget_units: float = 1.0
    max_missions: int = 2
    max_retries: int = 1
    min_confidence: float = 0.20
    min_reversibility: float = 0.30
    required_fabric_capabilities: tuple[str, ...] = (
        "cap.file.read",
        "cap.file.write",
        "cap.script.run",
        "cap.git.inspect",
        "cap.git.diff",
        "cap.git.commit",
        "cap.tests.run",
    )
    allowed_action_classes: frozenset[str] = _ALLOWED_ACTION_CLASSES

    def __post_init__(self) -> None:
        if self.autonomous_wip_cap != 1:
            raise ValueError("V11 A3 autonomous WIP cap is fixed at 1")
        if not math.isfinite(self.build_budget_units) or self.build_budget_units != 1.0:
            raise ValueError("V11 A3 build budget is fixed at one small objective")
        if not 1 <= self.max_missions <= 2:
            raise ValueError("A3 allows one mission plus at most one bounded retry")
        if self.max_retries != 1:
            raise ValueError("A3 bounded objective retry count is fixed at 1")
        if not 0.0 <= self.min_confidence <= 1.0:
            raise ValueError("min_confidence must be within [0, 1]")
        if not 0.0 <= self.min_reversibility <= 1.0:
            raise ValueError("min_reversibility must be within [0, 1]")


@dataclass(frozen=True, slots=True)
class A3Admission:
    candidate_id: str
    objective_id: str | None
    mission_id: str | None
    worktree: str | None
    resource_id: str | None
    status: str
    reason: str
    replayed: bool = False

    @property
    def admitted(self) -> bool:
        return self.status == "ADMITTED"

    def to_payload(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "objective_id": self.objective_id,
            "mission_id": self.mission_id,
            "worktree": self.worktree,
            "resource_id": self.resource_id,
            "status": self.status,
            "reason": self.reason,
            "replayed": self.replayed,
        }


def _utc(value: datetime | str | None = None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _canonical(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", text.lower()))


def _json(value: Any, default: Any) -> Any:
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError):
        return default
    return parsed


def _run_git(root: Path, *args: str, timeout: int = 60) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout.strip()


def _event_payload(database: FoundryDatabase, event_type: str, candidate_id: str) -> dict[str, Any] | None:
    for event in reversed(database.events(event_types=(event_type,), readonly=True)):
        if str(event.payload.get("candidate_id") or "") == candidate_id:
            return {**dict(event.payload), "event_id": event.event_id}
    return None


def _judgment(database: FoundryDatabase, candidate_id: str) -> dict[str, Any] | None:
    with database.connect(readonly=True) as connection:
        row = connection.execute(
            "SELECT * FROM candidate_judgments WHERE candidate_id = ? "
            "ORDER BY rowid DESC LIMIT 1",
            (candidate_id,),
        ).fetchone()
    return dict(row) if row is not None else None


def _recorded_select(database: FoundryDatabase, decision_id: str, candidate_id: str) -> bool:
    for event in database.events(event_types=("executive.decision_recorded",), readonly=True):
        if (
            str(event.payload.get("decision_id") or "") == decision_id
            and str(event.payload.get("candidate_id") or "") == candidate_id
            and str(event.payload.get("decision") or "") == "SELECT"
        ):
            return True
    return False


def pending_initiative_decision(database: FoundryDatabase) -> ExecutiveDecision | None:
    """Recover the newest durable A2 SELECT that has not crossed admission.

    A2 records the decision and final eligibility judgment atomically before
    A3 is allowed to create an objective.  A process can therefore stop in
    that intentional seam.  Replaying a new observation cycle would abandon
    the already-durable choice, so A3 resumes it by identity instead.  The
    admission authority still rechecks STOP, freshness, WIP, budget,
    capability readiness, and every other mutable boundary.
    """
    admitted = {
        str(event.payload.get("candidate_id") or "")
        for event in database.events(
            event_types=("initiative.candidate_admitted",), readonly=True,
        )
    }
    decisions = database.events(
        event_types=("executive.decision_recorded",), readonly=True,
    )
    for event in reversed(decisions):
        payload = dict(event.payload)
        if str(payload.get("decision") or "") != "SELECT":
            continue
        candidate_id = str(payload.get("candidate_id") or "")
        decision_id = str(payload.get("decision_id") or "")
        if not candidate_id or not decision_id or candidate_id in admitted:
            continue
        candidate = database.row("candidates", "candidate_id", candidate_id)
        judgment = _judgment(database, candidate_id)
        if (
            candidate is None
            or str(candidate.get("status") or "") != "ELIGIBLE"
            or judgment is None
            or str(judgment.get("decision_id") or "") != decision_id
            or str(judgment.get("outcome") or "") != "ELIGIBLE"
            or str(judgment.get("reason_code") or "") != "READY_FOR_ADMISSION"
        ):
            continue
        return ExecutiveDecision(
            kind="SELECT",
            decision_id=decision_id,
            reason=str(payload.get("reason") or "resumed durable A2 selection"),
            candidate_id=candidate_id,
            rejected=tuple(payload.get("rejected") or ()),
            deferred=tuple(payload.get("deferred") or ()),
            factor_breakdown=dict(payload.get("factor_breakdown") or {}),
            active_goals=tuple(payload.get("active_goals") or ()),
            eligibility=dict(payload.get("eligibility") or {}),
            calibration_version=str(payload.get("calibration_version") or ""),
            owner_controls=dict(payload.get("owner_controls") or {}),
        )
    return None


def _candidate_context(candidate: Mapping[str, Any]) -> tuple[list[dict[str, Any]], str, tuple[str, ...]]:
    evidence = _json(candidate.get("evidence_json"), [])
    rows = [dict(row) for row in evidence if isinstance(row, Mapping)] if isinstance(evidence, list) else []
    project = next((str(row.get("target_project") or "") for row in rows if row.get("target_project")), "")
    capabilities = tuple(dict.fromkeys(
        str(value)
        for row in rows
        for value in (row.get("required_capabilities") or ())
        if str(value)
    ))
    return rows, project, capabilities


def _candidate_intent(
    *,
    candidate_id: str,
    candidate_type: str,
    objective_text: str,
    objective_id: str,
    evidence: Sequence[Mapping[str, Any]],
    prepared: Mapping[str, str],
) -> tuple[Any, str, bool]:
    """Derive the normalized intent for one admitted candidate.

    The Executive side answers "what is worth doing"; the binder's declared
    class table answers "which normalized needs that implies".  Only
    runtime-owned facts are supplied as input roles, so no recipe id and no
    registry-shaped parameter block crosses this boundary.

    Returns ``(intent, error, bindable)``.  ``bindable`` is False for a class
    the binder cannot yet express (a bounded repository change): those keep
    the existing admitted-objective build path instead of being downgraded to
    a read-only review.
    """
    if not needs_for_candidate_class(candidate_type):
        return None, "", False
    try:
        intent = intent_from_observation(
            candidate_id=candidate_id,
            candidate_type=candidate_type,
            objective=objective_text,
            resource_id=str(prepared["resource_id"]),
            objective_id=objective_id,
            evidence_rows=evidence,
            supplied_inputs={
                "owned_repository_root": str(prepared["worktree"]),
                "repository_branch": str(prepared["branch"]),
            },
            success_criteria=(
                "the review reads the repository state the observation reported",
                "the independent verifier accepts the recorded receipts",
            ),
            constraints=(
                "reads only in the isolated worktree",
                "no public writes, live runtime, production database or Scheduler mutation",
            ),
        )
    except MissionIntentError as error:
        return None, str(error), True
    return intent, "", True


def _factor_value(judgment: Mapping[str, Any], name: str) -> float | None:
    factors = _json(judgment.get("factors_json"), {})
    raw = factors.get(name) if isinstance(factors, Mapping) else None
    if isinstance(raw, Mapping):
        raw = raw.get("value")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _active_autonomous_objectives(database: FoundryDatabase) -> list[str]:
    return sorted(
        objective.objective_id
        for objective in rebuild_executive_state(database).objectives.values()
        if objective.status not in _TERMINAL_OBJECTIVE
        and str(objective.owner_authority or "owner").lower() != "owner"
    )


def _consumed_build_budget(database: FoundryDatabase) -> float:
    consumed = 0.0
    for event in database.events(
        event_types=("initiative.candidate_admitted",), readonly=True,
    ):
        try:
            units = float(event.payload.get("build_cost_units", 1.0))
        except (TypeError, ValueError):
            units = 1.0
        if math.isfinite(units) and units > 0:
            consumed += units
    return consumed


def _equivalent_work(database: FoundryDatabase, objective: str, objective_id: str) -> str | None:
    target = _canonical(objective)
    state = rebuild_executive_state(database)
    for row in state.objectives.values():
        if row.objective_id != objective_id and _canonical(row.objective) == target:
            return f"objective:{row.objective_id}:{row.status}"
    with database.connect(readonly=True) as connection:
        missions = connection.execute(
            "SELECT mission_id, objective, state FROM missions ORDER BY rowid DESC LIMIT 1000"
        ).fetchall()
    for row in missions:
        if _canonical(str(row["objective"] or "")) == target:
            return f"mission:{row['mission_id']}:{row['state']}"
    return None


def _resolve_project_root(
    database: FoundryDatabase,
    candidate_id: str,
    project: str,
    project_roots: Mapping[str, str | Path],
) -> Path | None:
    for key in (project, project.lower()):
        if key in project_roots:
            return Path(project_roots[key]).expanduser().resolve()
    lowered = {str(key).lower(): value for key, value in project_roots.items()}
    if project.lower() in lowered:
        return Path(lowered[project.lower()]).expanduser().resolve()
    with database.connect(readonly=True) as connection:
        rows = connection.execute(
            "SELECT source_kind, source_identity FROM candidate_sources "
            "WHERE candidate_id = ? ORDER BY rowid",
            (candidate_id,),
        ).fetchall()
    for row in rows:
        if str(row["source_kind"] or "").lower() == "project":
            root = Path(str(row["source_identity"] or "")).expanduser()
            if root.is_absolute():
                return root.resolve()
    return None


def _prepare_worktree(
    database: FoundryDatabase,
    *,
    candidate_id: str,
    fingerprint: str,
    source_root: Path,
    worktree_parent: Path,
) -> dict[str, str]:
    if not source_root.is_dir():
        raise RuntimeError("configured owner repository is unavailable")
    top = Path(_run_git(source_root, "rev-parse", "--show-toplevel")).resolve()
    if _run_git(top, "status", "--porcelain=v1"):
        raise RuntimeError("owner repository is dirty; isolated admission refuses ambiguous source")
    base_sha = _run_git(top, "rev-parse", "HEAD")
    suffix = (fingerprint or hashlib.sha256(candidate_id.encode("utf-8")).hexdigest())[:16]
    branch = f"candidate/v11-a3-autonomous-{suffix}"
    worktree_parent.mkdir(parents=True, exist_ok=True)
    worktree = (worktree_parent / f"a3-{suffix}").resolve()

    if worktree.exists():
        resolved_top = Path(_run_git(worktree, "rev-parse", "--show-toplevel")).resolve()
        if resolved_top != worktree:
            raise RuntimeError("existing A3 worktree path resolves to another root")
        if _run_git(worktree, "branch", "--show-current") != branch:
            raise RuntimeError("existing A3 worktree has the wrong branch identity")
    else:
        branch_exists = subprocess.run(
            ["git", "-C", str(top), "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"],
            capture_output=True,
            timeout=30,
            check=False,
        ).returncode == 0
        args = ("worktree", "add", str(worktree), branch) if branch_exists else (
            "worktree", "add", "-b", branch, str(worktree), base_sha
        )
        _run_git(top, *args, timeout=120)

    if _run_git(worktree, "status", "--porcelain=v1"):
        raise RuntimeError("prepared A3 worktree is not clean")
    resource_id = f"initiative-resource:{candidate_id}"
    resource = inspect_git_resource(worktree, resource_id=resource_id)
    database.append(registration_event(resource, source_id="v11-a3-admission"))
    capability_evidence = {"candidate_id": candidate_id, "branch": branch, "base_sha": base_sha}
    for capability in (
        "write_registered_worktree_files",
        "run_registered_local_commands",
        "git_local_commit",
    ):
        set_capability_state(
            database,
            resource_id,
            capability,
            "READY",
            source_id="v11-a3-admission",
            evidence=capability_evidence,
        )
    database.append(Event.create(
        "initiative.worktree_prepared",
        {
            "candidate_id": candidate_id,
            "resource_id": resource_id,
            "source_root": str(top),
            "worktree": str(worktree),
            "branch": branch,
            "base_sha": base_sha,
        },
        source_type="supervisor",
        source_id="v11-a3-admission",
        idempotency_key=f"initiative.worktree_prepared:{candidate_id}",
    ))
    return {
        "source_root": str(top),
        "worktree": str(worktree),
        "branch": branch,
        "base_sha": base_sha,
        "resource_id": resource_id,
    }


def _blocked(
    database: FoundryDatabase,
    candidate_id: str,
    reason_code: str,
    reason: str,
    *,
    details: Mapping[str, Any] | None = None,
) -> A3Admission:
    payload = {
        "candidate_id": candidate_id,
        "status": "DEFERRED",
        "reason_code": reason_code,
        "reason": reason,
        "details": dict(details or {}),
    }
    digest = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()[:20]
    database.append(Event.create(
        "initiative.candidate_admission_blocked",
        payload,
        source_type="supervisor",
        source_id="v11-a3-admission",
        idempotency_key=f"initiative.candidate_admission_blocked:{candidate_id}:{digest}",
    ))
    return A3Admission(candidate_id, None, None, None, None, "DEFERRED", reason)


class InitiativeAdmissionAuthority:
    """The single A2-candidate to Executive-objective admission authority."""

    def __init__(
        self,
        database: FoundryDatabase,
        *,
        worktree_parent: str | Path,
        project_roots: Mapping[str, str | Path],
        policy: A3AdmissionPolicy | None = None,
        stop_path: str | Path | None = None,
    ) -> None:
        self.database = database
        raw_parent = Path(worktree_parent).expanduser()
        if not str(worktree_parent).strip() or not raw_parent.is_absolute():
            raise ValueError("A3 worktree_parent must be an explicit absolute path")
        self.worktree_parent = raw_parent.resolve()
        self.project_roots = dict(project_roots)
        self.policy = policy or A3AdmissionPolicy()
        self.stop_path = Path(stop_path).expanduser().resolve() if stop_path else None

    def admit(
        self,
        decision: ExecutiveDecision,
        *,
        now: datetime | str | None = None,
        replacing_objective_id: str | None = None,
    ) -> A3Admission:
        candidate_id = str(decision.candidate_id or "")
        if decision.kind != "SELECT" or not candidate_id:
            return A3Admission(candidate_id, None, None, None, None, "WAIT", "A2 selected no candidate")

        prior = _event_payload(self.database, "initiative.candidate_admitted", candidate_id)
        if prior is not None:
            objective_id = str(prior.get("objective_id") or "") or None
            state = rebuild_executive_state(self.database)
            mission_id = None
            if objective_id and objective_id in state.objectives:
                mission_id = next(iter(state.objectives[objective_id].mission_ids), None)
            return A3Admission(
                candidate_id,
                objective_id,
                mission_id,
                str(prior.get("worktree") or "") or None,
                str(prior.get("resource_id") or "") or None,
                "ADMITTED",
                "existing durable candidate-to-objective linkage",
                replayed=True,
            )

        stop = emergency_stop_reason(self.database, self.stop_path)
        if stop:
            return _blocked(self.database, candidate_id, "STOP_ACTIVE", f"STOP active: {stop}")
        candidate = self.database.row("candidates", "candidate_id", candidate_id)
        if candidate is None or str(candidate.get("status") or "") != "ELIGIBLE":
            return _blocked(self.database, candidate_id, "NOT_ELIGIBLE", "candidate is no longer A2 ELIGIBLE")
        judgment = _judgment(self.database, candidate_id)
        if judgment is None or str(judgment.get("outcome") or "") != "ELIGIBLE" \
                or str(judgment.get("reason_code") or "") != "READY_FOR_ADMISSION":
            return _blocked(self.database, candidate_id, "INVALID_JUDGMENT", "current A2 eligibility judgment is unavailable")
        if str(judgment.get("decision_id") or "") != decision.decision_id:
            return _blocked(self.database, candidate_id, "DECISION_MISMATCH", "A2 decision and judgment identities differ")
        if not _recorded_select(self.database, decision.decision_id, candidate_id):
            return _blocked(self.database, candidate_id, "DECISION_MISSING", "durable A2 SELECT decision is unavailable")

        expires_at = str(candidate.get("expires_at") or "").strip()
        if not expires_at or _utc(expires_at) <= _utc(now):
            return _blocked(self.database, candidate_id, "STALE", "candidate is stale at admission")
        evidence, project, candidate_capabilities = _candidate_context(candidate)
        provenance_hash = str(candidate.get("provenance_hash") or "")
        if not evidence or len(provenance_hash) != 64:
            return _blocked(self.database, candidate_id, "INSUFFICIENT_EVIDENCE", "candidate evidence/provenance is no longer valid")
        confidence = _factor_value(judgment, "confidence")
        if confidence is None or confidence < self.policy.min_confidence:
            return _blocked(self.database, candidate_id, "LOW_CONFIDENCE", "candidate confidence is below the A2 floor")
        reversibility = _factor_value(judgment, "reversibility")
        if reversibility is None or reversibility < self.policy.min_reversibility:
            return _blocked(self.database, candidate_id, "REVERSIBILITY_POLICY_BOUNDARY", "candidate is outside the reversible action class")
        action_class = str(candidate.get("candidate_type") or "")
        if action_class not in self.policy.allowed_action_classes:
            return _blocked(
                self.database,
                candidate_id,
                "ACTION_CLASS_NOT_ALLOWED",
                f"candidate action class {action_class or 'UNKNOWN'} is outside the admitted "
                "owner-repository policy",
            )
        objective_text = str(candidate.get("objective") or "").strip()
        objective_id = f"initiative-objective:{candidate_id}"
        equivalent = _equivalent_work(self.database, objective_text, objective_id)
        if equivalent:
            return _blocked(self.database, candidate_id, "EQUIVALENT_ACTIVE_OR_COMPLETE", f"equivalent work already exists: {equivalent}")
        active = _active_autonomous_objectives(self.database)
        if len(active) >= self.policy.autonomous_wip_cap:
            return _blocked(self.database, candidate_id, "WIP_LIMIT", "autonomous WIP cap 1 is occupied", details={"active": active})
        consumed_budget = _consumed_build_budget(self.database)
        replacement_credit = 0.0
        if replacing_objective_id:
            replacement = rebuild_executive_state(self.database).objectives.get(
                str(replacing_objective_id)
            )
            linked = any(
                str(event.payload.get("objective_id") or "")
                == str(replacing_objective_id)
                for event in self.database.events(
                    event_types=("initiative.execution_linked",), readonly=True
                )
            )
            if (
                replacement is not None
                and replacement.status == "SUSPENDED"
                and replacement.metadata.get("initiative") == "V11_A3"
                and not linked
            ):
                # The old objective held the one build-unit reservation but
                # never produced a mission. A research-driven replacement
                # transfers that unused reservation; it does not enlarge the
                # budget or bypass the unchanged WIP cap.
                replacement_credit = 1.0
        effective_consumed = max(0.0, consumed_budget - replacement_credit)
        if effective_consumed + 1.0 > self.policy.build_budget_units:
            return _blocked(
                self.database,
                candidate_id,
                "BUDGET_EXCEEDED",
                "the one-objective A3 build budget is already consumed",
                details={
                    "consumed_units": consumed_budget,
                    "replacement_credit": replacement_credit,
                    "effective_consumed_units": effective_consumed,
                },
            )

        source_root = _resolve_project_root(self.database, candidate_id, project, self.project_roots)
        identities = {"GLOBAL", candidate_id, project, str(source_root or "")}
        identities.discard("")
        veto = matching_veto_reason(self.database, identities)
        if veto:
            return _blocked(self.database, candidate_id, "OWNER_VETO", f"owner veto active: {veto}")
        if source_root is None:
            return _blocked(self.database, candidate_id, "TARGET_UNAVAILABLE", "candidate has no explicit owner-repository mapping")

        required = tuple(dict.fromkeys((*candidate_capabilities, *self.policy.required_fabric_capabilities)))
        missing: list[str] = []
        for capability_id in required:
            spec = get_spec(self.database, capability_id)
            readiness = observed_readiness(self.database, capability_id)
            unattended = getattr(getattr(spec, "unattended", None), "value", "UNKNOWN")
            if spec is None or readiness.state.value != "READY" or unattended != "ALLOWED":
                missing.append(f"{capability_id}={readiness.state.value}/{unattended}")
        if missing:
            return _blocked(
                self.database,
                candidate_id,
                "CAPABILITY_UNAVAILABLE",
                "required canonical Fabric capabilities unavailable: " + ", ".join(missing),
            )

        try:
            prepared = _prepare_worktree(
                self.database,
                candidate_id=candidate_id,
                fingerprint=str(candidate.get("fingerprint") or ""),
                source_root=source_root,
                worktree_parent=self.worktree_parent,
            )
        except (OSError, RuntimeError, subprocess.SubprocessError) as error:
            return _blocked(self.database, candidate_id, "WORKTREE_BLOCKED", str(error)[:500])

        intent, intent_error, intent_bindable = _candidate_intent(
            candidate_id=candidate_id,
            candidate_type=action_class,
            objective_text=objective_text,
            objective_id=objective_id,
            evidence=evidence,
            prepared=prepared,
        )
        if intent_bindable and intent is None:
            return _blocked(
                self.database,
                candidate_id,
                "INTENT_UNBINDABLE",
                f"observed work has no normalized intent: {intent_error}",
            )
        intent_payload = intent.as_dict() if intent is not None else None

        score = float(judgment.get("score") or 0.5)
        objective = create_objective(
            self.database,
            objective_text,
            objective_id=objective_id,
            owner_authority="v11-a3-autonomous-initiative",
            constraints=(
                "small reversible change in the isolated worktree only",
                "no merge, push, deploy, email, live runtime, production database, or Scheduler mutation",
            ),
            success_criteria=(
                "a real repository improvement satisfies the admitted candidate",
                "relevant local tests pass",
                "the independent verifier records verification.accepted",
            ),
            failure_criteria=(
                "build or tests remain failed after bounded repair",
                "independent verifier rejects the output",
            ),
            autonomy_boundaries=("C1", "LOCAL_ONLY", "REVERSIBLE", "NO_PUBLIC_WRITES"),
            budget={
                "build_units": self.policy.build_budget_units,
                "max_missions": float(self.policy.max_missions),
                "max_retries": float(self.policy.max_retries),
            },
            expected_impact=max(0.0, min(1.0, score)),
            effort=0.25,
            risk=0.15,
            reversibility=reversibility,
            confidence=confidence,
            resource_id=prepared["resource_id"],
            required_capabilities=("write_registered_worktree_files",),
            provenance={
                "candidate_id": candidate_id,
                "candidate_fingerprint": candidate.get("fingerprint"),
                "a2_decision_id": decision.decision_id,
                "a2_judgment_id": judgment.get("judgment_id"),
                "candidate_provenance_hash": provenance_hash,
            },
            metadata={
                "initiative": "V11_A3",
                "project_id": project,
                "candidate_type": action_class,
                "source_root": prepared["source_root"],
                "worktree": prepared["worktree"],
                "branch": prepared["branch"],
                "base_sha": prepared["base_sha"],
                **({"intent": intent_payload} if intent_payload else {}),
            },
            source_id="v11-a3-admission",
        )
        build_plan(
            self.database,
            objective.objective_id,
            (
                PlanStep(
                    step_id="execute",
                    objective_id=objective.objective_id,
                    description=(
                        f"Perform the admitted candidate in the isolated worktree: {objective_text}. "
                        "Make the smallest useful repository change and run its relevant tests."
                    ),
                    required_capabilities=("write_registered_worktree_files",),
                    resource_id=prepared["resource_id"],
                    output_contract=("real repository diff", "recorded local test result"),
                    verification_contract=("independent verifier accepts direct worktree and test evidence",),
                    max_retries=self.policy.max_retries,
                ),
            ),
            plan_id=f"initiative-plan:{candidate_id}",
            source_id="v11-a3-admission",
        )
        self.database.append(Event.create(
            "initiative.candidate_admitted",
            {
                "candidate_id": candidate_id,
                "candidate_fingerprint": candidate.get("fingerprint"),
                "decision_id": decision.decision_id,
                "judgment_id": judgment.get("judgment_id"),
                "objective_id": objective.objective_id,
                "plan_id": f"initiative-plan:{candidate_id}",
                "resource_id": prepared["resource_id"],
                "source_root": prepared["source_root"],
                "worktree": prepared["worktree"],
                "branch": prepared["branch"],
                "base_sha": prepared["base_sha"],
                "required_fabric_capabilities": list(required),
                "wip_cap": self.policy.autonomous_wip_cap,
                "build_budget_units": self.policy.build_budget_units,
                "build_cost_units": 1.0,
                "replacing_objective_id": replacing_objective_id,
                "replacement_credit_units": replacement_credit,
                "action_class": action_class,
                "reversibility": reversibility,
                "confidence": confidence,
                "intent_id": (intent_payload or {}).get("intent_id"),
                "intent_needs": list((intent_payload or {}).get("needs") or ()),
                "intent_source": (intent_payload or {}).get("source"),
            },
            source_type="supervisor",
            source_id="v11-a3-admission",
            correlation_id=objective.objective_id,
            idempotency_key=f"initiative.candidate_admitted:{candidate_id}",
        ))
        return A3Admission(
            candidate_id,
            objective.objective_id,
            None,
            prepared["worktree"],
            prepared["resource_id"],
            "ADMITTED",
            "A2 ELIGIBLE candidate admitted to the existing Executive",
        )


def active_initiative_objective(database: FoundryDatabase) -> str | None:
    state = rebuild_executive_state(database)
    retryable: set[str] = set()
    for plan in state.plans.values():
        if any(
            step.status == "FAILED" and step.retries < step.max_retries
            for step in plan.steps
        ):
            retryable.add(plan.objective_id)
    active = sorted(
        objective.objective_id
        for objective in state.objectives.values()
        if (objective.status not in _TERMINAL_OBJECTIVE or objective.objective_id in retryable)
        and objective.metadata.get("initiative") == "V11_A3"
    )
    return active[0] if active else None


def continue_initiative_objective(
    database: FoundryDatabase,
    objective_id: str,
    *,
    cycle_id: str,
) -> A3Admission:
    state = rebuild_executive_state(database)
    objective = state.objectives.get(objective_id)
    if objective is None:
        return A3Admission("", None, None, None, None, "WAIT", "initiative objective unavailable")
    candidate_id = str(objective.provenance.get("candidate_id") or "")
    cycle = run_autonomy_cycle(
        database,
        cycle_id=cycle_id,
        resource_id=objective.resource_id,
        capability="write_registered_worktree_files",
        max_new_missions=1,
    )
    # ``run_autonomy_cycle`` remains the canonical Executive selector and may
    # legitimately choose a higher-priority owner objective.  Never attach
    # that mission to this initiative: a candidate/objective link is valid
    # only when the selected objective identity is the one being continued.
    if cycle.objective_id != objective_id:
        selected = cycle.objective_id or "no objective"
        return A3Admission(
            candidate_id,
            objective_id,
            None,
            str(objective.metadata.get("worktree") or "") or None,
            objective.resource_id,
            "WAIT",
            f"existing Executive selected {selected}; initiative objective remains pending",
            replayed=True,
        )
    mission_id = str(cycle.materialization.get("mission_id") or "") or None
    if mission_id:
        database.append(Event.create(
            "initiative.execution_linked",
            {
                "candidate_id": candidate_id,
                "objective_id": objective_id,
                "mission_id": mission_id,
                "resource_id": objective.resource_id,
                "worktree": objective.metadata.get("worktree"),
                "branch": objective.metadata.get("branch"),
                "base_sha": objective.metadata.get("base_sha"),
            },
            source_type="supervisor",
            source_id="v11-a3-admission",
            correlation_id=objective_id,
            idempotency_key=f"initiative.execution_linked:{objective_id}:{mission_id}",
        ))
    return A3Admission(
        candidate_id,
        objective_id,
        mission_id,
        str(objective.metadata.get("worktree") or "") or None,
        objective.resource_id,
        "ADMITTED" if mission_id else "WAIT",
        cycle.reason,
        replayed=not bool(mission_id),
    )


def record_terminal_initiative_outcome(
    database: FoundryDatabase,
    *,
    mission_id: str,
    runtime_outcome: str,
    verdict_id: str | None,
) -> dict[str, Any] | None:
    link = None
    for event in reversed(database.events(event_types=("initiative.execution_linked",), readonly=True)):
        if str(event.payload.get("mission_id") or "") == mission_id:
            link = dict(event.payload)
            break
    if link is None:
        return None
    candidate_id = str(link.get("candidate_id") or "")
    objective_id = str(link.get("objective_id") or "")
    mission = database.row("missions", "mission_id", mission_id) or {}
    accepted = accepted_verifications(database, mission_id=mission_id)
    receipt = accepted.get(str(verdict_id or ""))
    accepted_terminal = (
        runtime_outcome == "PASSED"
        and str(mission.get("state") or "") == "COMPLETE"
        and receipt is not None
    )
    state = rebuild_executive_state(database)
    retry_available = any(
        plan.objective_id == objective_id
        and any(
            step.status == "FAILED" and step.retries < step.max_retries
            for step in plan.steps
        )
        for plan in state.plans.values()
    )
    status = "ACCEPTED" if accepted_terminal else (
        "IN_PROGRESS" if retry_available else
        "FAILED" if runtime_outcome in {"FAILED", "BLOCKED"} else "IN_PROGRESS"
    )
    worktree = Path(str(link.get("worktree") or ""))
    final_sha = None
    if worktree.is_dir():
        try:
            final_sha = _run_git(worktree, "rev-parse", "HEAD")
        except RuntimeError:
            final_sha = None
    payload = {
        "candidate_id": candidate_id,
        "objective_id": objective_id,
        "mission_id": mission_id,
        "worktree": str(worktree) if str(worktree) != "." else None,
        "base_sha": link.get("base_sha"),
        "final_sha": final_sha,
        "runtime_outcome": runtime_outcome,
        "status": status,
        "verdict_id": verdict_id,
        "acceptance_event_id": receipt.get("acceptance_event_id") if receipt else None,
        "verification_accepted": receipt is not None,
        "public_writes": 0,
    }
    if status in {"ACCEPTED", "FAILED"}:
        database.append(Event.create(
            "initiative.candidate_terminal",
            payload,
            source_type="supervisor",
            source_id="v11-a3-outcome",
            correlation_id=objective_id,
            idempotency_key=f"initiative.candidate_terminal:{candidate_id}:{mission_id}",
        ))
        from .discovery_candidates import record_outcome
        record_outcome(database, candidate_id, mission_id, payload)
    else:
        database.append(Event.create(
            "initiative.mission_attempt_recorded",
            payload,
            source_type="supervisor",
            source_id="v11-a3-outcome",
            correlation_id=objective_id,
            idempotency_key=f"initiative.mission_attempt_recorded:{mission_id}:{verdict_id or runtime_outcome}",
        ))
    return payload


__all__ = [
    "A3Admission",
    "A3AdmissionPolicy",
    "InitiativeAdmissionAuthority",
    "active_initiative_objective",
    "continue_initiative_objective",
    "pending_initiative_decision",
    "record_terminal_initiative_outcome",
]
