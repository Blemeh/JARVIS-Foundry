"""Safe structural tokenization for semantic observations."""
from __future__ import annotations

import re
import unicodedata
from typing import Iterable

from ..control.redaction import contains_secret_like
from ..perception.contracts import PRIVACY_EXCLUDED, PerceptionObservation
from .contracts import ActivityToken

_SECRET_WORD = re.compile(
    r"(?i)(?:api[_-]?key|token|password|passwd|secret|private[_-]?key|"
    r"authorization|bearer)")
_UNTRUSTED_TEXT = re.compile(
    r"(?i)(?:ignore|disable|promote|owner|directive|private|confidential|"
    r"authentication|prompt\s+injection)")
_UUID = re.compile(r"(?i)^[0-9a-f]{8}-[0-9a-f-]{17,}$")
_HEX = re.compile(r"(?i)^[0-9a-f]{10,}$")
_DATE = re.compile(r"^\d{4}[-/.]\d{1,2}[-/.]\d{1,2}$")
_PARAM = re.compile(r"(?<![A-Za-z0-9_])([A-Za-z][A-Za-z0-9_-]{0,31})\s*=\s*([^,;\s)]+)")
_SAFE_PARAMETER_KEYS = frozenset({
    "field", "view", "route", "operation", "mode", "kind", "action",
    "resource", "screen", "tab", "form", "entity",
})


def _safe_component(value: object, *, field: str) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).strip().lower()
    if not text or contains_secret_like(text) or _SECRET_WORD.search(text):
        return "redacted"
    if _UNTRUSTED_TEXT.search(text):
        return "untrusted"
    if text.startswith(("http://", "https://", "www.")):
        return "url"
    if "\\" in text or "/" in text:
        return "path"
    if field == "resource" and (len(text) > 32 or bool(re.search(r"\s", text))):
        return "resource"
    if _UUID.match(text) or _HEX.match(text) or _DATE.match(text):
        return "variable"
    # Keep only a short structural label; values and coordinates become
    # variable instead of being hashed or copied into derived state.
    ascii_text = text.encode("ascii", "ignore").decode("ascii")
    ascii_text = re.sub(r"[^a-z0-9._-]+", "_", ascii_text).strip("._-")
    ascii_text = re.sub(r"\d{4,}", "variable", ascii_text)
    if not ascii_text:
        return "unknown"
    return ascii_text[:48]


def _base_and_parameter_keys(resource: str) -> tuple[str, tuple[str, ...]]:
    matches = list(_PARAM.finditer(resource or ""))
    keys = sorted({match.group(1).lower() for match in matches
                   if match.group(1).lower() in _SAFE_PARAMETER_KEYS})
    base = resource.split("(", 1)[0]
    if matches and not base.strip():
        base = "resource"
    return _safe_component(base, field="resource"), tuple(keys)


def tokenize_observation(observation: PerceptionObservation) -> ActivityToken | None:
    """Convert one already privacy-processed observation to a safe token."""
    observation.validate()
    if observation.privacy_class == PRIVACY_EXCLUDED:
        return None
    application = _safe_component(observation.application, field="application")
    resource, parameter_keys = _base_and_parameter_keys(observation.resource)
    activity = _safe_component(observation.activity_class, field="activity_class")
    structure = (application, resource, activity)
    token_id = ":".join(structure)
    return ActivityToken(
        observation_id=observation.observation_id,
        token_id=token_id,
        structure=structure,
        safe_parameter_keys=parameter_keys,
        captured_at=observation.captured_at,
        interval_end=observation.interval_end,
        privacy_class=observation.privacy_class,
        redaction_state=observation.redaction_state,
    )


def tokenize(observations: Iterable[PerceptionObservation]) -> tuple[ActivityToken, ...]:
    tokens: list[ActivityToken] = []
    for observation in observations:
        token = tokenize_observation(observation)
        if token is not None:
            tokens.append(token)
    return tuple(tokens)
