"""V5-13 disposable three-connector mission harness."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..control.broker import emergency_stop_reason, matching_veto_reason
from ..database import FoundryDatabase
from ..events import Event, payload_hash
from .browser import BrowserConnector
from .evidence import record_attempt
from .git import GitConnector
from .http import HttpServiceConnector
from .lineage import record_lineage


class ComposedMissionHarness:
    """HTTP observation -> Git candidate -> browser verification.

    Every external step is still performed by its canonical connector. The
    harness only supplies mission sequencing and append-only lineage; it is
    not a release controller or a second effect journal.
    """

    def __init__(
        self,
        database: FoundryDatabase,
        *,
        http: HttpServiceConnector,
        git: GitConnector,
        browser: BrowserConnector,
        worktree: str | Path,
        remote: str | Path,
    ) -> None:
        self.database = database
        self.http = http
        self.git = git
        self.browser = browser
        self.worktree = Path(worktree)
        self.remote = Path(remote)

    def _guard(self, mission_id: str, *, stop_path: str | Path | None) -> str | None:
        stop = emergency_stop_reason(self.database, stop_path)
        if stop:
            return stop
        veto = matching_veto_reason(self.database, {
            mission_id, "http.read", "git.push", "browser.execute",
            "conn-http-v5", "conn-git-v5", "conn-browser-v5",
        })
        return veto

    def _completed(self, mission_id: str) -> dict[str, Any] | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM events WHERE event_type = "
                "'composed.mission.completed' AND json_extract(payload_json, "
                "'$.mission_id') = ? ORDER BY sequence DESC LIMIT 1",
                (mission_id,),
            ).fetchone()
        return None if row is None else json.loads(row[0])

    def run(self, mission_id: str, *, stop_path: str | Path | None = None) -> dict[str, Any]:
        if not mission_id or not mission_id.strip():
            raise ValueError("mission_id is required")
        prior = self._completed(mission_id)
        if prior is not None:
            return {**prior, "status": "REPLAYED", "replayed": True}
        blocked = self._guard(mission_id, stop_path=stop_path)
        if blocked:
            return {"mission_id": mission_id, "status": "BLOCKED", "reason": blocked}

        http_attempt, document = self.http.read_document("/state")
        record_attempt(
            self.database, http_attempt, mission_id=mission_id,
            resource_id="resource-http", effect_ticket_id=f"effect-http:{mission_id}",
        )
        if http_attempt.outcome != "COMPLETED" or document is None:
            return {
                "mission_id": mission_id, "status": "BLOCKED",
                "reason": f"HTTP observation {http_attempt.outcome.lower()}: {http_attempt.error_code}",
            }
        http_lineage = record_lineage(
            self.database, resource_id="resource-http", mission_id=mission_id,
            connector_request_id=f"http-read:{mission_id}", effect_ticket_id=f"effect-http:{mission_id}",
            external_object_id=f"http-state:{http_attempt.after_state['response_hash']}",
            evidence_refs=(f"attempt:{http_attempt.attempt_id}",), outcome="COMPLETED",
            source_refs=("http:/state",),
        )
        blocked = self._guard(mission_id, stop_path=stop_path)
        if blocked:
            return {"mission_id": mission_id, "status": "BLOCKED", "reason": blocked}

        candidate = f"observed-count={int(document.get('count', 0) or 0)}\n"
        head = self.git.create_commit(
            self.worktree, {f"missions/{mission_id}.txt": candidate},
            f"compose {mission_id}",
        )
        pushed = self.git.push(
            action_id=f"compose-git:{mission_id}", worktree=self.worktree,
            remote=self.remote, branch="main", expected_head=head,
        )
        if pushed.get("outcome") != "COMPLETED":
            return {"mission_id": mission_id, "status": "BLOCKED", "reason": f"Git push {pushed.get('outcome')}"}
        git_lineage = record_lineage(
            self.database, resource_id="resource-git", mission_id=mission_id,
            connector_request_id=f"git-push:{mission_id}", effect_ticket_id=f"effect-git:{mission_id}",
            external_object_id=f"git:{pushed['remote_head']}",
            evidence_refs=(f"git-action:{pushed['action_key']}",), outcome="COMPLETED",
            source_refs=(f"git:{self.remote}",),
        )
        blocked = self._guard(mission_id, stop_path=stop_path)
        if blocked:
            return {"mission_id": mission_id, "status": "BLOCKED", "reason": blocked}

        target = self.browser.discover_target(target_id="counter-form", role="button", name="Apply")
        browser_attempt = self.browser.execute(
            target, action=target.action, params={"mission_id": mission_id},
            idempotency_key=f"compose-browser:{mission_id}",
        )
        if browser_attempt.outcome == "UNKNOWN":
            browser_attempt = self.browser.reconcile(f"compose-browser:{mission_id}")
        record_attempt(
            self.database, browser_attempt, mission_id=mission_id,
            resource_id="resource-browser", effect_ticket_id=f"effect-browser:{mission_id}",
        )
        if browser_attempt.outcome != "COMPLETED":
            return {"mission_id": mission_id, "status": "BLOCKED", "reason": f"Browser {browser_attempt.outcome}"}
        browser_lineage = record_lineage(
            self.database, resource_id="resource-browser", mission_id=mission_id,
            connector_request_id=f"browser:{mission_id}", effect_ticket_id=f"effect-browser:{mission_id}",
            external_object_id=browser_attempt.provider_ref,
            evidence_refs=(f"attempt:{browser_attempt.attempt_id}",), outcome="COMPLETED",
            source_refs=("browser:counter-form",),
        )
        payload = {
            "mission_id": mission_id, "status": "COMPLETED",
            "git_head": pushed["remote_head"], "browser_provider_ref": browser_attempt.provider_ref,
            "lineage_ids": [http_lineage.lineage_id, git_lineage.lineage_id, browser_lineage.lineage_id],
            "connector_classes": ["http_service", "git", "browser"],
        }
        self.database.append(Event.create(
            "composed.mission.completed", payload,
            source_type="supervisor", source_id="v5-composed-mission",
            correlation_id=mission_id,
            idempotency_key=f"composed.mission.completed:{mission_id}:{payload_hash(payload)}",
        ))
        return payload


__all__ = ["ComposedMissionHarness"]
