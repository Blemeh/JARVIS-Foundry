"""Small V5 external-content and target safety boundaries."""

from __future__ import annotations

import hashlib
import html
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from ..control.redaction import contains_secret_like
from ..procedural_learning.contracts import safe_page_text


class SafetyBoundaryError(ValueError):
    """Untrusted content or an external target crossed a safety boundary."""


@dataclass(frozen=True, slots=True)
class ExternalContentRecord:
    text: str
    source_ref: str
    content_hash: str
    authority: str = "DATA_ONLY"

    def to_evidence(self) -> dict[str, Any]:
        return {
            "source_ref": self.source_ref,
            "content_hash": self.content_hash,
            "authority": self.authority,
        }


def sanitize_external_content(value: str, *, source_ref: str, max_chars: int = 4096) -> ExternalContentRecord:
    if not isinstance(value, str) or not isinstance(source_ref, str) or not source_ref.strip():
        raise SafetyBoundaryError("external content and source_ref are required")
    if len(value) > max_chars:
        raise SafetyBoundaryError("external content exceeds maximum size")
    if contains_secret_like(value):
        raise SafetyBoundaryError("secret-like external content is refused")
    try:
        safe = safe_page_text(value)
    except ValueError as error:
        raise SafetyBoundaryError(str(error)) from error
    # Control characters are data corruption, never executable instruction.
    safe = "".join(character if ord(character) >= 0x20 or character in "\r\n\t" else " " for character in safe)
    safe = html.escape(safe, quote=True)
    if len(safe) > max_chars:
        raise SafetyBoundaryError("external content exceeds maximum size after normalization")
    return ExternalContentRecord(
        text=safe, source_ref=source_ref.strip(),
        content_hash=hashlib.sha256(safe.encode("utf-8")).hexdigest(),
    )


def validate_external_url(value: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise SafetyBoundaryError("external URL must be trimmed")
    if any(ord(character) < 0x20 for character in value):
        raise SafetyBoundaryError("external URL contains control characters")
    parsed = urlsplit(value)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise SafetyBoundaryError("external URL scheme or host is unsupported")
    if parsed.username is not None or parsed.password is not None:
        raise SafetyBoundaryError("external URL credentials are refused")
    return value


def validate_redirect_chain(chain: list[str] | tuple[str, ...], *, max_hops: int = 5) -> tuple[str, ...]:
    if not isinstance(max_hops, int) or max_hops < 0:
        raise SafetyBoundaryError("max_hops must be non-negative")
    normalized = tuple(validate_external_url(value) for value in chain)
    if len(normalized) - 1 > max_hops:
        raise SafetyBoundaryError("redirect chain exceeds maximum hops")
    if len(set(normalized)) != len(normalized):
        raise SafetyBoundaryError("redirect cycle is refused")
    return normalized


__all__ = ["ExternalContentRecord", "SafetyBoundaryError", "sanitize_external_content", "validate_external_url", "validate_redirect_chain"]
