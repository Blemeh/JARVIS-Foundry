"""Dependency-free implementation of JARVIS's ``hash.file.sha256`` action."""

from __future__ import annotations

import hashlib
from pathlib import Path


def sha256_path(path: str | Path) -> str:
    """Return the SHA-256 digest of one file without importing Fabric runtime."""

    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


__all__ = ["sha256_path"]
