"""Deterministic source capture, hashing, freshness, and git truth helpers."""

from __future__ import annotations

import hashlib
import re
import subprocess  # nosec B404 - read-only git capture with a fixed command prefix
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jarvis_foundry.kernel.canonical import canonical_bytes, canonical_hash


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def json_hash(value: Any) -> str:
    return canonical_hash(value)


def canonical_bytes_json(value: Any) -> bytes:
    return canonical_bytes(value)


def is_fresh(captured_at: str, now: str, freshness_seconds: int) -> bool:
    if not captured_at or not now:
        return False
    try:
        captured = datetime.fromisoformat(captured_at)
        reference = datetime.fromisoformat(now)
    except ValueError:
        return False
    delta = (reference - captured).total_seconds()
    return 0 <= delta <= max(0, freshness_seconds)


def canonical_objective(text: str) -> str:
    lowered = text.lower()
    lowered = re.sub(r"[^a-z0-9\s]+", " ", lowered)
    return re.sub(r"\s+", " ", lowered).strip()


def excerpt(text: str, limit: int = 240) -> str:
    compact = re.sub(r"\s+", " ", text).strip()
    return compact[:limit]


def run_git(repo: Path, args: list[str]) -> str:
    completed = subprocess.run(  # nosec B603 B607 - no shell, fixed `git` prefix only
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if completed.returncode != 0:
        return ""
    return completed.stdout.strip()


def capture_file(path: Path, *, limit_bytes: int = 262_144) -> bytes:
    data = path.read_bytes()
    if len(data) > limit_bytes:
        data = data[:limit_bytes]
    return data


def now_utc() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def source_identity(source_type: str, source_id: str, captured_at: str, content_hash: str) -> str:
    return canonical_hash([source_type, source_id, captured_at, content_hash])
