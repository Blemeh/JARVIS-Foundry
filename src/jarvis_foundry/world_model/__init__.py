"""V9 World Model & Proactive Intelligence (Lane 3)."""
from jarvis_foundry.world_model.facade import WorldModel
from jarvis_foundry.world_model.read_model import project_world_intelligence
from jarvis_foundry.world_model.connector import (
    scan_and_connect,
    detect_surprises,
    reconcile_outcome_learning,
    writeback_mission_outcome,
)
from jarvis_foundry.world_model.research_handoff import handoff_to_research, consume_research_result
from jarvis_foundry.world_model.procedures import detect_procedures

__all__ = ["WorldModel", "project_world_intelligence", "scan_and_connect", "detect_surprises", "reconcile_outcome_learning", "writeback_mission_outcome", "handoff_to_research", "consume_research_result", "detect_procedures"]
