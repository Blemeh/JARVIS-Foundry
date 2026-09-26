"""INTENT and NEED loops for the V9 capability fabric."""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Mapping

from ..clock import isoformat
from ..control.broker import emergency_stop_reason
from ..control.capabilities import blocking_capability_state
from ..database import FoundryDatabase
from ..platform.windows_subprocess import headless_run_kwargs
from .catalog import (
    capability_metadata,
    capability_trust,
    catalog_snapshot,
    get_spec,
    observed_readiness,
    record_gap,
    record_outcome,
    record_probation_block,
    register_capability,
    reliability,
    specs_for_action,
)
from .executors import ExecutorContext, dispatch, finalize_mcp_mutation_effect
from .expansion import acquire_hash_capability
from .model import (
    ActionResult,
    CapabilityTrust,
    CapabilitySpec,
    ClaimOutcome,
    FabricError,
    GapKind,
    GapRecord,
    GapStatus,
    Kind,
    Readiness,
    Selection,
    Semantic,
    TaskIntent,
    UnattendedMode,
    VerifyVerdict,
)
from .contracts import (
    AdapterRequest,
    system_id_for_action,
    EffectClass,
    PostconditionStatus,
    build_adapter_request,
    evaluate_postconditions,
    metadata_for_spec,
    normalize_receipt,
)
from ..effects import (
    COMPLETED,
    FAILED,
    FROZEN,
    STARTED,
    effect_state,
    execute_fabric_effect,
)
from .providers import route_provider
from .computer_operator import (
    PERSONAL_OPERATOR_SHARED_CAPABILITY_IDS,
    computer_operator_specs,
    verify_computer_operator,
)


_DEVELOPER_RECOVERY_ACTIONS = frozenset({
    "git.push",
    "github.issue.create",
    "github.issue.comment.create",
    "github.pull_request.create",
    "github.pull_request.publish",
})


def _developer_recovery_intent(
    action_kind: str,
    payload: Mapping[str, Any],
    ctx: ExecutorContext,
) -> dict[str, Any] | None:
    """Persist the bounded original intent needed for restart reconciliation."""

    if action_kind not in _DEVELOPER_RECOVERY_ACTIONS:
        return None
    request = {
        str(key): (str(value) if isinstance(value, Path) else value)
        for key, value in payload.items()
        if key not in {"claim_ttl_s", "stop_path"}
    }
    intent: dict[str, Any] = {
        "action_kind": action_kind,
        "request": request,
        "reconciliation_action": (
            "git.push.reconcile"
            if action_kind == "git.push"
            else "github.write.reconcile"
        ),
    }
    if action_kind == "git.push":
        worktree = Path(str(payload.get("worktree") or ctx.owned_root)).resolve()
        observed = subprocess.run(
            ["git", "-C", str(worktree), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
            check=False,
            **headless_run_kwargs(),
        )
        if observed.returncode != 0 or not observed.stdout.strip():
            raise FabricError("git.push recovery intent requires a readable local HEAD")
        branch = str(payload.get("branch") or "main")
        action_id = str(payload.get("action_id") or f"fabric-push:{branch}")
        request.update({
            "worktree": str(worktree),
            "branch": branch,
            "action_id": action_id,
        })
        intent.update({
            "action_id": action_id,
            "remote": str(payload.get("remote") or "origin"),
            "branch": branch,
            "expected_head": observed.stdout.strip(),
        })
    return intent


def seed_builtins(database: FoundryDatabase) -> tuple[CapabilitySpec, ...]:
    """Register the practical built-in actions. Readiness stays UNKNOWN."""
    builtins = (
        CapabilitySpec(
            capability_id="cap.file.write", version="1.0.0", kind=Kind.CONNECTOR,
            action_kind="file.write", semantic=Semantic.WRITE, executor="filesystem",
            unattended=UnattendedMode.ALLOWED,
        ),
        CapabilitySpec(
            capability_id="cap.file.read", version="1.0.0", kind=Kind.CONNECTOR,
            action_kind="file.read", semantic=Semantic.READ, executor="filesystem",
            unattended=UnattendedMode.ALLOWED,
        ),
        CapabilitySpec(
            capability_id="cap.file.replace", version="1.0.0", kind=Kind.CONNECTOR,
            action_kind="file.replace", semantic=Semantic.WRITE, executor="filesystem",
            unattended=UnattendedMode.ALLOWED,
        ),
        CapabilitySpec(
            capability_id="cap.file.append", version="1.0.0", kind=Kind.CONNECTOR,
            action_kind="file.append", semantic=Semantic.WRITE, executor="filesystem",
            unattended=UnattendedMode.ALLOWED,
        ),
        CapabilitySpec(
            capability_id="cap.dir.list", version="1.0.0", kind=Kind.TOOL,
            action_kind="dir.list", semantic=Semantic.READ, executor="local",
            unattended=UnattendedMode.ALLOWED,
        ),
        CapabilitySpec(
            capability_id="cap.git.inspect", version="1.0.0", kind=Kind.CONNECTOR,
            action_kind="git.inspect", semantic=Semantic.READ, executor="git",
            unattended=UnattendedMode.ALLOWED,
        ),
        CapabilitySpec(
            capability_id="cap.git.diff", version="1.0.0", kind=Kind.CONNECTOR,
            action_kind="git.diff", semantic=Semantic.READ, executor="git",
            unattended=UnattendedMode.ALLOWED,
        ),
        CapabilitySpec(
            capability_id="cap.git.branch", version="1.0.0", kind=Kind.CONNECTOR,
            action_kind="git.branch", semantic=Semantic.WRITE, executor="git",
            unattended=UnattendedMode.ALLOWED,
            limitations=("owned clean worktree only",),
        ),
        CapabilitySpec(
            capability_id="cap.git.commit", version="1.0.0", kind=Kind.CONNECTOR,
            action_kind="git.commit", semantic=Semantic.WRITE, executor="git",
            unattended=UnattendedMode.ALLOWED,
            limitations=("owned clean worktree only",),
        ),
        CapabilitySpec(
            capability_id="cap.git.read_ref", version="1.0.0", kind=Kind.CONNECTOR,
            action_kind="git.read_ref", semantic=Semantic.READ, executor="git",
            unattended=UnattendedMode.ALLOWED,
        ),
        CapabilitySpec(
            capability_id="cap.git.log", version="1.0.0", kind=Kind.CONNECTOR,
            action_kind="git.log", semantic=Semantic.READ, executor="git",
            unattended=UnattendedMode.ALLOWED,
        ),
        CapabilitySpec(
            capability_id="cap.git.push", version="1.0.0", kind=Kind.CONNECTOR,
            action_kind="git.push", semantic=Semantic.EXTERNAL_EFFECT, executor="git",
            unattended=UnattendedMode.ALLOWED,
            limitations=("owned disposable remote only; no live production remotes",),
        ),
        CapabilitySpec(
            capability_id="cap.git.push.reconcile", version="1.0.0",
            kind=Kind.CONNECTOR, action_kind="git.push.reconcile",
            semantic=Semantic.READ, executor="git",
            unattended=UnattendedMode.ALLOWED,
            limitations=(
                "read-only ls-remote reconciliation of one ambiguous push; "
                "confirms the original effect without a second push",
            ),
        ),
        CapabilitySpec(
            capability_id="cap.github.repo.read", version="1.0.0", kind=Kind.CONNECTOR,
            action_kind="github.repo.read", semantic=Semantic.READ, executor="github_research",
            unattended=UnattendedMode.ALLOWED,
            limitations=("read-only; gh session required",),
        ),
        CapabilitySpec(
            capability_id="cap.github.identity.read", version="1.0.0", kind=Kind.CONNECTOR,
            action_kind="github.identity.read", semantic=Semantic.READ, executor="github_research",
            unattended=UnattendedMode.ALLOWED,
        ),
        CapabilitySpec(
            capability_id="cap.github.issue.read", version="1.0.0", kind=Kind.CONNECTOR,
            action_kind="github.issue.read", semantic=Semantic.READ, executor="github_research",
            unattended=UnattendedMode.ALLOWED,
        ),
        CapabilitySpec(
            capability_id="cap.github.pull_request.read", version="1.0.0", kind=Kind.CONNECTOR,
            action_kind="github.pull_request.read", semantic=Semantic.READ, executor="github_research",
            unattended=UnattendedMode.ALLOWED,
        ),
        CapabilitySpec(
            capability_id="cap.github.commit.read", version="1.0.0", kind=Kind.CONNECTOR,
            action_kind="github.commit.read", semantic=Semantic.READ, executor="github_research",
            unattended=UnattendedMode.ALLOWED,
        ),
        CapabilitySpec(
            capability_id="cap.github.check.read", version="1.0.0", kind=Kind.CONNECTOR,
            action_kind="github.check.read", semantic=Semantic.READ, executor="github_research",
            unattended=UnattendedMode.ALLOWED,
        ),
        CapabilitySpec(
            capability_id="cap.github.release.read", version="1.0.0", kind=Kind.CONNECTOR,
            action_kind="github.release.read", semantic=Semantic.READ, executor="github_research",
            unattended=UnattendedMode.ALLOWED,
        ),
        CapabilitySpec(
            capability_id="cap.github.search.read", version="1.0.0", kind=Kind.CONNECTOR,
            action_kind="github.search.read", semantic=Semantic.READ, executor="github_research",
            unattended=UnattendedMode.ALLOWED,
            limitations=("code search is refused",),
        ),
        CapabilitySpec(
            capability_id="cap.github.issue.create", version="1.0.0", kind=Kind.CONNECTOR,
            action_kind="github.issue.create", semantic=Semantic.EXTERNAL_EFFECT,
            executor="github_write",
            unattended=UnattendedMode.ALLOWED,
            limitations=("explicit write transport required; no silent live mutation",),
        ),
        CapabilitySpec(
            capability_id="cap.github.issue.comment.create", version="1.0.0",
            kind=Kind.CONNECTOR, action_kind="github.issue.comment.create",
            semantic=Semantic.EXTERNAL_EFFECT, executor="github_write",
            unattended=UnattendedMode.ALLOWED,
            limitations=(
                "explicit write transport required; owner text preserved verbatim; "
                "provider readback required",
            ),
        ),
        CapabilitySpec(
            capability_id="cap.github.pull_request.create", version="1.0.0", kind=Kind.CONNECTOR,
            action_kind="github.pull_request.create", semantic=Semantic.EXTERNAL_EFFECT,
            executor="github_write",
            unattended=UnattendedMode.ALLOWED,
            limitations=("explicit write transport required; no silent live mutation",),
        ),
        CapabilitySpec(
            capability_id="cap.github.pull_request.publish", version="1.0.0", kind=Kind.CONNECTOR,
            action_kind="github.pull_request.publish", semantic=Semantic.EXTERNAL_EFFECT,
            executor="github_write",
            unattended=UnattendedMode.ALLOWED,
            limitations=(
                "disabled by default; exact owner repository/base/remote authorization required; "
                "deterministic branch, canonical effect journal, and provider readback required",
            ),
        ),
        CapabilitySpec(
            capability_id="cap.github.write.reconcile", version="1.0.0",
            kind=Kind.CONNECTOR, action_kind="github.write.reconcile",
            semantic=Semantic.READ, executor="github_write",
            unattended=UnattendedMode.ALLOWED,
            limitations=(
                "read-only provider reconciliation of one terminal GitHub issue/PR "
                "create effect; finalizes the original effect without redispatch",
            ),
        ),
        CapabilitySpec(
            capability_id="cap.gmail.send", version="1.0.0", kind=Kind.CONNECTOR,
            action_kind="gmail.send", semantic=Semantic.COMMUNICATE,
            executor="gmail_api",
            unattended=UnattendedMode.ALLOWED,
            limitations=(
                "disabled by default; exact owner self-recipient authorization required; "
                "PROBATION capabilities forbidden; provider readback required",
            ),
        ),
        CapabilitySpec(
            capability_id="cap.http.read", version="1.0.0", kind=Kind.CONNECTOR,
            action_kind="http.read", semantic=Semantic.READ, executor="http",
            unattended=UnattendedMode.ALLOWED,
            limitations=("loopback services only",),
        ),
        CapabilitySpec(
            capability_id="cap.http.write", version="1.0.0", kind=Kind.CONNECTOR,
            action_kind="http.write", semantic=Semantic.EXTERNAL_EFFECT, executor="http",
            unattended=UnattendedMode.ALLOWED,
            limitations=("loopback services only",),
        ),
        CapabilitySpec(
            capability_id="cap.http.doc.read", version="1.0.0", kind=Kind.CONNECTOR,
            action_kind="http.doc.read", semantic=Semantic.READ, executor="github_research",
            unattended=UnattendedMode.ALLOWED,
            limitations=("HTTPS documents; host allow-list required for untrusted hosts",),
        ),
        CapabilitySpec(
            capability_id="cap.browser.web.read", version="1.0.0", kind=Kind.CONNECTOR,
            action_kind="browser.web.read", semantic=Semantic.READ, executor="browser",
            unattended=UnattendedMode.ALLOWED,
            limitations=(
                "one bounded Playwright page; structural identity; external page data is DATA_ONLY",
            ),
        ),
        CapabilitySpec(
            capability_id="cap.browser.web.inspect", version="1.0.0",
            kind=Kind.CONNECTOR, action_kind="browser.web.inspect",
            semantic=Semantic.READ, executor="browser",
            unattended=UnattendedMode.ALLOWED,
            limitations=(
                "bounded accessibility structure of one page; no form values, "
                "no raw HTML; external page data is DATA_ONLY",
            ),
        ),
        CapabilitySpec(
            capability_id="cap.browser.web.wait", version="1.0.0", kind=Kind.CONNECTOR,
            action_kind="browser.web.wait", semantic=Semantic.READ, executor="browser",
            unattended=UnattendedMode.ALLOWED,
            limitations=(
                "one bounded Playwright page; exact accessibility target; "
                "STOP-aware observation only",
            ),
        ),
        CapabilitySpec(
            capability_id="cap.browser.web.reconcile", version="1.0.0",
            kind=Kind.CONNECTOR, action_kind="browser.web.reconcile",
            semantic=Semantic.READ, executor="browser",
            unattended=UnattendedMode.ALLOWED,
            limitations=(
                "read-only exact browser postcondition reconciliation; "
                "finalizes one original terminal effect without redispatch",
            ),
        ),
        CapabilitySpec(
            capability_id="cap.browser.web.execute", version="1.0.0", kind=Kind.CONNECTOR,
            action_kind="browser.web.execute", semantic=Semantic.EXTERNAL_EFFECT,
            executor="browser",
            unattended=UnattendedMode.ALLOWED,
            limitations=(
                "disabled by default; exact owner BrowserExecutionAuthorization "
                "required in the executor context (one origin, bounded verbs, "
                "brokered session state); session authentication and effect-journal "
                "readback required; file transfer requires explicit payload opt-in "
                "and grant opt-in; no destructive/public actions",
            ),
        ),
        CapabilitySpec(
            capability_id="cap.system.diagnose", version="1.0.0", kind=Kind.TOOL,
            action_kind="system.diagnose", semantic=Semantic.READ, executor="local",
            unattended=UnattendedMode.ALLOWED,
        ),
        CapabilitySpec(
            capability_id="cap.system.runtime", version="1.0.0", kind=Kind.TOOL,
            action_kind="system.runtime", semantic=Semantic.READ, executor="local",
            unattended=UnattendedMode.ALLOWED,
        ),
        CapabilitySpec(
            capability_id="cap.tests.run", version="1.0.0", kind=Kind.TOOL,
            action_kind="tests.run", semantic=Semantic.EXECUTE, executor="pytest",
            unattended=UnattendedMode.ALLOWED,
        ),
        CapabilitySpec(
            capability_id="cap.script.run", version="1.0.0", kind=Kind.TOOL,
            action_kind="script.run", semantic=Semantic.EXECUTE, executor="python",
            unattended=UnattendedMode.ALLOWED,
            limitations=("owned-root python files only",),
        ),
        CapabilitySpec(
            capability_id="cap.artifact.research", version="1.0.0", kind=Kind.WORKFLOW,
            action_kind="artifact.research", semantic=Semantic.WRITE, executor="compose",
            requirements=("github.repo.read", "file.write"),
            unattended=UnattendedMode.ALLOWED,
        ),
        CapabilitySpec(
            capability_id="cap.workflow.run", version="1.0.0", kind=Kind.WORKFLOW,
            action_kind="workflow.run", semantic=Semantic.EXECUTE, executor="compose",
            unattended=UnattendedMode.ALLOWED,
            limitations=("named recipe only; bounded nesting; canonical effect journal retained",),
        ),
        CapabilitySpec(
            capability_id="cap.artifact.parse", version="1.0.0", kind=Kind.ADAPTER,
            action_kind="artifact.parse", semantic=Semantic.READ, executor="local_artifacts",
            unattended=UnattendedMode.ALLOWED,
            limitations=("owned output root plus explicitly registered read-only source roots; bounded parser output; external content is DATA_ONLY",),
        ),
        CapabilitySpec(
            capability_id="cap.artifact.table.summary", version="1.0.0", kind=Kind.ADAPTER,
            action_kind="artifact.table.summary", semantic=Semantic.EXECUTE, executor="local_artifacts",
            requirements=("artifact.parse",), unattended=UnattendedMode.ALLOWED,
            limitations=("named columns and bounded calculations only",),
        ),
        CapabilitySpec(
            capability_id="cap.artifact.docx.create", version="1.0.0", kind=Kind.ADAPTER,
            action_kind="artifact.docx.create", semantic=Semantic.WRITE, executor="local_artifacts",
            requirements=("artifact.table.summary",), unattended=UnattendedMode.ALLOWED,
            limitations=("new owned-root DOCX target; source is never overwritten",),
        ),
        CapabilitySpec(
            capability_id="cap.artifact.docx.verify", version="1.0.0", kind=Kind.ADAPTER,
            action_kind="artifact.docx.verify", semantic=Semantic.READ, executor="local_artifacts",
            unattended=UnattendedMode.ALLOWED,
            limitations=("independent reopen/readback of an owned-root DOCX",),
        ),
        CapabilitySpec(
            capability_id="cap.artifact.table.export", version="1.0.0", kind=Kind.ADAPTER,
            action_kind="artifact.table.export", semantic=Semantic.WRITE, executor="local_artifacts",
            requirements=("artifact.parse",), unattended=UnattendedMode.ALLOWED,
            limitations=("new CSV/TSV/XLSX target; source is never overwritten",),
        ),
        CapabilitySpec(
            capability_id="cap.artifact.pdf.copy", version="1.0.0", kind=Kind.ADAPTER,
            action_kind="artifact.pdf.copy", semantic=Semantic.WRITE, executor="local_artifacts",
            requirements=("artifact.parse",), unattended=UnattendedMode.ALLOWED,
            limitations=("bounded pypdf readback; new target only",),
        ),
        CapabilitySpec(
            capability_id="cap.artifact.pptx.copy", version="1.0.0", kind=Kind.ADAPTER,
            action_kind="artifact.pptx.copy", semantic=Semantic.WRITE, executor="local_artifacts",
            requirements=("artifact.parse",), unattended=UnattendedMode.ALLOWED,
            limitations=("bounded python-pptx readback; new target only",),
        ),
        CapabilitySpec(
            capability_id="cap.artifact.image.transform", version="1.0.0", kind=Kind.ADAPTER,
            action_kind="artifact.image.transform", semantic=Semantic.WRITE, executor="local_artifacts",
            requirements=("artifact.parse",), unattended=UnattendedMode.ALLOWED,
            limitations=("bounded Pillow resize/format transform; new target only",),
        ),
        CapabilitySpec(
            capability_id="cap.llm.dispatch", version="1.0.0", kind=Kind.PROVIDER,
            action_kind="llm.dispatch", semantic=Semantic.EXECUTE, executor="hermes",
            provider_deps=("opencode_go",),
            unattended=UnattendedMode.INTERACTIVE_REQUIRED,
            limitations=("contributor-tier Hermes requires noninteractive consent",),
        ),
    )
    computer_specs = computer_operator_specs()
    computer_capability_ids = {spec.capability_id for spec in computer_specs}
    from .general_work import general_work_specs
    from .personal_operator import personal_operator_specs

    general_specs = general_work_specs()
    general_capability_ids = {spec.capability_id for spec in general_specs}
    owned_capability_ids = computer_capability_ids | general_capability_ids
    personal_specs = tuple(
        spec
        for spec in personal_operator_specs()
        if spec.capability_id not in owned_capability_ids
    )
    builtins = (*builtins, *computer_specs, *general_specs, *personal_specs)
    registered = []
    for spec in builtins:
        existing = get_spec(database, spec.capability_id)
        canonical_operator_override = (
            spec.capability_id in PERSONAL_OPERATOR_SHARED_CAPABILITY_IDS
            and (
                existing is None
                or existing.executor != spec.executor
                or existing.action_kind != spec.action_kind
            )
        )
        if existing is None or canonical_operator_override:
            register_capability(
                database,
                spec,
                note=(
                    "canonical computer operator authority"
                    if canonical_operator_override
                    else "builtin seed"
                ),
                adoption=(
                    {
                        "source_sha": (
                            "db4ad1d11af14a666ad5b1b6b1be0059d6950da1"
                        ),
                        "authority": "computer_operator",
                        "reason": "shared Personal Operator capability convergence",
                    }
                    if canonical_operator_override
                    else None
                ),
            )
        registered.append(spec)
    return tuple(registered)


def _rank(spec: CapabilitySpec, readiness, stats: Mapping[str, int]) -> tuple:
    state_rank = {
        Readiness.READY: 0,
        Readiness.UNKNOWN: 1,
        Readiness.NOT_READY: 2,
        Readiness.DEGRADED: 3,
        Readiness.VETOED: 4,
        Readiness.MISSING: 5,
    }
    unattended_rank = 0 if spec.unattended is UnattendedMode.ALLOWED else 1
    trust_rank = 0 if readiness.trust_state is CapabilityTrust.ESTABLISHED else 1
    return (
        state_rank.get(readiness.state, 9),
        trust_rank,
        unattended_rank,
        -int(stats.get("successes") or 0),
        int(stats.get("failures") or 0),
        spec.capability_id,
    )


def select_capability(
    database: FoundryDatabase,
    intent: TaskIntent,
    action_kind: str,
) -> Selection:
    if intent.resource_id and intent.resource_capability:
        suspended = blocking_capability_state(
            database, intent.resource_id, intent.resource_capability,
        )
        if suspended is not None:
            return Selection(
                task_id=intent.task_id,
                action_kind=action_kind,
                capability_id=None,
                fallbacks=(),
                reason=str(suspended["reason"]),
            )
    specs = list(specs_for_action(database, action_kind))
    if not specs:
        gap = GapRecord(
            gap_id=f"gap.missing.{action_kind.replace('.', '-')}",
            needed_action=action_kind,
            missing=GapKind.TOOL,
            status=GapStatus.DETECTED,
            detail=f"I understand the task but cannot currently perform {action_kind}",
        )
        record_gap(database, gap)
        return Selection(
            task_id=intent.task_id, action_kind=action_kind,
            capability_id=None, fallbacks=(),
            reason=gap.detail, gap=gap,
        )
    ranked: list[tuple[CapabilitySpec, Any]] = []
    skipped: list[str] = []
    for spec in specs:
        readiness = observed_readiness(database, spec.capability_id)
        if readiness.trust_state is CapabilityTrust.DISABLED:
            skipped.append(f"{spec.capability_id} DISABLED")
            continue
        if readiness.state is Readiness.DEGRADED:
            skipped.append(f"{spec.capability_id} DEGRADED")
            continue
        if readiness.state is Readiness.VETOED:
            skipped.append(f"{spec.capability_id} VETOED")
            continue
        if intent.unattended and spec.unattended is UnattendedMode.INTERACTIVE_REQUIRED:
            skipped.append(f"{spec.capability_id} INTERACTIVE_REQUIRED")
            continue
        ranked.append((spec, readiness, reliability(database, spec.capability_id)))
    ranked.sort(key=lambda item: _rank(item[0], item[1], item[2]))
    if intent.preferred:
        preferred = [item for item in ranked if item[0].capability_id == intent.preferred]
        others = [item for item in ranked if item[0].capability_id != intent.preferred]
        if preferred:
            chosen = preferred[0][0]
            fallbacks = tuple(item[0].capability_id for item in others)
            return Selection(
                task_id=intent.task_id, action_kind=action_kind,
                capability_id=chosen.capability_id,
                fallbacks=fallbacks,
                reason=f"preferred {intent.preferred}",
            )
        skipped.append(f"preferred {intent.preferred} unavailable")
    if not ranked:
        gap = GapRecord(
            gap_id=f"gap.unusable.{action_kind.replace('.', '-')}",
            needed_action=action_kind,
            missing=GapKind.CONFIGURATION,
            status=GapStatus.DETECTED,
            detail="; ".join(skipped) or f"no usable capability for {action_kind}",
        )
        record_gap(database, gap)
        return Selection(
            task_id=intent.task_id, action_kind=action_kind,
            capability_id=None, fallbacks=(),
            reason=gap.detail, gap=gap,
        )
    chosen = ranked[0][0]
    fallbacks = tuple(item[0].capability_id for item in ranked[1:])
    return Selection(
        task_id=intent.task_id, action_kind=action_kind,
        capability_id=chosen.capability_id,
        fallbacks=fallbacks,
        reason=f"selected {chosen.capability_id}",
    )


def _verify_action(
    action_kind: str,
    claimed: Mapping[str, Any],
    owned_root: Path,
    payload: Mapping[str, Any] | None = None,
    executor_context: ExecutorContext | None = None,
    route_owner: str | None = None,
) -> VerifyVerdict:
    claimed_status = str(claimed.get("claimed") or "UNKNOWN")
    if claimed_status == "UNKNOWN":
        return VerifyVerdict.UNKNOWN
    if claimed_status == "BLOCKED":
        return VerifyVerdict.UNKNOWN
    if claimed_status == "FAILED":
        # Honest failure is not a lying claim. Keep the capability retryable
        # until consecutive failures trip DEGRADED.
        return VerifyVerdict.MATCH
    computer_verdict = verify_computer_operator(
        action_kind, claimed, payload, executor_context,
    )
    if computer_verdict is not None:
        return computer_verdict

    verification_context: Any = executor_context
    if verification_context is None:
        from types import SimpleNamespace

        verification_context = SimpleNamespace(
            owned_root=owned_root,
            artifact_source_roots=(),
        )
    source_roots = (
        executor_context.artifact_source_roots
        if executor_context is not None
        else ()
    )

    def personal_operator_verdict() -> VerifyVerdict | None:
        from .personal_operator import verify_personal_operator

        return verify_personal_operator(
            action_kind,
            claimed,
            owned_root,
            payload,
            executable_allowlist=(
                executor_context.operator_executable_allowlist
                if executor_context is not None
                else ()
            ),
            environment_allowlist=(
                executor_context.operator_environment_allowlist
                if executor_context is not None
                else ()
            ),
            artifact_source_roots=source_roots,
        )

    from .personal_operator import PERSONAL_OPERATOR_ACTIONS

    prefer_personal_operator = (
        route_owner == "personal_operator"
        or (
            route_owner is None
            and action_kind in PERSONAL_OPERATOR_ACTIONS
        )
    )
    if prefer_personal_operator:
        verdict = personal_operator_verdict()
        if verdict is not None:
            return verdict
        if route_owner == "personal_operator":
            return VerifyVerdict.UNKNOWN

    from .general_work import verify_general_work_action

    general_work_verdict = verify_general_work_action(
        verification_context,
        action_kind,
        claimed,
        payload,
    )
    if general_work_verdict is not None:
        return general_work_verdict

    def artifact_connector():
        from ..connectors.local_artifacts import LocalArtifactConnector
        return LocalArtifactConnector(owned_root, source_roots=source_roots)

    def artifact_parse_options() -> dict[str, object]:
        """Carry the requested parser policy into independent verification."""
        options: dict[str, object] = {}
        if payload is not None:
            for key in (
                "expected_columns", "allow_formulas", "allow_external_links", "sheet",
            ):
                if key in payload:
                    options[key] = payload[key]
        return options

    if action_kind in {"file.write", "file.replace", "artifact.write", "artifact.research"}:
        path = Path(claimed.get("path") or (owned_root / str(claimed.get("target") or "")))
        if not path.is_file():
            return VerifyVerdict.MISMATCH
        expected = claimed.get("content_hash")
        if expected:
            from ..connectors.filesystem import FilesystemConnector
            snap = FilesystemConnector(owned_root).snapshot(str(claimed.get("target")))
            if snap.content_hash != expected:
                return VerifyVerdict.MISMATCH
        if action_kind == "file.replace" and not claimed.get("replaced"):
            return VerifyVerdict.MISMATCH
        return VerifyVerdict.MATCH
    if action_kind == "file.append":
        path = Path(claimed.get("path") or (owned_root / str(claimed.get("target") or "")))
        if not path.is_file() or not claimed.get("appended"):
            return VerifyVerdict.MISMATCH
        return VerifyVerdict.MATCH
    if action_kind == "artifact.parse":
        source_ref = claimed.get("source_ref")
        source_hash = claimed.get("source_sha256")
        if claimed_status != "COMPLETED" or not source_ref or not source_hash:
            return VerifyVerdict.MISMATCH
        try:
            parsed = artifact_connector().parse(
                str(source_ref), **artifact_parse_options()
            )
        except Exception:  # noqa: BLE001 - verifier fails closed
            return VerifyVerdict.MISMATCH
        return VerifyVerdict.MATCH if parsed.source.sha256 == source_hash else VerifyVerdict.MISMATCH
    if action_kind == "artifact.table.summary":
        summary = claimed.get("summary") or {}
        source_ref = claimed.get("source_ref") or summary.get("source_ref")
        source_hash = claimed.get("source_sha256") or summary.get("source_sha256")
        if claimed_status != "COMPLETED" or not source_ref or not source_hash:
            return VerifyVerdict.MISMATCH
        try:
            connector = artifact_connector()
            parse_options = artifact_parse_options()
            parse_options["expected_columns"] = summary.get("headers")
            parse_options["sheet"] = summary.get("sheet_name")
            parsed = connector.parse(str(source_ref), **parse_options)
            if parsed.source.sha256 != source_hash:
                return VerifyVerdict.MISMATCH
            recalculated = connector.summarize_table(
                parsed,
                group_by=summary.get("group_by"),
                sum_column=summary.get("sum_column"),
            )
        except Exception:  # noqa: BLE001 - verifier fails closed
            return VerifyVerdict.MISMATCH
        for key in ("row_count", "column_count", "missing_by_column", "group_counts", "sum_value"):
            if key in summary and recalculated.get(key) != summary.get(key):
                return VerifyVerdict.MISMATCH
        return VerifyVerdict.MATCH
    if action_kind == "artifact.docx.create":
        target_ref = claimed.get("target_ref")
        expected_hash = claimed.get("output_sha256")
        source_hash = claimed.get("source_sha256")
        summary = claimed.get("summary") or {}
        if claimed_status != "COMPLETED" or not target_ref or not expected_hash or not source_hash:
            return VerifyVerdict.MISMATCH
        try:
            connector = artifact_connector()
            current_source = connector.capture(str(claimed.get("source_ref")))
            if current_source.sha256 != source_hash:
                return VerifyVerdict.MISMATCH
            readback = connector.verify_docx(
                str(target_ref), expected_sha256=str(expected_hash),
                required_values=(str(source_hash), str(summary.get("row_count", ""))),
            )
        except Exception:  # noqa: BLE001 - verifier fails closed
            return VerifyVerdict.MISMATCH
        return VerifyVerdict.MATCH if readback.get("verified") else VerifyVerdict.MISMATCH
    if action_kind == "artifact.docx.verify":
        target_ref = claimed.get("target_ref")
        expected_hash = claimed.get("output_sha256")
        required_values = ()
        if payload is not None:
            required_values = payload.get("required_values") or ()
            expected_hash = payload.get("expected_sha256") or expected_hash
            target_ref = payload.get("target") or target_ref
        if claimed_status != "COMPLETED" or not target_ref or not expected_hash:
            return VerifyVerdict.MISMATCH
        try:
            readback = artifact_connector().verify_docx(
                str(target_ref), expected_sha256=str(expected_hash),
                required_values=required_values,
            )
        except Exception:  # noqa: BLE001 - verifier fails closed
            return VerifyVerdict.MISMATCH
        return VerifyVerdict.MATCH if (
            claimed.get("readback_verified")
            and readback.get("verified")
            and readback.get("sha256") == str(claimed.get("output_sha256"))
        ) else VerifyVerdict.MISMATCH
    if action_kind in {
        "artifact.table.export", "artifact.pdf.copy",
        "artifact.pptx.copy", "artifact.image.transform",
    }:
        target_ref = claimed.get("target_ref")
        expected_hash = claimed.get("output_sha256")
        if claimed_status != "COMPLETED" or not target_ref or not expected_hash:
            return VerifyVerdict.MISMATCH
        try:
            connector = artifact_connector()
            source_ref = claimed.get("source_ref")
            source_hash = claimed.get("source_sha256")
            if source_ref and source_hash:
                current_source = connector.capture(str(source_ref))
                if current_source.sha256 != source_hash:
                    return VerifyVerdict.MISMATCH
            parsed = connector.parse(str(target_ref))
        except Exception:  # noqa: BLE001 - verifier fails closed
            return VerifyVerdict.MISMATCH
        return VerifyVerdict.MATCH if (
            parsed.source.sha256 == expected_hash and claimed.get("readback_verified")
        ) else VerifyVerdict.MISMATCH
    if action_kind == "file.read":
        return VerifyVerdict.MATCH if claimed.get("exists") else VerifyVerdict.MISMATCH
    if action_kind == "git.commit":
        if claimed.get("head") and claimed.get("head") == claimed.get("observed_head"):
            return VerifyVerdict.MATCH
        return VerifyVerdict.MISMATCH
    if action_kind == "git.branch":
        worktree = claimed.get("worktree") or (payload or {}).get("worktree")
        branch = claimed.get("branch")
        head = claimed.get("head")
        if not worktree or not branch:
            return VerifyVerdict.MISMATCH
        try:
            observed_branch = subprocess.run(
                ["git", "-C", str(worktree), "branch", "--show-current"],
                capture_output=True, text=True, timeout=30, check=False,
                **headless_run_kwargs(),
            )
            observed_head = subprocess.run(
                ["git", "-C", str(worktree), "rev-parse", "HEAD"],
                capture_output=True, text=True, timeout=30, check=False,
                **headless_run_kwargs(),
            )
        except OSError:
            return VerifyVerdict.MISMATCH
        if observed_branch.returncode != 0 or observed_head.returncode != 0:
            return VerifyVerdict.MISMATCH
        matches = (
            observed_branch.stdout.strip() == str(branch)
            and (not head or observed_head.stdout.strip() == str(head))
        )
        return VerifyVerdict.MATCH if matches else VerifyVerdict.MISMATCH
    if action_kind == "git.push":
        if claimed.get("remote_head") and claimed.get("remote_head") == claimed.get("observed_remote_head"):
            return VerifyVerdict.MATCH
        return VerifyVerdict.MISMATCH
    if action_kind == "git.push.reconcile":
        if (
            claimed_status == "COMPLETED"
            and claimed.get("reconciled") is True
            and claimed.get("expected_head")
            and claimed.get("remote_head") == claimed.get("expected_head")
        ):
            return VerifyVerdict.MATCH
        return VerifyVerdict.UNKNOWN if claimed_status in {"UNKNOWN", "BLOCKED"} else VerifyVerdict.MISMATCH
    if action_kind == "git.read_ref":
        return VerifyVerdict.MATCH if claimed.get("head") else VerifyVerdict.MISMATCH
    if action_kind == "git.log":
        return VerifyVerdict.MATCH if claimed.get("head") else VerifyVerdict.MISMATCH
    if action_kind == "hash.file.sha256":
        path = Path(claimed["path"])
        import hashlib
        if hashlib.sha256(path.read_bytes()).hexdigest() == claimed.get("sha256"):
            return VerifyVerdict.MATCH
        return VerifyVerdict.MISMATCH
    if action_kind in {"tests.run", "script.run"}:
        return VerifyVerdict.MATCH if claimed.get("returncode") == 0 else VerifyVerdict.MISMATCH
    if action_kind.startswith("github.") and action_kind.endswith(".read"):
        if claimed.get("document") is not None or claimed.get("authenticated_login"):
            return VerifyVerdict.MATCH
        return VerifyVerdict.MISMATCH
    def github_write_readback_verified() -> bool:
        readback = claimed.get("readback")
        return (
            claimed_status == "COMPLETED"
            and bool(claimed.get("number"))
            and claimed.get("reconciled") is True
            and claimed.get("readback_status") == "MATCH"
            and isinstance(readback, Mapping)
            and readback.get("matches") is True
        )
    if action_kind == "github.issue.create":
        return VerifyVerdict.MATCH if github_write_readback_verified() else VerifyVerdict.MISMATCH
    if action_kind == "github.issue.comment.create":
        return VerifyVerdict.MATCH if github_write_readback_verified() else VerifyVerdict.MISMATCH
    if action_kind == "github.pull_request.create":
        return VerifyVerdict.MATCH if github_write_readback_verified() else VerifyVerdict.MISMATCH
    if action_kind == "github.write.reconcile":
        readback = claimed.get("readback")
        if (
            claimed_status == "COMPLETED"
            and claimed.get("original_effect_id")
            and claimed.get("accepted") is True
            and claimed.get("reconciled") is True
            and isinstance(readback, Mapping)
            and readback.get("status") == "MATCH"
            and readback.get("matches") is True
        ):
            return VerifyVerdict.MATCH
        return VerifyVerdict.UNKNOWN if claimed_status in {"UNKNOWN", "BLOCKED"} else VerifyVerdict.MISMATCH
    if action_kind == "github.pull_request.publish":
        push = claimed.get("push_effect")
        pull_request = claimed.get("pull_request_effect")
        if (
            claimed_status == "COMPLETED"
            and claimed.get("accepted") is True
            and claimed.get("terminal_truth") is True
            and isinstance(push, Mapping)
            and push.get("accepted") is True
            and push.get("readback_status") == "MATCH"
            and isinstance(pull_request, Mapping)
            and pull_request.get("accepted") is True
            and pull_request.get("readback_status") == "MATCH"
            and claimed.get("provider_ref")
            and claimed.get("number")
        ):
            return VerifyVerdict.MATCH
        return VerifyVerdict.UNKNOWN if claimed_status in {"UNKNOWN", "BLOCKED"} else VerifyVerdict.MISMATCH
    if action_kind == "gmail.send":
        readback = claimed.get("readback")
        if (
            claimed_status == "COMPLETED"
            and claimed.get("accepted") is True
            and claimed.get("terminal_truth") is True
            and claimed.get("provider_id")
            and claimed.get("provider_ref")
            and isinstance(readback, Mapping)
            and readback.get("status") == "MATCH"
            and readback.get("matches") is True
        ):
            return VerifyVerdict.MATCH
        return VerifyVerdict.UNKNOWN if claimed_status in {"UNKNOWN", "BLOCKED"} else VerifyVerdict.MISMATCH
    if action_kind == "http.read":
        return VerifyVerdict.MATCH if claimed.get("document") is not None else VerifyVerdict.MISMATCH
    if action_kind == "http.write":
        observed = claimed.get("observed_state") or {}
        if (
            claimed.get("claimed") == "COMPLETED"
            and claimed.get("idempotency_key")
            and observed.get("last_key") == claimed.get("idempotency_key")
        ):
            return VerifyVerdict.MATCH
        return VerifyVerdict.MISMATCH
    if action_kind == "http.doc.read":
        return VerifyVerdict.MATCH if claimed.get("raw_sha256") else VerifyVerdict.MISMATCH
    if action_kind == "browser.web.read":
        if (
            claimed_status == "COMPLETED"
            and claimed.get("authority") == "DATA_ONLY"
            and claimed.get("source_sha256")
            and claimed.get("document_id")
            and claimed.get("page_revision")
        ):
            if payload is not None and payload.get("require_authenticated"):
                session = claimed.get("session") or {}
                return (
                    VerifyVerdict.MATCH
                    if session.get("state") == "AUTHENTICATED"
                    else VerifyVerdict.MISMATCH
                )
            return VerifyVerdict.MATCH
        return VerifyVerdict.MISMATCH
    if action_kind == "browser.web.inspect":
        if (
            claimed_status == "COMPLETED"
            and claimed.get("authority") == "DATA_ONLY"
            and claimed.get("document_id")
            and claimed.get("page_revision")
            and claimed.get("structural_hash")
            and isinstance(claimed.get("element_count"), int)
            and isinstance(claimed.get("elements"), list)
        ):
            return VerifyVerdict.MATCH
        return VerifyVerdict.MISMATCH
    if action_kind == "browser.web.wait":
        readback = claimed.get("readback")
        if (
            claimed_status == "COMPLETED"
            and claimed.get("authority") == "DATA_ONLY"
            and claimed.get("action") == "wait"
            and claimed.get("target_id")
            and claimed.get("page_revision")
            and claimed.get("readback_verified") is True
            and claimed.get("reconciled") is True
            and isinstance(readback, Mapping)
            and readback.get("verified") is True
            and readback.get("wait_state") == claimed.get("wait_state")
        ):
            return VerifyVerdict.MATCH
        return VerifyVerdict.MISMATCH
    if action_kind == "browser.web.reconcile":
        readback = claimed.get("readback")
        if (
            claimed_status == "COMPLETED"
            and claimed.get("authority") == "DATA_ONLY"
            and claimed.get("original_effect_id")
            and claimed.get("accepted") is True
            and claimed.get("terminal_truth") is True
            and claimed.get("reconciled") is True
            and isinstance(readback, Mapping)
            and readback.get("status") == "MATCH"
            and readback.get("matches") is True
        ):
            return VerifyVerdict.MATCH
        return VerifyVerdict.MISMATCH
    if action_kind == "browser.web.execute":
        session = claimed.get("session") or {}
        require_authenticated = bool(
            payload is not None and payload.get("require_authenticated")
        ) or bool(claimed.get("authorization_require_authenticated"))
        auth_ok = (
            not require_authenticated
            or session.get("state") == "AUTHENTICATED"
        )
        if payload is not None and payload.get("action") == "download":
            download = claimed.get("download")
            if not isinstance(download, Mapping):
                return VerifyVerdict.MISMATCH
            path_value = download.get("path")
            expected_hash = download.get("sha256")
            expected_size = download.get("size_bytes")
            if (
                claimed_status != "COMPLETED"
                or claimed.get("authority") != "DATA_ONLY"
                or claimed.get("readback_verified") is not True
                or claimed.get("reconciled") is not True
                or not isinstance(path_value, str)
                or not isinstance(expected_hash, str)
                or not isinstance(expected_size, int)
                or not auth_ok
            ):
                return VerifyVerdict.MISMATCH
            try:
                path = Path(path_value).expanduser().resolve(strict=True)
                path.relative_to(owned_root)
                content = path.read_bytes()
            except (OSError, ValueError):
                return VerifyVerdict.MISMATCH
            from hashlib import sha256

            return (
                VerifyVerdict.MATCH
                if len(content) == expected_size
                and sha256(content).hexdigest() == expected_hash
                else VerifyVerdict.MISMATCH
            )
        if payload is not None and payload.get("action") == "upload":
            upload = claimed.get("upload")
            postcondition = upload.get("postcondition") if isinstance(
                upload, Mapping
            ) else None
            precondition = upload.get("precondition") if isinstance(
                upload, Mapping
            ) else None
            if not isinstance(upload, Mapping):
                return VerifyVerdict.MISMATCH
            source_ref = upload.get("source_ref")
            expected_hash = upload.get("sha256")
            expected_size = upload.get("size_bytes")
            readback = upload.get("readback")
            if (
                claimed_status != "COMPLETED"
                or claimed.get("authority") != "DATA_ONLY"
                or claimed.get("readback_verified") is not True
                or claimed.get("reconciled") is not True
                or not isinstance(source_ref, str)
                or not isinstance(expected_hash, str)
                or not isinstance(expected_size, int)
                or not isinstance(readback, Mapping)
                or readback.get("verified") is not True
                or readback.get("selected_count") != 1
                or not isinstance(precondition, Mapping)
                or precondition.get("matched") is not False
                or not isinstance(postcondition, Mapping)
                or postcondition.get("verified") is not True
                or postcondition.get("matched") is not True
                or not auth_ok
            ):
                return VerifyVerdict.MISMATCH
            try:
                relative = Path(source_ref)
                current = owned_root
                for part in relative.parts:
                    current /= part
                    if current.exists() and current.is_symlink():
                        return VerifyVerdict.MISMATCH
                path = (owned_root / relative).resolve(strict=True)
                path.relative_to(owned_root)
                if not path.is_file():
                    return VerifyVerdict.MISMATCH
                content = path.read_bytes()
            except (OSError, ValueError):
                return VerifyVerdict.MISMATCH
            from hashlib import sha256

            files = readback.get("files")
            browser_file = (
                files[0]
                if isinstance(files, list)
                and len(files) == 1
                and isinstance(files[0], Mapping)
                else {}
            )
            return (
                VerifyVerdict.MATCH
                if len(content) == expected_size
                and sha256(content).hexdigest() == expected_hash
                and browser_file.get("size_bytes") == expected_size
                and browser_file.get("sha256") == expected_hash
                else VerifyVerdict.MISMATCH
            )
        if (
            claimed_status == "COMPLETED"
            and claimed.get("authority") == "DATA_ONLY"
            and claimed.get("effect_id")
            and claimed.get("idempotency_key")
            and claimed.get("page_revision")
            and claimed.get("readback_verified") is True
            and claimed.get("reconciled") is True
            and auth_ok
        ):
            return VerifyVerdict.MATCH
        return VerifyVerdict.MISMATCH
    if action_kind == "system.diagnose":
        if claimed.get("python") and claimed.get("owned_root_exists"):
            return VerifyVerdict.MATCH
        return VerifyVerdict.MISMATCH
    if action_kind == "system.runtime":
        if claimed.get("python") and claimed.get("source_kind"):
            return VerifyVerdict.MATCH
        return VerifyVerdict.MISMATCH
    if action_kind == "git.inspect":
        return VerifyVerdict.MATCH if claimed.get("head") else VerifyVerdict.MISMATCH
    if action_kind == "git.diff":
        return VerifyVerdict.MATCH if claimed.get("head") else VerifyVerdict.MISMATCH
    if action_kind == "git.branch":
        return VerifyVerdict.MATCH if claimed.get("branch") and claimed.get("head") else VerifyVerdict.MISMATCH
    if action_kind == "dir.list":
        names = claimed.get("names") or []
        path = Path(claimed.get("path") or owned_root)
        if claimed.get("claimed") != "COMPLETED" or not path.is_dir():
            return VerifyVerdict.MISMATCH
        if all((path / name).exists() for name in names):
            return VerifyVerdict.MATCH
        return VerifyVerdict.MISMATCH
    if action_kind.startswith("mcp."):
        receipt = claimed.get("mcp_receipt")
        if (
            claimed_status != "COMPLETED"
            or claimed.get("authority") != "DATA_ONLY"
            or claimed.get("bounded") is not True
            or not claimed.get("server_identity")
            or not claimed.get("tool_name")
            or not claimed.get("result_sha256")
        ):
            return VerifyVerdict.MISMATCH
        effect_class = str(claimed.get("effect_class") or "")
        if effect_class == EffectClass.READ_ONLY.value:
            return VerifyVerdict.MATCH if (
                isinstance(receipt, Mapping)
                and receipt.get("result_sha256") == claimed.get("result_sha256")
                and receipt.get("authority") == "DATA_ONLY"
            ) else VerifyVerdict.MISMATCH
        readback = claimed.get("readback")
        provider_receipt = claimed.get("provider_receipt")
        if (
            claimed.get("effect_id")
            and claimed.get("idempotency_key")
            and claimed.get("readback_verified") is True
            and claimed.get("reconciled") is True
            and claimed.get("accepted") is True
            and claimed.get("terminal_truth") is True
            and isinstance(readback, Mapping)
            and readback.get("status") == "MATCH"
            and readback.get("matches") is True
            and isinstance(provider_receipt, Mapping)
            and provider_receipt.get("provider_id")
            and provider_receipt.get("provider_ref")
        ):
            return VerifyVerdict.MATCH
        return VerifyVerdict.UNKNOWN

    personal_verdict = personal_operator_verdict()
    if personal_verdict is not None:
        return personal_verdict
    if action_kind == "workflow.run":
        if claimed_status == "COMPLETED" and claimed.get("nested_success") is True:
            nested = claimed.get("nested_receipts")
            if isinstance(nested, list) and nested and all(
                isinstance(item, Mapping) and item.get("state") == "VERIFIED"
                for item in nested
            ):
                return VerifyVerdict.MATCH
            return VerifyVerdict.MISMATCH
        if claimed_status == "COMPLETED":
            return VerifyVerdict.MISMATCH
        return VerifyVerdict.UNKNOWN
    if action_kind == "llm.dispatch":
        return VerifyVerdict.UNKNOWN
    return VerifyVerdict.UNKNOWN


def _verify_with_postconditions(
    action_kind: str,
    claimed: Mapping[str, Any],
    owned_root: Path,
    payload: Mapping[str, Any] | None = None,
    executor_context: ExecutorContext | None = None,
    route_owner: str | None = None,
) -> tuple[VerifyVerdict, tuple[Any, ...]]:
    """Run the existing action verifier plus declared postconditions."""

    verdict = _verify_action(
        action_kind,
        claimed,
        owned_root,
        payload,
        executor_context,
        route_owner,
    )
    results = evaluate_postconditions(
        claimed,
        payload.get("postconditions") if payload is not None else (),
    )
    if verdict is VerifyVerdict.MATCH and results:
        statuses = {item.status for item in results}
        if PostconditionStatus.FAIL in statuses:
            verdict = VerifyVerdict.MISMATCH
        elif PostconditionStatus.UNKNOWN in statuses:
            verdict = VerifyVerdict.UNKNOWN
    return verdict, results


def _verify(
    action_kind: str,
    claimed: Mapping[str, Any],
    owned_root: Path,
    payload: Mapping[str, Any] | None = None,
) -> VerifyVerdict:
    """Preserve the historical verifier return shape for existing callers."""

    return _verify_with_postconditions(
        action_kind, claimed, owned_root, payload,
    )[0]


def _verify_canonical_artifact(
    executor: str,
    capability_id: str,
    payload: Mapping[str, Any],
    claimed: Mapping[str, Any],
    *,
    python_executable: str | None,
    owned_root: Path,
) -> tuple[VerifyVerdict, dict[str, Any]]:
    """Independently replay a read-only Factory artifact through its contract."""

    from ..adapter_factory import invoke_artifact, load_verified_artifact, validate_schema

    try:
        root, manifest, descriptor = load_verified_artifact(executor)
        capability = next(
            item for item in descriptor.capabilities
            if item.capability_id == capability_id
        )
        errors = validate_schema(claimed, capability.output_schema)
        status = str(claimed.get("claimed") or "UNKNOWN")
        evidence: dict[str, Any] = {
            "adapter_protocol": manifest.get("protocol"),
            "output_schema_valid": not errors,
            "output_schema_errors": list(errors),
            "independent_readback": None,
        }
        if status == "FAILED":
            return VerifyVerdict.MATCH, evidence
        if status != "COMPLETED" or errors:
            return (
                VerifyVerdict.UNKNOWN if status == "BLOCKED" else VerifyVerdict.MISMATCH,
                evidence,
            )
        if capability.effect_class is not EffectClass.READ_ONLY:
            # Mutating adapters remain under the effect journal and their
            # declared reconciliation seam.  This build-ahead engine adopts
            # only read-only artifacts, so it never promotes an unverified
            # mutation from this conservative fallback.
            return VerifyVerdict.UNKNOWN, evidence
        readback = invoke_artifact(
            root,
            capability_id,
            payload,
            python_executable=python_executable,
            allowed_root=owned_root,
        )
        readback_errors = validate_schema(readback, capability.output_schema)
        evidence["independent_readback"] = readback
        evidence["readback_schema_valid"] = not readback_errors
        evidence["readback_matches"] = readback == dict(claimed)
        if readback_errors or readback != dict(claimed):
            return VerifyVerdict.MISMATCH, evidence
        return VerifyVerdict.MATCH, evidence
    except Exception as error:  # noqa: BLE001 - verification fails closed
        return VerifyVerdict.UNKNOWN, {
            "adapter_protocol": "jarvis.capability.adapter.v1",
            "verification_error": type(error).__name__,
        }


def canonical_predicate_verdict(
    database: FoundryDatabase,
    *,
    action_kind: str,
    capability_id: str,
    declared: Mapping[str, Any],
    claimed: Mapping[str, Any],
    owned_root: Path,
    executor_context: ExecutorContext | None = None,
) -> VerifyVerdict:
    """Re-derive an action's verdict with the canonical JARVIS predicate.

    Shares the exact predicate the Fabric spine applies to the action itself
    (a Factory artifact replay when the capability is a canonical adapter,
    otherwise the action verifier plus declared postconditions), so an
    independent verifier session re-derives the same truth from reality
    instead of re-judging a deterministic effect.
    """

    spec = get_spec(database, capability_id)
    if spec is not None and spec.kind is Kind.ADAPTER:
        metadata = metadata_for_spec(spec)
        if metadata.execute.get("artifact_id"):
            try:
                verdict, _ = _verify_canonical_artifact(
                    spec.executor,
                    spec.capability_id,
                    declared,
                    claimed,
                    python_executable=(
                        str(metadata.execute.get("python_executable") or "") or None
                    ),
                    owned_root=owned_root,
                )
            except Exception:  # noqa: BLE001 - a re-derivation that cannot run
                return VerifyVerdict.UNKNOWN  # is not a verdict; fail closed.
            return verdict
    try:
        verdict, _ = _verify_with_postconditions(
            action_kind,
            claimed,
            owned_root,
            declared,
            executor_context,
            None,
        )
    except Exception:  # noqa: BLE001 - predicates may raise on vanished
        return VerifyVerdict.UNKNOWN  # subjects; never a verdict, never a crash.
    return verdict


_SPECIALIZED_EFFECT_ACTIONS = frozenset({
    "git.push",
    "browser.web.execute",
    "github.pull_request.publish",
    "gmail.send",
})
_RECOVERABLE_EFFECT_STATES = frozenset({
    STARTED,
    COMPLETED,
    FAILED,
    FROZEN,
})


def _requires_canonical_effect(
    request: AdapterRequest,
    action_kind: str,
) -> bool:
    return (
        request.effect_class is not EffectClass.READ_ONLY
        and action_kind not in _SPECIALIZED_EFFECT_ACTIONS
    )


def _existing_canonical_effect_binding(
    database: FoundryDatabase,
    intent: TaskIntent,
    action_kind: str,
    payload: Mapping[str, Any],
    *,
    workflow_id: str | None,
    step_id: str | None,
    step_version: int,
    attempt: int,
) -> tuple[CapabilitySpec, AdapterRequest] | None:
    """Resolve an exact prior effect before current readiness admission.

    Capability readiness governs new dispatch. It must not hide a journaled
    effect that now needs idempotent replay, crash reaping, or reconciliation.
    """

    matches: list[tuple[CapabilitySpec, AdapterRequest]] = []
    for spec in specs_for_action(database, action_kind):
        metadata = capability_metadata(database, spec.capability_id)
        if metadata is None:
            metadata = metadata_for_spec(spec)
        try:
            request = build_adapter_request(
                mission_id=intent.task_id,
                workflow_id=workflow_id,
                step_id=step_id or action_kind,
                step_version=step_version,
                attempt=attempt,
                action_kind=action_kind,
                payload=payload,
                spec=spec,
                metadata=metadata,
            )
        except (KeyError, TypeError, ValueError):
            continue
        if not _requires_canonical_effect(request, action_kind):
            continue
        state = effect_state(database, request.effect_id)
        if state.get("status") in _RECOVERABLE_EFFECT_STATES:
            matches.append((spec, request))
    if len(matches) > 1:
        raise FabricError(
            "multiple canonical prior effects match this action; "
            "refusing ambiguous replay"
        )
    return matches[0] if matches else None


def execute_action(
    database: FoundryDatabase,
    ctx: ExecutorContext,
    intent: TaskIntent,
    action_kind: str,
    payload: Mapping[str, Any],
    *,
    claim_override: Mapping[str, Any] | None = None,
    workflow_id: str | None = None,
    step_id: str | None = None,
    step_version: int = 1,
    attempt: int = 1,
    route_owner: str | None = None,
) -> ActionResult:
    if route_owner not in {None, "general_work", "personal_operator"}:
        raise FabricError(f"unsupported Fabric route owner: {route_owner}")
    if route_owner is None:
        normalized_task_id = intent.task_id.lower().replace("_", "-")
        if normalized_task_id.startswith("general-work."):
            route_owner = "general_work"
    existing_effect = _existing_canonical_effect_binding(
        database,
        intent,
        action_kind,
        payload,
        workflow_id=workflow_id,
        step_id=step_id,
        step_version=step_version,
        attempt=attempt,
    )
    stop = emergency_stop_reason(
        database,
        payload.get("stop_path"),
        identities={"GLOBAL", intent.task_id},
    )
    reconciliation_capable = (
        action_kind in {
            "github.pull_request.publish", "gmail.send", "github.write.reconcile",
            "git.push.reconcile",
        }
        or action_kind.startswith("mcp.")
        or action_kind in {
            "browser.web.reconcile",
            "local.process.reconcile",
            "desktop.action.reconcile",
        }
    )
    if stop and not reconciliation_capable and existing_effect is None:
        return ActionResult(
            capability_id="blocked",
            action_kind=action_kind,
            claimed=ClaimOutcome.BLOCKED,
            verified=VerifyVerdict.UNKNOWN,
            note=f"STOP active: {stop}",
        )
    request: AdapterRequest | None = None
    if existing_effect is None:
        selection = select_capability(database, intent, action_kind)
        if not selection.available:
            return ActionResult(
                capability_id="missing",
                action_kind=action_kind,
                claimed=ClaimOutcome.BLOCKED,
                verified=VerifyVerdict.UNKNOWN,
                note=selection.reason,
            )
        spec = get_spec(database, selection.capability_id)
        if spec is None:
            return ActionResult(
                capability_id=selection.capability_id or "missing",
                action_kind=action_kind,
                claimed=ClaimOutcome.BLOCKED,
                verified=VerifyVerdict.UNKNOWN,
                note="selected capability is missing from the Fabric catalog",
            )
    else:
        spec, request = existing_effect
        selection = Selection(
            task_id=intent.task_id,
            action_kind=action_kind,
            capability_id=spec.capability_id,
            fallbacks=(),
            reason="reconciling existing canonical effect",
        )
    metadata = capability_metadata(database, spec.capability_id)
    if metadata is None:
        metadata = metadata_for_spec(spec)
    inferred_metadata = metadata_for_spec(spec)
    if request is None:
        request = build_adapter_request(
            mission_id=intent.task_id,
            workflow_id=workflow_id,
            step_id=step_id or action_kind,
            step_version=step_version,
            attempt=attempt,
            action_kind=action_kind,
            payload=payload,
            spec=spec,
            metadata=metadata,
        )
    if (
        stop
        and request.effect_class is EffectClass.READ_ONLY
        and not reconciliation_capable
    ):
        return ActionResult(
            capability_id=spec.capability_id,
            action_kind=action_kind,
            claimed=ClaimOutcome.BLOCKED,
            verified=VerifyVerdict.UNKNOWN,
            note=f"STOP active: {stop}",
        )
    trust = capability_trust(database, spec.capability_id)
    prior_effect = effect_state(database, request.effect_id)
    probation_irreversible = (
        request.effect_class is EffectClass.IRREVERSIBLE_OR_HIGH_COST
        or metadata.effect_class is EffectClass.IRREVERSIBLE_OR_HIGH_COST
        or inferred_metadata.effect_class is EffectClass.IRREVERSIBLE_OR_HIGH_COST
    )
    if (
        trust is CapabilityTrust.PROBATION
        and probation_irreversible
        and prior_effect.get("status")
        not in {STARTED, "COMPLETED", "FAILED", "FROZEN"}
    ):
        record_probation_block(
            database,
            spec.capability_id,
            action_kind=action_kind,
            effect_class=EffectClass.IRREVERSIBLE_OR_HIGH_COST.value,
        )
        return ActionResult(
            capability_id=spec.capability_id,
            action_kind=action_kind,
            claimed=ClaimOutcome.BLOCKED,
            verified=VerifyVerdict.UNKNOWN,
            note="PROBATION capability cannot dispatch irreversible external effects",
        )
    execution_payload = dict(payload)
    if request is not None:
        # The selected adapter receives the canonical identity sidecar, never
        # a second identity/effect implementation or the raw request payload.
        execution_payload.setdefault("mission_id", request.mission_id)
        execution_payload.setdefault("workflow_id", request.workflow_id)
        execution_payload.setdefault("stop_scope", request.stop_scope)
        execution_payload["_adapter_request"] = request.to_evidence()
    if request is not None and request.effect_class.value != "READ_ONLY":
        execution_payload.setdefault("idempotency_key", request.idempotency_key)
    # The existing Fabric effect journal remains the sole authority for every
    # mutating adapter invocation. Read-only adapters need no effect claim.
    canonical_effect_required = _requires_canonical_effect(
        request,
        action_kind,
    )
    canonical_artifact = bool(
        spec.kind is Kind.ADAPTER and metadata.execute.get("artifact_id")
    )
    def perform() -> Mapping[str, Any]:
        if claim_override is not None:
            return dict(claim_override)
        if canonical_artifact:
            from ..adapter_factory import invoke_artifact

            return invoke_artifact(
                spec.executor,
                spec.capability_id,
                payload,
                adapter_request=request.to_evidence(),
                canonical_effect_authorized=canonical_effect_required,
                python_executable=metadata.execute.get("python_executable"),
                stop_path=payload.get("stop_path"),
                allowed_root=ctx.owned_root,
                timeout_s=float(payload.get("adapter_timeout_s") or 120.0),
            )
        if spec.kind is Kind.SCRIPT:
            from .expansion import load_candidate

            path = Path(str(payload["path"]))
            if not path.is_absolute():
                path = ctx.owned_root / path
            module = load_candidate(Path(spec.executor))
            digest = module.run(str(path))
            return {"claimed": "COMPLETED", "path": str(path), "sha256": digest}
        return dispatch(
            ctx,
            action_kind,
            execution_payload,
            route_owner=route_owner,
        )

    if canonical_effect_required:
        stop_identities = set(request.stop_scope)
        stop_identities.update({"GLOBAL", intent.task_id})
        request_identity = request.to_evidence()
        recovery_intent = _developer_recovery_intent(
            action_kind,
            execution_payload,
            ctx,
        )
        if recovery_intent is not None:
            request_identity["recovery_intent"] = recovery_intent
        claimed = execute_fabric_effect(
            database,
            action_key=request.effect_id,
            system_id=system_id_for_action(action_kind, request.target_ref),
            target_ref=request.target_ref,
            payload_hash=request.input_hash,
            kind=action_kind,
            connector_id=f"fabric:{request.capability_id}",
            before_state_capture=(
                {"before_state_ref": request.before_state_ref}
                if request.before_state_ref is not None else {}
            ),
            expected_effects=(action_kind,),
            action_id=request.request_id,
            request_identity=request_identity,
            perform=perform,
            stop_check=lambda: bool(
                emergency_stop_reason(
                    database,
                    payload.get("stop_path"),
                    identities=stop_identities,
                )
            ),
            claim_ttl_s=float(payload.get("claim_ttl_s") or 300.0),
        )
    else:
        claimed = perform()
    if (
        action_kind.startswith("mcp.")
        and request.effect_class is not EffectClass.READ_ONLY
    ):
        claimed = finalize_mcp_mutation_effect(
            ctx, execution_payload, claimed,
        )
    adapter_verification: dict[str, Any] = {}
    if canonical_artifact and claim_override is None:
        verdict, adapter_verification = _verify_canonical_artifact(
            spec.executor,
            spec.capability_id,
            payload,
            claimed,
            python_executable=str(metadata.execute.get("python_executable") or "") or None,
            owned_root=ctx.owned_root,
        )
        postconditions = evaluate_postconditions(claimed, payload.get("postconditions") or ())
        statuses = {item.status for item in postconditions}
        if verdict is VerifyVerdict.MATCH and PostconditionStatus.FAIL in statuses:
            verdict = VerifyVerdict.MISMATCH
        elif verdict is VerifyVerdict.MATCH and PostconditionStatus.UNKNOWN in statuses:
            verdict = VerifyVerdict.UNKNOWN
    else:
        verdict, postconditions = _verify_with_postconditions(
            action_kind,
            claimed,
            ctx.owned_root,
            payload,
            ctx,
            route_owner,
        )
    claimed_outcome = ClaimOutcome(str(claimed.get("claimed") or "UNKNOWN"))
    if claimed_outcome is ClaimOutcome.COMPLETED and verdict is VerifyVerdict.MISMATCH:
        note = "worker claim diverged from observed effect"
    else:
        note = str(claimed.get("error_code") or "")
    receipt = (
        normalize_receipt(
            request,
            claimed=claimed,
            claimed_outcome=claimed_outcome.value,
            verified=verdict.value,
            note=note,
            postcondition_results=postconditions,
        ).as_dict()
        if request is not None
        else {}
    )
    result = ActionResult(
        capability_id=selection.capability_id or "missing",
        action_kind=action_kind,
        claimed=claimed_outcome,
        verified=verdict,
        observed={
            "verified_at": isoformat(),
            **{k: v for k, v in claimed.items() if k != "document" or action_kind != "artifact.research"},
            **({"adapter_verification": adapter_verification} if adapter_verification else {}),
        },
        claimed_effect=dict(claimed),
        note=note,
        receipt=receipt,
    )
    record_outcome(
        database, result.capability_id,
        success=result.success,
        verified=verdict,
        note=result.note,
    )
    return result


def run_intent(
    database: FoundryDatabase,
    ctx: ExecutorContext,
    intent: TaskIntent,
    payloads: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    seed_builtins(database)
    results: list[ActionResult] = []
    for action in intent.required_actions:
        result = execute_action(database, ctx, intent, action, payloads.get(action, {}))
        if result.claimed is ClaimOutcome.BLOCKED and result.capability_id == "missing":
            acquired = try_acquire(database, ctx, action)
            if acquired.get("status") == "REGISTERED":
                result = execute_action(database, ctx, intent, action, payloads.get(action, {}))
        results.append(result)
        # Only a definite pre-effect failure may select another provider.
        # COMPLETED-but-unverified and UNKNOWN may already have mutated the
        # target, so falling through would create a second logical effect.
        if result.claimed is ClaimOutcome.FAILED:
            fallbacks = select_capability(database, intent, action).fallbacks
            if fallbacks:
                alt_intent = TaskIntent(
                    task_id=intent.task_id,
                    required_actions=intent.required_actions,
                    unattended=intent.unattended,
                    preferred=fallbacks[0],
                )
                retry = execute_action(database, ctx, alt_intent, action, payloads.get(action, {}))
                results[-1] = retry
    return {
        "task_id": intent.task_id,
        "results": [item.as_dict() for item in results],
        "success": all(item.success for item in results),
        "catalog": catalog_snapshot(database),
        "provider": route_provider(unattended=intent.unattended).as_dict(),
    }


def try_acquire(
    database: FoundryDatabase,
    ctx: ExecutorContext,
    action_kind: str,
) -> dict[str, Any]:
    if action_kind == "hash.file.sha256":
        return acquire_hash_capability(database, ctx.owned_root)
    gap = GapRecord(
        gap_id=f"gap.unsupported.{action_kind.replace('.', '-')}",
        needed_action=action_kind,
        missing=GapKind.EXTERNAL_DEPENDENCY,
        status=GapStatus.DETECTED,
        detail=f"no acquisition recipe for {action_kind}",
    )
    record_gap(database, gap)
    return {"status": "UNSUPPORTED", "gap": gap.as_dict()}
