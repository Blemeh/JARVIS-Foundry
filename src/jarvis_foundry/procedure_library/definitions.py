"""The lane's high-value procedure repertoire."""
from __future__ import annotations

from typing import Iterable

from .contracts import (
    FailureMode,
    OracleSpec,
    ParameterSpec,
    ProcedureDefinition,
    ProcedureStep,
    RetryPolicy,
)
from .validator import ProcedureCatalog


def _failures(runner: str, extra: tuple[tuple[str, str], ...] = ()) -> tuple[FailureMode, ...]:
    raw_pairs = (
        ("missing-precondition", "MISSING_PRECONDITION"),
        ("verifier-rejection", "VERIFIER_REJECTION"),
        *extra,
    )
    pairs: list[tuple[str, str]] = []
    seen: set[str] = set()
    for name, classification in raw_pairs:
        if name not in seen:
            pairs.append((name, classification))
            seen.add(name)
    return tuple(
        FailureMode(
            failure_id=f"{runner}:{name}",
            trigger=f"fixture injects {name}",
            classification=classification,
            expected_behavior="record the failure, stop the effect, and expose evidence before repair",
        )
        for name, classification in pairs
    )


def _entry(
    procedure_id: str,
    name: str,
    domain: str,
    runner: str,
    purpose: str,
    capabilities: tuple[str, ...],
    *,
    risk: str = "R1",
    reversibility: str = "mixed",
    parameters: tuple[ParameterSpec, ...] = (),
    dependencies: tuple[str, ...] = (),
    extra_failures: tuple[tuple[str, str], ...] = (),
    retry: RetryPolicy | None = None,
    source_operation: str = "procedure-library",
) -> ProcedureDefinition:
    retry = retry or RetryPolicy()
    target = f"fixture:{runner}"
    steps = (
        ProcedureStep(
            step_id="orient",
            action=f"{runner}.orient",
            target=target,
            preconditions=("disposable fixture root is exact", "owner STOP state is checked"),
            capabilities=capabilities,
            risk_class="R0",
            reversibility="reversible",
            recovery="discard the untouched fixture root",
            postconditions=("target identity recorded",),
            evidence_outputs=(f"evidence:{runner}:orientation",),
            stop_behavior="recheck STOP or veto before execution",
        ),
        ProcedureStep(
            step_id="execute",
            action=f"{runner}.execute",
            target=target,
            preconditions=("orientation evidence exists", "required capability is ready"),
            capabilities=capabilities,
            risk_class=risk,
            reversibility=reversibility,
            recovery="apply the declared repair or restore the fixture before retry",
            postconditions=("oracle checks pass", "evidence is hash-bound"),
            evidence_outputs=(f"evidence:{runner}:result", f"evidence:{runner}:failure"),
            stop_behavior="STOP or veto freezes before any irreversible effect",
            retry_policy=retry,
        ),
    )
    return ProcedureDefinition(
        procedure_id=procedure_id,
        name=name,
        domain=domain,
        version="1.0.0",
        purpose=purpose,
        parameters=parameters,
        preconditions=("disposable fixture root is available", "target identity is exact"),
        capabilities=capabilities,
        risk_class=risk,
        reversibility=reversibility,
        steps=steps,
        oracle=OracleSpec(
            oracle_id=f"oracle:{runner}",
            checks=("observed state satisfies postconditions", "evidence hash matches observed output"),
        ),
        evidence_outputs=(f"evidence:{runner}:orientation", f"evidence:{runner}:result", f"evidence:{runner}:verifier"),
        known_failures=_failures(runner, extra_failures),
        repair_policy="bounded explicit repair with a retry ceiling and escalation on unresolved ambiguity",
        stop_behavior="STOP and owner veto are checked before every material effect",
        timeout_seconds=120,
        estimated_cost_usd=0.0,
        runner_id=runner,
        dependencies=dependencies,
        metadata={"environment": "disposable", "real_accounts": False, "source_operation": source_operation},
    )


PROCEDURES: tuple[ProcedureDefinition, ...] = (
    _entry(
        "jarvis.software-engineering", "Software Engineer", "software-engineering", "software-engineering",
        "Inspect a specification, reproduce a defect, repair a disposable project, test, commit, and reconcile its local remote.",
        ("filesystem.read", "filesystem.write", "git.write", "git.push.disposable", "tests.run"),
        extra_failures=(("test-failure", "TEST_FAILURE"),), source_operation="operations.project",
    ),
    _entry(
        "jarvis.bug-triage", "Bug Triage and Root Cause", "bug-triage", "bug-triage",
        "Reproduce a symptom, compare hypotheses, classify the cause, and emit the smallest repair with regression evidence.",
        ("filesystem.read", "tests.run", "evidence.verify"),
        extra_failures=(("wrong-target", "WRONG_TARGET"), ("unknown-cause", "UNKNOWN")),
    ),
    _entry(
        "jarvis.flake-diagnosis", "Test Flake Diagnosis", "flake-diagnosis", "flake-diagnosis",
        "Use bounded repetition and deterministic prerequisite checks to distinguish a product defect, test defect, or host-sensitive flake.",
        ("tests.run", "filesystem.read", "evidence.verify"),
        extra_failures=(("host-jitter", "ENVIRONMENT_HOST"), ("test-fixture", "TEST_DEFECT")),
    ),
    _entry(
        "jarvis.repository-maintenance", "Repository Maintenance", "repository-maintenance", "repository-maintenance",
        "Choose one highest-value maintenance action from tests, docs, dependencies, and generated-artifact evidence.",
        ("git.read", "filesystem.read", "filesystem.write", "tests.run"),
        extra_failures=(("generated-artifact", "TEST_DEFECT"),), source_operation="operations.project",
    ),
    _entry(
        "jarvis.dependency-update", "Dependency Update", "dependency-update", "dependency-update",
        "Bound a dependency upgrade with compatibility evidence, regression proof, and a tested rollback path.",
        ("dependency.read", "dependency.write.disposable", "tests.run", "filesystem.write"),
        extra_failures=(("compatibility-regression", "PRODUCT_DEFECT"),), source_operation="operations.project",
    ),
    _entry(
        "jarvis.documentation-staleness", "Documentation Staleness", "documentation-staleness", "documentation-staleness",
        "Compare canonical source claims to documentation, repair exact stale statements, and verify no stale claim remains.",
        ("filesystem.read", "filesystem.write", "evidence.verify"),
        extra_failures=(("source-mismatch", "UNKNOWN"),),
    ),
    _entry(
        "jarvis.release-preparation", "Release Preparation", "release-preparation", "release-preparation",
        "Prepare an immutable disposable release with manifest provenance, predecessor validation, health, and rollback readiness.",
        ("release.build", "release.switch.disposable", "filesystem.read", "evidence.verify"),
        risk="R2", extra_failures=(("health-failure", "TEST_FAILURE"),), source_operation="operations.deployment",
    ),
    _entry(
        "jarvis.research-synthesis", "Research and Evidence Synthesis", "research-synthesis", "research-synthesis",
        "Assess source quality, preserve contradictions, state uncertainty, and turn evidence into a bounded next action.",
        ("research.read", "evidence.verify", "filesystem.write"),
        extra_failures=(("contradiction", "UNKNOWN"),),
    ),
    _entry(
        "jarvis.knowledge-revalidation", "Knowledge Revalidation", "knowledge-revalidation", "knowledge-revalidation",
        "Revalidate an aged claim against new evidence while preserving provenance and supersession history.",
        ("knowledge.read", "knowledge.write.disposable", "evidence.verify"),
        extra_failures=(("expired-claim", "UNKNOWN"),),
    ),
    _entry(
        "jarvis.worktree-archaeology", "Branch and Worktree Archaeology", "worktree-archaeology", "worktree-archaeology",
        "Inventory a disposable Git topology, classify branch lineage, identify stranded work, and produce a no-force integration plan.",
        ("git.read", "filesystem.read", "evidence.verify"),
        extra_failures=(("stranded-work", "UNKNOWN"),), source_operation="operations.project",
    ),
    _entry(
        "jarvis.system-diagnostics", "System Diagnostics", "system-diagnostics", "system-diagnostics",
        "Run bounded read-only process, service, database, release, resource, and scheduler checks with honest unknowns.",
        ("diagnostics.read", "service.read", "filesystem.read", "evidence.verify"),
        extra_failures=(("unknown-health", "UNKNOWN"),), source_operation="diagnostics.runtime",
    ),
    _entry(
        "jarvis.system-repair", "Bounded System Repair", "system-repair", "system-repair",
        "Diagnose a disposable service, apply one exact repair, verify health, and stop at a retry ceiling.",
        ("diagnostics.read", "service.repair.disposable", "service.read", "evidence.verify"),
        extra_failures=(("repair-timeout", "TIMEOUT"),), source_operation="operations.services",
    ),
    _entry(
        "jarvis.incident-triage", "Incident Triage", "incident-triage", "incident-triage",
        "Separate impact, scope, proven and suspected cause, effect ambiguity, safe work, and repair options.",
        ("incident.read", "evidence.verify", "procedure.select"),
        extra_failures=(("ambiguous-effect", "AMBIGUOUS_EFFECT"),),
    ),
    _entry(
        "jarvis.project-health", "Project Health Review", "project-health", "project-health",
        "Review tests, release identity, open problems, dependencies, incidents, performance, and stale knowledge into one next action.",
        ("project.read", "git.read", "dependency.read", "diagnostics.read", "evidence.verify"),
        extra_failures=(("missing-signal", "UNKNOWN"),),
    ),
    _entry(
        "jarvis.safe-local-deployment", "Safe Local Deployment", "safe-local-deployment", "safe-local-deployment",
        "Build, stage, health-check, switch, verify, and roll back a disposable local service.",
        ("release.build", "release.switch.disposable", "service.read", "evidence.verify"),
        risk="R2", retry=RetryPolicy(2, ("TIMEOUT", "AMBIGUOUS_EFFECT"), True), source_operation="operations.deployment",
    ),
    _entry(
        "jarvis.communication-draft", "Communication Draft", "communication-draft", "communication-draft",
        "Create an evidence-backed draft with an exact placeholder recipient and transparent JARVIS identity without sending it.",
        ("communication.draft", "evidence.verify"),
        parameters=(ParameterSpec("recipient_placeholder", "identifier", description="Synthetic recipient identity"),),
        extra_failures=(("hostile-message", "UNKNOWN"),), source_operation="operations.services",
    ),
    _entry(
        "jarvis.calendar-planning", "Calendar Planning", "calendar-planning", "calendar-planning",
        "Find available synthetic slots, surface conflicts, and produce a reversible event plan with exact attendees.",
        ("calendar.read", "calendar.draft", "evidence.verify"),
        parameters=(ParameterSpec("calendar_id", "identifier", description="Exact synthetic calendar identity"),),
        extra_failures=(("calendar-conflict", "TEST_FAILURE"),), source_operation="operations.services",
    ),
    _entry(
        "jarvis.inbox-triage", "Inbox Triage", "inbox-triage", "inbox-triage",
        "Classify a synthetic inbox, preserve malicious messages as data, draft responses, and recommend labels without owner-free mutation.",
        ("inbox.read", "communication.draft", "calendar.read", "evidence.verify"),
        extra_failures=(("malicious-message", "UNKNOWN"),),
    ),
    _entry(
        "jarvis.research-build-verify", "Research to Build to Verify", "composite", "research-build-verify",
        "Chain research, implementation, focused tests, independent verification, outcome recording, and maintenance revisit.",
        ("research.read", "filesystem.write", "tests.run", "evidence.verify", "procedure.select"),
        dependencies=("jarvis.research-synthesis", "jarvis.software-engineering"),
        risk="R2", extra_failures=(("verifier-rejection", "VERIFIER_REJECTION"),), source_operation="operations.lab",
    ),
    _entry(
        "jarvis.failure-repair-factory", "Failure and Repair Factory", "failure-repair", "failure-repair-factory",
        "Inject missing preconditions, wrong targets, capability gaps, test failures, timeouts, ambiguity, verifier rejection, and restart, then repair only where allowed.",
        ("procedure.select", "evidence.verify", "service.repair.disposable", "tests.run"),
        dependencies=("jarvis.bug-triage", "jarvis.system-repair"),
        extra_failures=(("timeout", "TIMEOUT"), ("ambiguous-effect", "AMBIGUOUS_EFFECT"), ("restart", "RESTART")),
    ),
    _entry(
        "jarvis.versioning-supersession", "Versioning and Supersession", "versioning", "versioning-supersession",
        "Prove v1 to v2 promotion, supersede history, new selection, and rollback to a prior proven version.",
        ("procedure.version", "procedure.select", "evidence.verify"),
        extra_failures=(("candidate-rejected", "VERIFIER_REJECTION"),),
    ),
    _entry(
        "jarvis.quality-ranking", "Quality Ranking", "quality-ranking", "quality-ranking",
        "Rank procedures from observed success, repair, cost, duration, verifier outcome, and recency rather than model self-rating.",
        ("procedure.select", "evidence.verify", "filesystem.write"),
        extra_failures=(("insufficient-observations", "UNKNOWN"),),
    ),
)


def build_catalog() -> ProcedureCatalog:
    return ProcedureCatalog(PROCEDURES)


__all__ = ["PROCEDURES", "build_catalog"]
