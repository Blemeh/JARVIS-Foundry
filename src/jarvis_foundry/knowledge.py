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

_CONTRACTIONS: dict[str, str] = {
    # apostrophe forms (expanded BEFORE punctuation stripping, which would
    # otherwise destroy the 'n't' token — verifier deleg_642b84b2 K1)
    "isn't": "is not", "doesn't": "does not", "don't": "do not",
    "can't": "can not", "won't": "will not", "couldn't": "could not",
    "shouldn't": "should not", "wouldn't": "would not", "didn't": "did not",
    "wasn't": "was not", "weren't": "were not", "aren't": "are not",
    "hasn't": "has not", "haven't": "have not",
    # apostrophe-free spellings (copula must survive: 'isnt' -> 'is not',
    # never just 'isnt' removed, which would break core equality)
    "isnt": "is not", "doesnt": "does not", "dont": "do not",
    "cant": "can not", "cannot": "can not", "wont": "will not",
    "couldnt": "could not", "shouldnt": "should not", "wouldnt": "would not",
    "didnt": "did not", "wasnt": "was not", "werent": "were not",
    "arent": "are not", "hasnt": "has not", "havent": "have not",
    # suffix form (matches 'do n't' style splits; full forms above win first)
    "n't": " not",
}
_CONTRACTION_RE = re.compile(
    r"\b(" + "|".join(sorted(_CONTRACTIONS, key=len, reverse=True)) + r")\b")

_NEGATION_TOKENS = frozenset({"not", "no", "never"})
_WHITESPACE = re.compile(r"\s+")
_NON_ALNUM = re.compile(r"[^a-z0-9 ]")


def expand_contractions(statement: str) -> str:
    """Expand English contractions to their 'not' forms (word boundaries).

    Applied BEFORE punctuation stripping so apostrophe tokens survive.
    """
    return _CONTRACTION_RE.sub(
        lambda match: _CONTRACTIONS[match.group(0).lower()], statement.lower())


def canonicalize_statement(statement: str) -> str:
    """Lowercase, expand contractions, collapse whitespace, strip punctuation."""
    text = expand_contractions(statement)
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
