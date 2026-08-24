"""Disposable deployment slot using immutable artifacts and CURRENT/PREVIOUS.

The adapter reuses the canonical release pointer helpers but keeps all
artifacts under an explicitly disposable root.  It is a lab deployment seam,
not permission to mutate the installed JARVIS release or production slot.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ..deployment.release import read_pointer, write_pointer
from .procedure import OperationError


class DeploymentError(OperationError):
    pass


def _identity(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DeploymentError(f"{field_name} must be non-empty")
    result = value.strip()
    if any(char in result for char in ("/", "\\", "..", "*", "${", "%")):
        raise DeploymentError(f"{field_name} must be an exact safe identity")
    return result


def _disposable(root: Path) -> None:
    if not any(part.lower() in {"temp", "tmp", "disposable", "sandbox", "lab"}
               for part in root.parts):
        raise DeploymentError(f"refusing non-disposable deployment root {root}")


@dataclass(frozen=True, slots=True)
class ArtifactIdentity:
    artifact_id: str
    content_hash: str
    source_ref: str
    manifest_path: str


@dataclass(frozen=True, slots=True)
class DeploymentReceipt:
    artifact_id: str
    previous: str | None
    current: str
    health: bool
    identity_hash: str


class DisposableDeploymentSlot:
    """Build, stage, health-check, switch, reconcile, and roll back locally."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()
        _disposable(self.root)
        self.artifacts = self.root / "artifacts"
        self.staging = self.root / "staging"
        self.current_pointer = self.root / "CURRENT"
        self.previous_pointer = self.root / "PREVIOUS"
        self.artifacts.mkdir(parents=True, exist_ok=True)
        self.staging.mkdir(parents=True, exist_ok=True)
        self._health: dict[str, bool] = {}

    def build(self, source_dir: str | Path, *, artifact_id: str, source_ref: str) -> ArtifactIdentity:
        artifact_id = _identity(artifact_id, "artifact_id")
        source_ref = _identity(source_ref, "source_ref")
        source = Path(source_dir).expanduser().resolve()
        if not source.is_dir():
            raise DeploymentError(f"deployment source is not a directory: {source}")
        destination = self.artifacts / artifact_id
        if destination.exists():
            raise DeploymentError(f"artifact already exists: {artifact_id}")
        destination.mkdir(parents=True)
        digest = hashlib.sha256()
        for path in sorted(item for item in source.rglob("*") if item.is_file()):
            relative = path.relative_to(source).as_posix()
            digest.update(relative.encode("utf-8"))
            content = path.read_bytes()
            digest.update(content)
            output = destination / relative
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(content)
        content_hash = digest.hexdigest()
        manifest = {
            "artifact_id": artifact_id,
            "content_hash": content_hash,
            "source_ref": source_ref,
        }
        manifest_path = destination / "ARTIFACT.json"
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        self._health[artifact_id] = True
        return ArtifactIdentity(
            artifact_id=artifact_id, content_hash=content_hash,
            source_ref=source_ref, manifest_path=str(manifest_path),
        )

    def stage(self, artifact: ArtifactIdentity) -> ArtifactIdentity:
        manifest_path = self.artifacts / artifact.artifact_id / "ARTIFACT.json"
        if not manifest_path.is_file():
            raise DeploymentError(f"artifact manifest missing: {artifact.artifact_id}")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("content_hash") != artifact.content_hash:
            raise DeploymentError("artifact identity hash mismatch during stage")
        staged_manifest = self.staging / f"{artifact.artifact_id}.json"
        staged_manifest.write_text(
            json.dumps({"artifact": artifact.artifact_id, "content_hash": artifact.content_hash},
                       sort_keys=True) + "\n", encoding="utf-8")
        return artifact

    def health_check(
        self,
        artifact: ArtifactIdentity,
        oracle: Callable[[Path], bool] | None = None,
    ) -> bool:
        directory = self.artifacts / artifact.artifact_id
        if not directory.is_dir():
            self._health[artifact.artifact_id] = False
            return False
        result = bool(oracle(directory)) if oracle is not None else self._health.get(artifact.artifact_id, True)
        self._health[artifact.artifact_id] = result
        return result

    def set_health(self, artifact_id: str, healthy: bool) -> None:
        self._health[_identity(artifact_id, "artifact_id")] = bool(healthy)

    def switch(self, artifact: ArtifactIdentity) -> DeploymentReceipt:
        staged = self.staging / f"{artifact.artifact_id}.json"
        if not staged.is_file():
            raise DeploymentError("artifact must be staged before switch")
        if not self.health_check(artifact):
            raise DeploymentError(f"health check failed for {artifact.artifact_id}")
        previous = read_pointer(self.current_pointer)
        if previous != artifact.artifact_id and previous:
            write_pointer(self.previous_pointer, previous)
        write_pointer(self.current_pointer, artifact.artifact_id)
        return DeploymentReceipt(
            artifact_id=artifact.artifact_id, previous=previous,
            current=artifact.artifact_id, health=True,
            identity_hash=artifact.content_hash,
        )

    def rollback(self) -> DeploymentReceipt:
        previous = read_pointer(self.previous_pointer)
        current = read_pointer(self.current_pointer)
        if not previous:
            raise DeploymentError("no PREVIOUS artifact exists for rollback")
        if not (self.artifacts / previous).is_dir():
            raise DeploymentError(f"PREVIOUS artifact missing: {previous}")
        write_pointer(self.current_pointer, previous)
        if current:
            write_pointer(self.previous_pointer, current)
        manifest = json.loads((self.artifacts / previous / "ARTIFACT.json").read_text(encoding="utf-8"))
        return DeploymentReceipt(
            artifact_id=previous, previous=current, current=previous,
            health=self._health.get(previous, False),
            identity_hash=str(manifest["content_hash"]),
        )

    def reconcile(self) -> dict[str, Any]:
        current = read_pointer(self.current_pointer)
        previous = read_pointer(self.previous_pointer)
        errors: list[str] = []
        for label, artifact_id in (("CURRENT", current), ("PREVIOUS", previous)):
            if artifact_id and not (self.artifacts / artifact_id / "ARTIFACT.json").is_file():
                errors.append(f"{label} artifact missing: {artifact_id}")
        return {
            "current": current,
            "previous": previous,
            "consistent": not errors,
            "errors": tuple(errors),
        }


__all__ = ["ArtifactIdentity", "DeploymentError", "DeploymentReceipt", "DisposableDeploymentSlot"]
