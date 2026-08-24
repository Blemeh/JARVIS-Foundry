"""Live GitHub research connector — bounded read contract proofs."""

from __future__ import annotations

import json

import pytest

from jarvis_foundry.connectors.contract import (
    ConnectorRequest,
    validate_connector_spec,
)
from jarvis_foundry.connectors.evidence import (
    attempts_for_mission,
    record_attempt,
)
from jarvis_foundry.connectors.github_research import (
    GitHubResearchConnector,
    GitHubResearchError,
)
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.control.redaction import contains_secret_like


def _transport(status: int = 200, payload: dict | None = None):
    body = json.dumps(payload or {"ok": True}).encode("utf-8")

    def _fetch(endpoint: str) -> tuple[int, bytes]:
        if status == 0:
            raise TimeoutError(endpoint)
        return status, body

    return _fetch, body


def test_spec_satisfies_canonical_contract_and_only_declares_reads() -> None:
    spec = GitHubResearchConnector().spec
    assert validate_connector_spec(spec) is None
    assert spec.actions, "research connector must declare actions"
    for semantic in spec.actions.values():
        assert semantic == "read", "research surface must be read-only"


def test_successful_read_returns_document_and_hashed_evidence() -> None:
    fetch, body = _transport(payload={"login": "someone"})
    connector = GitHubResearchConnector(api_transport=fetch)
    attempt, document = connector.read_api("github.repo.read", "/user")
    assert attempt.outcome == "COMPLETED"
    assert document == {"login": "someone"}
    import hashlib
    expected = hashlib.sha256(body).hexdigest()
    assert attempt.after_state["response_hash"] == expected
    assert attempt.provider_ref == "https://api.github.com/user"
    assert attempt.cost == {"requests": 1}


def test_not_found_maps_to_failed_with_http_code() -> None:
    fetch, _ = _transport(status=404)
    connector = GitHubResearchConnector(api_transport=fetch)
    attempt, document = connector.read_api("github.repo.read", "/repos/x/y")
    assert attempt.outcome == "FAILED"
    assert attempt.error_code == "http_404"
    assert document is None


def test_timeout_is_unknown_never_completed() -> None:
    def fetch(endpoint: str) -> tuple[int, bytes]:
        raise TimeoutError("lost")

    connector = GitHubResearchConnector(api_transport=fetch)
    attempt, document = connector.read_api("github.issue.read", "/issues")
    assert attempt.outcome == "FAILED"
    assert document is None


def test_malformed_json_is_refused() -> None:
    connector = GitHubResearchConnector(
        api_transport=lambda endpoint: (200, b"{not-json"))
    attempt, document = connector.read_api("github.commit.read", "/commits")
    assert attempt.outcome == "FAILED"
    assert attempt.error_code == "malformed_response"
    assert document is None


def test_undeclared_action_and_foreign_endpoint_fail_closed() -> None:
    connector = GitHubResearchConnector()
    with pytest.raises(GitHubResearchError):
        connector.read_api("github.admin.delete", "/whatever")
    with pytest.raises(GitHubResearchError):
        connector.read_api("github.repo.read", "https://evil.example/user")


def test_doc_read_requires_https_and_allow_listed_hosts() -> None:
    connector = GitHubResearchConnector(allowed_doc_hosts=("docs.example",))
    with pytest.raises(GitHubResearchError):
        connector.read_document("http://docs.example/guide")
    with pytest.raises(GitHubResearchError):
        connector.read_document("https://unlisted.example/guide")
    # real happy path through a stub transport:
    doc_connector = GitHubResearchConnector(
        doc_transport=lambda url: (200, b"<html>guide</html>"),
        allowed_doc_hosts=("docs.example",),
    )
    attempt, body = doc_connector.read_document("https://docs.example/guide")
    assert attempt.outcome == "COMPLETED"
    assert body == b"<html>guide</html>"
    assert attempt.provider_ref == "https://docs.example/guide"


def test_identity_probe_reports_rate_limit_without_exposing_secrets() -> None:
    payloads = {
        "/user": {"login": "octocat"},
        "/rate_limit": {"resources": {"core": {
            "limit": 5000, "remaining": 4999, "reset": 1787599162}}},
    }

    def fetch(endpoint: str) -> tuple[int, bytes]:
        return 200, json.dumps(payloads[endpoint]).encode("utf-8")

    probe = GitHubResearchConnector(api_transport=fetch).identity_probe()
    assert probe["authenticated_login"] == "octocat"
    assert probe["rate_limit"]["remaining"] == 4999
    serialized = json.dumps(probe)
    assert not contains_secret_like(serialized)


def test_attempts_record_into_canonical_attempt_evidence(tmp_path) -> None:
    fetch, _ = _transport(payload={"full_name": "x/y"})
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    connector = GitHubResearchConnector(api_transport=fetch)
    attempt, _document = connector.read_api("github.repo.read", "/repos/x/y")
    record_attempt(
        database, attempt,
        mission_id="mission-research-proof",
        resource_id="github:x/y",
        effect_ticket_id="read-only-no-ticket",
    )
    stored = attempts_for_mission(database, "mission-research-proof")
    assert len(stored) == 1
    assert stored[0]["action_kind"] == "github.repo.read"
    assert stored[0]["outcome"] == "COMPLETED"


def test_request_validation_accepts_read_request_shape() -> None:
    from jarvis_foundry.connectors.contract import validate_connector_request
    spec = GitHubResearchConnector().spec
    request = ConnectorRequest(
        connector_id=spec.connector_id,
        action_kind="github.repo.read",
        target_ref="/user",
        idempotency_key="probe:1",
        payload_hash="a" * 64,
    )
    assert validate_connector_request(spec, request) is None
