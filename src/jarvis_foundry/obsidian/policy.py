"""WP6 — governed V1 Obsidian write-back: write policy, note set, and
frontmatter contract (03 spec §13)."""
from __future__ import annotations

from pathlib import Path

#: The exact V1 note set the kernel may write (03 spec §13 layout).
V1_NOTE_NAMES: frozenset[str] = frozenset({
    "00 - V1 State.md",
    "01 - Registered Project.md",
    "02 - Active Mission.md",
    "03 - Decisions.md",
    "04 - Evidence Index.md",
    "05 - Failure and Repair Log.md",
    "06 - Lessons.md",
    "07 - Daily State.md",
})

AUDIT_DIR = "Audit"

#: Every V1 note must carry exactly this provenance contract.
FRONTMATTER: dict[str, str] = {
    "origin": "jarvis",
    "current_steward": "jarvis",
    "system": "jarvis-foundry",
    "version": "v1",
}

JARVIS_STATE_DIR = ".jarvis"


class ObsidianPolicyError(ValueError):
    pass


class ObsidianWriter:
    """Boundary + policy for governed V1 Obsidian writes.

    JARVIS may READ the entire vault but WRITE only the V1 projection area
    (``<vault>/JARVIS/V1`` by default) and only the governed note set —
    the 00-07 notes and Audit/ trail notes. Everything else is refused
    before any file or ledger mutation.
    """

    def __init__(self, vault_root: str | Path,
                 v1_write_root: str = "JARVIS/V1", *,
                 allow_projects: bool = False) -> None:
        self.vault = Path(vault_root).expanduser().resolve()
        # Root-cause fix (review deleg_1e6e9421, R4): historical callers
        # passed the governed dir itself (…/JARVIS/V1) or the JARVIS dir
        # and the writer silently doubled/offset the path
        # (…/JARVIS/V1/JARVIS/V1, …/JARVIS/JARVIS/V1). Normalize by path
        # shape so all three caller classes resolve to the SAME governed
        # dir deterministically:
        #   root                -> root/JARVIS/V1
        #   root/JARVIS         -> root/JARVIS/V1
        #   root/JARVIS/V1      -> root/JARVIS/V1 (idempotent)
        parts = [p for p in self.vault.parts if p not in ("/", "\\", "")]
        tail = [p.lower() for p in parts[-2:]]
        v1_parts = [p.lower() for p in v1_write_root.split("/")]
        if tail == v1_parts:
            write_root = self.vault                      # already the governed dir
        elif tail[-1:] == v1_parts[-2:-1]:
            write_root = self.vault / v1_write_root.split("/")[-1]   # root/JARVIS
        else:
            write_root = self.vault / v1_write_root
        self.write_root = write_root.resolve()
        #: software-intelligence extension: Projects/ subnamespace opt-in
        self.allow_projects = bool(allow_projects)
        if self.write_root != self.vault and self.vault not in self.write_root.parents:
            raise ObsidianPolicyError(
                f"v1_write_root must live inside the vault: {self.write_root}")

    # -- boundary -----------------------------------------------------------

    def _inside(self, candidate: Path, root: Path) -> bool:
        try:
            candidate.resolve(strict=False).relative_to(root)
            return True
        except ValueError:
            return False

    def _validate_note_name(self, note_name: str) -> str:
        if not note_name or not str(note_name).strip():
            raise ObsidianPolicyError("note name is required")
        name = str(note_name).strip()
        # Software-intelligence lane: governed project-intelligence namespace.
        # Opt-in per writer instance; never enabled for the live V1 writer.
        # Exactly one safe path segment under Projects/.
        if self.allow_projects and (
                name.startswith("Projects/") or name.startswith("Projects\\")):
            separator = "/" if name.startswith("Projects/") else "\\"
            tail = name.split(separator, 1)[1]
            if not tail or tail in {".", ".."} or "/" in tail or "\\" in tail \
                    or tail.strip() != tail:
                raise ObsidianPolicyError(
                    f"unsafe project note name: {name!r}")
            return name
        if "/" in name or "\\" in name or name in {".", ".."}:
            raise ObsidianPolicyError(f"note name must be a plain file name: {name!r}")
        if name in V1_NOTE_NAMES:
            return name
        if name.startswith(f"{AUDIT_DIR}/") or name.startswith(f"{AUDIT_DIR}\\") or name == "Audit":
            raise ObsidianPolicyError("audit notes are written by the writer, not callers")
        raise ObsidianPolicyError(
            f"note {name!r} is not in the governed V1 note set")

    def note_path(self, note_name: str) -> Path:
        """Resolve a governed note to its on-disk path (inside the write root)."""
        name = self._validate_note_name(note_name)
        path = (self.write_root / name).resolve()
        if not self._inside(path, self.write_root):
            raise ObsidianPolicyError("note path escapes the V1 write root")
        return path

    @staticmethod
    def _ensure_parent(path: Path) -> None:
        """Create the note's parent directory when missing (Projects/x)."""
        parent = path.parent
        if not parent.exists():
            parent.mkdir(parents=True, exist_ok=True)

    def audit_path(self, relative: str) -> Path:
        """Resolve a path under the Audit/ area (conflict preserves, trail)."""
        relative = relative.replace("\\", "/")
        if relative.startswith("/") or ".." in relative.split("/"):
            raise ObsidianPolicyError(f"unsafe audit path: {relative!r}")
        path = (self.write_root / AUDIT_DIR / relative).resolve()
        if not self._inside(path, self.write_root):
            raise ObsidianPolicyError("audit path escapes the V1 write root")
        return path

    def read_path(self, vault_relative: str) -> Path:
        """Whole-vault READ is allowed; the path must still resolve inside the
        vault (read-only boundary — JARVIS never reads outside the vault)."""
        relative = vault_relative.replace("\\", "/").lstrip("/")
        if ".." in relative.split("/"):
            raise ObsidianPolicyError(f"unsafe read path: {vault_relative!r}")
        path = (self.vault / relative).resolve()
        if not self._inside(path, self.vault):
            raise ObsidianPolicyError("read path escapes the vault")
        return path

    # -- layout -------------------------------------------------------------

    def ensure_v1_root(self) -> list[Path]:
        """Create the V1 projection area (notes + Audit/). Returns the paths."""
        self.write_root.mkdir(parents=True, exist_ok=True)
        (self.write_root / AUDIT_DIR).mkdir(exist_ok=True)
        return [self.write_root, self.write_root / AUDIT_DIR]

    # -- frontmatter contract ----------------------------------------------

    @staticmethod
    def parse_frontmatter(content: str) -> dict[str, str]:
        """Parse the leading ``---`` YAML block (fixed key: scalar subset).

        Returns the keys found; raises ObsidianPolicyError on malformed
        blocks. Values are single-line scalars only.
        """
        if not content.startswith("---\n") and not content.startswith("---\r\n"):
            raise ObsidianPolicyError("note must start with a frontmatter block")
        newline = "\r\n" if content.startswith("---\r\n") else "\n"
        rest = content[4:] if newline == "\n" else content[5:]
        end = rest.find(f"{newline}---")
        if end < 0:
            raise ObsidianPolicyError("unterminated frontmatter block")
        fields: dict[str, str] = {}
        for line in rest[:end].split(newline):
            if not line.strip():
                continue
            if ":" not in line:
                raise ObsidianPolicyError(f"malformed frontmatter line: {line!r}")
            key, _, value = line.partition(":")
            key = key.strip()
            value = value.strip().strip("\"'")
            if not key or not value:
                raise ObsidianPolicyError(f"malformed frontmatter line: {line!r}")
            fields[key] = value
        return fields

    def validate_frontmatter(self, content: str) -> None:
        """Enforce the provenance contract on a note's content."""
        fields = self.parse_frontmatter(content)
        missing = [key for key, value in FRONTMATTER.items()
                   if fields.get(key) != value]
        if missing:
            raise ObsidianPolicyError(
                f"frontmatter contract violated; keys {sorted(missing)} "
                f"must equal {FRONTMATTER}")
