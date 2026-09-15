"""Truth-driven owner status lines from Command Center snapshots."""
from __future__ import annotations

from typing import Any


def render_owner_status(snapshot: dict[str, Any], lang: str) -> str:
    language = "sq" if str(lang or "en").lower().startswith("sq") else "en"
    controls = snapshot.get("controls") if isinstance(snapshot.get("controls"), dict) else {}
    missions = snapshot.get("missions") if isinstance(snapshot.get("missions"), list) else []
    decision_queue = snapshot.get("decision_queue") if isinstance(snapshot.get("decision_queue"), dict) else {}
    open_attention = decision_queue.get("open") if isinstance(decision_queue.get("open"), list) else []
    stopped = bool(controls.get("stopped") or snapshot.get("emergency_stop"))
    paused = bool(controls.get("paused"))
    active = [
        mission for mission in missions
        if str(mission.get("state") or "") not in {"ACCEPTED", "CANCELLED", "FAILED", "COMPLETED"}
    ]
    current = active[0] if active else (missions[0] if missions else None)
    objective = ""
    state = ""
    next_action = ""
    if isinstance(current, dict):
        objective = str(current.get("objective") or current.get("title") or current.get("mission_id") or "").strip()
        state = str(current.get("state") or "").strip()
        next_action = str(current.get("next_action") or "").strip()
    blocker = ""
    if open_attention:
        first = open_attention[0]
        if isinstance(first, dict):
            blocker = str(first.get("summary") or first.get("classification") or "").strip()
    if language == "sq":
        if stopped:
            hold = "STOP është aktiv."
        elif paused:
            hold = "Pauza e pronarit është aktive."
        else:
            hold = "Nuk ka STOP/pauzë aktive."
        if not current:
            work = "Asnjë mision aktiv."
        else:
            work = f"Misioni: {objective or 'UNKNOWN'}. Gjendja: {state or 'UNKNOWN'}."
        wait = f"Bllokuesi: {blocker}." if blocker else "Nuk ka bllokues të hapur."
        nxt = f"Hapi tjetër: {next_action}." if next_action else "Hapi tjetër: UNKNOWN."
        return " ".join((hold, work, wait, nxt))
    if stopped:
        hold = "STOP is active."
    elif paused:
        hold = "Owner pause is active."
    else:
        hold = "No STOP or pause is active."
    if not current:
        work = "No active mission."
    else:
        work = f"Mission: {objective or 'UNKNOWN'}. State: {state or 'UNKNOWN'}."
    wait = f"Blocker: {blocker}." if blocker else "No open blocker."
    nxt = f"Next: {next_action}." if next_action else "Next: UNKNOWN."
    return " ".join((hold, work, wait, nxt))
