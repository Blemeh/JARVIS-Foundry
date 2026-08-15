"""V8 hybrid memory — production test harness (no simulators; real tables).

Usage: ``from .v8_harness import make_lab`` then ``lab = make_lab()``, or
``import pytest; from .v8_harness import lab as lab_fixture``.
"""
from __future__ import annotations

import pathlib
import tempfile

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.hybrid_memory import (ContextCompiler, CoreMemoryRegistry,
                                          CurationPipeline, ProcedureRegistry,
                                          RecallEngine, TierCGitVersioning,
                                          TierCMemoryStore)


class V8Lab:
    """Bounded production test harness (tier_c tables only)."""

    def __init__(self, root: pathlib.Path) -> None:
        self.db = FoundryDatabase(root / "lab.db")
        self.schema_version = self.db.initialize()
        self.memories = TierCMemoryStore(self.db)
        self.curation = CurationPipeline(self.db)
        self.recall = RecallEngine(self.db)
        self.blocks = CoreMemoryRegistry(self.db)
        self.procedures = ProcedureRegistry(self.db)
        self.git = TierCGitVersioning(self.db, root / "memgit")
        self.compiler = ContextCompiler(self.db)
        self.blocks.ensure_defaults()

    def observe(self, content: str, **kwargs) -> str:
        kwargs.setdefault("source_ids", ["v8-test"])
        return self.memories.record_observation(content, **kwargs)

    def promote(self, content: str, *, placement: str = "CORE", **kwargs) -> str:
        mid = self.observe(content, **kwargs)
        self.curation.propose_candidate(mid, actor="worker-1")
        self.curation.validate_and_accept(mid, placement=placement, actor="curation")
        return mid


def make_lab() -> V8Lab:
    root = pathlib.Path(tempfile.mkdtemp(prefix="v8-test-"))
    return V8Lab(root)


@pytest.fixture
def lab() -> V8Lab:
    return make_lab()
