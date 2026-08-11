"""WP6 — governed V1 Obsidian write-back: write policy, snapshots, and the
transactional writer with conflict detection and byte-exact rollback."""
from __future__ import annotations

from .policy import (
    AUDIT_DIR,
    FRONTMATTER,
    JARVIS_STATE_DIR,
    V1_NOTE_NAMES,
    ObsidianPolicyError,
    ObsidianWriter,
)
from .snapshots import SnapshotStore, sha256_bytes, sha256_file
from .writer import ObsidianConflictError, ObsidianService, WriteResult

__all__ = [
    "AUDIT_DIR",
    "FRONTMATTER",
    "JARVIS_STATE_DIR",
    "V1_NOTE_NAMES",
    "ObsidianConflictError",
    "ObsidianPolicyError",
    "ObsidianService",
    "ObsidianWriter",
    "SnapshotStore",
    "WriteResult",
    "sha256_bytes",
    "sha256_file",
]
