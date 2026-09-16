"""Built-in V14 product recipes (data, not orchestrators)."""
from __future__ import annotations

from typing import Any, Mapping

from .model import recipe_from_mapping


def _payload(value: Mapping[str, Any]) -> dict[str, Any]:
    return dict(value)


BUILTIN_RECIPE_MAPPINGS: tuple[dict[str, Any], ...] = (
    {
        "recipe_id": "v14.research_to_artifact",
        "version": "1.0.0",
        "description": "Read one bounded web source, compose a local markdown artifact, verify by readback.",
        "required_capabilities": ("browser.web.read", "file.write", "file.read"),
        "parameters": {
            "source_url": {"required": True, "description": "Bounded HTTPS source to read."},
            "artifact_target": {"default": "research/artifact.md", "description": "Owned-root markdown target."},
            "artifact_title": {"default": "Research artifact", "description": "Artifact heading."},
        },
        "steps": (
            {
                "step_id": "fetch",
                "action_kind": "browser.web.read",
                "payload": {"url": "$params.source_url"},
            },
            {
                "step_id": "compose",
                "action_kind": "file.write",
                "payload": {
                    "target": "$params.artifact_target",
                    "content": "# ${params.artifact_title}\n\nSource: ${fetch.final_url}\nSource SHA: ${fetch.source_sha256}\nDocument: ${fetch.document_id}\n",
                },
            },
            {
                "step_id": "verify",
                "action_kind": "file.read",
                "payload": {"target": "$compose.target"},
            },
        ),
        "outputs": {
            "artifact_target": "$compose.target",
            "artifact_hash": "$verify.content_hash",
            "source_url": "$fetch.final_url",
        },
        "tags": ("v14", "research", "artifact"),
    },
    {
        "recipe_id": "v14.open_and_verify",
        "version": "1.0.0",
        "description": "Inspect a local artifact, open it natively on the desktop, assert visible content.",
        "required_capabilities": ("file.inspect", "desktop.open", "desktop.control.assert"),
        "parameters": {
            "artifact_target": {"required": True, "description": "Owned-root artifact to open."},
            "process_id": {"required": True, "description": "Stable logical process id for the open."},
            "application": {"default": "notepad.exe", "description": "Native viewer executable."},
            "idempotency_key": {"default": "v14-open-default", "description": "Stable open idempotency key."},
            "expected_text": {"default": "", "description": "Text the assert step must observe."},
        },
        "steps": (
            {
                "step_id": "inspect",
                "action_kind": "file.inspect",
                "payload": {"target": "$params.artifact_target"},
            },
            {
                "step_id": "open",
                "action_kind": "desktop.open",
                "payload": {
                    "process_id": "$params.process_id",
                    "application": "$params.application",
                    "target": "$params.artifact_target",
                    "idempotency_key": "$params.idempotency_key",
                },
            },
            {
                "step_id": "assert",
                "action_kind": "desktop.control.assert",
                "payload": {
                    "window": {"process_id": "$open.process_id"},
                    "value": "$params.expected_text",
                },
            },
        ),
        "outputs": {
            "process_id": "$open.process_id",
            "artifact_target": "$params.artifact_target",
        },
        "tags": ("v14", "desktop", "verify"),
    },
    {
        "recipe_id": "v14.publish_verified_work",
        "version": "1.0.0",
        "description": "Verify local repo state then publish via local git push (disposable remote only).",
        "required_capabilities": ("git.inspect", "git.log", "git.push"),
        "parameters": {
            "worktree": {"required": True, "description": "Owned worktree path."},
            "remote": {"default": "origin", "description": "Disposable remote name."},
            "branch": {"default": "", "description": "Branch to push; empty lets the connector decide."},
        },
        "steps": (
            {
                "step_id": "inspect",
                "action_kind": "git.inspect",
                "payload": {"worktree": "$params.worktree"},
            },
            {
                "step_id": "log",
                "action_kind": "git.log",
                "payload": {"worktree": "$inspect.worktree"},
            },
            {
                "step_id": "push",
                "action_kind": "git.push",
                "payload": {
                    "worktree": "$log.worktree",
                    "remote": "$params.remote",
                    "branch": "$params.branch",
                },
            },
        ),
        "outputs": {
            "head": "$log.head",
            "remote_head": "$push.remote_head",
        },
        "tags": ("v14", "git", "publish"),
    },
    {
        "recipe_id": "v14.digital_operator.close",
        "version": "1.0.0",
        "description": "Close one owner objective: read, compose artifact, verify natively, publish.",
        "required_capabilities": (
            "browser.web.read",
            "file.write",
            "file.read",
            "desktop.open_artifact_and_assert",
            "git.inspect",
            "git.commit",
            "git.log",
            "git.push",
        ),
        "parameters": {
            "source_url": {"required": True, "description": "Bounded source to research."},
            "artifact_target": {"default": "research/close.md", "description": "Owned-root artifact target."},
            "artifact_title": {"default": "Operator close-out", "description": "Artifact heading."},
            "process_id": {"required": True, "description": "Stable logical process id for native open."},
            "application": {"default": "notepad.exe", "description": "Native viewer executable."},
            "idempotency_key": {"default": "v14-close-default", "description": "Stable open idempotency key."},
            "expected_text": {"default": "", "description": "Text the assert step must observe."},
            "worktree": {"required": True, "description": "Owned worktree to publish from."},
            "remote": {"default": "origin", "description": "Disposable remote name."},
            "branch": {"default": "main", "description": "Disposable remote branch."},
            "commit_message": {"default": "Preserve verified operator artifact", "description": "Local commit message."},
        },
        "steps": (
            {
                "step_id": "fetch",
                "action_kind": "browser.web.read",
                "payload": {"url": "$params.source_url"},
            },
            {
                "step_id": "compose",
                "action_kind": "file.write",
                "payload": {
                    "target": "$params.artifact_target",
                    "content": "# ${params.artifact_title}\n\nSource: ${fetch.final_url}\nSource SHA: ${fetch.source_sha256}\n",
                },
            },
            {
                "step_id": "readback",
                "action_kind": "file.read",
                "payload": {"target": "$compose.target"},
            },
            {
                "step_id": "open",
                "action_kind": "desktop.open_artifact_and_assert",
                "payload": {
                    "process_id": "$params.process_id",
                    "application": "$params.application",
                    "target": "$readback.target",
                    "expected": "$readback.text",
                    "idempotency_key": "$params.idempotency_key",
                },
            },
            {
                "step_id": "inspect",
                "action_kind": "git.inspect",
                "payload": {
                    "worktree": "$params.worktree",
                },
            },
            {
                "step_id": "commit",
                "action_kind": "git.commit",
                "payload": {
                    "worktree": "$inspect.worktree",
                    "message": "$params.commit_message",
                    "existing": True,
                    "paths": ["$compose.target"],
                },
            },
            {
                "step_id": "log",
                "action_kind": "git.log",
                "payload": {"worktree": "$inspect.worktree", "max_count": 1},
            },
            {
                "step_id": "push",
                "action_kind": "git.push",
                "payload": {
                    "worktree": "$inspect.worktree",
                    "remote": "$params.remote",
                    "branch": "$params.branch",
                    "action_id": "${params.idempotency_key}:push",
                },
            },
        ),
        "outputs": {
            "artifact_target": "$compose.target",
            "artifact_hash": "$readback.content_hash",
            "native_readback_verified": "$open.readback_verified",
            "commit_head": "$log.head",
            "remote_head": "$push.remote_head",
        },
        "tags": ("v14", "digital-operator", "close"),
    },
    {
        "recipe_id": "v14.research_to_docx",
        "version": "1.0.0",
        "description": "Compose a DOCX from researched source notes and verify by reopen.",
        "required_capabilities": ("artifact.docx.compose", "artifact.docx.edit"),
        "parameters": {
            "doc_title": {"default": "Research document", "description": "DOCX title."},
            "doc_target": {"default": "research/report.docx", "description": "Owned-root DOCX target."},
            "idempotency_key": {"default": "v14-docx-default", "description": "Stable DOCX idempotency key."},
            "heading": {"default": "Findings", "description": "First section heading."},
            "body": {"default": "TBD", "description": "First section body text."},
        },
        "steps": (
            {
                "step_id": "compose",
                "action_kind": "artifact.docx.compose",
                "payload": {
                    "title": "$params.doc_title",
                    "sections": [{"heading": "$params.heading", "paragraphs": ["$params.body"]}],
                    "target": "$params.doc_target",
                    "idempotency_key": "$params.idempotency_key",
                },
            },
            {
                "step_id": "verify",
                "action_kind": "file.inspect",
                "payload": {"target": "$compose.target_ref"},
            },
        ),
        "outputs": {
            "doc_target": "$compose.target_ref",
            "output_hash": "$compose.output_sha256",
        },
        "tags": ("v14", "general-work", "docx"),
    },
    {
        "recipe_id": "v14.research_to_xlsx",
        "version": "1.0.0",
        "description": "Build an XLSX from a tabular source and verify by readback.",
        "required_capabilities": ("artifact.table.transform", "artifact.xlsx.create"),
        "parameters": {
            "source": {"required": True, "description": "Owned tabular source ref."},
            "sheet_name": {"default": "Data", "description": "Workbook sheet name."},
            "xlsx_target": {"default": "research/table.xlsx", "description": "Owned-root XLSX target."},
            "idempotency_key": {"default": "v14-xlsx-default", "description": "Stable XLSX idempotency key."},
        },
        "steps": (
            {
                "step_id": "transform",
                "action_kind": "artifact.table.transform",
                "payload": {"source": "$params.source"},
            },
            {
                "step_id": "build",
                "action_kind": "artifact.xlsx.create",
                "payload": {
                    "source": "$transform.source_ref",
                    "target": "$params.xlsx_target",
                    "idempotency_key": "$params.idempotency_key",
                    "sheet_name": "$params.sheet_name",
                },
            },
            {
                "step_id": "verify",
                "action_kind": "file.inspect",
                "payload": {"target": "$build.target_ref"},
            },
        ),
        "outputs": {
            "xlsx_target": "$build.target_ref",
            "output_hash": "$build.output_sha256",
        },
        "tags": ("v14", "general-work", "xlsx"),
    },
    {
        "recipe_id": "v14.artifact_to_local_repo",
        "version": "1.0.0",
        "description": "Copy a verified artifact into an owned repo worktree and inspect the result.",
        "required_capabilities": ("file.inspect", "file.copy", "git.inspect"),
        "parameters": {
            "artifact_source": {"required": True, "description": "Owned-root artifact source."},
            "repo_target": {"required": True, "description": "Destination inside the owned worktree."},
            "worktree": {"required": True, "description": "Owned worktree path."},
        },
        "steps": (
            {
                "step_id": "inspect",
                "action_kind": "file.inspect",
                "payload": {"target": "$params.artifact_source"},
            },
            {
                "step_id": "stage",
                "action_kind": "file.copy",
                "payload": {"source": "$params.artifact_source", "target": "$params.repo_target"},
            },
            {
                "step_id": "inspect_repo",
                "action_kind": "git.inspect",
                "payload": {"worktree": "$params.worktree"},
            },
        ),
        "outputs": {
            "staged_target": "$stage.target",
            "head": "$inspect_repo.head",
        },
        "tags": ("v14", "artifact", "repo"),
    },
)


def builtin_recipes() -> tuple[Any, ...]:
    return tuple(recipe_from_mapping(dict(item)) for item in BUILTIN_RECIPE_MAPPINGS)
