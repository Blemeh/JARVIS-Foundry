"""Perception privacy firewall (hard gate, contract section C).

Order: EXCLUDED app/window -> SENSITIVE title / private zone -> secret-like
text (reusing the existing JARVIS redactor) -> only then normalization.
Default behavior prefers under-capture: exclusions and secret scanning are
on unless explicitly disabled in configuration.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..control.redaction import contains_secret_like, redact_text


class PrivacyClass:
    PUBLIC = "public"
    SENSITIVE = "sensitive"
    EXCLUDED = "excluded"


@dataclass(frozen=True, slots=True)
class PrivacyPolicy:
    """Deterministic, configurable privacy policy for the perception path."""

    excluded_apps: frozenset[str] = frozenset()
    excluded_windows: frozenset[str] = frozenset()
    sensitive_titles: frozenset[str] = frozenset()
    private_zones: tuple[tuple[str, str], ...] = ()
    secret_like_enabled: bool = True
    raw_frame_escalation: bool = False
    max_text_fragments: int = 8

    def exclude_reason(self, application: str, window: str) -> str | None:
        app_key = (application or "").strip().lower()
        window_key = (window or "").strip().lower()
        if app_key in {x.strip().lower() for x in self.excluded_apps}:
            return f"excluded_app:{application}"
        if window_key in {x.strip().lower() for x in self.excluded_windows}:
            return f"excluded_window:{window}"
        for app_pat, window_pat in self.private_zones:
            if (not app_pat or app_key == app_pat.strip().lower()) and (
                    not window_pat or window_key == window_pat.strip().lower()):
                return f"private_zone:{app_pat}:{window_pat}"
        return None

    def sensitive_reason(self, window: str) -> str | None:
        window_key = (window or "").strip().lower()
        for title in self.sensitive_titles:
            if title.strip().lower() in window_key:
                return f"sensitive_title:{title}"
        return None

    def redact_window(self, window: str) -> str:
        if self.sensitive_reason(window):
            return "[REDACTED-TITLE]"
        return window

    def redact_fragment(self, fragment: str) -> str | None:
        """Return the redacted fragment, or None when it must be dropped
        (secret-like values are never summarized)."""
        if not fragment:
            return None
        if self.secret_like_enabled and contains_secret_like(fragment):
            return None
        return redact_text(fragment)


@dataclass(frozen=True, slots=True)
class PrivacyOutcome:
    """Result of applying the firewall to one raw source record."""

    suppressed: bool
    reason: str | None
    redacted: bool
    application: str
    window: str
    text_fragments: tuple[str, ...]


def apply_policy(record: dict[str, Any],
                 policy: PrivacyPolicy) -> PrivacyOutcome:
    """Filter/redact one raw record before normalization.

    ``record`` fields: application, window, text_fragments (tuple of
    bounded fragments), activity, captured_at, source ids, content_hash.
    """
    application = str(record.get("application") or "unknown")
    window = str(record.get("window") or "")
    excluded = policy.exclude_reason(application, window)
    if excluded:
        return PrivacyOutcome(
            suppressed=True, reason=excluded, redacted=False,
            application=application, window=window, text_fragments=())
    sensitive = policy.sensitive_reason(window)
    window_safe = policy.redact_window(window)
    fragments: list[str] = []
    redacted = False
    for fragment in list(record.get("text_fragments") or ())[
            :policy.max_text_fragments]:
        safe = policy.redact_fragment(str(fragment))
        if safe is None:
            redacted = True
            continue
        if safe != fragment:
            redacted = True
        fragments.append(safe)
    if sensitive:
        redacted = True
    return PrivacyOutcome(
        suppressed=False, reason=sensitive, redacted=redacted,
        application=application, window=window_safe,
        text_fragments=tuple(fragments))
