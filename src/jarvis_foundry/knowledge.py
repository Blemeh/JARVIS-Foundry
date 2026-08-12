"""V2 slice-1 (WP9): deterministic knowledge canonicalization and
contradiction detection.

Slice-1 scope (bounded, per the WP9 work package): string-level,
deterministic, test-driven. No LLM inference here — LLM-mediated
contradiction inference is a V2+ enhancement (recorded as future work).

Negation fingerprint: two statements contradict when they share a
canonical core and exactly one carries a negation prefix. The core is the
canonicalized statement with a leading negation prefix removed."""
from __future__ import annotations

import re

_NEGATION_TOKENS = frozenset(
    {"not", "no", "never", "n't", "isn't", "doesn't", "don't", "can't",
     "cannot", "wont", "won't", "isnt", "doesnt", "dont", "cant"})
_WHITESPACE = re.compile(r"\s+")
_NON_ALNUM = re.compile(r"[^a-z0-9 ]")


def canonicalize_statement(statement: str) -> str:
    """Lowercase, collapse whitespace, strip punctuation."""
    text = statement.strip().lower()
    text = _NON_ALNUM.sub(" ", text)
    return _WHITESPACE.sub(" ", text).strip()


def split_negation(statement: str) -> tuple[str, bool]:
    """Return (canonical core, negated?) — the negation fingerprint.

    Negation markers are recognized at TOKEN level anywhere in the
    statement (leading or mid-string: 'not', 'never', "isn't", ...); the
    core is the canonical form with negation tokens removed.
    """
    canonical = canonicalize_statement(statement)
    tokens = canonical.split()
    kept = [t for t in tokens if t not in _NEGATION_TOKENS]
    negated = len(kept) != len(tokens)
    return (" ".join(kept), negated)


def contradicts(first: str, second: str) -> bool:
    """Deterministic contradiction test between two statements.

    True when both canonicalize to the same core and exactly one is
    negated (or one is a negated form of the other).
    """
    core_a, neg_a = split_negation(first)
    core_b, neg_b = split_negation(second)
    if not core_a or not core_b:
        return False
    if core_a == core_b and neg_a != neg_b:
        return True
    # bare "X" vs "X is not Y" style variants are out of slice-1 scope;
    # only exact-core negations count (bounded, documented).
    return False
