"""INTENT and NEED loops for the V9 capability fabric."""
from __future__ import annotations

from pathlib import Path
from typing import Mapping

from ..clock import isoformat
from ..database import FoundryDatabase
from .catalog import (
    catalog_snapshot,
    get_spec,
    observed_readiness,
    record_gap,
    record_outcome,
    register_capability,
    reliability,
    specs_for_action,
)
from .executors import ExecutorContext, dispatch
from .expansion import acquire_hash_capability
from .model import (
    ActionResult,
    CapabilitySpec,
    ClaimOutcome,
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
from .providers import route_provider


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
            capability_id="cap.git.push", version="1.0.0", kind=Kind.CONNECTOR,
            action_kind="git.push", semantic=Semantic.EXTERNAL_EFFECT, executor="git",
            unattended=UnattendedMode.ALLOWED,
            limitations=("owned disposable remote only; no live production remotes",),
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
            capability_id="cap.github.pull_request.create", version="1.0.0", kind=Kind.CONNECTOR,
            action_kind="github.pull_request.create", semantic=Semantic.EXTERNAL_EFFECT,
            executor="github_write",
            unattended=UnattendedMode.ALLOWED,
            limitations=("explicit write transport required; no silent live mutation",),
        ),
        CapabilitySpec(
            capability_id="cap.system.diagnose", version="1.0.0", kind=Kind.TOOL,
            action_kind="system.diagnose", semantic=Semantic.READ, executor="local",
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
            capability_id="cap.llm.dispatch", version="1.0.0", kind=Kind.PROVIDER,
            action_kind="llm.dispatch", semantic=Semantic.EXECUTE, executor="hermes",
            provider_deps=("opencode_go",),
            unattended=UnattendedMode.INTERACTIVE_REQUIRED,
            limitations=("contributor-tier Hermes requires noninteractive consent",),
        ),
    )
    registered = []
    for spec in builtins:
        if get_spec(database, spec.capability_id) is None:
            register_capability(database, spec, note="builtin seed")
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
    return (
        state_rank.get(readiness.state, 9),
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


def _verify(action_kind: str, claimed: Mapping[str, Any], owned_root: Path) -> VerifyVerdict:
    claimed_status = str(claimed.get("claimed") or "UNKNOWN")
    if claimed_status != "COMPLETED":
        return VerifyVerdict.UNKNOWN if claimed_status == "UNKNOWN" else VerifyVerdict.MISMATCH
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
    if action_kind == "file.read":
        return VerifyVerdict.MATCH if claimed.get("exists") else VerifyVerdict.MISMATCH
    if action_kind == "git.commit":
        if claimed.get("head") and claimed.get("head") == claimed.get("observed_head"):
            return VerifyVerdict.MATCH
        return VerifyVerdict.MISMATCH
    if action_kind == "git.push":
        if claimed.get("remote_head") and claimed.get("remote_head") == claimed.get("observed_remote_head"):
            return VerifyVerdict.MATCH
        return VerifyVerdict.MISMATCH
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
    if action_kind == "github.issue.create":
        return VerifyVerdict.MATCH if claimed.get("number") else VerifyVerdict.MISMATCH
    if action_kind == "github.pull_request.create":
        return VerifyVerdict.MATCH if claimed.get("number") else VerifyVerdict.MISMATCH
    if action_kind == "system.diagnose":
        if claimed.get("python") and claimed.get("owned_root_exists"):
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
    if action_kind == "llm.dispatch":
        return VerifyVerdict.UNKNOWN
    return VerifyVerdict.UNKNOWN


def execute_action(
    database: FoundryDatabase,
    ctx: ExecutorContext,
    intent: TaskIntent,
    action_kind: str,
    payload: Mapping[str, Any],
    *,
    claim_override: Mapping[str, Any] | None = None,
) -> ActionResult:
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
    if claim_override is not None:
        claimed = dict(claim_override)
    elif spec is not None and spec.kind is Kind.SCRIPT:
        from .expansion import load_candidate
        path = Path(str(payload["path"]))
        if not path.is_absolute():
            path = ctx.owned_root / path
        module = load_candidate(Path(spec.executor))
        digest = module.run(str(path))
        claimed = {"claimed": "COMPLETED", "path": str(path), "sha256": digest}
    else:
        claimed = dispatch(ctx, action_kind, payload)
    verdict = _verify(action_kind, claimed, ctx.owned_root)
    claimed_outcome = ClaimOutcome(str(claimed.get("claimed") or "UNKNOWN"))
    if claimed_outcome is ClaimOutcome.COMPLETED and verdict is VerifyVerdict.MISMATCH:
        note = "worker claim diverged from observed effect"
    else:
        note = str(claimed.get("error_code") or "")
    result = ActionResult(
        capability_id=selection.capability_id or "missing",
        action_kind=action_kind,
        claimed=claimed_outcome,
        verified=verdict,
        observed={"verified_at": isoformat(), **{k: v for k, v in claimed.items() if k != "document" or action_kind != "artifact.research"}},
        claimed_effect=dict(claimed),
        note=note,
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
        if not result.success and result.claimed is not ClaimOutcome.BLOCKED:
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
