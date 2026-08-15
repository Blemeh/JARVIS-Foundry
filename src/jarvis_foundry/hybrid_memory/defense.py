"""V8 hybrid memory — Tier C defense (poison/secret gate, quarantine).

Defense-in-depth only: structural truth precedence (Tier A/B) is the
authority boundary; this scanner reduces the chance that prohibited content
ever reaches recallable state or version history. It is not mathematically
complete — treat it as a gate, not a proof.
"""
from __future__ import annotations

import re

# Patterns carried over from the audited lab candidate (post R1/C9 repairs).
SUSPICIOUS_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"(?i)ignore\s+(owner\s+)?(stop|veto|directive)", "directive_imposter"),
    (r"(?i)disable\s+(the\s+)?stop", "directive_imposter"),
    (r"(?i)push\s+(all\s+)?repositor", "unauthorized_action"),
    (r"(?i)override(s)?\s+the\s+(architecture\s+)?contract", "authority_override"),
    (r"(?i)read\s+(the\s+)?(host\s+)?credentials?", "credential_exfil"),
    (r"(?i)(api[_-]?key|password|secret\s*token|BEGIN\s+(RSA|OPENSSH|PRIVATE))", "secret_like"),
    (r"(?i)automatically\s+pass\s+my\s+work", "verdict_imposter"),
    (r"(?i)verifier\s+should\s+(automatically\s+)?pass", "verdict_imposter"),
    (r"(?i)(automatically|always|should)\s+pass\s+(my|our|this)\s+work", "verdict_imposter"),
    (r"(?i)this\s+memory\s+overrides", "authority_override"),
    (r"(?i)disable\s+(the\s+)?(emergency\s+)?stop", "directive_imposter"),
)

# Fake-secret probe strings used by tests are deliberately simple patterns;
# real deployments should extend this list via configuration.


class PoisonedMemoryError(ValueError):
    """Content matched a prohibited pattern and was refused/quarantined."""


class DefenseScanner:
    """Deterministic pattern scan over a single text value.

    ``scan(text)`` returns the list of matched threat labels (empty = clean).
    ``assert_clean(text)`` raises :class:`PoisonedMemoryError` on a match.
    """

    def __init__(self, patterns: tuple[tuple[str, str], ...] = SUSPICIOUS_PATTERNS) -> None:
        self._compiled = [(re.compile(p), label) for p, label in patterns]

    def scan(self, text: str) -> list[str]:
        hits: list[str] = []
        for pattern, label in self._compiled:
            if pattern.search(text or ""):
                hits.append(label)
        return hits

    def assert_clean(self, text: str) -> None:
        hits = self.scan(text)
        if hits:
            raise PoisonedMemoryError(f"suspicious content: {sorted(set(hits))}")
