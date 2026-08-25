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


def _doc_response(status: int = 200, body: bytes = b"",
                  headers: dict | None = None):
    from jarvis_foundry.connectors.github_research import DocumentResponse
    return DocumentResponse(status=status, headers=headers or {}, body=body)


def test_doc_read_requires_https_and_allow_listed_hosts() -> None:
    connector = GitHubResearchConnector(allowed_doc_hosts=("docs.example",))
    with pytest.raises(GitHubResearchError):
        connector.read_document("http://docs.example/guide")
    with pytest.raises(GitHubResearchError):
        connector.read_document("https://unlisted.example/guide")
    # real happy path through a stub transport:
    doc_connector = GitHubResearchConnector(
        doc_transport=lambda url: _doc_response(body=b"<html>guide</html>"),
        allowed_doc_hosts=("docs.example",),
    )
    attempt, content = doc_connector.read_document("https://docs.example/guide")
    assert attempt.outcome == "COMPLETED"
    assert content is not None
    assert content.text == "<html>guide</html>"
    assert content.final_url == "https://docs.example/guide"
    assert content.redirects == ()
    assert attempt.provider_ref == "https://docs.example/guide"


def test_same_host_redirect_is_followed_and_recorded() -> None:
    def transport(url: str):
        if url == "https://docs.example/a":
            return _doc_response(
                status=302, headers={"Location": "/b"})
        assert url == "https://docs.example/b"
        return _doc_response(body=b"final")

    connector = GitHubResearchConnector(
        doc_transport=transport, allowed_doc_hosts=("docs.example",))
    attempt, content = connector.read_document("https://docs.example/a")
    assert attempt.outcome == "COMPLETED"
    assert content.final_url == "https://docs.example/b"
    assert content.redirects == ("https://docs.example/a",)
    assert content.raw_sha256


def test_allow_listed_cross_host_redirect_is_followed() -> None:
    calls = []

    def transport(url: str):
        calls.append(url)
        if url.endswith("/old"):
            return _doc_response(
                status=301, headers={"Location": "https://mirror.example/new"})
        return _doc_response(body=b"mirrored")

    connector = GitHubResearchConnector(
        doc_transport=transport,
        allowed_doc_hosts=("docs.example", "mirror.example"))
    attempt, content = connector.read_document("https://docs.example/old")
    assert attempt.outcome == "COMPLETED"
    assert content.final_url == "https://mirror.example/new"


def test_disallowed_host_redirect_is_rejected_without_fetching() -> None:
    def transport(url: str):
        if url.endswith("/old"):
            return _doc_response(
                status=302, headers={"Location": "https://evil.example/x"})
        raise AssertionError(f"disallowed host fetched: {url}")

    connector = GitHubResearchConnector(
        doc_transport=transport, allowed_doc_hosts=("docs.example",))
    attempt, content = connector.read_document("https://docs.example/old")
    assert attempt.outcome == "FAILED"
    assert attempt.error_code == "redirect_policy_rejected"
    assert content is None
    assert len(attempt.error_code) > 0


def test_same_host_policy_blocks_cross_host_when_no_allowlist() -> None:
    def transport(url: str):
        return _doc_response(
            status=301, headers={"Location": "https://other.example/y"})

    connector = GitHubResearchConnector(doc_transport=transport)
    attempt, content = connector.read_document("https://docs.example/z")
    assert attempt.outcome == "FAILED"
    assert attempt.error_code == "redirect_policy_rejected"
    assert content is None


def test_http_downgrade_redirect_is_rejected() -> None:
    fetched = []

    def transport(url: str):
        fetched.append(url)
        return _doc_response(
            status=302, headers={"Location": "http://docs.example/insecure"})

    connector = GitHubResearchConnector(
        doc_transport=transport, allowed_doc_hosts=("docs.example",))
    attempt, content = connector.read_document("https://docs.example/secure")
    assert attempt.outcome == "FAILED"
    assert attempt.error_code == "redirect_policy_rejected"
    # the downgraded target was never fetched
    assert fetched == ["https://docs.example/secure"]


def test_redirect_loop_fails_bounded() -> None:
    def transport(url: str):
        target = "/b" if url.endswith("/a") else "/a"
        return _doc_response(status=302, headers={"Location": target})

    connector = GitHubResearchConnector(
        doc_transport=transport, allowed_doc_hosts=("docs.example",))
    attempt, content = connector.read_document("https://docs.example/a")
    assert attempt.outcome == "FAILED"
    assert attempt.error_code == "redirect_loop"
    assert content is None


def test_secret_like_documents_are_redacted_never_persisted() -> None:
    payload = {"full_name": "x/y", "custom_properties": {
        "token": "SUPERSECRETVALUE123"}}
    connector = GitHubResearchConnector(
        api_transport=lambda endpoint: (
            200, json.dumps(payload).encode("utf-8")))
    attempt, document = connector.read_api("github.repo.read", "/repos/x/y")
    assert attempt.outcome == "COMPLETED"
    assert document["full_name"] == "x/y"
    redacted_value = document["custom_properties"]["token"]
    assert redacted_value == "[REDACTED]"
    assert attempt.after_state.get("redacted") is True
    # the raw value never appears in the durable-safe evidence; the
    # canonical scanner flags assignment SHAPES (including redacted
    # ones), so the value-level check is what matters here:
    evidence = json.dumps(attempt.to_evidence())
    assert "SUPERSECRET" not in evidence
    assert "SUPERSECRET" not in json.dumps(document)


def test_string_embedded_assignments_are_redacted_too() -> None:
    payload = {"note": "authorization: Bearer abcdef123456"}
    connector = GitHubResearchConnector(
        api_transport=lambda endpoint: (
            200, json.dumps(payload).encode("utf-8")))
    attempt, document = connector.read_api("github.repo.read", "/repos/x/y")
    assert attempt.outcome == "COMPLETED"
    assert "abcdef" not in json.dumps(document)
    assert attempt.after_state.get("redacted") is True


def test_document_secret_material_is_redacted_raw_hash_only() -> None:
    raw = (
        b"<html><body>"
        b"API_KEY=sk-live-abcdef1234567890 "
        b"token=ghp_SUPERSECRETVALUE "
        b"Authorization: Bearer abc.def.ghi"
        b"</body></html>"
    )

    connector = GitHubResearchConnector(
        doc_transport=lambda url: _doc_response(body=raw),
        allowed_doc_hosts=("docs.example",))
    attempt, content = connector.read_document("https://docs.example/config")
    assert attempt.outcome == "COMPLETED"
    # raw secret material must not appear in returned content or evidence
    for secret in ("sk-live-abcdef1234567890", "ghp_SUPERSECRETVALUE",
                   "abc.def.ghi"):
        assert secret not in content.text
        assert secret not in json.dumps(attempt.to_evidence())
    assert "[REDACTED]" in content.text
    assert attempt.after_state["redacted"] is True
    # provenance remains possible WITHOUT storing the raw secret:
    import hashlib
    assert content.raw_sha256 == hashlib.sha256(raw).hexdigest()
    assert attempt.after_state["raw_sha256"] == content.raw_sha256
    assert raw not in json.dumps(attempt.to_evidence()).encode()


def test_document_nested_html_assignment_is_redacted() -> None:
    raw = (b"<html><meta name='desc' content='password=hunter2secret'>"
           b"<pre>api_key=nested-secret-999</pre></html>")
    connector = GitHubResearchConnector(
        doc_transport=lambda url: _doc_response(body=raw))
    _attempt, content = connector.read_document("https://docs.example/page")
    assert "hunter2secret" not in content.text
    assert "nested-secret-999" not in content.text
    # Sanitization strength == the canonical JARVIS secret-pattern
    # family (control.redaction); this module does not fork a second
    # scanner. Exotic quoting around assignments inside HTML prose is
    # outside the documented canonical guarantee.


def test_normal_documentation_text_is_unchanged_by_sanitization() -> None:
    raw = "# Install Guide\npip install jarvis-foundry\nSee docs at /guide."
    connector = GitHubResearchConnector(
        doc_transport=lambda url: _doc_response(body=raw.encode("utf-8")))
    attempt, content = connector.read_document("https://docs.example/install")
    assert attempt.after_state.get("redacted") is False
    assert content.text == raw


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
