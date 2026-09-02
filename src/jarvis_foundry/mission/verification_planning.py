"""Pure, fail-closed verification-plan compilation for autonomous work.

The compiler translates a structured candidate outcome plus supplied project
context into the specification already understood by the MissionLoop verifier:
criteria, expected artifacts, and forbidden paths.  It performs no filesystem
inspection, command execution, mission admission, or acceptance decision.

``project_context`` is structural on purpose.  A current or future
``ProjectProfile`` may be passed directly when it exposes ``to_payload()`` or
the documented attributes; plain mappings remain useful at integration seams.
Command values must be argv lists, never shell strings.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping, Sequence

from ..events import canonical_json


PLAN_SCHEMA = "jarvis.verification-plan.v1"
READY = "READY"
NEEDS_CONTEXT = "NEEDS_CONTEXT"

# These are the capabilities present at the existing MissionLoop verifier
# seam: the verifier receives criteria/artifact/path scope, independently
# inspects the frozen tree, and reviews observed command evidence.  Structured
# binary-document parsing must be advertised explicitly by its caller.
CURRENT_VERIFIER_CAPABILITIES = frozenset({
    "criteria_evaluation",
    "expected_artifacts",
    "forbidden_paths",
    "artifact_hash_readback",
    "file_content_readback",
    "git_diff_readback",
    "command_evidence_review",
    "independent_session",
})

_POSTCONDITION_KINDS = frozenset({
    "artifact", "behavior", "content", "git", "schema", "state",
})
_DOCUMENT_EXTENSIONS = frozenset({
    ".csv", ".docx", ".md", ".pdf", ".pptx", ".txt", ".xlsx",
})
_STRUCTURED_DOCUMENT_EXTENSIONS = frozenset({
    ".docx", ".pdf", ".pptx", ".xlsx",
})
_VAGUE_ASSERTIONS = frozenset({
    "artifact exists", "complete", "completed", "done", "exists",
    "file exists", "implemented", "is present", "it works", "nonempty",
    "pass", "passed", "present", "success", "successful", "task complete",
    "works",
})
_UNTRUSTWORTHY_EVIDENCE = frozenset({
    "builder claim", "builder_claim", "candidate claim", "none",
    "self report", "self_report", "success claim",
})
_PROFILE_FIELDS = (
    "schema", "profile_id", "profile_hash", "project_id", "name", "root",
    "path", "project_type", "kind", "repository_identity",
    "repository_state", "stack", "languages", "frameworks", "manifests",
    "important_manifests", "source_roots", "documentation_roots", "vcs",
    "base_sha", "branch", "commands", "required_checks", "known_constraints",
    "unknowns", "forbidden_paths", "protected_paths",
    "forbidden_side_effects", "rollback_cleanup_expectations",
    "verifier_capabilities",
)


class VerificationPlanningError(ValueError):
    """A candidate cannot be compiled into an honest verification contract."""


def _text(value: object, field_name: str, *, limit: int = 2000) -> str:
    if not isinstance(value, str) or not value.strip():
        raise VerificationPlanningError(f"{field_name} must be a non-empty string")
    raw = value.strip()
    if any(ord(char) < 32 for char in raw):
        raise VerificationPlanningError(f"{field_name} contains a control character")
    result = " ".join(raw.split())
    if len(result) > limit:
        raise VerificationPlanningError(f"{field_name} exceeds {limit} characters")
    return result


def _dedupe(values: Iterable[str]) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value not in seen:
            result.append(value)
            seen.add(value)
    return tuple(result)


def _string_tuple(value: object, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple, set, frozenset)):
        raise VerificationPlanningError(f"{field_name} must be a list of strings")
    ordered = sorted(value, key=str) if isinstance(value, (set, frozenset)) else value
    return _dedupe(_text(item, f"{field_name} item") for item in ordered)


def _payload(value: object | None, field_name: str) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return dict(value)
    for method_name in ("to_payload", "to_dict", "as_dict"):
        method = getattr(value, method_name, None)
        if callable(method):
            raw = method()
            if not isinstance(raw, Mapping):
                raise VerificationPlanningError(
                    f"{field_name}.{method_name}() must return an object")
            return dict(raw)
    if is_dataclass(value):
        raw = asdict(value)
        if isinstance(raw, Mapping):
            return dict(raw)
    raw = {
        name: getattr(value, name)
        for name in _PROFILE_FIELDS
        if hasattr(value, name)
    }
    if raw:
        return raw
    raise VerificationPlanningError(f"{field_name} must be a mapping or profile object")


def _relative(value: object, field_name: str, *, allow_node_id: bool = False) -> str:
    raw = _text(value, field_name, limit=500).replace("\\", "/")
    path_text, marker, node_id = raw.partition("::") if allow_node_id else (raw, "", "")
    path = PurePosixPath(path_text)
    first = path.parts[0] if path.parts else ""
    if (
        not path.parts
        or path.is_absolute()
        or path_text.startswith("//")
        or ":" in first
        or ".." in path.parts
        or any(char in raw for char in ";&|><`\n\r")
        or any(char in path_text for char in "*?[]{}")
    ):
        raise VerificationPlanningError(f"{field_name} must be one exact relative path")
    result = "/".join(part for part in path.parts if part not in ("", "."))
    if not result:
        raise VerificationPlanningError(f"{field_name} must identify a file or test")
    if marker:
        node = _text(node_id, f"{field_name} node id", limit=300)
        if any(char in node for char in ";&|><`\n\r"):
            raise VerificationPlanningError(f"{field_name} node id is not exact")
        result += f"::{node}"
    return result


def _command_argv(value: object, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or not value:
        raise VerificationPlanningError(
            f"{field_name} must be a non-empty argv list; shell strings are forbidden")
    return tuple(_text(item, f"{field_name} argv item", limit=500) for item in value)


@dataclass(frozen=True, slots=True)
class VerificationCommand:
    check_id: str
    kind: str
    argv: tuple[str, ...]
    cwd: str
    proves: str
    required: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "check_id", _text(self.check_id, "check_id", limit=120))
        object.__setattr__(self, "kind", _text(self.kind, "command kind", limit=40).lower())
        object.__setattr__(self, "argv", _command_argv(self.argv, "command"))
        cwd = "." if self.cwd == "." else _relative(self.cwd, "command cwd")
        object.__setattr__(self, "cwd", cwd)
        object.__setattr__(self, "proves", _text(self.proves, "command proof"))
        if not isinstance(self.required, bool):
            raise VerificationPlanningError("command required flag must be boolean")

    def to_payload(self) -> dict[str, Any]:
        return {
            "check_id": self.check_id,
            "kind": self.kind,
            "argv": list(self.argv),
            "cwd": self.cwd,
            "proves": self.proves,
            "required": self.required,
        }


@dataclass(frozen=True, slots=True)
class ArtifactExpectation:
    path: str
    kind: str = "file"
    state: str = "present"
    content_requirements: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        path = _relative(self.path, "artifact path")
        if ".git" in PurePosixPath(path).parts:
            raise VerificationPlanningError(
                "expected artifacts cannot target Git internal metadata")
        object.__setattr__(self, "path", path)
        object.__setattr__(self, "kind", _text(self.kind, "artifact kind", limit=80).lower())
        state = _text(self.state, "artifact state", limit=40).lower()
        if state not in {"absent", "changed", "created", "present"}:
            raise VerificationPlanningError(f"unsupported artifact state {state!r}")
        object.__setattr__(self, "state", state)
        object.__setattr__(self, "content_requirements", _dedupe(
            _text(item, "artifact content requirement")
            for item in self.content_requirements
        ))

    def to_payload(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "kind": self.kind,
            "state": self.state,
            "content_requirements": list(self.content_requirements),
        }


@dataclass(frozen=True, slots=True)
class ReadbackRequirement:
    readback_id: str
    kind: str
    target: str
    assertions: tuple[str, ...]
    verifier_capability: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "readback_id", _text(
            self.readback_id, "readback_id", limit=120))
        object.__setattr__(self, "kind", _text(
            self.kind, "readback kind", limit=80).lower())
        object.__setattr__(self, "target", _text(
            self.target, "readback target", limit=500))
        assertions = _dedupe(
            _text(item, "readback assertion") for item in self.assertions)
        if not assertions:
            raise VerificationPlanningError("readback assertions must not be empty")
        object.__setattr__(self, "assertions", assertions)
        object.__setattr__(self, "verifier_capability", _text(
            self.verifier_capability, "verifier capability", limit=120))

    def to_payload(self) -> dict[str, Any]:
        return {
            "readback_id": self.readback_id,
            "kind": self.kind,
            "target": self.target,
            "assertions": list(self.assertions),
            "verifier_capability": self.verifier_capability,
        }


@dataclass(frozen=True, slots=True)
class VerificationPlan:
    """Immutable specification subordinate to MissionLoop and AcceptanceGate."""

    candidate_ref: str
    objective_ref: str | None
    objective: str
    target_project: str
    project_type: str
    project_context_status: str
    expected_outcome: str
    required_postconditions: tuple[str, ...]
    tests_to_run: tuple[VerificationCommand, ...]
    checks_to_run: tuple[VerificationCommand, ...]
    expected_artifacts: tuple[ArtifactExpectation, ...]
    forbidden_side_effects: tuple[str, ...]
    forbidden_paths: tuple[str, ...]
    independent_readback: tuple[ReadbackRequirement, ...]
    rollback_cleanup_expectations: tuple[str, ...]
    evidence_requirements: tuple[str, ...]
    acceptance_criteria: tuple[str, ...]
    verifier_capabilities: tuple[str, ...]
    source_contract_refs: tuple[str, ...] = ()
    context_gaps: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _text(self.candidate_ref, "candidate_ref", limit=300)
        if self.objective_ref is not None:
            _text(self.objective_ref, "objective_ref", limit=300)
        _text(self.objective, "objective")
        _text(self.target_project, "target_project", limit=500)
        _text(self.project_type, "project_type", limit=80)
        if self.project_context_status not in {"AVAILABLE", "MISSING"}:
            raise VerificationPlanningError(
                "project_context_status must be AVAILABLE or MISSING")
        _text(self.expected_outcome, "expected_outcome")
        if not self.required_postconditions:
            raise VerificationPlanningError("verification plan requires postconditions")
        for name in (
            "required_postconditions", "forbidden_side_effects",
            "forbidden_paths", "rollback_cleanup_expectations",
            "evidence_requirements", "acceptance_criteria",
            "verifier_capabilities", "source_contract_refs", "context_gaps",
        ):
            values = getattr(self, name)
            if not isinstance(values, tuple):
                raise VerificationPlanningError(f"{name} must be a tuple")
            for item in values:
                _text(item, f"{name} item")
            if len(set(values)) != len(values):
                raise VerificationPlanningError(f"{name} must not contain duplicates")
        if not self.forbidden_side_effects:
            raise VerificationPlanningError("verification plan requires a forbidden-effect scope")
        if not self.independent_readback:
            raise VerificationPlanningError("verification plan requires independent readback")
        if not self.rollback_cleanup_expectations:
            raise VerificationPlanningError("verification plan requires rollback/cleanup expectations")
        if not self.evidence_requirements or not self.acceptance_criteria:
            raise VerificationPlanningError("verification plan requires evidence and acceptance criteria")
        checks = (*self.tests_to_run, *self.checks_to_run)
        if len({item.check_id for item in checks}) != len(checks):
            raise VerificationPlanningError("verification command ids must be unique")
        if any(item.kind != "test" for item in self.tests_to_run):
            raise VerificationPlanningError("tests_to_run may contain only test commands")
        if any(item.kind == "test" for item in self.checks_to_run):
            raise VerificationPlanningError("checks_to_run cannot contain test commands")
        if len({item.path for item in self.expected_artifacts}) \
                != len(self.expected_artifacts):
            raise VerificationPlanningError("expected artifact paths must be unique")
        if len({item.readback_id for item in self.independent_readback}) \
                != len(self.independent_readback):
            raise VerificationPlanningError("readback ids must be unique")

    @property
    def status(self) -> str:
        return NEEDS_CONTEXT if self.context_gaps else READY

    @property
    def ready(self) -> bool:
        return self.status == READY

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "schema": PLAN_SCHEMA,
            "status": self.status,
            "candidate_ref": self.candidate_ref,
            "objective_ref": self.objective_ref,
            "objective": self.objective,
            "target_project": self.target_project,
            "project_type": self.project_type,
            "project_context_status": self.project_context_status,
            "expected_outcome": self.expected_outcome,
            "required_postconditions": list(self.required_postconditions),
            "tests_to_run": [item.to_payload() for item in self.tests_to_run],
            "checks_to_run": [item.to_payload() for item in self.checks_to_run],
            "expected_artifacts": [item.to_payload() for item in self.expected_artifacts],
            "forbidden_side_effects": list(self.forbidden_side_effects),
            "forbidden_paths": list(self.forbidden_paths),
            "independent_readback": [item.to_payload() for item in self.independent_readback],
            "rollback_cleanup_expectations": list(self.rollback_cleanup_expectations),
            "evidence_requirements": list(self.evidence_requirements),
            "acceptance_criteria": list(self.acceptance_criteria),
            "verifier_capabilities": list(self.verifier_capabilities),
            "source_contract_refs": list(self.source_contract_refs),
            "context_gaps": list(self.context_gaps),
        }

    @property
    def contract_hash(self) -> str:
        return hashlib.sha256(
            canonical_json(self.canonical_payload()).encode("utf-8")
        ).hexdigest()

    @property
    def plan_id(self) -> str:
        return f"verification-plan:{self.contract_hash[:24]}"

    def to_payload(self) -> dict[str, Any]:
        return {
            **self.canonical_payload(),
            "plan_id": self.plan_id,
            "contract_hash": self.contract_hash,
        }

    def _require_ready(self) -> None:
        if not self.ready:
            raise VerificationPlanningError(
                "verification plan needs context before verifier dispatch: "
                + "; ".join(self.context_gaps))

    def to_verifier_input(self) -> dict[str, Any]:
        """Return the complete immutable input for an independent verifier."""
        self._require_ready()
        return {
            "schema": PLAN_SCHEMA,
            "plan_id": self.plan_id,
            "contract_hash": self.contract_hash,
            "candidate_ref": self.candidate_ref,
            "objective_ref": self.objective_ref,
            "target_project": self.target_project,
            "expected_outcome": self.expected_outcome,
            "verification_criteria": list(self.acceptance_criteria),
            "tests_to_run": [item.to_payload() for item in self.tests_to_run],
            "checks_to_run": [item.to_payload() for item in self.checks_to_run],
            "expected_artifacts": [item.to_payload() for item in self.expected_artifacts],
            "forbidden_paths": list(self.forbidden_paths),
            "forbidden_side_effects": list(self.forbidden_side_effects),
            "independent_readback": [item.to_payload() for item in self.independent_readback],
            "rollback_cleanup_expectations": list(self.rollback_cleanup_expectations),
            "evidence_requirements": list(self.evidence_requirements),
        }

    def mission_verifier_kwargs(self) -> dict[str, Any]:
        """Adapt to ``MissionLoop.run_step`` without changing its authority."""
        self._require_ready()
        return {
            "verification_criteria": list(self.acceptance_criteria),
            "expected_artifacts": tuple(
                item.path for item in self.expected_artifacts
                if item.state != "absent"
            ),
            "forbidden_paths": self.forbidden_paths,
        }

    def a3_plan_step_fields(self) -> dict[str, tuple[str, ...]]:
        """Fields A3 may place on the existing ``PlanStep`` before execution."""
        self._require_ready()
        outputs = _dedupe((
            self.expected_outcome,
            *self.required_postconditions,
            *(f"{item.path} is {item.state}" for item in self.expected_artifacts),
        ))
        return {
            "output_contract": outputs,
            "verification_contract": self.acceptance_criteria,
        }


def _structured_outcome(candidate: Mapping[str, Any]) -> dict[str, Any]:
    raw = candidate.get("structured_outcome") or candidate.get("outcome_contract")
    if raw is None and isinstance(candidate.get("expected_outcome"), Mapping):
        raw = candidate.get("expected_outcome")
    if raw is None and (
        "required_postconditions" in candidate or "postconditions" in candidate
    ):
        raw = {
            key: candidate[key]
            for key in (
                "expected_outcome", "summary", "task_kind", "project_type",
                "required_postconditions", "postconditions", "test_targets",
                "changed_paths", "expected_artifacts", "required_checks",
                "build_required", "build_justification",
                "forbidden_side_effects", "rollback_cleanup_expectations",
                "evidence_requirements",
            )
            if key in candidate
        }
    if not isinstance(raw, Mapping):
        raise VerificationPlanningError(
            "candidate requires a structured_outcome with observable postconditions; "
            "acceptance text or a success claim is insufficient")
    return dict(raw)


def _parse_postconditions(outcome: Mapping[str, Any]) -> tuple[dict[str, str], ...]:
    raw = outcome.get("required_postconditions", outcome.get("postconditions"))
    if not isinstance(raw, (list, tuple)) or not raw:
        raise VerificationPlanningError(
            "structured_outcome requires at least one observable postcondition")
    result: list[dict[str, str]] = []
    for index, item in enumerate(raw, 1):
        if not isinstance(item, Mapping):
            raise VerificationPlanningError(
                "candidate postconditions must be structured objects, not success text")
        kind = _text(item.get("kind"), f"postcondition {index} kind", limit=40).lower()
        if kind not in _POSTCONDITION_KINDS:
            raise VerificationPlanningError(f"unsupported postcondition kind {kind!r}")
        target = _text(item.get("target"), f"postcondition {index} target", limit=500)
        assertion = _text(item.get("assertion"), f"postcondition {index} assertion")
        evidence = _text(
            item.get("evidence") or item.get("evidence_source"),
            f"postcondition {index} evidence",
        )
        if assertion.lower().strip(" .!") in _VAGUE_ASSERTIONS:
            raise VerificationPlanningError(
                f"postcondition {index} is a success claim, not an observable condition")
        if evidence.lower().strip(" .!") in _UNTRUSTWORTHY_EVIDENCE:
            raise VerificationPlanningError(
                f"postcondition {index} relies on a builder/candidate claim")
        result.append({
            "kind": kind,
            "target": target,
            "assertion": assertion,
            "evidence": evidence,
        })
    return tuple(result)


def _postcondition_text(item: Mapping[str, str]) -> str:
    return (
        f"{item['kind']} readback for {item['target']}: {item['assertion']} "
        f"[evidence: {item['evidence']}]"
    )


def _artifacts(
    outcome: Mapping[str, Any],
    postconditions: Sequence[Mapping[str, str]],
) -> tuple[ArtifactExpectation, ...]:
    collected: dict[str, ArtifactExpectation] = {}

    def add(item: object, *, default_state: str) -> None:
        if isinstance(item, str):
            path = _relative(item, "expected artifact")
            kind = Path(path).suffix.lower().lstrip(".") or "file"
            state = default_state
            content: tuple[str, ...] = ()
        elif isinstance(item, Mapping):
            path = _relative(item.get("path"), "expected artifact path")
            kind = str(item.get("kind") or Path(path).suffix.lower().lstrip(".") or "file")
            state = str(item.get("state") or default_state)
            content = _string_tuple(item.get("content_requirements"), "artifact content requirements")
        else:
            raise VerificationPlanningError("expected artifacts must be paths or objects")
        matching = tuple(
            row["assertion"] for row in postconditions
            if row["kind"] in {"artifact", "content"}
            and row["target"].replace("\\", "/") == path
        )
        collected[path] = ArtifactExpectation(
            path=path,
            kind=kind,
            state=state,
            content_requirements=_dedupe((*content, *matching)),
        )

    raw_artifacts = outcome.get("expected_artifacts") or ()
    if not isinstance(raw_artifacts, (list, tuple)):
        raise VerificationPlanningError("expected_artifacts must be a list")
    for raw in raw_artifacts:
        add(raw, default_state="present")
    raw_changed = outcome.get("changed_paths") or ()
    if not isinstance(raw_changed, (list, tuple)):
        raise VerificationPlanningError("changed_paths must be a list")
    for raw in raw_changed:
        add(raw, default_state="changed")
    return tuple(collected[key] for key in sorted(collected))


def _knowledge_names(value: object) -> set[str]:
    if isinstance(value, Mapping):
        value = value.get("values") or ()
    if isinstance(value, str):
        return {value.lower()}
    if not isinstance(value, (list, tuple, set, frozenset)):
        return set()
    names: set[str] = set()
    for item in value:
        if isinstance(item, Mapping):
            item = item.get("name") or item.get("kind") or ""
        if str(item).strip():
            names.add(str(item).strip().lower())
    return names


def _profile_stack(profile: Mapping[str, Any]) -> Mapping[str, Any]:
    stack = profile.get("stack")
    return stack if isinstance(stack, Mapping) else {}


def _profile_command(
    profile: Mapping[str, Any],
    kind: str,
    *,
    relevant_paths: Sequence[str] = (),
    project_type: str = "unknown",
) -> tuple[tuple[str, ...], str, bool] | None:
    commands = profile.get("commands")
    raw: object | None = commands.get(kind) if isinstance(commands, Mapping) else None
    if raw is None:
        raw = profile.get(f"{kind}_command")
    if raw is None:
        return None

    if isinstance(raw, Mapping) and "items" in raw:
        if str(raw.get("status") or "UNKNOWN").upper() != "KNOWN":
            return None
        items = raw.get("items")
        if not isinstance(items, (list, tuple)) or not items:
            return None
        candidates: list[tuple[tuple[str, ...], str, bool]] = []
        for item in items:
            if not isinstance(item, Mapping) or not item.get("argv"):
                continue
            candidates.append((
                _command_argv(item.get("argv"), f"project {kind} command"),
                str(item.get("cwd") or "."),
                bool(item.get("targeted", False)),
            ))
        if not candidates:
            return None

        path_parts = tuple(
            path.partition("::")[0].replace("\\", "/")
            for path in relevant_paths
        )

        def rank(item: tuple[tuple[str, ...], str, bool]) -> tuple[int, int, int]:
            argv, cwd, _ = item
            normalized_cwd = cwd.replace("\\", "/").strip("/")
            if normalized_cwd in {"", "."}:
                path_score = 1 if path_parts else 0
            else:
                matches = sum(
                    path == normalized_cwd or path.startswith(normalized_cwd + "/")
                    for path in path_parts
                )
                path_score = matches * 100 + len(PurePosixPath(normalized_cwd).parts)
            lowered = {part.lower() for part in argv}
            if project_type == "python":
                affinity = int("pytest" in lowered or "python" in lowered)
            elif project_type in {"nextjs", "node"}:
                affinity = int(bool(lowered & {
                    "bun", "jest", "npm", "pnpm", "vitest", "yarn",
                }))
            else:
                affinity = 0
            # Shorter commands are the safer base when profile variants such
            # as fast/full share a runner; exact candidate targets are added.
            return path_score, affinity, -len(argv)

        ordered = sorted(
            candidates,
            key=lambda item: (rank(item), canonical_json({
                "argv": list(item[0]), "cwd": item[1], "targeted": item[2],
            })),
            reverse=True,
        )
        best = ordered[0]
        if len(ordered) > 1 and rank(best) == rank(ordered[1]) \
                and best != ordered[1]:
            return None
        return best

    cwd = "."
    targeted = False
    if isinstance(raw, Mapping):
        cwd = str(raw.get("cwd") or ".")
        targeted = bool(raw.get("targeted", False))
        raw = raw.get("argv")
    return _command_argv(raw, f"project {kind} command"), cwd, targeted


def _target_for_cwd(target: str, cwd: str) -> str:
    path, marker, node = target.partition("::")
    normalized_cwd = cwd.replace("\\", "/").strip("/")
    if normalized_cwd not in {"", "."} and path.startswith(normalized_cwd + "/"):
        path = path[len(normalized_cwd) + 1:]
    return path + (f"::{node}" if marker else "")


def _targeted_test_argv(
    argv: tuple[str, ...], targets: Sequence[str], cwd: str,
) -> tuple[str, ...]:
    scoped = tuple(_target_for_cwd(target, cwd) for target in targets)
    lowered = tuple(item.lower() for item in argv)
    if "pytest" in lowered:
        index = lowered.index("pytest")
        return (*argv[:index + 1], *scoped)
    for runner in ("vitest", "jest"):
        if runner in lowered:
            index = lowered.index(runner)
            end = index + 2 if len(argv) > index + 1 \
                and lowered[index + 1] == "run" else index + 1
            return (*argv[:end], *scoped)
    if argv[-1] == "--":
        return (*argv, *scoped)
    if lowered[0] in {"bun", "npm", "pnpm", "yarn"}:
        return (*argv, "--", *scoped)
    return (*argv, *scoped)


def _python_compile_command(
    test_command: tuple[tuple[str, ...], str, bool] | None,
    paths: Sequence[str],
) -> tuple[tuple[str, ...], str]:
    if test_command is None:
        return ("python", "-m", "py_compile", *paths), "."
    argv, cwd, _ = test_command
    scoped = tuple(_target_for_cwd(path, cwd) for path in paths)
    lowered = tuple(item.lower() for item in argv)
    if "pytest" in lowered:
        index = lowered.index("pytest")
        prefix = argv[:index]
        if len(prefix) >= 2 and tuple(item.lower() for item in prefix[-2:]) \
                == ("python", "-m"):
            return (*prefix, "py_compile", *scoped), cwd
        if prefix and prefix[-1].lower() == "run":
            return (*prefix, "python", "-m", "py_compile", *scoped), cwd
    return ("python", "-m", "py_compile", *scoped), cwd


def _project_type(profile: Mapping[str, Any], outcome: Mapping[str, Any]) -> str:
    task_kind = str(outcome.get("task_kind") or "").strip().lower()
    if task_kind in {"artifact", "document", "documentation"}:
        return "document"
    if task_kind in {"git", "version_control"}:
        return "git"
    candidate_type = str(outcome.get("project_type") or "").strip().lower()
    profile_type = str(
        profile.get("project_type") or profile.get("kind") or ""
    ).strip().lower()
    stack = _profile_stack(profile)
    classification = str(stack.get("classification") or "").strip().lower()
    languages = _knowledge_names(
        stack.get("languages") if stack else profile.get("languages"))
    frameworks = _knowledge_names(
        stack.get("frameworks") if stack else profile.get("frameworks"))
    manifest_values = profile.get("important_manifests") or profile.get("manifests") or ()
    manifests = {
        str(item.get("path") if isinstance(item, Mapping) else item).lower()
        for item in manifest_values
    } if isinstance(manifest_values, (list, tuple, set, frozenset)) else set()

    def node_type() -> str:
        return "nextjs" if any("next" in item for item in frameworks) else "node"

    # Supplied ProjectProfile context outranks a candidate's classification.
    # In a MIXED profile, candidate/component evidence may choose only a stack
    # that the profile actually observed.
    if profile_type:
        lowered = " ".join((profile_type, *frameworks, *languages, *manifests))
        if "next" in lowered:
            return "nextjs"
        if "python" in lowered:
            return "python"
        if profile_type in {"node", "javascript", "typescript"}:
            return node_type()
        return profile_type
    if classification == "python":
        return "python"
    if classification == "node":
        return node_type()
    if classification == "mixed":
        if candidate_type == "python" and "python" in languages:
            return "python"
        if candidate_type in {"next", "nextjs"} \
                and any("next" in item for item in frameworks):
            return "nextjs"
        if candidate_type in {"node", "javascript", "typescript"} \
                and languages & {"javascript", "typescript"}:
            return node_type()
        component_root = str(outcome.get("component_root") or "").strip("/\\")
        components = stack.get("components") or ()
        if component_root and isinstance(components, (list, tuple)):
            matches = [
                str(item.get("kind") or "").lower()
                for item in components if isinstance(item, Mapping)
                and str(item.get("root") or "").strip("/\\") == component_root
            ]
            if len(set(matches)) == 1:
                return "python" if matches[0] == "python" else node_type()
        return "mixed"
    if "python" in languages or any("pyproject.toml" in item for item in manifests):
        return "python"
    if languages & {"javascript", "typescript"}:
        return node_type()
    if candidate_type:
        return candidate_type
    return "unknown"


def _repository_value(profile: Mapping[str, Any], key: str) -> str:
    direct = profile.get(key)
    if direct:
        return str(direct).strip()
    state = profile.get("repository_state")
    if isinstance(state, Mapping) and state.get(key):
        return str(state[key]).strip()
    return ""


def _project_constraints(profile: Mapping[str, Any]) -> tuple[str, ...]:
    raw = profile.get("known_constraints") or ()
    if not isinstance(raw, (list, tuple)):
        return ()
    result: list[str] = []
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        text = str(item.get("text") or "").strip()
        if not text or not re.search(
            r"\b(never|must not|do not|forbidden|prohibited)\b", text, re.I,
        ):
            continue
        evidence = str(item.get("evidence") or "project profile").strip()
        result.append(
            f"project constraint: {_text(text, 'project constraint')} "
            f"[source: {_text(evidence, 'constraint evidence', limit=500)}]")
    return _dedupe(result)


def _contract_payloads(contracts: object | None) -> tuple[dict[str, Any], ...]:
    if contracts is None:
        return ()
    if isinstance(contracts, Mapping) or hasattr(contracts, "to_payload"):
        values = (contracts,)
    elif isinstance(contracts, Sequence) and not isinstance(contracts, (str, bytes)):
        values = tuple(contracts)
    else:
        values = (contracts,)
    return tuple(_payload(item, "mission/postcondition contract") for item in values)


def _contract_values(
    contracts: Sequence[Mapping[str, Any]],
) -> tuple[tuple[str, ...], tuple[ArtifactExpectation, ...], tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    criteria: list[str] = []
    artifacts: dict[str, ArtifactExpectation] = {}
    forbidden_paths: list[str] = []
    rollback: list[str] = []
    refs: list[str] = []
    for index, contract in enumerate(contracts, 1):
        ref = contract.get("contract_id") or contract.get("plan_id") \
            or contract.get("contract_hash") or f"contract:{index}"
        refs.append(_text(str(ref), "source contract ref", limit=300))
        for key in ("verification_criteria", "verification_contract", "postconditions"):
            raw = contract.get(key) or ()
            if isinstance(raw, (list, tuple)):
                for item in raw:
                    if isinstance(item, Mapping):
                        parsed = _parse_postconditions({"postconditions": [item]})[0]
                        criteria.append(_postcondition_text(parsed))
                    elif isinstance(item, str) and item.strip():
                        criteria.append(_text(item, f"{key} item"))
        raw_artifacts = contract.get("expected_artifacts") or ()
        if isinstance(raw_artifacts, (list, tuple)):
            for item in raw_artifacts:
                path = _relative(item, "contract expected artifact")
                artifacts[path] = ArtifactExpectation(
                    path, Path(path).suffix.lower().lstrip(".") or "file")
        for item in contract.get("forbidden_paths") or ():
            forbidden_paths.append(_relative(item, "contract forbidden path"))
        raw_rollback = contract.get("rollback")
        if isinstance(raw_rollback, Mapping) and raw_rollback:
            rollback.append(
                "Honor existing rollback contract: " + canonical_json(dict(raw_rollback)))
    return (
        _dedupe(criteria),
        tuple(artifacts[key] for key in sorted(artifacts)),
        _dedupe(forbidden_paths),
        _dedupe(rollback),
        _dedupe(refs),
    )


def _target_identity(target_project: object) -> tuple[str, dict[str, Any]]:
    if isinstance(target_project, (str, Path)):
        return _text(str(target_project), "target_project", limit=500), {}
    payload = _payload(target_project, "target_project")
    for key in ("root", "path", "project_id", "name"):
        if payload.get(key):
            return _text(str(payload[key]), "target_project", limit=500), payload
    raise VerificationPlanningError("target_project profile has no root, path, project_id, or name")


def _project_context_payload(value: object) -> dict[str, Any]:
    raw = _payload(value, "project_context")
    actionable = raw.get("actionable_context")
    if not isinstance(actionable, Mapping):
        return raw
    # ``ProjectProfileService.resolve_candidate_context`` intentionally wraps
    # the advisory profile.  Flatten only its machine-readable context and
    # preserve the hash reference; execution/admission claims stay ignored.
    result = dict(actionable)
    profile_ref = raw.get("profile_ref")
    if isinstance(profile_ref, Mapping):
        for key in ("profile_id", "profile_hash", "evidence_hash", "source_fingerprint"):
            if profile_ref.get(key):
                result[key] = profile_ref[key]
    candidate = raw.get("candidate")
    if isinstance(candidate, Mapping) and candidate.get("target_project"):
        result["project_id"] = candidate["target_project"]
    return result


def _verifier_capabilities(
    supplied: object | None,
    profile: Mapping[str, Any],
) -> tuple[str, ...]:
    if supplied is None:
        values: set[str] = set(CURRENT_VERIFIER_CAPABILITIES)
    elif isinstance(supplied, Mapping):
        values = {str(key) for key, enabled in supplied.items() if enabled}
    elif isinstance(supplied, Iterable) and not isinstance(supplied, (str, bytes)):
        values = {str(item) for item in supplied}
    else:
        raise VerificationPlanningError("verifier_capabilities must be a mapping or list")
    advertised = profile.get("verifier_capabilities") or ()
    if isinstance(advertised, (list, tuple, set, frozenset)):
        values.update(str(item) for item in advertised)
    return tuple(sorted(_text(item, "verifier capability", limit=120) for item in values))


def compile_verification_plan(
    candidate: object,
    target_project: object,
    project_context: object | None = None,
    *,
    mission_contracts: object | None = None,
    verifier_capabilities: object | None = None,
) -> VerificationPlan:
    """Compile the minimum honest plan A3 can bind before mission execution.

    The function is deterministic for identical inputs and deliberately pure.
    A malformed candidate raises ``VerificationPlanningError``.  A valid
    candidate with insufficient project/verifier context returns a
    ``NEEDS_CONTEXT`` plan whose verifier adapters refuse dispatch.
    """
    candidate_data = _payload(candidate, "candidate")
    candidate_ref = _text(
        candidate_data.get("candidate_id") or candidate_data.get("id"),
        "candidate reference",
        limit=300,
    )
    objective_ref_raw = candidate_data.get("objective_id")
    objective_ref = (
        _text(str(objective_ref_raw), "objective reference", limit=300)
        if objective_ref_raw else None
    )
    objective = _text(
        candidate_data.get("objective") or candidate_data.get("problem_statement"),
        "candidate objective",
    )
    outcome = _structured_outcome(candidate_data)
    expected = _text(
        outcome.get("summary")
        or outcome.get("expected_outcome")
        or (candidate_data.get("expected_outcome")
            if isinstance(candidate_data.get("expected_outcome"), str) else None),
        "structured outcome summary",
    )
    postconditions = _parse_postconditions(outcome)
    candidate_postconditions = tuple(_postcondition_text(item) for item in postconditions)

    target_identity, target_profile = _target_identity(target_project)
    if project_context is None:
        profile = target_profile
    else:
        profile = _project_context_payload(project_context)
    context_status = "AVAILABLE" if profile else "MISSING"
    project_type = _project_type(profile, outcome)
    capabilities = _verifier_capabilities(verifier_capabilities, profile)
    capability_set = set(capabilities)
    contracts = _contract_payloads(mission_contracts)
    (
        contract_criteria,
        contract_artifacts,
        contract_forbidden_paths,
        contract_rollback,
        contract_refs,
    ) = _contract_values(contracts)

    artifact_map = {item.path: item for item in _artifacts(outcome, postconditions)}
    for item in contract_artifacts:
        artifact_map.setdefault(item.path, item)
    artifacts = tuple(artifact_map[key] for key in sorted(artifact_map))

    gaps: list[str] = []
    if not profile:
        gaps.append("ProjectProfile/project context is missing; project-specific commands were not inferred")
    if project_type == "unknown":
        gaps.append("project type is unknown")
    if project_type == "mixed":
        gaps.append(
            "mixed ProjectProfile requires an observed component_root or matching project_type")

    raw_targets = outcome.get("test_targets") or ()
    if not isinstance(raw_targets, (list, tuple)):
        raise VerificationPlanningError("test_targets must be a list")
    test_targets = tuple(
        _relative(item, "test target", allow_node_id=True) for item in raw_targets)
    changed_paths = tuple(
        item.path for item in artifacts
        if item.state in {"changed", "created"}
    )
    required_checks = set(
        item.lower() for item in _string_tuple(
            outcome.get("required_checks"), "required_checks"))
    required_checks.update(
        item.lower() for item in _string_tuple(
            profile.get("required_checks"), "project required_checks"))
    unsupported_checks = required_checks - {
        "build", "compile", "lint", "test", "typecheck",
    }
    gaps.extend(
        f"required check kind is unsupported: {item}"
        for item in sorted(unsupported_checks)
    )

    tests: list[VerificationCommand] = []
    checks: list[VerificationCommand] = []
    relevant_paths = (*test_targets, *changed_paths)
    test_command = _profile_command(
        profile,
        "test",
        relevant_paths=relevant_paths,
        project_type=project_type,
    ) if profile else None
    code_project = project_type in {"node", "python", "nextjs"}
    if test_targets:
        if test_command is None:
            gaps.append("targeted test runner is unavailable in project context")
        else:
            argv, cwd, already_targeted = test_command
            if already_targeted:
                gaps.append(
                    "project test command is already targeted but candidate also supplies test_targets")
            else:
                tests.append(VerificationCommand(
                    "targeted-tests",
                    "test",
                    _targeted_test_argv(argv, test_targets, cwd),
                    cwd,
                    f"targeted behavior for {objective}",
                ))
    elif code_project:
        if test_command is not None and test_command[2]:
            argv, cwd, _ = test_command
            tests.append(VerificationCommand(
                "targeted-tests", "test", argv, cwd,
                f"targeted behavior for {objective}",
            ))
        else:
            gaps.append("code outcome has no exact targeted test target")

    behavior_present = any(item["kind"] == "behavior" for item in postconditions)
    if code_project and not behavior_present:
        gaps.append("code outcome lacks a behavioral postcondition")

    if project_type == "python":
        python_paths = tuple(path for path in changed_paths if path.lower().endswith(".py"))
        if python_paths:
            compile_argv, compile_cwd = _python_compile_command(
                test_command, python_paths)
            checks.append(VerificationCommand(
                "python-compile",
                "compile",
                compile_argv,
                compile_cwd,
                "changed Python modules parse and compile",
            ))
        else:
            gaps.append("Python outcome has no exact changed .py path for compile proof")

    stack_languages = _knowledge_names(_profile_stack(profile).get("languages"))
    typescript_change = any(
        path.lower().endswith((".ts", ".tsx")) for path in changed_paths)
    if project_type in {"nextjs", "node"} \
            and ("typescript" in stack_languages or typescript_change):
        required_checks.add("typecheck")

    build_required = bool(outcome.get("build_required")) or "build" in required_checks
    for kind in ("lint", "typecheck", "build"):
        wanted = kind in required_checks or (kind == "build" and build_required)
        if not wanted:
            continue
        command = _profile_command(
            profile,
            kind,
            relevant_paths=relevant_paths,
            project_type=project_type,
        ) if profile else None
        if command is None:
            gaps.append(f"required {kind} command is unavailable in project context")
            continue
        argv, cwd, _ = command
        if kind == "build":
            justification = outcome.get("build_justification")
            if not justification:
                gaps.append("build check is requested without a build justification")
                continue
            proves = "build integration: " + _text(
                justification, "build justification")
        else:
            proves = f"{project_type} {kind} contract for changed sources"
        checks.append(VerificationCommand(
            f"project-{kind}", kind, argv, cwd, proves))

    base_sha = _repository_value(profile, "base_sha") \
        or _repository_value(profile, "head")
    vcs = str(profile.get("vcs") or "").strip().lower()
    if isinstance(profile.get("repository_state"), Mapping):
        vcs = vcs or "git"
    git_context = project_type == "git" or vcs == "git" or bool(base_sha)
    if project_type == "git":
        if not base_sha:
            gaps.append("Git task requires an exact base_sha in project context")
        else:
            checks.append(VerificationCommand(
                "git-diff-check",
                "git",
                ("git", "diff", "--check", f"{base_sha}...HEAD"),
                ".",
                "the candidate diff has no whitespace errors",
            ))

    forbidden_paths = list(contract_forbidden_paths)
    for key in ("forbidden_paths", "protected_paths"):
        raw = profile.get(key) or ()
        if not isinstance(raw, (list, tuple)):
            raise VerificationPlanningError(f"project {key} must be a list")
        forbidden_paths.extend(_relative(item, f"project {key} item") for item in raw)
    forbidden_paths_tuple = _dedupe(forbidden_paths)
    conflicting_paths = sorted(
        path for path in artifact_map
        if any(
            path == forbidden or path.startswith(forbidden.rstrip("/") + "/")
            for forbidden in forbidden_paths_tuple
        )
    )
    if conflicting_paths:
        raise VerificationPlanningError(
            "expected artifact conflicts with forbidden path: "
            + ", ".join(conflicting_paths))

    forbidden = [
        "no changes outside the registered target project/worktree",
        "no merge, push, deploy, release-pointer, live-runtime, Scheduler, or production-database mutation",
    ]
    forbidden.extend(_string_tuple(
        outcome.get("forbidden_side_effects"), "candidate forbidden_side_effects"))
    forbidden.extend(_string_tuple(
        profile.get("forbidden_side_effects"), "project forbidden_side_effects"))
    forbidden.extend(_project_constraints(profile))
    forbidden.extend(f"no change to forbidden path {path}" for path in forbidden_paths_tuple)
    forbidden_tuple = _dedupe(forbidden)

    rollback = list(_string_tuple(
        outcome.get("rollback_cleanup_expectations"),
        "candidate rollback_cleanup_expectations",
    ))
    rollback.extend(_string_tuple(
        profile.get("rollback_cleanup_expectations"),
        "project rollback_cleanup_expectations",
    ))
    rollback.extend(contract_rollback)
    if project_type in {"git", "nextjs", "node", "python"}:
        rollback.append(
            "revert only candidate-worktree changes and preserve the source checkout and unrelated work")
    else:
        rollback.append(
            "remove or restore only declared artifact paths and leave no temporary output outside the target")
    rollback_tuple = _dedupe(rollback)

    required_postconditions = _dedupe((*candidate_postconditions, *contract_criteria))
    readbacks: list[ReadbackRequirement] = [ReadbackRequirement(
        "postconditions",
        "criteria",
        target_identity,
        required_postconditions,
        "criteria_evaluation",
    )]
    if tests or checks:
        readbacks.append(ReadbackRequirement(
            "command-evidence",
            "command_evidence",
            target_identity,
            tuple(
                f"{item.check_id} receipt records exact argv/cwd, exit code 0, and an output digest"
                for item in (*tests, *checks)
            ),
            "command_evidence_review",
        ))
    if artifacts:
        readbacks.append(ReadbackRequirement(
            "artifact-state",
            "artifact_hash",
            target_identity,
            tuple(f"{item.path} independently reads back as {item.state}" for item in artifacts),
            "artifact_hash_readback",
        ))
    for artifact in artifacts:
        suffix = Path(artifact.path).suffix.lower()
        if suffix not in _DOCUMENT_EXTENSIONS or not artifact.content_requirements:
            continue
        capability = (
            "structured_document_readback"
            if suffix in _STRUCTURED_DOCUMENT_EXTENSIONS
            else "file_content_readback"
        )
        readbacks.append(ReadbackRequirement(
            f"content:{artifact.path}",
            "content",
            artifact.path,
            artifact.content_requirements,
            capability,
        ))
    if git_context:
        assertions = [
            "independent Git diff paths and states match the declared candidate scope",
            "independent HEAD and working-tree status are captured after verification",
        ]
        if base_sha:
            assertions.insert(0, f"HEAD is read back against exact base {base_sha}")
        branch = _repository_value(profile, "branch")
        if branch:
            assertions.append(f"checked-out branch reads back as {branch}")
        readbacks.append(ReadbackRequirement(
            "git-truth", "git", target_identity, tuple(assertions),
            "git_diff_readback",
        ))

    # Document success must be semantic. Presence/hash alone cannot close it.
    if project_type == "document":
        document_artifacts = [
            item for item in artifacts
            if Path(item.path).suffix.lower() in _DOCUMENT_EXTENSIONS
            and item.state != "absent"
        ]
        if not document_artifacts:
            gaps.append("document outcome has no exact expected document artifact")
        for item in document_artifacts:
            if not item.content_requirements:
                gaps.append(
                    f"document artifact {item.path} lacks a semantic content postcondition")

    for readback in readbacks:
        if readback.verifier_capability not in capability_set:
            gaps.append(
                f"verifier capability unavailable: {readback.verifier_capability} "
                f"(required by {readback.readback_id})")
    for required in (
        "criteria_evaluation", "independent_session", "expected_artifacts",
        "forbidden_paths",
    ):
        if required not in capability_set:
            gaps.append(f"existing verifier contract capability unavailable: {required}")

    evidence = list(_string_tuple(
        outcome.get("evidence_requirements"), "candidate evidence_requirements"))
    if tests or checks:
        evidence.append(
            "command receipts contain exact argv, cwd, exit code, and output digest for every required check")
    if artifacts:
        evidence.append(
            "artifact evidence contains observed state and independent content/hash readback as applicable")
    if git_context:
        evidence.append("Git evidence contains base, HEAD, branch, status, and diff-path readback")
    evidence.append(
        "independent verifier receipt is bound to the plan id, candidate "
        "reference, target, artifact hashes, and diff hash")
    evidence_tuple = _dedupe(evidence)

    acceptance: list[str] = list(required_postconditions)
    acceptance.extend(
        f"{item.check_id} exits 0 and its receipt proves: {item.proves}"
        for item in (*tests, *checks)
        if item.required
    )
    acceptance.extend(
        assertion
        for readback in readbacks
        for assertion in readback.assertions
    )
    acceptance.extend(
        f"independent scope readback confirms {item}" for item in forbidden_tuple)
    acceptance.append(
        "all required evidence is present and the authoritative "
        "MissionLoop/AcceptanceGate accepts the independent verdict")

    return VerificationPlan(
        candidate_ref=candidate_ref,
        objective_ref=objective_ref,
        objective=objective,
        target_project=target_identity,
        project_type=project_type,
        project_context_status=context_status,
        expected_outcome=expected,
        required_postconditions=required_postconditions,
        tests_to_run=tuple(tests),
        checks_to_run=tuple(checks),
        expected_artifacts=artifacts,
        forbidden_side_effects=forbidden_tuple,
        forbidden_paths=forbidden_paths_tuple,
        independent_readback=tuple(readbacks),
        rollback_cleanup_expectations=rollback_tuple,
        evidence_requirements=evidence_tuple,
        acceptance_criteria=_dedupe(acceptance),
        verifier_capabilities=capabilities,
        source_contract_refs=_dedupe((
            *contract_refs,
            *(
                (f"project-profile:{profile.get('profile_id') or 'unknown'}:"
                 f"{profile['profile_hash']}",)
                if profile.get("profile_hash") else ()
            ),
        )),
        context_gaps=_dedupe(gaps),
    )


__all__ = [
    "ArtifactExpectation",
    "CURRENT_VERIFIER_CAPABILITIES",
    "NEEDS_CONTEXT",
    "PLAN_SCHEMA",
    "READY",
    "ReadbackRequirement",
    "VerificationCommand",
    "VerificationPlan",
    "VerificationPlanningError",
    "compile_verification_plan",
]
