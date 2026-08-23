"""Contain hostile external content at the operation boundary."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .procedure import OperationError


@dataclass(frozen=True, slots=True)
class ExternalContentRecord:
    content_id: str
    source: str
    text: str
    trust: str
    blocked_reasons: tuple[str, ...]
    directive_candidate: bool = False
    authority: bool = False


class ExternalContentGuard:
    """Preserve inbound content as data; never promote it to owner authority."""

    _PATTERNS = (
        ("owner-impersonation", re.compile(r"\b(?:owner|admin|system)\s+(?:says|instruction|authority)\b", re.I)),
        ("secret-request", re.compile(r"\b(?:api[_ -]?key|password|token|secret|credential)s?\b", re.I)),
        ("wrong-target", re.compile(r"(?:redirect|open|write|send)\s+(?:to\s+)?(?:another|wrong|C:\\|/etc/|\\\\)", re.I)),
        ("path-widening", re.compile(r"(?:\.\./|\.\.\\|recursive\s+root|entire\s+drive)", re.I)),
        ("command-execution", re.compile(r"\b(?:powershell|cmd(?:\.exe)?|shell|execute|run\s+command)\b", re.I)),
        ("payment", re.compile(r"\b(?:pay|purchase|buy|wire|transfer|credit\s+card|invoice)\b", re.I)),
        ("stop-tampering", re.compile(r"(?:disable|ignore|bypass|override)\s+(?:the\s+)?(?:STOP|emergency stop|veto)", re.I)),
        ("goal-alteration", re.compile(r"(?:change|replace|widen|ignore)\s+(?:the\s+)?(?:goal|mission|objective)", re.I)),
    )

    def inspect(self, *, content_id: str, source: str, text: str) -> ExternalContentRecord:
        content_id = self._exact(content_id, "content_id")
        source = self._exact(source, "source")
        if not isinstance(text, str) or not text.strip():
            raise OperationError("external content text must be non-empty")
        reasons = tuple(name for name, pattern in self._PATTERNS if pattern.search(text))
        return ExternalContentRecord(
            content_id=content_id, source=source, text=text,
            trust="DATA_ONLY", blocked_reasons=reasons,
            directive_candidate=False, authority=False,
        )

    @staticmethod
    def assert_data_only(record: ExternalContentRecord) -> None:
        if record.trust != "DATA_ONLY" or record.directive_candidate or record.authority:
            raise OperationError("external content crossed the data-only boundary")

    @staticmethod
    def _exact(value: object, field_name: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise OperationError(f"{field_name} must be non-empty")
        if any(ord(char) < 32 for char in value):
            raise OperationError(f"{field_name} contains a control character")
        return value.strip()


__all__ = ["ExternalContentGuard", "ExternalContentRecord"]
