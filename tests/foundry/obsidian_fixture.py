"""Shared Obsidian service fixture for software-intelligence tests.

Disposable temp vault only; the owner's live vault is never touched.
"""
from __future__ import annotations

import sys
from pathlib import Path

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.obsidian import ObsidianService, ObsidianWriter


def make_service(root: Path, *, allow_projects: bool = False,
                 name: str = "foundry.db") -> ObsidianService:
    root.mkdir(parents=True, exist_ok=True)
    vault = root / "vault"
    (vault / "JARVIS" / "V1" / "Audit").mkdir(parents=True, exist_ok=True)
    writer = ObsidianWriter(vault, allow_projects=allow_projects)
    database = FoundryDatabase(root / name)
    database.initialize()
    return ObsidianService(database, writer)
