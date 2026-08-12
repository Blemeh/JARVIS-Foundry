"""WP11 slices C/F (V2-OPERATIONAL): deterministic statement extraction.

Same deterministic splitter used by continuous ingestion (slice C) and
consolidation meaning-verification (slice F): same markdown → same
statements, pinned by tests. Untrusted content: extraction is data-only,
no control semantics.
"""
from __future__ import annotations

import re

_MAX_STATEMENT = 300
_BULLET = re.compile(r"^[-*+]\s+|^\d+[.)]\s+")


def extract_statements(text: str) -> list[str]:
    """Split note text into statements (deterministic, bounded).

    - leading frontmatter block (--- ... ---) skipped entirely;
    - headings (# ...) skipped;
    - fenced code blocks (``` ... ```) skipped;
    - empty lines skipped;
    - bullet/list markers stripped; line trimmed and capped at 300 chars;
    - lines that canonicalize empty (symbols only) dropped.
    """
    statements: list[str] = []
    lines = text.splitlines()
    in_frontmatter = False
    in_code = False
    for index, raw in enumerate(lines):
        line = raw.strip()
        if index == 0 and line == "---":
            in_frontmatter = True
            continue
        if in_frontmatter:
            if line == "---":
                in_frontmatter = False
            continue
        if line.startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            continue
        if not line or line.startswith("#"):
            continue
        statement = _BULLET.sub("", line).strip()
        if statement:
            statements.append(statement[:_MAX_STATEMENT])
    return statements
