"""Bilingual owner-utterance matcher. Not an authority.

Maps English and Kosovo/Albanian speech/text onto the canonical
``OwnerControlService`` command types. STATUS is a read, not a directive.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

COMMAND_STOP = "STOP_EVERYTHING"
COMMAND_PAUSE = "PAUSE"
COMMAND_RESUME = "RESUME"
COMMAND_GOAL = "GOAL"
COMMAND_VETO = "VETO_ACTION"
COMMAND_REDIRECT = "REDIRECT"

KIND_COMMAND = "COMMAND"
KIND_STATUS = "STATUS"
KIND_UNKNOWN = "UNKNOWN"

TARGET_GLOBAL = "GLOBAL"

_E = r"[eë]"
_C = r"[cç]"
_WAKE_PREFIX = re.compile(r"^(?:(?:hey|ok|okay)\s+)?jarvis[,:\s]+", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class MatchedUtterance:
    kind: str
    command_type: str | None
    target: str
    exact_text: str
    remainder: str
    language: str
    pattern_id: str
    original: str


def normalize_language(value: str | None) -> str:
    token = (value or "").strip().lower().replace("_", "-")
    if token in {"sq", "sqi", "alb", "sq-al", "sq-xk", "albanian"}:
        return "sq"
    if token.startswith("en") or token in {"eng", "english"}:
        return "en"
    return token or "en"


def confirmation_text(command_type: str, language: str, *, applied: bool) -> str:
    lang = "sq" if normalize_language(language) == "sq" else "en"
    if not applied:
        return {
            "en": "That command was not applied.",
            "sq": "Ai urdhër nuk u zbatua.",
        }[lang]
    table = {
        COMMAND_STOP: {"en": "Stopped.", "sq": "U ndal."},
        COMMAND_PAUSE: {"en": "Paused.", "sq": "U pauzua."},
        COMMAND_RESUME: {"en": "Resumed.", "sq": "Po vazhdoj."},
        COMMAND_GOAL: {"en": "Goal recorded.", "sq": "E regjistrova qëllimin."},
        COMMAND_VETO: {"en": "Veto recorded.", "sq": "E regjistrova ndalimin."},
        COMMAND_REDIRECT: {"en": "Redirect recorded.", "sq": "E regjistrova ridrejtimin."},
    }
    return table.get(command_type, {"en": "Recorded.", "sq": "E regjistrova."})[lang]


def failure_text(language: str, *, reason: str | None = None) -> str:
    lang = "sq" if normalize_language(language) == "sq" else "en"
    if lang == "sq":
        return "Nuk mund ta ekzekutoj atë urdhër." if not reason else reason
    return "I could not execute that command." if not reason else reason


def _strip_utterance(text: str) -> str:
    cleaned = text.strip()
    cleaned = cleaned.replace("\u2019", "'").replace("\u2018", "'")
    cleaned = re.sub(r"\s+", " ", cleaned)
    cleaned = _WAKE_PREFIX.sub("", cleaned, count=1)
    return cleaned.strip(" \t.?!")


def _compile(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, re.IGNORECASE | re.UNICODE)


# Longer prefixes first so "ndalo krejt" does not collapse to "ndalo".
_PATTERNS: tuple[tuple[str, str, str, re.Pattern[str], str], ...] = (
    ("en", KIND_COMMAND, COMMAND_STOP, _compile(r"^(?:please\s+)?stop(?:\s+everything)?$"), "en.stop"),
    ("en", KIND_COMMAND, COMMAND_PAUSE, _compile(r"^(?:please\s+)?pause(?:\s+jarvis)?$"), "en.pause"),
    ("en", KIND_COMMAND, COMMAND_RESUME, _compile(r"^(?:please\s+)?(?:resume|continue)(?:\s+jarvis)?$"), "en.resume"),
    ("en", KIND_COMMAND, COMMAND_GOAL, _compile(r"^(?:handle this|take this over)\s*:?\s+(.+)$"), "en.handle"),
    ("en", KIND_COMMAND, COMMAND_GOAL, _compile(r"^(?:goal|the goal is)\s*:?\s+(.+)$"), "en.goal"),
    ("en", KIND_COMMAND, COMMAND_VETO, _compile(r"^(?:never do|don't ever do|do not ever do)\s+(.+)$"), "en.veto"),
    ("en", KIND_COMMAND, COMMAND_REDIRECT, _compile(r"^redirect(?:\s+work)?(?:\s+to)?\s*:?\s+(.+)$"), "en.redirect"),
    ("en", KIND_STATUS, None, _compile(r"^(?:what(?:'s| is) (?:going on|happening)|what are you doing|status)$"), "en.status"),
    (
        "sq",
        KIND_COMMAND,
        COMMAND_STOP,
        _compile(rf"^(?:te lutem\s+|t{_E} lutem\s+)?ndalo(?:\s+(?:krejt|gjith{_C}ka|gjithcka))?$"),
        "sq.stop",
    ),
    (
        "sq",
        KIND_COMMAND,
        COMMAND_PAUSE,
        _compile(rf"^(?:pauz{_E}|pauze|ndale p{_E}r momentin|ndale per momentin)$"),
        "sq.pause",
    ),
    ("sq", KIND_COMMAND, COMMAND_RESUME, _compile(r"^(?:vazhdo)(?:\s+jarvis)?$"), "sq.resume"),
    (
        "sq",
        KIND_COMMAND,
        COMMAND_GOAL,
        _compile(rf"^merre(?:\s+p{_E}rsip{_E}r)?(?:\s+k{_E}t{_E})?\s*:?\s+(.+)$"),
        "sq.handle",
    ),
    (
        "sq",
        KIND_COMMAND,
        COMMAND_GOAL,
        _compile(rf"^(?:q{_E}llimi(?:\s+(?:{_E}sht{_E}|eshte|is))?)\s*:?\s+(.+)$"),
        "sq.goal",
    ),
    (
        "sq",
        KIND_COMMAND,
        COMMAND_VETO,
        _compile(rf"^mos e b{_E}j kurr{_E}\s+(.+)$"),
        "sq.veto",
    ),
    (
        "sq",
        KIND_COMMAND,
        COMMAND_REDIRECT,
        _compile(rf"^(?:ridrejto|ndrysho drejtimin)\s*:?\s+(.+)$"),
        "sq.redirect",
    ),
    (
        "sq",
        KIND_STATUS,
        None,
        _compile(rf"^(?:{_C}ka je tu bo|{_C}far{_E} po b{_E}n|cfare po ben|statusi)$"),
        "sq.status",
    ),
)


def match_owner_utterance(text: str, *, language_hint: str | None = None) -> MatchedUtterance:
    original = _strip_utterance(text)
    if not original:
        return MatchedUtterance(
            KIND_UNKNOWN, None, TARGET_GLOBAL, "", "", normalize_language(language_hint), "empty", original,
        )
    hint = normalize_language(language_hint)
    ordered = _PATTERNS
    if hint in {"en", "sq"}:
        ordered = tuple(p for p in _PATTERNS if p[0] == hint) + tuple(p for p in _PATTERNS if p[0] != hint)
    hits: list[MatchedUtterance] = []
    for language, kind, command_type, pattern, pattern_id in ordered:
        matched = pattern.fullmatch(original)
        if matched is None:
            continue
        remainder = matched.group(1).strip() if matched.lastindex else ""
        if kind == KIND_COMMAND and command_type in {COMMAND_GOAL, COMMAND_VETO, COMMAND_REDIRECT}:
            if not remainder:
                continue
            exact_text = remainder
        else:
            exact_text = original
        hits.append(MatchedUtterance(
            kind=kind,
            command_type=command_type,
            target=TARGET_GLOBAL,
            exact_text=exact_text,
            remainder=remainder,
            language=language,
            pattern_id=pattern_id,
            original=original,
        ))
        break
    if not hits:
        return MatchedUtterance(
            KIND_UNKNOWN, None, TARGET_GLOBAL, original, "", hint if hint in {"en", "sq"} else "en", "none", original,
        )
    return hits[0]
