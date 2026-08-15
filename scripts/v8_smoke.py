"""V8 smoke test — migration v33 + Tier C core path."""
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.hybrid_memory import (ContextCompiler, CoreMemoryRegistry,
                                          CurationPipeline, RecallEngine,
                                          TierCMemoryStore)

db = FoundryDatabase(pathlib.Path(tempfile.mkdtemp()) / "t.db")
version = db.initialize()
print("schema version:", version)
assert version == 33, version

mem = TierCMemoryStore(db)
mid = mem.record_observation(
    "PYTHONPATH= prefix required in Hermes sessions",
    mission_id="M-1", source_ids=["s1"])
cur = CurationPipeline(db)
cur.propose_candidate(mid, actor="w")
cur.validate_and_accept(mid, placement="CORE", actor="curation")
assert mem.get(mid)["state"] == "CORE", mem.get(mid)["state"]
print("accepted state:", mem.get(mid)["state"])

blk = CoreMemoryRegistry(db)
blk.ensure_defaults()
print("blocks:", sorted(blk.labels()))
assert "owner" in blk.labels() and "memory_index" in blk.labels()

rec = RecallEngine(db)
hits = rec.recall("hermes python path prefix")
print("recall:", [(h["memory_id"][:6], h["relevance"]) for h in hits])
assert hits and "PYTHONPATH" in hits[0]["content"]

comp = ContextCompiler(db)
pkg = comp.compile("M-1", "executive", "hermes python path", budget_chars=2000)
print("sections:", list(pkg.sections.keys()))
print("used:", pkg.used_chars, "dropped:", pkg.dropped)
assert "mandatory_governance" in pkg.sections
assert "recalled_experience" in pkg.sections

# verifier isolation
vpkg = comp.compile("M-1", "verifier", "hermes python path", budget_chars=2000)
assert vpkg.section("recalled_experience") is None, "verifier must not get Tier C recall"
print("verifier isolation: OK")

# event-derived rebuild: projections rebuild from the ledger
db.rebuild_projections()
assert mem.get(mid)["state"] == "CORE"
assert mem.revisions(mid), "revision history must survive rebuild"
print("event-derived rebuild: OK")
print("SMOKE PASS")
