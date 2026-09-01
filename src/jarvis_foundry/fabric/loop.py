"""INTENT and NEED loops for the V9 capability fabric."""
from __future__ import annotations

from pathlib import Path
from typing import Mapping

from ..clock import isoformat
from ..control.broker import emergency_stop_reason
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
from .contracts import (
    AdapterRequest,
    PostconditionStatus,
    build_adapter_request,
    evaluate_postconditions,
    normalize_receipt,
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
            capability_id="cap.github.pull_request.create", version="1.0.0", kind=Kind.CONNECTOR,
            action_kind="github.pull_request.create", semantic=Semantic.EXTERNAL_EFFECT,
            executor="github_write",
            unattended=UnattendedMode.ALLOWED,
            limitations=("explicit write transport required; no silent live mutation",),
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
            capability_id="cap.browser.web.execute", version="1.0.0", kind=Kind.CONNECTOR,
            action_kind="browser.web.execute", semantic=Semantic.EXTERNAL_EFFECT,
            executor="browser",
            unattended=UnattendedMode.INTERACTIVE_REQUIRED,
            limitations=(
                "owner-authorized bounded click/fill/select/check/submit only; "
                "effect journal and readback required; no destructive/public actions",
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


def _verify_action(
    action_kind: str,
    claimed: Mapping[str, Any],
    owned_root: Path,
    payload: Mapping[str, Any] | None = None,
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

    source_roots = None
    if payload is not None:
        source_roots = payload.get("source_roots")
        if source_roots is None and payload.get("source_root") is not None:
            source_roots = [payload.get("source_root")]

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
    if action_kind == "git.push":
        if claimed.get("remote_head") and claimed.get("remote_head") == claimed.get("observed_remote_head"):
            return VerifyVerdict.MATCH
        return VerifyVerdict.MISMATCH
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
    if action_kind == "github.issue.create":
        return VerifyVerdict.MATCH if claimed.get("number") else VerifyVerdict.MISMATCH
    if action_kind == "github.pull_request.create":
        return VerifyVerdict.MATCH if claimed.get("number") else VerifyVerdict.MISMATCH
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
    if action_kind == "browser.web.execute":
        session = claimed.get("session") or {}
        auth_ok = (
            not (payload is not None and payload.get("require_authenticated"))
            or session.get("state") == "AUTHENTICATED"
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
    if action_kind == "llm.dispatch":
        return VerifyVerdict.UNKNOWN
    return VerifyVerdict.UNKNOWN


def _verify_with_postconditions(
    action_kind: str,
    claimed: Mapping[str, Any],
    owned_root: Path,
    payload: Mapping[str, Any] | None = None,
) -> tuple[VerifyVerdict, tuple[Any, ...]]:
    """Run the existing action verifier plus declared postconditions."""

    verdict = _verify_action(action_kind, claimed, owned_root, payload)
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
) -> ActionResult:
    stop = emergency_stop_reason(
        database,
        payload.get("stop_path"),
        identities={"GLOBAL", intent.task_id},
    )
    if stop:
        return ActionResult(
            capability_id="blocked",
            action_kind=action_kind,
            claimed=ClaimOutcome.BLOCKED,
            verified=VerifyVerdict.UNKNOWN,
            note=f"STOP active: {stop}",
        )
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
    request: AdapterRequest | None = None
    if spec is not None:
        request = build_adapter_request(
            mission_id=intent.task_id,
            workflow_id=workflow_id,
            step_id=step_id or action_kind,
            step_version=step_version,
            attempt=attempt,
            action_kind=action_kind,
            payload=payload,
            spec=spec,
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
        claimed = dispatch(ctx, action_kind, execution_payload)
    verdict, postconditions = _verify_with_postconditions(
        action_kind, claimed, ctx.owned_root, payload,
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
        observed={"verified_at": isoformat(), **{k: v for k, v in claimed.items() if k != "document" or action_kind != "artifact.research"}},
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
