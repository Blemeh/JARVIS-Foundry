"""V5-S3: deterministic secret redaction (architecture contract §8.2).

The redactor strips secret-like ASSIGNMENTS from any text that may
reach prompts, logs, errors, artifacts, or summaries. It mirrors the
S1 boundary guard's pattern family (systems._SECRET_PATTERN) so the
refusal boundary and the redaction boundary agree on what a secret
looks like. Values are never recovered — redaction is one-way.
"""
from __future__ import annotations

import re
from typing import Any

#: same pattern family as src/jarvis_foundry/systems.py (the API-boundary
#: refusal guard) — key[:=]value assignments for secret-like keys. The
#: value runs to the next comma/semicolon/newline so multi-token values
#: (e.g. PEM blocks) are fully replaced.
_SECRET_PATTERN = re.compile(
    r"(api[_-]?key|token|password|passwd|secret|private[_-]?key|"
    r"authorization|bearer)\s*[:=]\s*[^,;\n]+", re.IGNORECASE)


def redact_text(text: str) -> str:
    """Replace secret-like assignments with ``<key>=[REDACTED]``.

    ``api_key=sk-live-1234`` -> ``api_key=[REDACTED]``;
    ``Authorization: Bearer xyz`` -> ``Authorization=[REDACTED]``.
    Values are never echoed; the key label is kept for debuggability.
    """
    if not text:
        return text

    def _replace(match: re.Match[str]) -> str:
        return f"{match.group(1)}=[REDACTED]"

    return _SECRET_PATTERN.sub(_replace, text)


def redact_payload(payload: Any) -> Any:
    """Recursively redact secret-like material inside any JSON-like
    structure: a dict KEY that is secret-like replaces its VALUE with
    [REDACTED]; string values are redacted as text. Non-strings pass
    through."""
    if isinstance(payload, dict):
        redacted: dict[str, Any] = {}
        for key, value in payload.items():
            if _SECRET_PATTERN.search(f"{key}=x"):
                redacted[key] = "[REDACTED]"
            else:
                redacted[key] = redact_payload(value)
        return redacted
    if isinstance(payload, (list, tuple)):
        return [redact_payload(item) for item in payload]
    if isinstance(payload, str):
        return redact_text(payload)
    return payload


def contains_secret_like(text: str) -> bool:
    """Deterministic scan used by ledger/prompt/log checks: True when the
    text carries a secret-like assignment (V5 exit evidence 3)."""
    return _SECRET_PATTERN.search(text) is not None
