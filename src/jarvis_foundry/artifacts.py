"""Atomic evidence artifact hashing and storage.

Evidence artifacts are stored below a project-local base directory (the
Foundry ``var/evidence`` root in project staging) in per-bundle subdirectories:

    <base>/<bundle_id>/<name>

Every artifact is written atomically (temp file in the same directory,
fsync, ``os.replace``) so a crash or concurrent reader can never observe a
partial artifact. A ``<name>.sha256`` sidecar records the digest of the exact
bytes stored, and ``verify_artifact`` re-hashes the stored file and compares
it against the expected digest. Artifact names are validated to keep every
target explicit and inside the bundle directory — no separators, no traversal,
no drive letters.
"""

from __future__ import annotations

import hashlib
import os
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class UnsafeArtifactName(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ArtifactRecord:
    bundle_id: str
    name: str
    path: Path
    sha256: str
    size_bytes: int
    written_at: str


def validate_artifact_name(name: str) -> str:
    """Validate an artifact file name.

    Rejects empty names, path separators, traversal (``..``), drive letters,
    and names that do not fit a conservative plain-file pattern.
    """
    if not name or not _SAFE_NAME.match(name):
        raise UnsafeArtifactName(f"unsafe artifact name: {name!r}")
    if name in {".", ".."}:
        raise UnsafeArtifactName(f"unsafe artifact name: {name!r}")
    return name


def _validate_bundle_id(bundle_id: str) -> None:
    """Validate a bundle identifier.

    Bundle ids use the same conservative plain-file rule as artifact names so
    the bundle directory can never escape ``base_dir`` through separators,
    traversal components, drive letters, or the special ``.``/``..`` names.
    """
    validate_artifact_name(bundle_id)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_write(target: Path, data: bytes) -> None:
    """Write ``data`` to ``target`` atomically.

    Writes to a unique temporary file in the same directory, fsyncs it, then
    ``os.replace``s it over the target. Readers and crash recovery never see a
    partial file.
    """
    temporary = target.parent / f".{target.name}.{uuid.uuid4().hex}.tmp"
    try:
        with open(temporary, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    except BaseException:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise


def store_artifact(base_dir: str | Path, bundle_id: str, name: str, data: bytes) -> ArtifactRecord:
    """Store one evidence artifact below ``base_dir/<bundle_id>/``.

    Writes the artifact atomically and a ``<name>.sha256`` sidecar (also
    atomic) recording the digest of the stored bytes. Returns the record with
    path, digest, size, and write timestamp.
    """
    validate_artifact_name(name)
    _validate_bundle_id(bundle_id)
    bundle_dir = Path(base_dir).expanduser().resolve() / bundle_id
    bundle_dir.mkdir(parents=True, exist_ok=True)
    target = bundle_dir / name
    _atomic_write(target, data)
    digest = sha256_bytes(data)
    sidecar = bundle_dir / f"{name}.sha256"
    _atomic_write(sidecar, f"{digest}  {name}\n".encode("utf-8"))
    written_at = datetime.now(timezone.utc).isoformat()
    return ArtifactRecord(
        bundle_id=bundle_id,
        name=name,
        path=target,
        sha256=digest,
        size_bytes=len(data),
        written_at=written_at,
    )


def verify_artifact(path: str | Path, expected_sha256: str) -> bool:
    """Return True when the stored file's digest equals ``expected_sha256``."""
    return sha256_file(path) == expected_sha256
