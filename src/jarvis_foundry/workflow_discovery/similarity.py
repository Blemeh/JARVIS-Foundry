"""Small deterministic sequence similarity implementation."""
from __future__ import annotations

from collections.abc import Sequence



def edit_distance(first: Sequence[str], second: Sequence[str]) -> int:
    """Return Levenshtein distance with O(min(n, m)) memory."""
    if len(first) < len(second):
        first, second = second, first
    previous = list(range(len(second) + 1))
    for i, left in enumerate(first, start=1):
        current = [i]
        for j, right in enumerate(second, start=1):
            current.append(min(
                current[-1] + 1,
                previous[j] + 1,
                previous[j - 1] + (left != right),
            ))
        previous = current
    return previous[-1]


def normalized_edit_similarity(first: Sequence[str], second: Sequence[str]) -> float:
    if not first and not second:
        return 1.0
    scale = max(len(first), len(second))
    return round(1.0 - edit_distance(first, second) / scale, 6)


def structurally_similar(first: Sequence[str], second: Sequence[str],
                         threshold: float) -> bool:
    """Conservative equivalence: threshold plus matching endpoints.

    Matching endpoints prevent two workflows with only a shared prefix from
    being merged. A materially different inserted/deleted tail remains a
    separate pattern at the default 0.80 threshold.
    """
    if not first or not second:
        return False
    if first[0] != second[0] or first[-1] != second[-1]:
        return False
    return normalized_edit_similarity(first, second) >= threshold
