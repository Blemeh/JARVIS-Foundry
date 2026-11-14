"""Per-repo notes file ($JARVIS_HOME/notes/<repo>.md) and proposed lessons."""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from pathlib import Path

from .repo_profile import Profile

NOTE_LINE = re.compile(r"^\s*[-*]?\s*NOTE:\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)


def path(home: Path, repo: str) -> Path:
    return home / "notes" / f"{repo}.md"


def ensure(home: Path, repo: str, profile: Profile) -> Path:
    file = path(home, repo)
    if not file.exists():
        file.parent.mkdir(parents=True, exist_ok=True)
        cmds = "\n".join(f"- {name}: `{cmd}`" for name, cmd in
                         (("setup", profile.setup), *profile.commands()) if cmd) or "- (none)"
        file.write_text(f"# Notes for {repo}\n\n## Commands\n{cmds}\n\n## Conventions\n\n"
                        "## Lessons\n", encoding="utf-8")
    return file


def read(home: Path, repo: str) -> str:
    try:
        return path(home, repo).read_text(encoding="utf-8")
    except OSError:
        return ""


def extract(output: str, limit: int = 3) -> list[str]:
    found = []
    for match in NOTE_LINE.finditer(output or ""):
        text = " ".join(match.group(1).split())[:300]
        if text and text not in found:
            found.append(text)
    return found[:limit]


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text.lower()).split())


def _known(text: str, seen: list[str]) -> bool:
    norm = _norm(text)
    return any(norm == s or SequenceMatcher(None, norm, s).ratio() >= 0.8 for s in seen)


def propose(store, home: Path, repo: str, task_id: int, output: str) -> list[int]:
    """Store new NOTE lines as proposals, skipping ones already noted, proposed or
    rejected (also near-duplicates that differ only in wording details)."""
    seen = [_norm(line.lstrip("-* ")) for line in read(home, repo).splitlines() if line.strip()]
    seen += [_norm(row["text"]) for row in store.proposals(repo, status=None)]
    ids = []
    for text in extract(output):
        if not _known(text, seen):
            ids.append(store.add_proposal(repo, task_id, text))
            seen.append(_norm(text))
    return ids


def accept(store, home: Path, proposal_id: int):
    row = store.set_proposal(proposal_id, "accepted")
    if row is not None:
        file = path(home, row["repo"])
        file.parent.mkdir(parents=True, exist_ok=True)
        text = file.read_text(encoding="utf-8") if file.exists() else f"# Notes for {row['repo']}\n"
        if "## Lessons" not in text:
            text = text.rstrip("\n") + "\n\n## Lessons\n"
        file.write_text(text.rstrip("\n") + f"\n- {row['text']}\n", encoding="utf-8")
    return row
