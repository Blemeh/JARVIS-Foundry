"""V10-A bounded browser and HTML research adapters.

This module adds the missing real-web primitive without adding a crawler,
browser profile manager, or research authority.  Playwright owns the local
browser process; JARVIS owns the URL allow-list, target identity, STOP gate,
bounded output, and receipt shape.  Trafilatura parses captured HTML bytes;
its output is always marked ``DATA_ONLY`` and is never treated as a
directive, goal, target, or acceptance decision.

The adapters are deliberately synchronous.  Playwright creates one isolated
browser/context per session, which keeps the lane deterministic and avoids
sharing a persistent profile or a Playwright instance across threads.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import re
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import urlsplit

from ..clock import isoformat
from ..control.redaction import contains_secret_like, redact_text
from ..events import canonical_json
from .browser import BrowserTarget
from .contract import ConnectorAttempt, ConnectorSpec
from .safety import (
    SafetyBoundaryError,
    sanitize_external_content,
    validate_redirect_chain,
)


MAX_SOURCE_BYTES = 2 * 1024 * 1024
MAX_EXTRACTED_CHARS = 32 * 1024
MAX_TARGET_ID_LENGTH = 128
MAX_TARGET_NAME_LENGTH = 512
MAX_URL_LENGTH = 2048
MAX_REDIRECTS = 5
MAX_SESSION_STATE_BYTES = 8 * 1024 * 1024
MAX_SESSION_METADATA_BYTES = 16 * 1024
MAX_ACTION_REQUESTS = 8
MAX_DOWNLOAD_BYTES = 32 * 1024 * 1024
MAX_UPLOAD_BYTES = 32 * 1024 * 1024
MAX_STEP_COUNT = 16
INSPECT_MAX_ELEMENTS = 256
_LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
_ACCESSIBILITY_ROLE_UNSET = object()
_ACCESSIBILITY_ROLES = {
    ("a", ""): "link",
    ("button", ""): "button",
    ("select", ""): "combobox",
    ("textarea", ""): "textbox",
    ("input", ""): "textbox",
    ("input", "text"): "textbox",
    ("input", "search"): "searchbox",
    ("input", "email"): "textbox",
    ("input", "url"): "textbox",
    ("input", "tel"): "textbox",
    ("input", "number"): "spinbutton",
    ("input", "checkbox"): "checkbox",
    ("input", "radio"): "radio",
    ("input", "range"): "slider",
    ("input", "file"): "button",
    ("input", "submit"): "button",
    ("input", "button"): "button",
    ("input", "reset"): "button",
    ("input", "password"): "",
}
_IDENTITY = re.compile(r"^[A-Za-z][A-Za-z0-9_.:/-]*$")
_OPAQUE_REFERENCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{2,255}$")


def _release_local_browser_runtime() -> tuple[Path | None, str | None]:
    """Reject a silent fallback from an immutable release to host browsers."""
    prefix = Path(sys.prefix).resolve()
    if prefix.name.lower() != ".venv":
        return None, None
    release_root = prefix.parent
    if not (release_root / "RELEASE.json").is_file():
        return None, None
    expected = (release_root / ".pw").resolve()
    configured = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if configured:
        if Path(configured).expanduser().resolve() != expected:
            return None, "browser_path_mismatch"
    else:
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(expected)
    if not expected.is_dir():
        return expected, "browser_binary_missing"
    return expected, None


class WebResearchError(ValueError):
    """A web target, browser operation, or extraction crossed a boundary."""


class BrowserTargetError(WebResearchError):
    """A structural/accessibility target is missing, ambiguous, or stale."""


@dataclass(frozen=True, slots=True)
class BrowserPageIdentity:
    """Hash-bound identity and receipt for one main-document navigation."""

    requested_url: str
    final_url: str
    status: int
    content_type: str
    source_sha256: str
    source_bytes: int
    title: str
    document_id: str
    page_revision: str
    structural_hash: str
    redirects: tuple[str, ...]
    fetched_at: str
    target_count: int

    def to_evidence(self) -> dict[str, Any]:
        return {
            "requested_url": self.requested_url,
            "final_url": self.final_url,
            "status": self.status,
            "content_type": self.content_type,
            "source_sha256": self.source_sha256,
            "source_bytes": self.source_bytes,
            "title": self.title,
            "document_id": self.document_id,
            "page_revision": self.page_revision,
            "structural_hash": self.structural_hash,
            "redirects": list(self.redirects),
            "fetched_at": self.fetched_at,
            "target_count": self.target_count,
            "authority": "DATA_ONLY",
        }


@dataclass(frozen=True, slots=True)
class BrowserReadResult:
    """A browser read with transient HTML bytes and durable-safe evidence."""

    attempt: ConnectorAttempt
    page: BrowserPageIdentity | None
    source_bytes: bytes | None = None

    @property
    def success(self) -> bool:
        return self.attempt.outcome == "COMPLETED" and self.page is not None

    def to_evidence(self) -> dict[str, Any]:
        return {
            "attempt": self.attempt.to_evidence(),
            "page": self.page.to_evidence() if self.page else None,
        }


@dataclass(frozen=True, slots=True)
class BrowserSessionStatus:
    """Safe observation of one bound browser session.

    Storage state is deliberately not exposed.  The state file is consumed
    only by Playwright; durable receipts carry this reference and its hash,
    never cookies, headers, tokens, or credential material.
    """

    state: str
    session_ref: str
    profile_id: str
    site_origin: str
    owner_scope: str
    restored: bool
    storage_state_present: bool
    checked_at: str
    current_url: str | None = None
    auth_marker_found: bool | None = None
    state_sha256: str | None = None
    reason: str | None = None

    @property
    def authenticated(self) -> bool:
        return self.state == "AUTHENTICATED"

    def to_evidence(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "session_ref": self.session_ref,
            "profile_id": self.profile_id,
            "site_origin": self.site_origin,
            "owner_scope": self.owner_scope,
            "restored": self.restored,
            "storage_state_present": self.storage_state_present,
            "checked_at": self.checked_at,
            "current_url": _safe_url_ref(self.current_url)
            if self.current_url else None,
            "auth_marker_found": self.auth_marker_found,
            "state_sha256": self.state_sha256,
            "reason": redact_text(str(self.reason or ""))[:300] or None,
        }


@dataclass(frozen=True, slots=True)
class PlaywrightTarget(BrowserTarget):
    """The existing structural target contract plus web page identity."""

    document_id: str
    final_url: str
    page_revision: str
    role: str
    name: str
    frame_index: int | None = None

    def to_evidence(self) -> dict[str, Any]:
        evidence = {
            "target_id": self.target_id,
            "document_id": self.document_id,
            "final_url": self.final_url,
            "page_revision": self.page_revision,
            "structural_hash": self.structural_hash,
            "role": self.role,
            "name": self.name,
            "action": self.action,
            "targeting": "accessibility",
        }
        if self.frame_index is not None:
            evidence["frame_index"] = self.frame_index
        return evidence


@dataclass(frozen=True, slots=True)
class HtmlExtractionResult:
    """Source-linked, sanitized Trafilatura output.

    ``text`` is safe for model-visible evidence and Markdown rendering.  The
    raw HTML and raw extracted text are intentionally absent from
    ``to_evidence``; only their hashes cross a durable boundary.
    """

    attempt: ConnectorAttempt
    page: BrowserPageIdentity | None
    text: str
    metadata: dict[str, Any]
    extracted_sha256: str | None
    safe_text_sha256: str | None
    truncated: bool = False

    @property
    def success(self) -> bool:
        return self.attempt.outcome == "COMPLETED" and self.page is not None

    def to_evidence(self) -> dict[str, Any]:
        return {
            "attempt": self.attempt.to_evidence(),
            "page": self.page.to_evidence() if self.page else None,
            "metadata": dict(self.metadata),
            "extracted_sha256": self.extracted_sha256,
            "safe_text_sha256": self.safe_text_sha256,
            "text_chars": len(self.text),
            "truncated": self.truncated,
            "authority": "DATA_ONLY",
        }


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_text(value: str) -> str:
    return _sha256_bytes(value.encode("utf-8"))


def _digest(value: Any) -> str:
    return _sha256_text(canonical_json(value))


def _safe_identity(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WebResearchError(f"{field_name} is required")
    result = value.strip()
    if not _IDENTITY.fullmatch(result):
        raise WebResearchError(f"{field_name} is not a safe identity")
    if len(result) > MAX_TARGET_ID_LENGTH:
        raise WebResearchError(f"{field_name} exceeds maximum length")
    return result


def _safe_opaque_reference(value: object, field_name: str) -> str:
    """Validate a broker/session reference without accepting secret material."""

    if not isinstance(value, str) or not value.strip():
        raise WebResearchError(f"{field_name} is required")
    result = value.strip()
    if len(result) > 256 or not _OPAQUE_REFERENCE.fullmatch(result):
        raise WebResearchError(f"{field_name} must be an opaque reference")
    if contains_secret_like(result) or re.search(
        r"(?i)(bearer\s|sk-[a-z0-9]{8,}|-----begin|token=|secret=)", result,
    ):
        raise WebResearchError(f"{field_name} contains secret-like material")
    return result


def _normalize_auth_marker(
    value: Mapping[str, Any] | Sequence[str] | None,
) -> tuple[str, str] | None:
    if value is None:
        return None
    if isinstance(value, Mapping):
        role = value.get("role")
        name = value.get("name")
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        if len(value) != 2:
            raise WebResearchError("auth_marker must contain role and name")
        role, name = value[0], value[1]
    else:
        raise WebResearchError("auth_marker must be a role/name mapping")
    if not isinstance(role, str) or not role.strip():
        raise WebResearchError("auth_marker.role is required")
    if not isinstance(name, str) or not name.strip():
        raise WebResearchError("auth_marker.name is required")
    safe_role = _safe_identity(role, "auth_marker.role")
    bounded_name = name.strip()
    if len(bounded_name) > MAX_TARGET_NAME_LENGTH or any(
        ord(char) < 0x20 for char in bounded_name
    ) or contains_secret_like(bounded_name):
        raise WebResearchError("auth_marker.name is invalid")
    return safe_role, bounded_name


def _normalize_prefixes(value: Sequence[str] | None, field_name: str) -> tuple[str, ...]:
    prefixes: list[str] = []
    for item in value or ():
        if not isinstance(item, str) or not item.strip():
            raise WebResearchError(f"{field_name} must contain non-empty strings")
        prefix = item.strip()
        if len(prefix) > MAX_URL_LENGTH or any(ord(char) < 0x20 for char in prefix):
            raise WebResearchError(f"{field_name} contains an invalid prefix")
        if contains_secret_like(prefix):
            raise WebResearchError(f"{field_name} contains secret-like material")
        prefixes.append(prefix)
    return tuple(dict.fromkeys(prefixes))


def _normalize_hosts(hosts: Sequence[str] | None) -> frozenset[str]:
    normalized: set[str] = set()
    for host in hosts or ():
        if not isinstance(host, str) or not host.strip():
            raise WebResearchError("allowed hosts must be non-empty strings")
        value = host.strip().lower()
        if "://" in value or "/" in value or " " in value:
            raise WebResearchError("allowed hosts must contain hostnames only")
        normalized.add(value)
    return frozenset(normalized)


def _safe_url_ref(value: object) -> str:
    """Return a bounded URL reference safe for failed-attempt evidence."""
    if not isinstance(value, str) or not value or len(value) > MAX_URL_LENGTH:
        return "[INVALID_URL]"
    if value != value.strip() or any(ord(char) < 0x20 for char in value):
        return "[INVALID_URL]"
    if contains_secret_like(value):
        return "[INVALID_URL_SECRET_LIKE]"
    try:
        parsed = urlsplit(value)
        if parsed.username is not None or parsed.password is not None:
            return "[INVALID_URL_CREDENTIALS]"
    except ValueError:
        return "[INVALID_URL]"
    return value


def _origin_of(url: str) -> str:
    parsed = urlsplit(str(url))
    host = (parsed.hostname or "").lower()
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    port = parsed.port
    default_port = (parsed.scheme.lower() == "https" and port == 443) or (
        parsed.scheme.lower() == "http" and port == 80
    )
    suffix = "" if port is None or default_port else f":{port}"
    return f"{parsed.scheme.lower()}://{host}{suffix}"


BROWSER_EXECUTE_ACTIONS = frozenset({
    "click", "fill", "select", "check", "submit", "download", "upload",
})
BROWSER_ACTION_METHODS = frozenset({"POST", "PUT", "PATCH"})


@dataclass(frozen=True, slots=True)
class BrowserExecutionAuthorization:
    """Exact owner grant for one bounded browser mutation target.

    A storage state proves session state, never authorization.  This grant is
    the owner-configured authority that makes ``browser.web.execute``
    unattended-capable for one exact origin: it binds the brokered session
    material, the allowed mutation verbs, and the file-transfer opt-ins.  It
    carries no raw credentials -- only opaque references and a session-state
    path that Playwright consumes.
    """

    authorization_id: str
    site_origin: str
    owner_authorized: bool = False
    dispatch_enabled: bool = False
    allowed_actions: tuple[str, ...] = (
        "click", "fill", "select", "check", "submit",
    )
    allow_downloads: bool = False
    allow_uploads: bool = False
    session_state_path: str | None = None
    profile_id: str = "default"
    owner_scope: str = "owner-local"
    credential_reference: str | None = None
    auth_marker: tuple[str, str] | None = None
    require_authenticated: bool = True
    persist_session: bool = True
    allowed_action_methods: tuple[str, ...] = ("POST",)
    max_action_requests: int = MAX_ACTION_REQUESTS
    allow_insecure_localhost: bool = False
    capability_trust: str = "ESTABLISHED"

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "authorization_id",
            _safe_opaque_reference(self.authorization_id, "authorization_id"),
        )
        origin = self._normalized_origin(self.site_origin)
        object.__setattr__(self, "site_origin", origin)
        actions: list[str] = []
        for item in self.allowed_actions:
            value = str(item).strip().lower()
            if value not in BROWSER_EXECUTE_ACTIONS:
                raise WebResearchError(
                    f"allowed action {value!r} is outside the bounded browser verb set"
                )
            if value not in actions:
                actions.append(value)
        if not actions:
            raise WebResearchError(
                "BrowserExecutionAuthorization requires at least one allowed action"
            )
        if "download" in actions and not self.allow_downloads:
            raise WebResearchError(
                "allowed action download requires allow_downloads=True"
            )
        if "upload" in actions and not self.allow_uploads:
            raise WebResearchError(
                "allowed action upload requires allow_uploads=True"
            )
        object.__setattr__(self, "allowed_actions", tuple(actions))
        methods: list[str] = []
        for item in self.allowed_action_methods:
            value = str(item).strip().upper()
            if value not in BROWSER_ACTION_METHODS:
                raise WebResearchError(
                    "allowed_action_methods must contain only POST/PUT/PATCH"
                )
            if value not in methods:
                methods.append(value)
        if not methods:
            raise WebResearchError(
                "BrowserExecutionAuthorization requires an allowed action method"
            )
        object.__setattr__(self, "allowed_action_methods", tuple(methods))
        if (
            not isinstance(self.max_action_requests, int)
            or isinstance(self.max_action_requests, bool)
            or self.max_action_requests < 1
            or self.max_action_requests > 32
        ):
            raise WebResearchError(
                "max_action_requests must be within [1, 32]"
            )
        object.__setattr__(
            self,
            "profile_id",
            _safe_opaque_reference(self.profile_id, "profile_id"),
        )
        object.__setattr__(
            self,
            "owner_scope",
            _safe_opaque_reference(self.owner_scope, "owner_scope"),
        )
        if self.credential_reference is not None:
            object.__setattr__(
                self,
                "credential_reference",
                _safe_opaque_reference(
                    self.credential_reference, "credential_reference"
                ),
            )
        if self.auth_marker is not None:
            object.__setattr__(
                self,
                "auth_marker",
                _normalize_auth_marker(self.auth_marker),
            )
        if self.session_state_path is not None:
            raw_state = self.session_state_path
            if (
                not isinstance(raw_state, str)
                or not raw_state.strip()
                or any(ord(char) < 0x20 for char in raw_state)
                or len(raw_state) > 4096
            ):
                raise WebResearchError(
                    "session_state_path must be a bounded non-empty path"
                )
            if contains_secret_like(raw_state):
                raise WebResearchError(
                    "session_state_path contains secret-like material"
                )
            object.__setattr__(self, "session_state_path", raw_state.strip())
        for flag in (
            "owner_authorized", "dispatch_enabled", "allow_downloads",
            "allow_uploads", "require_authenticated", "persist_session",
            "allow_insecure_localhost",
        ):
            if not isinstance(getattr(self, flag), bool):
                raise WebResearchError(f"{flag} must be boolean")
        object.__setattr__(
            self,
            "capability_trust",
            str(self.capability_trust or "ESTABLISHED").upper(),
        )

    @staticmethod
    def _normalized_origin(value: object) -> str:
        if not isinstance(value, str) or not value.strip():
            raise WebResearchError("site_origin is required")
        raw = value.strip().rstrip("/")
        try:
            parsed = urlsplit(raw)
            if (
                parsed.scheme.lower() not in {"http", "https"}
                or not parsed.hostname
                or parsed.path not in {"", "/"}
                or parsed.query
                or parsed.fragment
                or parsed.username is not None
                or parsed.password is not None
            ):
                raise ValueError
            _ = parsed.port
        except ValueError as error:
            raise WebResearchError(
                "site_origin must be one exact http(s) origin"
            ) from error
        if contains_secret_like(raw):
            raise WebResearchError("site_origin contains secret-like material")
        return _origin_of(raw)

    @property
    def host(self) -> str:
        return (urlsplit(self.site_origin).hostname or "").lower()

    def allows_origin(self, url: str) -> bool:
        try:
            return _origin_of(url) == self.site_origin
        except ValueError:
            return False

    def refusal(
        self,
        *,
        url: str,
        action: str,
        download: bool = False,
        upload: bool = False,
    ) -> str | None:
        """Return a classified refusal for one attempted mutation, or None."""

        if self.capability_trust == "PROBATION":
            return "probation_capability_irreversible_effect_forbidden"
        if not self.owner_authorized:
            return "owner_authorization_required"
        if not self.dispatch_enabled:
            return "browser_execution_disabled"
        normalized_action = str(action or "").strip().lower()
        if normalized_action not in BROWSER_EXECUTE_ACTIONS:
            return "browser_action_not_supported"
        if normalized_action not in self.allowed_actions:
            return "browser_action_not_authorized"
        if download and not self.allow_downloads:
            return "download_not_authorized"
        if upload and not self.allow_uploads:
            return "upload_not_authorized"
        if not self.allows_origin(str(url)):
            return "browser_origin_not_authorized"
        if self.require_authenticated and self.auth_marker is None:
            return "browser_authorization_auth_marker_required"
        return None

    def session_kwargs(self) -> dict[str, Any]:
        """Return the exact connector session binding for this grant."""

        return {
            "profile_id": self.profile_id,
            "site_origin": self.site_origin,
            "owner_scope": self.owner_scope,
            "session_state_path": self.session_state_path,
            "credential_reference": self.credential_reference,
            "auth_marker": self.auth_marker,
            "persist_session": self.persist_session,
            "allowed_action_methods": tuple(self.allowed_action_methods),
            "max_action_requests": int(self.max_action_requests),
        }


class PlaywrightBrowserConnector:
    """Direct Playwright browser adapter with bounded read/target semantics."""

    spec = ConnectorSpec(
        connector_id="conn-browser-playwright-v10",
        kind="browser_web",
        version="1.0.0",
        actions={
            "browser.web.read": "read",
            "browser.web.inspect": "read",
            "browser.web.wait": "read",
            "browser.web.execute": "execute",
        },
        capabilities={
            "web_read": "PROBING",
            "web_inspect": "PROBING",
            "web_wait": "PROBING",
            "web_execute": "PROBING",
        },
        retry_policy={"max_attempts": 1, "backoff_s": [0]},
        target_kinds=("https_url", "browser_page", "browser_target"),
        idempotency_strategy="provider-key",
        reconciliation_strategy="provider-query",
        supports_cancellation=True,
        cost_metadata={"unit": "local_browser_session", "cost_known": True},
        evidence_fields=(
            "attempt_id", "requested_url", "final_url", "status",
            "redirects", "source_sha256", "page_revision", "outcome",
        ),
    )

    def __init__(
        self,
        *,
        browser_name: str = "chromium",
        headless: bool = True,
        timeout_ms: int = 30_000,
        max_source_bytes: int = MAX_SOURCE_BYTES,
        allowed_hosts: Sequence[str] | None = None,
        allow_insecure_localhost: bool = False,
    ) -> None:
        if browser_name not in {"chromium", "firefox", "webkit"}:
            raise WebResearchError("browser_name must be chromium, firefox, or webkit")
        if not isinstance(timeout_ms, int) or timeout_ms < 100 or timeout_ms > 120_000:
            raise WebResearchError("timeout_ms must be between 100 and 120000")
        if (
            not isinstance(max_source_bytes, int)
            or max_source_bytes < 1024
            or max_source_bytes > MAX_SOURCE_BYTES
        ):
            raise WebResearchError(
                f"max_source_bytes must be between 1024 and {MAX_SOURCE_BYTES}"
            )
        self.browser_name = browser_name
        self.headless = bool(headless)
        self.timeout_ms = timeout_ms
        self.max_source_bytes = max_source_bytes
        self.allowed_hosts = _normalize_hosts(allowed_hosts)
        self.allow_insecure_localhost = bool(allow_insecure_localhost)
        self._health_cache: dict[str, Any] | None = None

    def health(self) -> dict[str, Any]:
        """Report package and binary health without installing anything."""
        if self._health_cache is not None:
            return dict(self._health_cache)
        browser_path, browser_path_error = _release_local_browser_runtime()
        if browser_path_error is not None:
            return {
                "status": "NOT_READY",
                "available": False,
                "browser": self.browser_name,
                "browsers_path": str(browser_path) if browser_path is not None else None,
                "error_code": browser_path_error,
                "detail": (
                    "immutable release browser assets are missing"
                    if browser_path_error == "browser_binary_missing"
                    else "immutable release browser path does not match PLAYWRIGHT_BROWSERS_PATH"
                ),
            }
        browser_version: str | None = None
        try:
            package_version = importlib.metadata.version("playwright")
            from playwright.sync_api import sync_playwright

            with sync_playwright() as playwright:
                browser_type = getattr(playwright, self.browser_name)
                executable = Path(browser_type.executable_path)
                if not executable.is_file():
                    raise FileNotFoundError(str(executable))
                smoke_browser = browser_type.launch(headless=True)
                try:
                    browser_version = smoke_browser.version
                finally:
                    smoke_browser.close()
        except (ImportError, importlib.metadata.PackageNotFoundError) as error:
            return {
                "status": "NOT_READY",
                "available": False,
                "browser": self.browser_name,
                "error_code": "playwright_unavailable",
                "detail": str(error),
            }
        except Exception as error:  # pragma: no cover - platform-specific driver errors
            return {
                "status": "NOT_READY",
                "available": False,
                "browser": self.browser_name,
                "error_code": "playwright_driver_unavailable",
                "detail": str(error)[:300],
            }

        available = executable.is_file()
        result = {
            "status": "READY" if available else "NOT_READY",
            "available": available,
            "browser": self.browser_name,
            "headless": self.headless,
            "playwright_version": package_version,
            "browser_executable": str(executable),
            "browser_version": browser_version,
            "browsers_path": os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or "default",
            "error_code": None if available else "browser_binary_missing",
        }
        if available:
            self._health_cache = dict(result)
        return result

    def session(
        self,
        *,
        stop_check: Callable[[], bool] | None = None,
        allowed_hosts: Sequence[str] | None = None,
        profile_id: str = "default",
        site_origin: str | None = None,
        owner_scope: str = "owner-local",
        session_state_path: str | Path | None = None,
        session_ref: str | None = None,
        credential_reference: str | None = None,
        auth_marker: Mapping[str, Any] | Sequence[str] | None = None,
        expired_url_prefixes: Sequence[str] | None = None,
        expiry_url_prefixes: Sequence[str] | None = None,
        reauthenticate: Callable[["PlaywrightBrowserSession"], bool] | None = None,
        persist_session: bool = True,
        allowed_action_methods: Sequence[str] = ("POST",),
        max_action_requests: int = MAX_ACTION_REQUESTS,
        allow_downloads: bool = False,
        download_root: str | Path | None = None,
        max_download_bytes: int = MAX_DOWNLOAD_BYTES,
        allow_uploads: bool = False,
        upload_root: str | Path | None = None,
        max_upload_bytes: int = MAX_UPLOAD_BYTES,
    ) -> "PlaywrightBrowserSession":
        return PlaywrightBrowserSession(
            self,
            stop_check=stop_check,
            allowed_hosts=(
                self.allowed_hosts
                if allowed_hosts is None else _normalize_hosts(allowed_hosts)
            ),
            profile_id=profile_id,
            site_origin=site_origin,
            owner_scope=owner_scope,
            session_state_path=session_state_path,
            session_ref=session_ref,
            credential_reference=credential_reference,
            auth_marker=auth_marker,
            expired_url_prefixes=(
                expired_url_prefixes
                if expiry_url_prefixes is None else expiry_url_prefixes
            ),
            reauthenticate=reauthenticate,
            persist_session=persist_session,
            allowed_action_methods=allowed_action_methods,
            max_action_requests=max_action_requests,
            allow_downloads=allow_downloads,
            download_root=download_root,
            max_download_bytes=max_download_bytes,
            allow_uploads=allow_uploads,
            upload_root=upload_root,
            max_upload_bytes=max_upload_bytes,
        )

    def read(
        self,
        url: str,
        *,
        stop_check: Callable[[], bool] | None = None,
        allowed_hosts: Sequence[str] | None = None,
    ) -> BrowserReadResult:
        """Navigate to one approved page and return transient source bytes."""
        requested_hash = _digest({"action": "browser.web.read", "url": url})
        key = f"browser-read:{_digest(url)}"
        try:
            self._validate_url(
                url,
                allowed_hosts=(
                    self.allowed_hosts
                    if allowed_hosts is None else _normalize_hosts(allowed_hosts)
                ),
            )
        except WebResearchError as error:
            return BrowserReadResult(
                attempt=self._attempt(
                    action_kind="browser.web.read",
                    url=str(url),
                    idempotency_key=key,
                    payload_hash=requested_hash,
                    outcome="FAILED",
                    error_code="invalid_url",
                ),
                page=None,
            )
        if stop_check is not None and stop_check():
            return BrowserReadResult(
                attempt=self._attempt(
                    action_kind="browser.web.read",
                    url=url,
                    idempotency_key=key,
                    payload_hash=requested_hash,
                    outcome="CANCELLED",
                    error_code="stop_requested",
                ),
                page=None,
            )
        health = self.health()
        if not health.get("available"):
            return BrowserReadResult(
                attempt=self._attempt(
                    action_kind="browser.web.read",
                    url=url,
                    idempotency_key=key,
                    payload_hash=requested_hash,
                    outcome="UNKNOWN",
                    error_code=str(health.get("error_code") or "browser_unavailable"),
                    after_state={"health": health},
                ),
                page=None,
            )
        try:
            with self.session(stop_check=stop_check, allowed_hosts=allowed_hosts) as session:
                return session.navigate(url)
        except ImportError as error:
            return BrowserReadResult(
                attempt=self._attempt(
                    action_kind="browser.web.read",
                    url=url,
                    idempotency_key=key,
                    payload_hash=requested_hash,
                    outcome="UNKNOWN",
                    error_code="playwright_unavailable",
                    after_state={"detail": str(error)[:300]},
                ),
                page=None,
            )
        except Exception as error:  # pragma: no cover - driver/platform-specific
            return BrowserReadResult(
                attempt=self._attempt(
                    action_kind="browser.web.read",
                    url=url,
                    idempotency_key=key,
                    payload_hash=requested_hash,
                    outcome="UNKNOWN",
                    error_code="browser_runtime_error",
                    after_state={"detail": redact_text(str(error))[:300]},
                ),
                page=None,
            )

    def _validate_url(
        self,
        url: str,
        *,
        allowed_hosts: frozenset[str],
        expected_host: str | None = None,
    ) -> str:
        if not isinstance(url, str) or not url or url != url.strip():
            raise WebResearchError("URL must be a trimmed string")
        if len(url) > MAX_URL_LENGTH:
            raise WebResearchError("URL exceeds maximum length")
        if any(ord(char) < 0x20 for char in url):
            raise WebResearchError("URL contains control characters")
        if any(char.isspace() for char in url):
            raise WebResearchError("URL contains whitespace")
        if contains_secret_like(url):
            raise WebResearchError("URL contains secret-like material")
        try:
            parsed = urlsplit(url)
            host = (parsed.hostname or "").lower()
            # Force validation of malformed ports instead of deferring it to
            # the browser driver.
            _ = parsed.port
        except ValueError as error:
            raise WebResearchError("URL is malformed") from error
        if not host or parsed.username is not None or parsed.password is not None:
            raise WebResearchError("URL host or credentials are invalid")
        if parsed.fragment:
            raise WebResearchError("URL fragments are not accepted as page identity")
        if parsed.scheme.lower() != "https":
            if not (
                self.allow_insecure_localhost
                and parsed.scheme.lower() == "http"
                and host in _LOCAL_HOSTS
            ):
                raise WebResearchError("web reads require HTTPS")
        if allowed_hosts and host not in allowed_hosts:
            raise WebResearchError(f"host {host} is not allow-listed")
        if not allowed_hosts and expected_host is not None and host != expected_host:
            raise WebResearchError("redirect changed host outside same-host policy")
        return url

    @staticmethod
    def _structure(page: Any) -> list[dict[str, Any]]:
        script = """
        (body) => Array.from(body.querySelectorAll(
          'a,button,input,textarea,select,summary,[role]'
        )).slice(0, 512).map((element) => {
          const text = (element.innerText || element.textContent || '')
            .replace(/\\s+/g, ' ').trim().slice(0, 200);
          return {
            tag: element.tagName.toLowerCase(),
            role: element.getAttribute('role') || '',
            name: element.getAttribute('aria-label') ||
              element.getAttribute('name') || text,
            id: element.id || '',
            type: element.getAttribute('type') || '',
            disabled: element.hasAttribute('disabled') ||
              element.getAttribute('aria-disabled') === 'true'
          };
        })
        """
        try:
            value = page.locator("body").evaluate(script)
        except Exception:
            return []
        if not isinstance(value, list):
            return []
        return [dict(item) for item in value if isinstance(item, dict)]

    def _identity(
        self,
        *,
        requested_url: str,
        final_url: str,
        status: int,
        content_type: str,
        source: bytes,
        title: str,
        page_revision_content: str,
        structure: list[dict[str, Any]],
        redirects: tuple[str, ...],
        ) -> BrowserPageIdentity:
        source_sha256 = _sha256_bytes(source)
        structural_hash = _digest(structure)
        bounded_title = str(title)[:MAX_TARGET_NAME_LENGTH]
        try:
            safe_title = sanitize_external_content(
                redact_text(bounded_title),
                source_ref=final_url,
                max_chars=MAX_TARGET_NAME_LENGTH,
            ).text
        except SafetyBoundaryError:
            safe_title = "[UNTRUSTED_PAGE_TITLE]"
        page_revision = _digest({
            "final_url": final_url,
            "title": title,
            "content_sha256": _sha256_text(page_revision_content),
            "structural_hash": structural_hash,
        })
        document_id = _digest({
            "final_url": final_url,
            "source_sha256": source_sha256,
        })
        return BrowserPageIdentity(
            requested_url=requested_url,
            final_url=final_url,
            status=status,
            content_type=content_type,
            source_sha256=source_sha256,
            source_bytes=len(source),
            title=safe_title,
            document_id=document_id,
            page_revision=page_revision,
            structural_hash=structural_hash,
            redirects=redirects,
            fetched_at=isoformat(),
            target_count=len(structure),
        )

    def _attempt(
        self,
        *,
        action_kind: str,
        url: str,
        idempotency_key: str,
        payload_hash: str,
        outcome: str,
        error_code: str | None = None,
        after_state: dict[str, Any] | None = None,
        provider_ref: str | None = None,
        before_state: dict[str, Any] | None = None,
        reconciled: bool = False,
    ) -> ConnectorAttempt:
        safe_url = _safe_url_ref(url)
        safe_provider_ref = (
            _safe_url_ref(provider_ref) if provider_ref is not None else safe_url
        )
        return ConnectorAttempt(
            attempt_id=f"playwright-{_digest({'url': safe_url, 'idempotency_key': idempotency_key, 'created_at': isoformat()})[:24]}",
            connector_id=self.spec.connector_id,
            action_kind=action_kind,
            target_ref=safe_url,
            idempotency_key=idempotency_key,
            payload_hash=payload_hash,
            outcome=outcome,
            before_state=before_state or {"requested_url": safe_url},
            after_state=after_state,
            provider_ref=safe_provider_ref,
            error_code=error_code,
            evidence_refs=(f"browser-url:{_digest(safe_url)[:24]}",),
            cost={"browser_sessions": 1},
            reconciled=reconciled,
        )


class PlaywrightBrowserSession:
    """One isolated Playwright process/context used by a bounded operation."""

    def __init__(
        self,
        connector: PlaywrightBrowserConnector,
        *,
        stop_check: Callable[[], bool] | None,
        allowed_hosts: frozenset[str],
        profile_id: str,
        site_origin: str | None,
        owner_scope: str,
        session_state_path: str | Path | None,
        session_ref: str | None,
        credential_reference: str | None,
        auth_marker: Mapping[str, Any] | Sequence[str] | None,
        expired_url_prefixes: Sequence[str] | None,
        reauthenticate: Callable[["PlaywrightBrowserSession"], bool] | None,
        persist_session: bool,
        allowed_action_methods: Sequence[str],
        max_action_requests: int,
        allow_downloads: bool,
        download_root: str | Path | None,
        max_download_bytes: int,
        allow_uploads: bool,
        upload_root: str | Path | None,
        max_upload_bytes: int,
    ) -> None:
        self.connector = connector
        self.stop_check = stop_check
        self.allowed_hosts = allowed_hosts
        self.profile_id = _safe_opaque_reference(profile_id, "profile_id")
        self.owner_scope = _safe_opaque_reference(owner_scope, "owner_scope")
        self.credential_reference = (
            None
            if credential_reference is None
            else _safe_opaque_reference(credential_reference, "credential_reference")
        )
        self.auth_marker = _normalize_auth_marker(auth_marker)
        self.reauthenticate = reauthenticate
        self.expired_url_prefixes = _normalize_prefixes(
            expired_url_prefixes, "expired_url_prefixes",
        )
        if self.auth_marker and not self.expired_url_prefixes:
            self.expired_url_prefixes = (
                "/login", "/signin", "/sign-in", "/auth/login",
            )
        if (
            not isinstance(persist_session, bool)
            or not isinstance(max_action_requests, int)
            or max_action_requests < 1
            or max_action_requests > 32
        ):
            raise WebResearchError("invalid browser session persistence/action bounds")
        methods = tuple(str(method).strip().upper() for method in allowed_action_methods)
        if not methods or any(method not in {"POST", "PUT", "PATCH"} for method in methods):
            raise WebResearchError(
                "allowed_action_methods must contain only bounded POST/PUT/PATCH methods"
            )
        self.persist_session = persist_session
        self.allowed_action_methods = tuple(dict.fromkeys(methods))
        self.max_action_requests = max_action_requests
        if not isinstance(allow_downloads, bool):
            raise WebResearchError("allow_downloads must be boolean")
        if (
            not isinstance(max_download_bytes, int)
            or isinstance(max_download_bytes, bool)
            or max_download_bytes < 1
            or max_download_bytes > MAX_DOWNLOAD_BYTES
        ):
            raise WebResearchError(
                f"max_download_bytes must be within [1, {MAX_DOWNLOAD_BYTES}]"
            )
        if allow_downloads and download_root is None:
            raise WebResearchError(
                "download_root is required when downloads are enabled"
            )
        if not allow_downloads and download_root is not None:
            raise WebResearchError(
                "download_root cannot be supplied when downloads are disabled"
            )
        self.allow_downloads = allow_downloads
        self.download_root = (
            Path(download_root).expanduser().resolve()
            if download_root is not None
            else None
        )
        if self.download_root is not None and not self.download_root.is_dir():
            raise WebResearchError("download_root must be an existing directory")
        self.max_download_bytes = max_download_bytes
        if not isinstance(allow_uploads, bool):
            raise WebResearchError("allow_uploads must be boolean")
        if (
            not isinstance(max_upload_bytes, int)
            or isinstance(max_upload_bytes, bool)
            or max_upload_bytes < 1
            or max_upload_bytes > MAX_UPLOAD_BYTES
        ):
            raise WebResearchError(
                f"max_upload_bytes must be within [1, {MAX_UPLOAD_BYTES}]"
            )
        if allow_uploads and upload_root is None:
            raise WebResearchError(
                "upload_root is required when uploads are enabled"
            )
        if not allow_uploads and upload_root is not None:
            raise WebResearchError(
                "upload_root cannot be supplied when uploads are disabled"
            )
        self.allow_uploads = allow_uploads
        self.upload_root = (
            Path(upload_root).expanduser().resolve()
            if upload_root is not None
            else None
        )
        if self.upload_root is not None and not self.upload_root.is_dir():
            raise WebResearchError("upload_root must be an existing directory")
        self.max_upload_bytes = max_upload_bytes
        if session_state_path is not None:
            state_path = Path(session_state_path).expanduser().resolve(strict=False)
            self.session_state_path: Path | None = state_path
            self._session_metadata_path = Path(f"{state_path}.meta.json")
        else:
            self.session_state_path = None
            self._session_metadata_path = None
        self.site_origin = self._normalize_site_origin(site_origin)
        binding_site = self.site_origin or "unbound"
        computed_ref = f"session:{_digest({
            'profile_id': self.profile_id,
            'site_origin': binding_site,
            'owner_scope': self.owner_scope,
            'credential_reference': self.credential_reference,
        })[:40]}"
        self.session_ref = (
            _safe_opaque_reference(session_ref, "session_ref")
            if session_ref is not None else computed_ref
        )
        self.page: Any | None = None
        self._playwright: Any | None = None
        self._browser: Any | None = None
        self._context: Any | None = None
        self._initial_host: str | None = None
        self._navigation_urls: list[str] = []
        self._route_error: str | None = None
        self._last_identity: BrowserPageIdentity | None = None
        self._restored = False
        self._state_usable = False
        self._can_persist = self.session_state_path is not None and self.persist_session
        self._state_reason: str | None = None
        self._state_sha256: str | None = None
        self._auth_status = "UNKNOWN"
        self._auth_marker_found: bool | None = None
        self._session_checked_at = isoformat()
        self._action_in_progress = False
        self._action_request_count = 0
        self._action_request_seen = False
        self._download_in_progress = False
        self._reauth_attempted = False
        self._reauth_in_progress = False
        self._reauth_evidence: dict[str, Any] | None = None

    @staticmethod
    def _origin(url: str) -> str:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower()
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        port = parsed.port
        default_port = (parsed.scheme.lower() == "https" and port == 443) or (
            parsed.scheme.lower() == "http" and port == 80
        )
        suffix = "" if port is None or default_port else f":{port}"
        return f"{parsed.scheme.lower()}://{host}{suffix}"

    def _normalize_site_origin(self, value: str | None) -> str | None:
        if value is None:
            if self.session_state_path is not None:
                raise WebResearchError(
                    "site_origin is required when session_state_path is configured"
                )
            return None
        if not isinstance(value, str) or not value.strip():
            raise WebResearchError("site_origin must be a non-empty origin")
        origin = value.strip().rstrip("/")
        try:
            parsed = urlsplit(origin)
            if not parsed.scheme or not parsed.hostname or parsed.path not in {"", "/"}:
                raise ValueError
            if parsed.query or parsed.fragment or parsed.username or parsed.password:
                raise ValueError
            _ = parsed.port
            normalized = self._origin(origin)
            self.connector._validate_url(
                f"{normalized}/", allowed_hosts=self.allowed_hosts,
            )
        except (ValueError, WebResearchError) as error:
            raise WebResearchError("site_origin must be a valid allowed origin") from error
        return normalized

    def _binding_payload(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "session_ref": self.session_ref,
            "profile_id": self.profile_id,
            "site_origin": self.site_origin,
            "owner_scope": self.owner_scope,
            "credential_reference": self.credential_reference,
        }

    def _prepare_session_state(self) -> None:
        path = self.session_state_path
        metadata_path = self._session_metadata_path
        if path is None or metadata_path is None or not self.persist_session:
            self._can_persist = False
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        state_exists = path.is_file()
        metadata_exists = metadata_path.is_file()
        if not state_exists and not metadata_exists:
            self._can_persist = True
            return
        if not state_exists or not metadata_exists:
            self._can_persist = False
            self._state_reason = "session_state_unbound"
            return
        try:
            if path.stat().st_size > MAX_SESSION_STATE_BYTES:
                raise WebResearchError("session_state_too_large")
            if metadata_path.stat().st_size > MAX_SESSION_METADATA_BYTES:
                raise WebResearchError("session_metadata_too_large")
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            if not isinstance(metadata, Mapping):
                raise WebResearchError("session_metadata_malformed")
            expected = self._binding_payload()
            if any(metadata.get(key) != value for key, value in expected.items()):
                raise WebResearchError("session_binding_mismatch")
            state_hash = _sha256_bytes(path.read_bytes())
            if metadata.get("storage_state_sha256") != state_hash:
                raise WebResearchError("session_state_hash_mismatch")
        except (OSError, ValueError, WebResearchError) as error:
            self._can_persist = False
            self._state_reason = str(error)[:120]
            return
        self._state_usable = True
        self._restored = True
        self._state_sha256 = state_hash

    def _persist_session_state(self) -> None:
        path = self.session_state_path
        metadata_path = self._session_metadata_path
        if (
            path is None or metadata_path is None or not self._can_persist
            or self._context is None or self._auth_status != "AUTHENTICATED"
        ):
            return
        temporary_state = Path(f"{path}.tmp")
        temporary_metadata = Path(f"{metadata_path}.tmp")
        try:
            self._context.storage_state(path=str(temporary_state))
            state_hash = _sha256_bytes(temporary_state.read_bytes())
            temporary_state.replace(path)
            metadata = self._binding_payload()
            metadata.update({
                "storage_state_sha256": state_hash,
                "auth_state": self._auth_status,
                "persisted_at": isoformat(),
            })
            temporary_metadata.write_text(
                json.dumps(
                    metadata, ensure_ascii=True, sort_keys=True,
                    separators=(",", ":"),
                ),
                encoding="utf-8",
            )
            temporary_metadata.replace(metadata_path)
            self._state_sha256 = state_hash
            self._state_usable = True
        except Exception:  # pragma: no cover - driver/filesystem-specific
            self._state_reason = "session_state_persist_failed"
            try:
                temporary_state.unlink(missing_ok=True)
                temporary_metadata.unlink(missing_ok=True)
            except OSError:
                pass

    def __enter__(self) -> "PlaywrightBrowserSession":
        from playwright.sync_api import sync_playwright

        self._prepare_session_state()
        try:
            self._playwright = sync_playwright().start()
            browser_type = getattr(self._playwright, self.connector.browser_name)
            self._browser = browser_type.launch(headless=self.connector.headless)
            context_kwargs: dict[str, Any] = {
                "accept_downloads": self.allow_downloads,
            }
            if self._state_usable and self.session_state_path is not None:
                context_kwargs["storage_state"] = str(self.session_state_path)
            try:
                self._context = self._browser.new_context(**context_kwargs)
            except Exception:
                if "storage_state" not in context_kwargs:
                    raise
                # A corrupt or driver-incompatible state is never silently
                # treated as authenticated. Keep the bound file untouched and
                # start an empty context so the caller receives UNKNOWN/BLOCKED.
                self._state_usable = False
                self._restored = False
                self._can_persist = False
                self._state_reason = "session_state_restore_failed"
                self._context = self._browser.new_context(
                    accept_downloads=self.allow_downloads,
                )
            self.page = self._context.new_page()
            self.page.set_default_timeout(self.connector.timeout_ms)
            self._context.route("**/*", self._route)
            return self
        except BaseException:
            # Context entry can fail after a browser process has started. Run
            # the same best-effort teardown as normal exit so failed starts do
            # not leave an orphaned browser/profile authority behind.
            try:
                self.__exit__(None, None, None)
            except Exception:
                pass
            raise

    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        try:
            if self.page is not None:
                try:
                    self._refresh_session_status()
                except Exception:
                    pass
            self._persist_session_state()
            if self._context is not None:
                try:
                    self._context.unroute_all(behavior="wait")
                except Exception:
                    pass
            if self.page is not None:
                try:
                    self.page.close(reason="bounded browser session complete")
                except Exception:
                    pass
            if self._context is not None:
                self._context.close()
        finally:
            try:
                if self._browser is not None:
                    self._browser.close()
            finally:
                if self._playwright is not None:
                    self._playwright.stop()
        return False

    def _route(self, route: Any, request: Any) -> None:
        if self.stop_check is not None and self.stop_check():
            self._route_error = "stop_requested"
            route.abort()
            return
        method = str(request.method).upper()
        if self._download_in_progress:
            if method not in {"GET", "HEAD"}:
                self._route_error = "download_method_blocked"
                route.abort()
                return
            try:
                self.connector._validate_url(
                    request.url,
                    allowed_hosts=self.allowed_hosts,
                    expected_host=self._initial_host,
                )
            except WebResearchError:
                self._route_error = "action_origin_blocked"
                route.abort()
                return
            self._action_request_count += 1
            self._action_request_seen = True
            if self._action_request_count > self.max_action_requests:
                self._route_error = "action_request_limit"
                route.abort()
                return
        if method not in {"GET", "HEAD", "OPTIONS"}:
            if not self._action_in_progress:
                self._route_error = "mutating_request_blocked"
                route.abort()
                return
            if method not in self.allowed_action_methods:
                self._route_error = "action_method_blocked"
                route.abort()
                return
            try:
                self.connector._validate_url(
                    request.url,
                    allowed_hosts=self.allowed_hosts,
                    expected_host=self._initial_host,
                )
            except WebResearchError:
                self._route_error = "action_origin_blocked"
                route.abort()
                return
            self._action_request_count += 1
            self._action_request_seen = True
            if self._action_request_count > self.max_action_requests:
                self._route_error = "action_request_limit"
                route.abort()
                return
        if request.is_navigation_request():
            try:
                self.connector._validate_url(
                    request.url,
                    allowed_hosts=self.allowed_hosts,
                    expected_host=self._initial_host,
                )
            except WebResearchError:
                self._route_error = "redirect_policy_rejected"
                route.abort()
                return
            # Only main-document navigations participate in redirect
            # accounting; a child frame navigating to the same origin is not
            # a redirect hop of the bounded page identity.
            try:
                is_main_frame = request.frame == self.page.main_frame
            except Exception:  # pragma: no cover - driver-specific
                is_main_frame = True
            if is_main_frame:
                self._navigation_urls.append(request.url)
                if len(self._navigation_urls) > MAX_REDIRECTS + 1:
                    self._route_error = "redirect_limit"
                    route.abort()
                    return
        route.continue_()

    def _is_expiry_url(self, url: str) -> bool:
        if not self.expired_url_prefixes:
            return False
        safe_url = _safe_url_ref(url)
        path = urlsplit(url).path or "/"
        return any(
            safe_url.startswith(prefix) or path.startswith(prefix)
            for prefix in self.expired_url_prefixes
        )

    def _refresh_session_status(self) -> BrowserSessionStatus:
        current_url: str | None = None
        if self.page is not None:
            try:
                candidate = str(self.page.url)
                if candidate and candidate != "about:blank":
                    current_url = candidate
            except Exception:
                current_url = None
        marker_found: bool | None = None
        state = "UNKNOWN"
        reason = self._state_reason
        if current_url and self._is_expiry_url(current_url):
            state = "EXPIRED"
            marker_found = False
            reason = "session expiry URL observed"
        elif self.page is not None and self.auth_marker is not None:
            role, name = self.auth_marker
            try:
                locator = self.page.get_by_role(role, name=name, exact=True)
                marker_found = locator.count() == 1 and locator.is_visible()
            except Exception:
                marker_found = False
            if marker_found:
                state = "AUTHENTICATED"
                reason = None
            else:
                state = "MISSING"
                reason = reason or "configured authentication marker was not observed"
        self._auth_status = state
        self._auth_marker_found = marker_found
        self._session_checked_at = isoformat()
        return BrowserSessionStatus(
            state=state,
            session_ref=self.session_ref,
            profile_id=self.profile_id,
            site_origin=self.site_origin or "UNBOUND",
            owner_scope=self.owner_scope,
            restored=self._restored,
            storage_state_present=bool(
                self.session_state_path is not None and self.session_state_path.is_file()
            ),
            checked_at=self._session_checked_at,
            current_url=current_url,
            auth_marker_found=marker_found,
            state_sha256=self._state_sha256,
            reason=reason,
        )

    def session_status(self, *, refresh: bool = True) -> BrowserSessionStatus:
        """Return bound session truth without exposing storage-state contents."""

        if refresh:
            return self._refresh_session_status()
        return BrowserSessionStatus(
            state=self._auth_status,
            session_ref=self.session_ref,
            profile_id=self.profile_id,
            site_origin=self.site_origin or "UNBOUND",
            owner_scope=self.owner_scope,
            restored=self._restored,
            storage_state_present=bool(
                self.session_state_path is not None and self.session_state_path.is_file()
            ),
            checked_at=self._session_checked_at,
            current_url=(str(self.page.url) if self.page is not None else None),
            auth_marker_found=self._auth_marker_found,
            state_sha256=self._state_sha256,
            reason=self._state_reason,
        )

    def check_session(self) -> BrowserSessionStatus:
        """Alias used by callers that treat expiry detection as a probe."""

        return self.session_status(refresh=True)

    def session_info(self) -> dict[str, Any]:
        return self.session_status().to_evidence()

    def ensure_authenticated(self) -> BrowserSessionStatus:
        """Check the marker and use only an explicitly supplied re-auth seam."""

        status = self.session_status()
        if status.authenticated or self.auth_marker is None:
            return status
        if self._reauth_attempted or self.reauthenticate is None:
            return status
        if self.stop_check is not None and self.stop_check():
            return BrowserSessionStatus(
                **{**status.to_evidence(), "state": "BLOCKED", "reason": "stop_requested"}
            )
        self._reauth_attempted = True
        self._reauth_in_progress = True
        try:
            reauthenticated = bool(self.reauthenticate(self))
        except Exception as error:  # pragma: no cover - caller/provider-specific
            reauthenticated = False
            self._state_reason = "reauthentication_failed"
            reason = redact_text(str(error))[:300]
        else:
            reason = None
        finally:
            self._reauth_in_progress = False
        refreshed = self.session_status()
        if reauthenticated and refreshed.authenticated:
            # A login redirect leaves the bound page identity on the login
            # page. Rebuild it with a bounded same-origin GET so the caller
            # can continue the original operation where it is safe to do so.
            self._reread_current_page()
            refreshed = self.session_status()
            if refreshed.authenticated:
                return refreshed
        return BrowserSessionStatus(
            state="BLOCKED",
            session_ref=refreshed.session_ref,
            profile_id=refreshed.profile_id,
            site_origin=refreshed.site_origin,
            owner_scope=refreshed.owner_scope,
            restored=refreshed.restored,
            storage_state_present=refreshed.storage_state_present,
            checked_at=refreshed.checked_at,
            current_url=refreshed.current_url,
            auth_marker_found=refreshed.auth_marker_found,
            state_sha256=refreshed.state_sha256,
            reason=reason or "reauthentication was unavailable or unsuccessful",
        )

    @staticmethod
    def _redirects(response: Any, navigation_urls: list[str]) -> tuple[str, ...]:
        chain: list[str] = []
        request = getattr(response, "request", None)
        while request is not None:
            chain.append(str(request.url))
            request = getattr(request, "redirected_from", None)
        chain.reverse()
        if len(chain) > 1:
            return tuple(chain[:-1])
        if len(navigation_urls) > 1:
            return tuple(navigation_urls[:-1])
        return ()

    def navigate(self, url: str) -> BrowserReadResult:
        if self.page is None:
            raise WebResearchError("browser session is not open")
        key = f"browser-read:{_digest(url)}"
        request_hash = _digest({"action": "browser.web.read", "url": url})
        try:
            self.connector._validate_url(
                url,
                allowed_hosts=self.allowed_hosts,
            )
            if self.site_origin is not None and self._origin(url) != self.site_origin:
                raise WebResearchError("navigation crossed the bound session origin")
        except WebResearchError as error:
            return BrowserReadResult(
                attempt=self.connector._attempt(
                    action_kind="browser.web.read", url=url,
                    idempotency_key=key, payload_hash=request_hash,
                    outcome="FAILED", error_code="invalid_url",
                    after_state={"detail": str(error)[:300]},
                ),
                page=None,
            )
        self._initial_host = (urlsplit(url).hostname or "").lower()
        self._navigation_urls = []
        self._route_error = None
        if self.stop_check is not None and self.stop_check():
            return BrowserReadResult(
                attempt=self.connector._attempt(
                    action_kind="browser.web.read", url=url,
                    idempotency_key=key, payload_hash=request_hash,
                    outcome="CANCELLED", error_code="stop_requested",
                ),
                page=None,
            )
        try:
            response = self.page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=self.connector.timeout_ms,
            )
        except Exception as error:
            if self._route_error == "stop_requested":
                outcome = "CANCELLED"
                code = "stop_requested"
            elif self._route_error in {"redirect_policy_rejected", "redirect_limit"}:
                outcome = "FAILED"
                code = self._route_error
            elif self._route_error == "mutating_request_blocked":
                outcome = "UNKNOWN"
                code = "mutating_request_blocked"
            else:
                from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

                outcome = "UNKNOWN" if isinstance(error, PlaywrightTimeoutError) else "FAILED"
                code = "timeout" if outcome == "UNKNOWN" else "browser_navigation_failed"
            return BrowserReadResult(
                attempt=self.connector._attempt(
                    action_kind="browser.web.read", url=url,
                    idempotency_key=key, payload_hash=request_hash,
                    outcome=outcome, error_code=code,
                    after_state={"detail": redact_text(str(error))[:300]},
                ),
                page=None,
            )
        if self._route_error == "stop_requested":
            return BrowserReadResult(
                attempt=self.connector._attempt(
                    action_kind="browser.web.read", url=url,
                    idempotency_key=key, payload_hash=request_hash,
                    outcome="CANCELLED", error_code="stop_requested",
                ),
                page=None,
            )
        if self._route_error == "redirect_policy_rejected":
            return BrowserReadResult(
                attempt=self.connector._attempt(
                    action_kind="browser.web.read", url=url,
                    idempotency_key=key, payload_hash=request_hash,
                    outcome="FAILED", error_code="redirect_policy_rejected",
                ),
                page=None,
            )
        if self._route_error == "redirect_limit":
            return BrowserReadResult(
                attempt=self.connector._attempt(
                    action_kind="browser.web.read", url=url,
                    idempotency_key=key, payload_hash=request_hash,
                    outcome="FAILED", error_code="redirect_limit",
                    after_state={"redirects_seen": max(len(self._navigation_urls) - 1, 0)},
                ),
                page=None,
            )
        if self._route_error == "mutating_request_blocked":
            return BrowserReadResult(
                attempt=self.connector._attempt(
                    action_kind="browser.web.read", url=url,
                    idempotency_key=key, payload_hash=request_hash,
                    outcome="UNKNOWN", error_code="mutating_request_blocked",
                ),
                page=None,
            )
        if response is None:
            return BrowserReadResult(
                attempt=self.connector._attempt(
                    action_kind="browser.web.read", url=url,
                    idempotency_key=key, payload_hash=request_hash,
                    outcome="FAILED", error_code="missing_main_document",
                ),
                page=None,
            )
        status = int(response.status)
        if status < 200 or status >= 400:
            return BrowserReadResult(
                attempt=self.connector._attempt(
                    action_kind="browser.web.read", url=url,
                    idempotency_key=key, payload_hash=request_hash,
                    outcome="FAILED", error_code=f"http_{status}",
                    after_state={"status": status, "final_url": self.page.url},
                    provider_ref=self.page.url,
                ),
                page=None,
            )
        announced_length = str(response.headers.get("content-length") or "").strip()
        if announced_length:
            try:
                content_length = int(announced_length)
            except ValueError:
                return BrowserReadResult(
                    attempt=self.connector._attempt(
                        action_kind="browser.web.read", url=url,
                        idempotency_key=key, payload_hash=request_hash,
                        outcome="FAILED", error_code="invalid_content_length",
                        after_state={"status": status},
                        provider_ref=self.page.url,
                    ),
                    page=None,
                )
            if content_length < 0:
                return BrowserReadResult(
                    attempt=self.connector._attempt(
                        action_kind="browser.web.read", url=url,
                        idempotency_key=key, payload_hash=request_hash,
                        outcome="FAILED", error_code="invalid_content_length",
                        after_state={"status": status},
                        provider_ref=self.page.url,
                    ),
                    page=None,
                )
            if content_length > self.connector.max_source_bytes:
                return BrowserReadResult(
                    attempt=self.connector._attempt(
                        action_kind="browser.web.read", url=url,
                        idempotency_key=key, payload_hash=request_hash,
                        outcome="FAILED", error_code="source_too_large",
                        after_state={
                            "status": status,
                            "announced_source_bytes": content_length,
                        },
                        provider_ref=self.page.url,
                    ),
                    page=None,
                )
        try:
            source = response.body()
        except Exception as error:
            return BrowserReadResult(
                attempt=self.connector._attempt(
                    action_kind="browser.web.read", url=url,
                    idempotency_key=key, payload_hash=request_hash,
                    outcome="UNKNOWN", error_code="source_body_unavailable",
                    after_state={"detail": redact_text(str(error))[:300]},
                ),
                page=None,
            )
        if len(source) > self.connector.max_source_bytes:
            return BrowserReadResult(
                attempt=self.connector._attempt(
                    action_kind="browser.web.read", url=url,
                    idempotency_key=key, payload_hash=request_hash,
                    outcome="FAILED", error_code="source_too_large",
                    after_state={"status": status, "source_bytes": len(source)},
                    provider_ref=self.page.url,
                ),
                page=None,
            )
        final_url = self.page.url
        redirects = self._redirects(response, self._navigation_urls)
        try:
            redirect_chain = validate_redirect_chain(
                [*redirects, str(self.page.url)],
                max_hops=MAX_REDIRECTS,
            )
            for redirect_url in redirect_chain:
                self.connector._validate_url(
                    redirect_url,
                    allowed_hosts=self.allowed_hosts,
                    expected_host=self._initial_host,
                )
            redirects = tuple(redirect_chain[:-1])
        except (SafetyBoundaryError, WebResearchError) as error:
            code = (
                "redirect_limit" if "hops" in str(error)
                else "redirect_policy_rejected"
            )
            return BrowserReadResult(
                attempt=self.connector._attempt(
                    action_kind="browser.web.read", url=url,
                    idempotency_key=key, payload_hash=request_hash,
                    outcome="FAILED", error_code=code,
                    after_state={
                        "detail": str(error)[:300],
                        "redirects": [_safe_url_ref(item) for item in redirects],
                    },
                    provider_ref=final_url,
                ),
                page=None,
            )
        try:
            self.connector._validate_url(
                final_url,
                allowed_hosts=self.allowed_hosts,
                expected_host=self._initial_host,
            )
        except WebResearchError as error:
            return BrowserReadResult(
                attempt=self.connector._attempt(
                    action_kind="browser.web.read", url=url,
                    idempotency_key=key, payload_hash=request_hash,
                    outcome="FAILED", error_code="redirect_policy_rejected",
                    after_state={"detail": str(error)[:300]},
                ),
                page=None,
            )
        content_type = str(response.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
        if content_type and content_type not in {"text/html", "application/xhtml+xml"}:
            return BrowserReadResult(
                attempt=self.connector._attempt(
                    action_kind="browser.web.read", url=url,
                    idempotency_key=key, payload_hash=request_hash,
                    outcome="FAILED", error_code="unsupported_content_type",
                    after_state={"content_type_supported": False},
                    provider_ref=final_url,
                ),
                page=None,
            )
        try:
            title = self.page.title()
            page_content = self.page.content()
        except Exception as error:
            return BrowserReadResult(
                attempt=self.connector._attempt(
                    action_kind="browser.web.read", url=url,
                    idempotency_key=key, payload_hash=request_hash,
                    outcome="UNKNOWN", error_code="page_snapshot_unavailable",
                    after_state={"detail": redact_text(str(error))[:300]},
                ),
                page=None,
            )
        structure = self.connector._structure(self.page)
        identity = self.connector._identity(
            requested_url=url,
            final_url=final_url,
            status=status,
            content_type=content_type or "text/html",
            source=source,
            title=title,
            page_revision_content=page_content,
            structure=structure,
            redirects=redirects,
        )
        self._last_identity = identity
        self._refresh_session_status()
        attempt = self.connector._attempt(
            action_kind="browser.web.read", url=url,
            idempotency_key=key, payload_hash=request_hash,
            outcome="COMPLETED", after_state=identity.to_evidence(),
            provider_ref=final_url, reconciled=True,
        )
        return BrowserReadResult(attempt=attempt, page=identity, source_bytes=source)

    def _current_revision(self) -> tuple[str, str, str]:
        if self.page is None:
            raise BrowserTargetError("browser session is not open")
        final_url = str(self.page.url)
        title = self.page.title()
        content = self.page.content()
        structure = self.connector._structure(self.page)
        structural_hash = _digest(structure)
        revision = _digest({
            "final_url": final_url,
            "title": title,
            "content_sha256": _sha256_text(content),
            "structural_hash": structural_hash,
        })
        return final_url, revision, structural_hash

    def wait_for_target(
        self,
        *,
        target_id: str,
        role: str,
        name: str,
        state: str = "visible",
        timeout_ms: int | None = None,
        stop_check: Callable[[], bool] | None = None,
        idempotency_key: str | None = None,
        frame_index: int | None = None,
    ) -> ConnectorAttempt:
        """Wait for one exact accessibility target without mutating the page."""

        if self.page is None or self._last_identity is None:
            raise BrowserTargetError("navigate before waiting for a target")
        scope = self._frame(frame_index)
        target_id = _safe_identity(target_id, "target_id")
        role = _safe_identity(role, "role")
        if (
            not isinstance(name, str)
            or not name.strip()
            or len(name.strip()) > MAX_TARGET_NAME_LENGTH
        ):
            raise BrowserTargetError("target name is required and bounded")
        name = name.strip()
        if any(ord(char) < 0x20 for char in name) or contains_secret_like(name):
            raise BrowserTargetError("target name contains unsafe material")
        allowed_states = {
            "attached",
            "detached",
            "visible",
            "hidden",
            "enabled",
            "disabled",
        }
        if state not in allowed_states:
            raise BrowserTargetError(
                f"wait state must be one of {sorted(allowed_states)}"
            )
        effective_timeout = (
            self.connector.timeout_ms if timeout_ms is None else timeout_ms
        )
        if (
            not isinstance(effective_timeout, int)
            or isinstance(effective_timeout, bool)
            or effective_timeout < 100
            or effective_timeout > 120_000
        ):
            raise BrowserTargetError(
                "wait timeout_ms must be between 100 and 120000"
            )
        stop_check = stop_check or self.stop_check
        key = self._action_reference(
            idempotency_key, "idempotency_key"
        ) or f"browser-wait:{_digest({
            'document_id': self._last_identity.document_id,
            'target_id': target_id,
            'role': role,
            'name': name,
            'state': state,
        })}"
        request_hash = _digest({
            "action": "wait",
            "document_id": self._last_identity.document_id,
            "target_id": target_id,
            "role": role,
            "name": name,
            "state": state,
            "timeout_ms": effective_timeout,
        })
        before = {
            "final_url": _safe_url_ref(self._last_identity.final_url),
            "document_id": self._last_identity.document_id,
            "page_revision": self._last_identity.page_revision,
            "target_id": target_id,
            "role": role,
            "name": name,
            "action": "wait",
            "wait_state": state,
        }
        deadline = time.monotonic() + (effective_timeout / 1000.0)
        last_count = 0
        last_visible: bool | None = None
        last_enabled: bool | None = None
        while True:
            if stop_check is not None and stop_check():
                return self.connector._attempt(
                    action_kind="browser.web.wait",
                    url=self._last_identity.final_url,
                    idempotency_key=key,
                    payload_hash=request_hash,
                    outcome="CANCELLED",
                    error_code="stop_requested",
                    before_state=before,
                )
            try:
                locator = scope.get_by_role(role, name=name, exact=True)
                last_count = locator.count()
                if last_count > 1:
                    return self.connector._attempt(
                        action_kind="browser.web.wait",
                        url=self._last_identity.final_url,
                        idempotency_key=key,
                        payload_hash=request_hash,
                        outcome="FAILED",
                        error_code="target_ambiguous",
                        before_state=before,
                        after_state={"target_count": last_count},
                    )
                last_visible = (
                    locator.is_visible() if last_count == 1 else None
                )
                last_enabled = (
                    locator.is_enabled() if last_count == 1 else None
                )
                matched = (
                    (state == "attached" and last_count == 1)
                    or (state == "detached" and last_count == 0)
                    or (
                        state == "visible"
                        and last_count == 1
                        and last_visible is True
                    )
                    or (
                        state == "hidden"
                        and (
                            last_count == 0
                            or last_visible is False
                        )
                    )
                    or (
                        state == "enabled"
                        and last_count == 1
                        and last_enabled is True
                    )
                    or (
                        state == "disabled"
                        and last_count == 1
                        and last_enabled is False
                    )
                )
                if matched:
                    final_url, revision, structural_hash = (
                        self._current_revision()
                    )
                    readback = {
                        "verified": True,
                        "target_count": last_count,
                        "target_visible": last_visible,
                        "target_enabled": last_enabled,
                        "wait_state": state,
                        "authority": "DATA_ONLY",
                    }
                    return self.connector._attempt(
                        action_kind="browser.web.wait",
                        url=final_url,
                        idempotency_key=key,
                        payload_hash=request_hash,
                        outcome="COMPLETED",
                        before_state=before,
                        after_state={
                            "final_url": _safe_url_ref(final_url),
                            "page_revision": revision,
                            "structural_hash": structural_hash,
                            "target_id": target_id,
                            "action": "wait",
                            "wait_state": state,
                            "readback": readback,
                            "readback_verified": True,
                        },
                        provider_ref=final_url,
                        reconciled=True,
                    )
            except Exception as error:
                return self.connector._attempt(
                    action_kind="browser.web.wait",
                    url=self._last_identity.final_url,
                    idempotency_key=key,
                    payload_hash=request_hash,
                    outcome="FAILED",
                    error_code="browser_wait_failed",
                    before_state=before,
                    after_state={
                        "detail": redact_text(str(error))[:300],
                        "target_count": last_count,
                    },
                )
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return self.connector._attempt(
                    action_kind="browser.web.wait",
                    url=self._last_identity.final_url,
                    idempotency_key=key,
                    payload_hash=request_hash,
                    outcome="FAILED",
                    error_code="wait_timeout",
                    before_state=before,
                    after_state={
                        "target_count": last_count,
                        "target_visible": last_visible,
                        "target_enabled": last_enabled,
                        "wait_state": state,
                    },
                )
            try:
                self.page.wait_for_timeout(
                    min(50, max(1, int(remaining * 1000)))
                )
            except Exception as error:
                return self.connector._attempt(
                    action_kind="browser.web.wait",
                    url=self._last_identity.final_url,
                    idempotency_key=key,
                    payload_hash=request_hash,
                    outcome="FAILED",
                    error_code="browser_wait_failed",
                    before_state=before,
                    after_state={
                        "detail": redact_text(str(error))[:300],
                        "target_count": last_count,
                    },
                )

    def _frame(self, frame_index: int | None) -> Any:
        """Resolve one bounded frame scope; index 0 is the main frame."""

        if self.page is None:
            raise BrowserTargetError("browser session is not open")
        if frame_index is None:
            return self.page
        if (
            not isinstance(frame_index, int)
            or isinstance(frame_index, bool)
            or frame_index < 0
        ):
            raise BrowserTargetError(
                "frame_index must be a non-negative integer"
            )
        frames = self.page.frames
        if frame_index >= len(frames):
            raise BrowserTargetError("frame_index is out of range")
        return frames[frame_index]

    def frame_targets(self) -> list[dict[str, Any]]:
        """Return bounded frame identity for the current page, in order."""

        if self.page is None:
            return []
        rows: list[dict[str, Any]] = []
        for index, frame in enumerate(self.page.frames):
            try:
                url = _safe_url_ref(str(frame.url))
            except Exception:
                url = "[INVALID_URL]"
            try:
                name = str(frame.name or "")[:MAX_TARGET_NAME_LENGTH]
            except Exception:
                name = ""
            rows.append({"index": index, "url": url, "name": name})
        return rows

    def discover_target(
        self,
        *,
        target_id: str,
        role: str,
        name: str,
        action: str = "click",
        frame_index: int | None = None,
    ) -> PlaywrightTarget:
        if self.page is None or self._last_identity is None:
            raise BrowserTargetError("navigate before discovering a target")
        target_id = _safe_identity(target_id, "target_id")
        role = _safe_identity(role, "role")
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > MAX_TARGET_NAME_LENGTH:
            raise BrowserTargetError("target name is required and bounded")
        name = name.strip()
        if any(ord(char) < 0x20 for char in name) or contains_secret_like(name):
            raise BrowserTargetError("target name contains unsafe material")
        if action not in {
            "click",
            "fill",
            "input",
            "select",
            "check",
            "submit",
            "download",
            "upload",
        }:
            raise BrowserTargetError(
                "only bounded click, fill, select, check, submit, download, "
                "and upload actions are supported"
            )
        if action == "input":
            action = "fill"
        locator = self._frame(frame_index).get_by_role(
            role, name=name, exact=True,
        )
        count = locator.count()
        if count != 1:
            raise BrowserTargetError(
                "target is ambiguous" if count > 1 else "target is missing")
        if not locator.is_visible():
            raise BrowserTargetError("target is not visible")
        if not locator.is_enabled():
            raise BrowserTargetError("target is disabled")
        final_url, page_revision, structural_hash = self._current_revision()
        if final_url != self._last_identity.final_url:
            raise BrowserTargetError(
                "page navigated outside the bounded navigation authority"
            )
        return PlaywrightTarget(
            target_id=target_id,
            locator={"role": role, "name": name},
            action=action,
            version=1,
            structural_hash=structural_hash,
            document_id=self._last_identity.document_id,
            final_url=final_url,
            page_revision=page_revision,
            role=role,
            name=name,
            frame_index=(
                frame_index
                if frame_index not in (None, 0)
                else None
            ),
        )

    def inspect(self, *, frame_index: int | None = None) -> dict[str, Any]:
        """Return the bounded accessibility structure of one frame scope.

        This is the read-only discovery step of the operator loop: it lets a
        caller choose exact role/name targets for later ``execute`` calls
        without exposing raw HTML, form values, or credentials.
        """

        if self.page is None:
            raise BrowserTargetError("browser session is not open")
        self._reread_current_page()
        if self._last_identity is None:
            raise BrowserTargetError("navigate before inspecting the page")
        scope = self._frame(frame_index)
        final_url, revision, structural_hash = self._current_revision()
        structure = self.connector._structure(scope)
        elements: list[dict[str, Any]] = []
        for item in structure[:INSPECT_MAX_ELEMENTS]:
            raw_name = str(item.get("name") or "")[:512]
            try:
                safe_name = sanitize_external_content(
                    redact_text(raw_name),
                    source_ref=final_url,
                    max_chars=200,
                ).text
            except SafetyBoundaryError:
                safe_name = "[UNTRUSTED_ELEMENT_NAME]"
            tag = str(item.get("tag") or "")[:24]
            explicit_role = str(item.get("role") or "")[:64]
            mapped_role = _ACCESSIBILITY_ROLES.get(
                (tag, str(item.get("type") or "").lower()),
                _ACCESSIBILITY_ROLE_UNSET,
            )
            elements.append({
                "tag": tag,
                "role": (
                    mapped_role if mapped_role is not _ACCESSIBILITY_ROLE_UNSET
                    else (explicit_role or tag)
                ),
                "name": safe_name,
                "type": str(item.get("type") or "")[:32],
                "disabled": bool(item.get("disabled")),
            })
        status = self.session_status()
        return {
            "elements": elements,
            "element_count": len(elements),
            "frames": self.frame_targets(),
            "frame_index": frame_index,
            "final_url": _safe_url_ref(final_url),
            "document_id": self._last_identity.document_id,
            "page_revision": revision,
            "structural_hash": structural_hash,
            "session": status.to_evidence(),
            "authority": "DATA_ONLY",
        }

    def _reread_current_page(self) -> bool:
        """Rebuild bound page identity after a redirect changed the live URL.

        A bounded same-origin GET only; it never repeats a mutation. Returns
        True when the bound identity matches the live page afterwards.
        """

        if self.page is None:
            return False
        try:
            live = str(self.page.url)
        except Exception:
            return False
        if (
            self._last_identity is not None
            and self._last_identity.final_url == live
        ):
            return True
        if not live or live == "about:blank":
            return False
        return self.navigate(live).success

    def page_urls(self) -> list[str]:
        """Return bounded URL references for every open tab, in order."""

        if self._context is None:
            return []
        return [_safe_url_ref(str(page.url)) for page in self._context.pages]

    def active_page_index(self) -> int:
        if self._context is None or self.page is None:
            return -1
        for index, page in enumerate(self._context.pages):
            if page is self.page:
                return index
        return -1

    def _adopt_page(self, page: Any) -> None:
        self.page = page
        try:
            self.page.set_default_timeout(self.connector.timeout_ms)
        except Exception:  # pragma: no cover - driver-specific
            pass

    def switch_page(
        self,
        *,
        index: int | None = None,
        url: str | None = None,
        reload_identity: bool = True,
    ) -> dict[str, Any]:
        """Select one open tab deterministically and rebuild page identity."""

        if self._context is None or self.page is None:
            raise BrowserTargetError("browser session is not open")
        pages = list(self._context.pages)
        if url is not None:
            matches = [
                position
                for position, page in enumerate(pages)
                if str(page.url) == str(url)
            ]
            if len(matches) != 1:
                raise BrowserTargetError(
                    "tab selection by url must match exactly one open page"
                )
            chosen = matches[0]
        elif index is not None:
            if not isinstance(index, int) or isinstance(index, bool):
                raise BrowserTargetError("tab index must be an integer")
            if index < 0 or index >= len(pages):
                raise BrowserTargetError("tab index is out of range")
            chosen = index
        else:
            raise BrowserTargetError(
                "tab selection requires an exact index or url"
            )
        page = pages[chosen]
        self._adopt_page(page)
        evidence: dict[str, Any] = {
            "index": chosen,
            "page_count": len(pages),
            "url": _safe_url_ref(str(page.url)),
            "authority": "DATA_ONLY",
        }
        if reload_identity:
            current = str(page.url)
            if current and current != "about:blank":
                result = self.navigate(current)
                evidence["navigated"] = result.success
                evidence["page"] = (
                    result.page.to_evidence() if result.page is not None else None
                )
            else:
                self._last_identity = None
                evidence["navigated"] = False
        else:
            self._last_identity = None
            evidence["navigated"] = False
        evidence["session"] = self.session_status().to_evidence()
        return evidence

    def wait_for_new_page(
        self,
        *,
        timeout_ms: int | None = None,
        url: str | None = None,
        stop_check: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        """Wait for a tab that was already triggered to appear.

        This never clicks anything itself; it only observes the context.
        """

        if self._context is None:
            raise BrowserTargetError("browser session is not open")
        effective_timeout = (
            self.connector.timeout_ms if timeout_ms is None else timeout_ms
        )
        if (
            not isinstance(effective_timeout, int)
            or isinstance(effective_timeout, bool)
            or effective_timeout < 100
            or effective_timeout > 120_000
        ):
            raise BrowserTargetError(
                "wait timeout_ms must be between 100 and 120000"
            )
        stop_check = stop_check or self.stop_check
        deadline = time.monotonic() + (effective_timeout / 1000.0)
        initial = len(self._context.pages)
        while True:
            if stop_check is not None and stop_check():
                return {
                    "state": "CANCELLED",
                    "error_code": "stop_requested",
                    "page_count": len(self._context.pages),
                    "authority": "DATA_ONLY",
                }
            pages = list(self._context.pages)
            candidates = [
                position
                for position, page in enumerate(pages)
                if url is None or str(page.url) == str(url)
            ]
            if url is not None and len(candidates) == 1:
                self._adopt_page(pages[candidates[0]])
                return {
                    "state": "OBSERVED",
                    "index": candidates[0],
                    "page_count": len(pages),
                    "url": _safe_url_ref(str(pages[candidates[0]].url)),
                    "authority": "DATA_ONLY",
                }
            if url is None and len(pages) > initial:
                return {
                    "state": "OBSERVED",
                    "index": len(pages) - 1,
                    "page_count": len(pages),
                    "url": _safe_url_ref(str(pages[-1].url)),
                    "authority": "DATA_ONLY",
                }
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return {
                    "state": "FAILED",
                    "error_code": "new_page_timeout",
                    "page_count": len(pages),
                    "authority": "DATA_ONLY",
                }
            try:
                self.page.wait_for_timeout(
                    min(50, max(1, int(remaining * 1000)))
                )
            except Exception as error:
                return {
                    "state": "FAILED",
                    "error_code": "new_page_wait_failed",
                    "detail": redact_text(str(error))[:300],
                    "authority": "DATA_ONLY",
                }

    @staticmethod
    def _action_reference(value: str | None, field_name: str) -> str | None:
        if value is None:
            return None
        return _safe_opaque_reference(value, field_name)

    def _download_destination(
        self, value: str | Path | None,
    ) -> tuple[Path, str]:
        if not self.allow_downloads or self.download_root is None:
            raise BrowserTargetError("downloads are not enabled for this session")
        if not isinstance(value, (str, Path)) or not str(value).strip():
            raise BrowserTargetError("download_target is required")
        if "\x00" in str(value):
            raise BrowserTargetError("download_target contains a NUL")
        relative = Path(value)
        if relative.is_absolute() or relative.anchor:
            raise BrowserTargetError("download_target must be relative")
        if any(part == ".." for part in relative.parts):
            raise BrowserTargetError("download_target escapes the owned root")
        unresolved = self.download_root / relative
        current = self.download_root
        for part in relative.parts[:-1]:
            current /= part
            if current.exists() and current.is_symlink():
                raise BrowserTargetError(
                    "download_target crosses a symbolic link"
                )
        destination = unresolved.resolve(strict=False)
        try:
            bounded_ref = destination.relative_to(self.download_root)
        except ValueError as error:
            raise BrowserTargetError(
                "download_target escapes the owned root"
            ) from error
        if destination == self.download_root:
            raise BrowserTargetError("download_target must name a file")
        if destination.exists() or destination.is_symlink():
            raise BrowserTargetError(
                "download_target already exists; overwrite is refused"
            )
        return destination, bounded_ref.as_posix()

    def _upload_source(
        self, value: str | Path | None,
    ) -> tuple[Path, str, dict[str, Any]]:
        if not self.allow_uploads or self.upload_root is None:
            raise BrowserTargetError("uploads are not enabled for this session")
        if not isinstance(value, (str, Path)) or not str(value).strip():
            raise BrowserTargetError("upload_source is required")
        if "\x00" in str(value):
            raise BrowserTargetError("upload_source contains a NUL")
        relative = Path(value)
        if relative.is_absolute() or relative.anchor:
            raise BrowserTargetError("upload_source must be relative")
        if any(part == ".." for part in relative.parts):
            raise BrowserTargetError("upload_source escapes the owned root")
        current = self.upload_root
        for part in relative.parts:
            current /= part
            if current.exists() and current.is_symlink():
                raise BrowserTargetError(
                    "upload_source crosses a symbolic link"
                )
        try:
            source = (self.upload_root / relative).resolve(strict=True)
            bounded_ref = source.relative_to(self.upload_root)
        except (OSError, ValueError) as error:
            raise BrowserTargetError(
                "upload_source is missing or escapes the owned root"
            ) from error
        if not source.is_file() or source.is_symlink():
            raise BrowserTargetError(
                "upload_source must be a regular file"
            )
        try:
            announced_size = source.stat().st_size
        except OSError as error:
            raise BrowserTargetError(
                "upload_source could not be inspected"
            ) from error
        if announced_size > self.max_upload_bytes:
            raise BrowserTargetError("upload exceeds the byte limit")
        try:
            content = source.read_bytes()
        except OSError as error:
            raise BrowserTargetError(
                "upload_source could not be read"
            ) from error
        if len(content) != announced_size:
            raise BrowserTargetError(
                "upload_source changed during inspection"
            )
        source_ref = bounded_ref.as_posix()
        return source, source_ref, {
            "source_ref": source_ref,
            "size_bytes": len(content),
            "sha256": _sha256_bytes(content),
            "source_name_sha256": _sha256_text(source.name),
            "source_name_length": len(source.name),
        }

    @staticmethod
    def _upload_readback(
        locator: Any,
        expected: Mapping[str, Any],
    ) -> dict[str, Any]:
        files = locator.evaluate(
            """
            async element => Promise.all(
                Array.from(element.files || []).map(async file => {
                    const bytes = await file.arrayBuffer();
                    const digest = await crypto.subtle.digest("SHA-256", bytes);
                    const hex = Array.from(new Uint8Array(digest))
                        .map(value => value.toString(16).padStart(2, "0"))
                        .join("");
                    return {name: file.name, size_bytes: file.size, sha256: hex};
                })
            )
            """
        )
        rows = files if isinstance(files, list) else []
        safe_rows = [
            {
                "size_bytes": int(row.get("size_bytes") or 0),
                "sha256": str(row.get("sha256") or ""),
                "name_sha256": _sha256_text(str(row.get("name") or "")),
                "name_length": len(str(row.get("name") or "")),
            }
            for row in rows
            if isinstance(row, Mapping)
        ]
        matches = (
            len(safe_rows) == 1
            and safe_rows[0]["size_bytes"] == expected.get("size_bytes")
            and safe_rows[0]["sha256"] == expected.get("sha256")
            and safe_rows[0]["name_sha256"]
            == expected.get("source_name_sha256")
        )
        return {
            "verified": matches,
            "selected_count": len(safe_rows),
            "files": safe_rows,
            "status": "MATCH" if matches else "MISMATCH",
            "authority": "DATA_ONLY",
        }

    def _publish_download(
        self,
        download: Any,
        *,
        destination: Path,
        target_ref: str,
    ) -> dict[str, Any]:
        suggested = str(download.suggested_filename or "")
        failure = download.failure()
        if failure:
            raise WebResearchError("download provider reported failure")
        source_value = download.path()
        if not source_value:
            raise WebResearchError("download source path is unavailable")
        source = Path(source_value)
        if not source.is_file() or source.is_symlink():
            raise WebResearchError("download source is not a regular file")
        announced_size = source.stat().st_size
        if announced_size > self.max_download_bytes:
            raise WebResearchError("download exceeds the byte limit")

        destination.parent.mkdir(parents=True, exist_ok=True)
        resolved_parent = destination.parent.resolve(strict=True)
        assert self.download_root is not None
        try:
            resolved_parent.relative_to(self.download_root)
        except ValueError as error:
            raise WebResearchError(
                "download parent escaped the owned root"
            ) from error
        if destination.exists() or destination.is_symlink():
            raise WebResearchError(
                "download target appeared before publish"
            )

        temporary: str | None = None
        digest = hashlib.sha256()
        total = 0
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=destination.parent,
                prefix=f".{destination.name}.",
                suffix=".download",
                delete=False,
            ) as output:
                temporary = output.name
                with source.open("rb") as downloaded:
                    while True:
                        chunk = downloaded.read(1024 * 1024)
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > self.max_download_bytes:
                            raise WebResearchError(
                                "download exceeds the byte limit"
                            )
                        output.write(chunk)
                        digest.update(chunk)
                output.flush()
                os.fsync(output.fileno())
            os.link(temporary, destination)
            os.unlink(temporary)
            temporary = None
            readback = destination.read_bytes()
            readback_hash = _sha256_bytes(readback)
            if len(readback) != total or readback_hash != digest.hexdigest():
                raise WebResearchError("download readback mismatch")
        finally:
            if temporary is not None:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass
            try:
                download.delete()
            except Exception:
                pass

        return {
            "target_ref": target_ref,
            "path": str(destination),
            "size_bytes": total,
            "sha256": digest.hexdigest(),
            "suggested_filename_sha256": (
                _sha256_text(suggested) if suggested else None
            ),
            "suggested_filename_length": len(suggested),
            "readback": {
                "exists": True,
                "size_bytes": len(readback),
                "sha256": readback_hash,
                "matches": True,
                "status": "MATCH",
            },
        }

    def _readback_locator(
        self,
        *,
        target: PlaywrightTarget | None = None,
        role: str | None = None,
        name: str | None = None,
        frame_index: int | None = None,
    ) -> tuple[Any | None, str | None]:
        if self.page is None:
            return None, "browser session is not open"
        target_role = target.role if target is not None else role
        target_name = target.name if target is not None else name
        if not target_role or not target_name:
            return None, None
        try:
            scope = self._frame(frame_index)
        except BrowserTargetError as error:
            return None, str(error)
        locator = scope.get_by_role(target_role, name=target_name, exact=True)
        count = locator.count()
        if count != 1:
            return None, "target_missing" if count == 0 else "target_ambiguous"
        return locator, None

    def readback(
        self,
        *,
        target: PlaywrightTarget | None = None,
        role: str | None = None,
        name: str | None = None,
        expected_value: Any = None,
        action: str | None = None,
        frame_index: int | None = None,
    ) -> dict[str, Any]:
        """Read bounded page state for independent action reconciliation."""

        try:
            final_url, revision, structural_hash = self._current_revision()
        except Exception as error:
            return {
                "verified": False,
                "error_code": "page_snapshot_unavailable",
                "detail": redact_text(str(error))[:300],
                "authority": "DATA_ONLY",
            }
        status = self.session_status()
        result: dict[str, Any] = {
            "verified": status.state != "EXPIRED",
            "final_url": _safe_url_ref(final_url),
            "page_revision": revision,
            "structural_hash": structural_hash,
            "session_state": status.state,
            "session_ref": self.session_ref,
            "authority": "DATA_ONLY",
        }
        locator, error_code = self._readback_locator(
            target=target, role=role, name=name,
            frame_index=(
                frame_index if target is None else target.frame_index
            ),
        )
        if error_code:
            result.update({"verified": False, "error_code": error_code})
            return result
        if locator is None:
            return result
        try:
            result.update({
                "target_present": True,
                "target_visible": locator.is_visible(),
                "target_enabled": locator.is_enabled(),
            })
            readback_action = target.action if target is not None else str(
                action or ""
            )
            if readback_action == "fill":
                current = locator.input_value(timeout=self.connector.timeout_ms)
                result["value_sha256"] = _sha256_text(current)
                if isinstance(expected_value, str):
                    result["verified"] = result["verified"] and current == expected_value
            elif readback_action == "select":
                selected = locator.evaluate(
                    "element => Array.from(element.selectedOptions || []).map(option => option.value)"
                )
                selected_values = (
                    [str(item) for item in selected]
                    if isinstance(selected, list) else []
                )
                result["selected_count"] = len(selected_values)
                result["selected_values_sha256"] = _digest(selected_values)
                if isinstance(expected_value, str):
                    result["verified"] = (
                        result["verified"] and selected_values == [expected_value]
                    )
                elif isinstance(expected_value, (list, tuple)):
                    expected = [str(item) for item in expected_value]
                    result["verified"] = (
                        result["verified"] and selected_values == expected
                    )
            elif readback_action == "check" and isinstance(expected_value, bool):
                result["checked"] = locator.is_checked()
                result["verified"] = (
                    result["verified"] and result["checked"] == expected_value
                )
        except Exception as error:
            result.update({
                "verified": False,
                "error_code": "readback_failed",
                "detail": redact_text(str(error))[:300],
            })
        return result

    def execute(
        self,
        target: PlaywrightTarget,
        *,
        value: Any = None,
        stop_check: Callable[[], bool] | None = None,
        effect_id: str | None = None,
        idempotency_key: str | None = None,
        readback_target: Mapping[str, Any] | None = None,
        download_target: str | Path | None = None,
        upload_source: str | Path | None = None,
        expect_new_page: bool = False,
    ) -> ConnectorAttempt:
        if self.page is None:
            raise BrowserTargetError("browser session is not open")
        stop_check = stop_check or self.stop_check
        effect_id = self._action_reference(effect_id, "effect_id")
        key = self._action_reference(idempotency_key, "idempotency_key") or (
            f"browser-execute:{_digest({
                'target_id': target.target_id,
                'page_revision': target.page_revision,
                'action': target.action,
            })}"
        )
        download_destination: Path | None = None
        download_ref: str | None = None
        upload_path: Path | None = None
        upload_ref: str | None = None
        upload_source_evidence: dict[str, Any] | None = None
        upload_postcondition: dict[str, Any] | None = None
        upload_precondition: dict[str, Any] | None = None
        if target.action == "download":
            if value is not None:
                raise BrowserTargetError("download does not accept a value")
            download_destination, download_ref = self._download_destination(
                download_target
            )
        elif target.action == "upload":
            if value is not None:
                raise BrowserTargetError("upload does not accept a value")
            if not isinstance(readback_target, Mapping):
                raise BrowserTargetError(
                    "upload requires an exact acknowledgement postcondition"
                )
            post_role = _safe_identity(
                readback_target.get("role"), "readback role"
            )
            post_name_value = readback_target.get("name")
            if (
                not isinstance(post_name_value, str)
                or not post_name_value.strip()
                or len(post_name_value.strip()) > MAX_TARGET_NAME_LENGTH
            ):
                raise BrowserTargetError(
                    "upload acknowledgement name is required and bounded"
                )
            post_name = post_name_value.strip()
            if (
                any(ord(char) < 0x20 for char in post_name)
                or contains_secret_like(post_name)
            ):
                raise BrowserTargetError(
                    "upload acknowledgement name contains unsafe material"
                )
            post_state = str(
                readback_target.get("wait_state") or "visible"
            )
            if post_state not in {"attached", "visible", "enabled"}:
                raise BrowserTargetError(
                    "upload acknowledgement must become attached, visible, or enabled"
                )
            upload_postcondition = {
                "role": post_role,
                "name": post_name,
                "wait_state": post_state,
                "timeout_ms": int(
                    readback_target.get("timeout_ms")
                    or self.connector.timeout_ms
                ),
            }
            upload_path, upload_ref, upload_source_evidence = (
                self._upload_source(upload_source)
            )
        if target.action != "download" and download_target is not None:
            raise BrowserTargetError(
                "download_target is valid only for a download action"
            )
        if target.action != "upload" and upload_source is not None:
            raise BrowserTargetError(
                "upload_source is valid only for an upload action"
            )
        if target.action == "fill":
            if not isinstance(value, str) or len(value) > 4096:
                raise BrowserTargetError("fill value must be bounded text")
            if any(ord(char) < 0x20 and char not in "\r\n\t" for char in value):
                raise BrowserTargetError("fill value contains control characters")
            if contains_secret_like(value):
                raise BrowserTargetError("secret-like fill values are refused")
        elif target.action == "select":
            values = [value] if isinstance(value, str) else value
            if not isinstance(values, (list, tuple)) or not values or len(values) > 32:
                raise BrowserTargetError("select requires one to thirty-two bounded values")
            if any(
                not isinstance(item, str) or not item or len(item) > 256
                or any(ord(char) < 0x20 for char in item)
                or contains_secret_like(item)
                for item in values
            ):
                raise BrowserTargetError("select values are invalid")
            value = list(values)
        elif target.action == "check":
            if value is None:
                value = True
            if not isinstance(value, bool):
                raise BrowserTargetError("check value must be boolean")
        request_hash = _digest({
            "target": target.to_evidence(),
            "value": value,
            "download_target": download_ref,
            "upload_source": upload_ref,
            "readback_target": upload_postcondition,
            "effect_id": effect_id,
            "idempotency_key": key,
        })
        before = target.to_evidence()
        if download_ref is not None:
            before["download_target"] = download_ref
        if upload_source_evidence is not None:
            before["upload"] = upload_source_evidence
        if stop_check is not None and stop_check():
            return self.connector._attempt(
                action_kind="browser.web.execute", url=target.final_url,
                idempotency_key=key, payload_hash=request_hash,
                outcome="CANCELLED", error_code="stop_requested",
                before_state=before,
            )
        if self.auth_marker is not None and not self._reauth_in_progress:
            status = self.ensure_authenticated()
            if not status.authenticated:
                return self.connector._attempt(
                    action_kind="browser.web.execute", url=target.final_url,
                    idempotency_key=key, payload_hash=request_hash,
                    outcome="BLOCKED",
                    error_code=(
                        "stop_requested"
                        if status.state == "BLOCKED"
                        and status.reason == "stop_requested"
                        else "session_expired"
                        if status.state == "EXPIRED"
                        else "session_not_authenticated"
                    ),
                    before_state={**before, "session": status.to_evidence()},
                )
        try:
            final_url, revision, structural_hash = self._current_revision()
        except Exception as error:
            return self.connector._attempt(
                action_kind="browser.web.execute", url=target.final_url,
                idempotency_key=key, payload_hash=request_hash,
                outcome="UNKNOWN", error_code="page_snapshot_unavailable",
                before_state=before,
                after_state={"detail": redact_text(str(error))[:300]},
            )
        if (
            final_url != target.final_url
            or revision != target.page_revision
            or structural_hash != target.structural_hash
        ):
            return self.connector._attempt(
                action_kind="browser.web.execute", url=final_url,
                idempotency_key=key, payload_hash=request_hash,
                outcome="UNKNOWN", error_code="target_drift",
                before_state=before,
                after_state={
                    "final_url": _safe_url_ref(final_url),
                    "page_revision": revision,
                    "structural_hash": structural_hash,
                },
            )
        locator = self._frame(target.frame_index).get_by_role(
            target.role, name=target.name, exact=True,
        )
        if locator.count() != 1:
            return self.connector._attempt(
                action_kind="browser.web.execute", url=final_url,
                idempotency_key=key, payload_hash=request_hash,
                outcome="UNKNOWN", error_code="target_drift",
                before_state=before,
            )
        if upload_postcondition is not None:
            acknowledgement = self.page.get_by_role(
                upload_postcondition["role"],
                name=upload_postcondition["name"],
                exact=True,
            )
            acknowledgement_count = acknowledgement.count()
            if acknowledgement_count > 1:
                raise BrowserTargetError(
                    "upload acknowledgement target is ambiguous"
                )
            acknowledgement_visible = (
                acknowledgement.is_visible()
                if acknowledgement_count == 1
                else None
            )
            acknowledgement_enabled = (
                acknowledgement.is_enabled()
                if acknowledgement_count == 1
                else None
            )
            post_state = upload_postcondition["wait_state"]
            matched_before = (
                (post_state == "attached" and acknowledgement_count == 1)
                or (
                    post_state == "visible"
                    and acknowledgement_count == 1
                    and acknowledgement_visible is True
                )
                or (
                    post_state == "enabled"
                    and acknowledgement_count == 1
                    and acknowledgement_enabled is True
                )
            )
            upload_precondition = {
                "matched": matched_before,
                "role": upload_postcondition["role"],
                "name": upload_postcondition["name"],
                "target_count": acknowledgement_count,
                "target_visible": acknowledgement_visible,
                "target_enabled": acknowledgement_enabled,
                "wait_state": post_state,
                "authority": "DATA_ONLY",
            }
            before["upload_postcondition"] = upload_precondition
            if matched_before:
                return self.connector._attempt(
                    action_kind="browser.web.execute",
                    url=final_url,
                    idempotency_key=key,
                    payload_hash=request_hash,
                    outcome="FAILED",
                    error_code="upload_postcondition_already_satisfied",
                    before_state=before,
                )
        self._route_error = None
        self._action_in_progress = True
        self._action_request_count = 0
        self._action_request_seen = False
        download_started = False
        download_evidence: dict[str, Any] | None = None
        upload_started = False
        upload_evidence: dict[str, Any] | None = None
        new_page_evidence: dict[str, Any] | None = None
        try:
            if target.action in {"click", "submit"}:
                if expect_new_page:
                    if self._context is None:
                        raise BrowserTargetError("browser session is not open")
                    with self._context.expect_page(
                        timeout=self.connector.timeout_ms,
                    ) as page_info:
                        locator.click(timeout=self.connector.timeout_ms)
                    opened = page_info.value
                    self._action_request_seen = True
                    self._action_request_count = max(
                        1, self._action_request_count,
                    )
                    new_page_evidence = {
                        "url": _safe_url_ref(str(opened.url)),
                        "page_count": len(self._context.pages),
                        "index": max(len(self._context.pages) - 1, 0),
                        "adopted": False,
                    }
                else:
                    locator.click(timeout=self.connector.timeout_ms)
            elif target.action == "fill":
                locator.fill(value, timeout=self.connector.timeout_ms)
            elif target.action == "select":
                locator.select_option(value=value, timeout=self.connector.timeout_ms)
            elif target.action == "check":
                if value:
                    locator.check(timeout=self.connector.timeout_ms)
                else:
                    locator.uncheck(timeout=self.connector.timeout_ms)
            elif target.action == "download":
                assert download_destination is not None
                assert download_ref is not None
                download_started = True
                self._download_in_progress = True
                with self.page.expect_download(
                    timeout=self.connector.timeout_ms,
                ) as download_info:
                    locator.click(timeout=self.connector.timeout_ms)
                # Attachment transfers are not consistently exposed through
                # page routing. A yielded Download object is direct provider
                # evidence that the request/effect boundary was crossed.
                self._action_request_seen = True
                self._action_request_count = max(
                    1, self._action_request_count,
                )
                if stop_check is not None and stop_check():
                    raise WebResearchError(
                        "STOP became active after the download started"
                    )
                download_evidence = self._publish_download(
                    download_info.value,
                    destination=download_destination,
                    target_ref=download_ref,
                )
            elif target.action == "upload":
                assert upload_path is not None
                assert upload_source_evidence is not None
                upload_started = True
                locator.set_input_files(
                    str(upload_path),
                    timeout=self.connector.timeout_ms,
                )
                upload_readback = self._upload_readback(
                    locator,
                    upload_source_evidence,
                )
                if not upload_readback["verified"]:
                    raise WebResearchError("upload readback mismatch")
                source_after = upload_path.read_bytes()
                if (
                    len(source_after)
                    != upload_source_evidence["size_bytes"]
                    or _sha256_bytes(source_after)
                    != upload_source_evidence["sha256"]
                ):
                    raise WebResearchError(
                        "upload source changed after dispatch"
                    )
                upload_evidence = {
                    **upload_source_evidence,
                    "readback": upload_readback,
                    "precondition": upload_precondition,
                }
                if upload_postcondition is not None:
                    wait_attempt = self.wait_for_target(
                        target_id=(
                            "readback-"
                            + _digest({
                                "effect_id": effect_id,
                                "target_id": target.target_id,
                            })[:24]
                        ),
                        role=upload_postcondition["role"],
                        name=upload_postcondition["name"],
                        state=upload_postcondition["wait_state"],
                        timeout_ms=upload_postcondition["timeout_ms"],
                        stop_check=stop_check,
                        frame_index=target.frame_index,
                    )
                    if wait_attempt.outcome != "COMPLETED":
                        if wait_attempt.error_code == "stop_requested":
                            self._route_error = "stop_requested"
                            raise WebResearchError(
                                "STOP became active after upload dispatch"
                            )
                        raise WebResearchError(
                            "upload postcondition was not observed"
                        )
                    upload_evidence["postcondition"] = {
                        **dict(wait_attempt.after_state or {}).get(
                            "readback", {}
                        ),
                        "matched": True,
                    }
            else:  # pragma: no cover - discover_target normalizes before this point
                raise BrowserTargetError("unsupported browser action")
            if (
                target.action in {"click", "submit"}
                and not self._action_request_seen
                and not download_started
                and not upload_started
            ):
                # A form submission inside a child frame can be dispatched a
                # few event-loop turns after the click returns Playwright
                # control. Keep the in-progress authority open for one short,
                # bounded window so the frame's own request is still governed
                # by this action instead of being refused as out-of-band.
                drain_deadline = time.monotonic() + 0.4
                while (
                    time.monotonic() < drain_deadline
                    and not self._action_request_seen
                ):
                    try:
                        self.page.wait_for_timeout(20)
                    except Exception:
                        break
        except Exception as error:
            from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

            self._action_in_progress = False
            if self._route_error == "stop_requested":
                effect_possible = (
                    self._action_request_seen
                    or download_started
                    or upload_started
                )
                outcome = "UNKNOWN" if effect_possible else "CANCELLED"
                code = "stop_after_effect" if effect_possible else "stop_requested"
            elif isinstance(error, PlaywrightTimeoutError):
                outcome = "UNKNOWN"
                code = (
                    "timeout_after_action" if self._action_request_seen
                    else "timeout_before_action"
                )
            elif self._action_request_seen:
                outcome = "UNKNOWN"
                code = (
                    "download_ambiguous"
                    if target.action == "download"
                    else "upload_ambiguous"
                    if target.action == "upload"
                    else "action_ambiguous"
                )
            elif download_started:
                outcome = "UNKNOWN"
                code = "download_ambiguous"
            elif upload_started:
                outcome = "UNKNOWN"
                code = "upload_ambiguous"
            elif self._route_error:
                outcome = "UNKNOWN"
                code = self._route_error
            else:
                outcome = "FAILED"
                code = "browser_action_failed"
            after_state: dict[str, Any] = {
                "detail": redact_text(str(error))[:300],
                "action_request_count": self._action_request_count,
                "action_request_seen": self._action_request_seen,
                "effect_id": effect_id,
                "idempotency_key": key,
            }
            if upload_evidence is not None:
                after_state["upload"] = upload_evidence
            if new_page_evidence is not None:
                after_state["new_page"] = new_page_evidence
            try:
                if readback_target is not None:
                    after_state["readback"] = self.readback(
                        role=(
                            str(readback_target["role"])
                            if readback_target.get("role") is not None else None
                        ),
                        name=(
                            str(readback_target["name"])
                            if readback_target.get("name") is not None else None
                        ),
                        expected_value=readback_target.get("expected_value"),
                        frame_index=target.frame_index,
                    )
                else:
                    after_state["readback"] = self.readback(
                        target=target, expected_value=value,
                    )
            except Exception:
                pass
            return self.connector._attempt(
                action_kind="browser.web.execute", url=target.final_url,
                idempotency_key=key, payload_hash=request_hash,
                outcome=outcome, error_code=code,
                before_state=before, after_state=after_state,
            )
        finally:
            self._action_in_progress = False
            self._download_in_progress = False
        if target.action == "download":
            assert download_evidence is not None
            after = {
                "final_url": _safe_url_ref(target.final_url),
                "page_revision": target.page_revision,
                "structural_hash": target.structural_hash,
                "target_id": target.target_id,
                "action": target.action,
                "effect_id": effect_id,
                "idempotency_key": key,
                "action_request_count": self._action_request_count,
                "action_request_seen": self._action_request_seen,
                "download": download_evidence,
                "readback": download_evidence["readback"],
                "readback_verified": True,
            }
            if stop_check is not None and stop_check():
                return self.connector._attempt(
                    action_kind="browser.web.execute",
                    url=target.final_url,
                    idempotency_key=key,
                    payload_hash=request_hash,
                    outcome="UNKNOWN",
                    error_code="stop_after_effect",
                    before_state=before,
                    after_state=after,
                )
            return self.connector._attempt(
                action_kind="browser.web.execute",
                url=target.final_url,
                idempotency_key=key,
                payload_hash=request_hash,
                outcome="COMPLETED",
                before_state=before,
                after_state=after,
                provider_ref=target.final_url,
                reconciled=True,
            )
        if target.action == "upload":
            assert upload_evidence is not None
            try:
                after_url, after_revision, after_structure_hash = (
                    self._current_revision()
                )
            except Exception as error:
                return self.connector._attempt(
                    action_kind="browser.web.execute",
                    url=target.final_url,
                    idempotency_key=key,
                    payload_hash=request_hash,
                    outcome="UNKNOWN",
                    error_code="page_snapshot_unavailable_after_action",
                    before_state=before,
                    after_state={
                        "detail": redact_text(str(error))[:300],
                        "upload": upload_evidence,
                    },
                )
            after = {
                "final_url": _safe_url_ref(after_url),
                "page_revision": after_revision,
                "structural_hash": after_structure_hash,
                "target_id": target.target_id,
                "action": target.action,
                "effect_id": effect_id,
                "idempotency_key": key,
                "action_request_count": self._action_request_count,
                "action_request_seen": self._action_request_seen,
                "upload": upload_evidence,
                "readback": upload_evidence["readback"],
                "readback_verified": True,
            }
            if stop_check is not None and stop_check():
                return self.connector._attempt(
                    action_kind="browser.web.execute",
                    url=after_url,
                    idempotency_key=key,
                    payload_hash=request_hash,
                    outcome="UNKNOWN",
                    error_code="stop_after_effect",
                    before_state=before,
                    after_state=after,
                )
            return self.connector._attempt(
                action_kind="browser.web.execute",
                url=after_url,
                idempotency_key=key,
                payload_hash=request_hash,
                outcome="COMPLETED",
                before_state=before,
                after_state=after,
                provider_ref=after_url,
                reconciled=True,
            )
        try:
            after_url, after_revision, after_structure_hash = self._current_revision()
        except Exception as error:
            return self.connector._attempt(
                action_kind="browser.web.execute", url=target.final_url,
                idempotency_key=key, payload_hash=request_hash,
                outcome="UNKNOWN", error_code="page_snapshot_unavailable_after_action",
                before_state=before,
                after_state={"detail": redact_text(str(error))[:300]},
            )
        after = {
            "final_url": _safe_url_ref(after_url),
            "page_revision": after_revision,
            "structural_hash": after_structure_hash,
            "target_id": target.target_id,
            "action": target.action,
            "effect_id": effect_id,
            "idempotency_key": key,
            "action_request_count": self._action_request_count,
            "action_request_seen": self._action_request_seen,
        }
        if new_page_evidence is not None:
            after["new_page"] = new_page_evidence
        if self._route_error in {
            "mutating_request_blocked", "action_method_blocked", "action_origin_blocked",
            "action_request_limit", "redirect_policy_rejected", "redirect_limit",
            "stop_requested",
        }:
            try:
                after["readback"] = (
                    self.readback(
                        role=(
                            str(readback_target["role"])
                            if readback_target is not None
                            and readback_target.get("role") is not None else None
                        ),
                        name=(
                            str(readback_target["name"])
                            if readback_target is not None
                            and readback_target.get("name") is not None else None
                        ),
                    )
                    if readback_target is not None
                    else self.readback(target=target)
                )
            except Exception:
                pass
            stop_outcome = (
                "UNKNOWN" if self._route_error == "stop_requested" and self._action_request_seen
                else "CANCELLED" if self._route_error == "stop_requested" else "UNKNOWN"
            )
            stop_code = (
                "stop_after_effect"
                if self._route_error == "stop_requested" and self._action_request_seen
                else self._route_error
            )
            return self.connector._attempt(
                action_kind="browser.web.execute", url=after_url,
                idempotency_key=key, payload_hash=request_hash,
                outcome=stop_outcome, error_code=stop_code,
                before_state=before, after_state=after,
            )
        if stop_check is not None and stop_check():
            return self.connector._attempt(
                action_kind="browser.web.execute", url=after_url,
                idempotency_key=key, payload_hash=request_hash,
                outcome="UNKNOWN", error_code="stop_after_effect",
                before_state=before, after_state=after,
            )
        status = self.session_status()
        if self.auth_marker is not None and not status.authenticated:
            after["session"] = status.to_evidence()
            return self.connector._attempt(
                action_kind="browser.web.execute", url=after_url,
                idempotency_key=key, payload_hash=request_hash,
                outcome="UNKNOWN", error_code="session_expired_after_action",
                before_state=before, after_state=after,
            )
        if readback_target is not None:
            rb = self.readback(
                role=(str(readback_target["role"])
                      if readback_target.get("role") is not None else None),
                name=(str(readback_target["name"])
                      if readback_target.get("name") is not None else None),
                expected_value=readback_target.get("expected_value"),
                frame_index=target.frame_index,
            )
        else:
            rb = self.readback(target=target, expected_value=value)
        after["readback"] = rb
        after["readback_verified"] = bool(rb.get("verified"))
        after["session"] = status.to_evidence()
        if not rb.get("verified"):
            return self.connector._attempt(
                action_kind="browser.web.execute", url=after_url,
                idempotency_key=key, payload_hash=request_hash,
                outcome="UNKNOWN", error_code="readback_not_verified",
                before_state=before, after_state=after,
            )
        return self.connector._attempt(
            action_kind="browser.web.execute", url=after_url,
            idempotency_key=key, payload_hash=request_hash,
            outcome="COMPLETED", before_state=before,
            after_state=after, provider_ref=after_url, reconciled=True,
        )

    def _safe_page_title(self, *, source_ref: str) -> str | None:
        if self.page is None:
            return None
        try:
            raw = str(self.page.title())[:MAX_TARGET_NAME_LENGTH]
        except Exception:
            return None
        try:
            return sanitize_external_content(
                redact_text(raw), source_ref=source_ref,
                max_chars=MAX_TARGET_NAME_LENGTH,
            ).text
        except SafetyBoundaryError:
            return "[UNTRUSTED_PAGE_TITLE]"

    def run_steps(
        self,
        steps: Sequence[Mapping[str, Any]],
        *,
        stop_check: Callable[[], bool] | None = None,
        step_scope: str | None = None,
        max_steps: int = MAX_STEP_COUNT,
    ) -> dict[str, Any]:
        """Execute ordered steps on this one session with step-level evidence.

        Steps are data, not code: ``navigate``, ``inspect``, ``authenticate``,
        ``wait``, ``readback``, ``switch_page``, and the bounded mutation
        verbs.  A failed or ambiguous step stops the sequence (honest partial
        result); nothing is retried inside this helper.  This is a bounded
        convenience over the existing single-action methods, never a second
        orchestrator or effect journal.
        """

        if self.page is None:
            raise BrowserTargetError("browser session is not open")
        ordered = tuple(steps)
        if not ordered:
            raise BrowserTargetError("run_steps requires at least one step")
        if not isinstance(max_steps, int) or isinstance(max_steps, bool) or max_steps < 1:
            raise BrowserTargetError("max_steps must be a positive integer")
        if len(ordered) > max_steps:
            raise BrowserTargetError(
                f"run_steps accepts at most {max_steps} steps"
            )
        stop_check = stop_check or self.stop_check
        scope = (
            _safe_opaque_reference(step_scope, "step_scope")
            if step_scope is not None else None
        )
        step_ids: list[str] = []
        for item in ordered:
            if not isinstance(item, Mapping):
                raise BrowserTargetError("each step must be an object")
            step_ids.append(_safe_identity(item.get("step_id"), "step_id"))
        if len(step_ids) != len(set(step_ids)):
            raise BrowserTargetError("step_id values must be unique")

        results: list[dict[str, Any]] = []
        stopped_at: str | None = None
        for position, item in enumerate(ordered):
            step_id = step_ids[position]
            action = str(item.get("action") or "").strip().lower()
            key = (
                f"{scope or 'browser-steps'}:{step_id}:{position}"
            )
            if stop_check is not None and stop_check():
                results.append({
                    "step_id": step_id, "action": action,
                    "outcome": "CANCELLED", "error_code": "stop_requested",
                })
                stopped_at = step_id
                break
            try:
                row = self._run_one_step(
                    step_id,
                    action,
                    item,
                    idempotency_key=key,
                    stop_check=stop_check,
                )
            except BrowserTargetError as error:
                row = {
                    "step_id": step_id, "action": action,
                    "outcome": "FAILED",
                    "error_code": "browser_target_error",
                    "detail": redact_text(str(error))[:300],
                }
            except (WebResearchError, TypeError, ValueError) as error:
                row = {
                    "step_id": step_id, "action": action,
                    "outcome": "FAILED",
                    "error_code": "browser_step_invalid",
                    "detail": redact_text(str(error))[:300],
                }
            results.append(row)
            if row["outcome"] != "COMPLETED":
                stopped_at = step_id
                break

        success = (
            stopped_at is None
            and len(results) == len(ordered)
            and all(row["outcome"] == "COMPLETED" for row in results)
        )
        final: dict[str, Any] = {}
        try:
            final_url, revision, structural_hash = self._current_revision()
            status = self.session_status()
            final = {
                "final_url": _safe_url_ref(final_url),
                "title": self._safe_page_title(source_ref=final_url),
                "page_revision": revision,
                "structural_hash": structural_hash,
                "session": status.to_evidence(),
                "pages": self.page_urls(),
                "active_page": self.active_page_index(),
            }
        except Exception as error:
            final = {
                "error_code": "page_snapshot_unavailable",
                "detail": redact_text(str(error))[:300],
            }
        return {
            "success": success,
            "step_count": len(results),
            "requested_step_count": len(ordered),
            "stopped_at": stopped_at,
            "steps": results,
            "final": final,
            "authority": "DATA_ONLY",
        }

    def _run_one_step(
        self,
        step_id: str,
        action: str,
        item: Mapping[str, Any],
        *,
        idempotency_key: str,
        stop_check: Callable[[], bool] | None,
    ) -> dict[str, Any]:
        if action == "navigate":
            url = item.get("url")
            if not isinstance(url, str) or not url.strip():
                raise BrowserTargetError("navigate step requires a url")
            capture = self.navigate(url.strip())
            row = {
                "step_id": step_id, "action": action,
                "outcome": capture.attempt.outcome,
                "error_code": capture.attempt.error_code,
            }
            if capture.page is not None:
                row["final_url"] = capture.page.final_url
                row["page_revision"] = capture.page.page_revision
                row["structural_hash"] = capture.page.structural_hash
                row["document_id"] = capture.page.document_id
            return row
        if action == "inspect":
            inspected = self.inspect(
                frame_index=(
                    int(item["frame_index"])
                    if item.get("frame_index") is not None else None
                ),
            )
            return {
                "step_id": step_id, "action": action,
                "outcome": "COMPLETED",
                "final_url": inspected["final_url"],
                "page_revision": inspected["page_revision"],
                "structural_hash": inspected["structural_hash"],
                "element_count": inspected["element_count"],
                "elements": inspected["elements"],
            }
        if action == "authenticate":
            status = self.ensure_authenticated()
            return {
                "step_id": step_id, "action": action,
                "outcome": "COMPLETED" if status.authenticated else "BLOCKED",
                "error_code": (
                    None if status.authenticated
                    else "session_expired" if status.state == "EXPIRED"
                    else "session_not_authenticated"
                ),
                "session": status.to_evidence(),
            }
        if action == "switch_page":
            evidence = self.switch_page(
                index=(
                    int(item["index"]) if item.get("index") is not None else None
                ),
                url=(
                    str(item["url"]) if item.get("url") is not None else None
                ),
            )
            return {"step_id": step_id, "action": action, **evidence}
        if action == "readback":
            rb = self.readback(
                role=(
                    str(item["role"]) if item.get("role") is not None else None
                ),
                name=(
                    str(item["name"]) if item.get("name") is not None else None
                ),
                expected_value=item.get("expected_value"),
                action=(
                    str(item["readback_action"])
                    if item.get("readback_action") is not None else None
                ),
                frame_index=(
                    int(item["frame_index"])
                    if item.get("frame_index") is not None else None
                ),
            )
            return {
                "step_id": step_id, "action": action,
                "outcome": "COMPLETED" if rb.get("verified") else "FAILED",
                "error_code": None if rb.get("verified") else str(
                    rb.get("error_code") or "readback_not_verified"
                ),
                "readback": rb,
                "final_url": rb.get("final_url"),
                "page_revision": rb.get("page_revision"),
            }
        if action == "wait":
            attempt = self.wait_for_target(
                target_id=str(item.get("target_id") or f"wait-{step_id}"),
                role=str(item.get("role") or ""),
                name=str(item.get("name") or ""),
                state=str(item.get("wait_state") or "visible"),
                timeout_ms=(
                    int(item["timeout_ms"])
                    if item.get("timeout_ms") is not None else None
                ),
                stop_check=stop_check,
                idempotency_key=idempotency_key,
                frame_index=(
                    int(item["frame_index"])
                    if item.get("frame_index") is not None else None
                ),
            )
            after = dict(attempt.after_state or {})
            return {
                "step_id": step_id, "action": action,
                "outcome": attempt.outcome,
                "error_code": attempt.error_code,
                "final_url": after.get("final_url"),
                "page_revision": after.get("page_revision"),
                "structural_hash": after.get("structural_hash"),
                "readback": after.get("readback"),
            }
        if action not in BROWSER_EXECUTE_ACTIONS:
            raise BrowserTargetError(
                f"unsupported step action {action!r}"
            )
        # A prior step may have redirected the page; rebuild the bound
        # identity with a bounded same-origin GET before discovering targets.
        self._reread_current_page()
        target = self.discover_target(
            target_id=str(item.get("target_id") or step_id),
            role=str(item.get("role") or ""),
            name=str(item.get("name") or ""),
            action=action,
            frame_index=(
                int(item["frame_index"])
                if item.get("frame_index") is not None else None
            ),
        )
        attempt = self.execute(
            target,
            value=item.get("value"),
            stop_check=stop_check,
            idempotency_key=idempotency_key,
            readback_target=(
                item.get("readback_target")
                if isinstance(item.get("readback_target"), Mapping) else None
            ),
            download_target=(
                item.get("download_target") if action == "download" else None
            ),
            upload_source=(
                item.get("upload_source") if action == "upload" else None
            ),
            expect_new_page=bool(item.get("expect_new_page")),
        )
        after = dict(attempt.after_state or {})
        return {
            "step_id": step_id, "action": action,
            "outcome": attempt.outcome,
            "error_code": attempt.error_code,
            "final_url": after.get("final_url"),
            "page_revision": after.get("page_revision"),
            "structural_hash": after.get("structural_hash"),
            "readback": after.get("readback"),
            "download": after.get("download"),
            "upload": after.get("upload"),
            "session": after.get("session"),
            "action_request_seen": after.get("action_request_seen"),
        }

    def reconcile(
        self,
        *,
        target: PlaywrightTarget | None = None,
        role: str | None = None,
        name: str | None = None,
        expected_value: Any = None,
        target_id: str | None = None,
        wait_state: str | None = None,
        timeout_ms: int | None = None,
        effect_id: str | None = None,
        idempotency_key: str | None = None,
        readback_action: str | None = None,
    ) -> ConnectorAttempt:
        """Re-read one stable effect; this method never repeats the action."""

        effect_id = self._action_reference(effect_id, "effect_id")
        key = self._action_reference(idempotency_key, "idempotency_key") or (
            f"browser-reconcile:{_digest({
                'effect_id': effect_id,
                'target_id': target.target_id if target else None,
                'role': role,
                'name': name,
                'wait_state': wait_state,
            })}"
        )
        request_hash = _digest({
            "effect_id": effect_id,
            "idempotency_key": key,
            "target": target.to_evidence()
            if target else {"role": role, "name": name},
            "expected_value": expected_value,
            "wait_state": wait_state,
            "readback_action": readback_action,
        })
        status = self.session_status()
        if status.state == "EXPIRED":
            return self.connector._attempt(
                action_kind="browser.web.execute",
                url=status.current_url or (self.site_origin or "https://browser.invalid"),
                idempotency_key=key, payload_hash=request_hash,
                outcome="UNKNOWN", error_code="session_expired",
                after_state={"session": status.to_evidence()},
            )
        if wait_state is not None:
            wait_attempt = self.wait_for_target(
                target_id=target_id or f"reconcile-{effect_id[-24:]}",
                role=str(role or ""),
                name=str(name or ""),
                state=wait_state,
                timeout_ms=timeout_ms,
                stop_check=lambda: False,
                idempotency_key=key,
            )
            wait_after = dict(wait_attempt.after_state or {})
            rb = dict(wait_after.get("readback") or {})
            current_url = str(
                wait_after.get("final_url")
                or (
                    self._last_identity.final_url
                    if self._last_identity is not None
                    else ""
                )
                or self.site_origin
                or "https://browser.invalid"
            )
            rb.setdefault("final_url", _safe_url_ref(current_url))
            rb.setdefault("page_revision", wait_after.get("page_revision"))
            rb.setdefault("structural_hash", wait_after.get("structural_hash"))
            rb["matched"] = wait_attempt.outcome == "COMPLETED"
            if wait_attempt.outcome != "COMPLETED":
                return self.connector._attempt(
                    action_kind="browser.web.execute",
                    url=current_url,
                    idempotency_key=key,
                    payload_hash=request_hash,
                    outcome="UNKNOWN",
                    error_code=str(
                        wait_attempt.error_code
                        or "reconciliation_not_verified"
                    ),
                    after_state={
                        "readback": rb,
                        "readback_verified": False,
                        "effect_id": effect_id,
                        "idempotency_key": key,
                        "reconciled": False,
                    },
                )
            if expected_value is not None:
                value_readback = self.readback(
                    role=role,
                    name=name,
                    expected_value=expected_value,
                    action=readback_action,
                )
                wait_verified = bool(rb.get("verified"))
                rb.update(value_readback)
                rb["verified"] = (
                    wait_verified and value_readback.get("verified") is True
                )
                rb["matched"] = rb["verified"]
        else:
            rb = self.readback(
                target=target,
                role=role,
                name=name,
                expected_value=expected_value,
                action=readback_action,
            )
            rb["matched"] = bool(rb.get("verified"))
        outcome = "COMPLETED" if rb.get("verified") else "UNKNOWN"
        return self.connector._attempt(
            action_kind="browser.web.execute",
            url=str(rb.get("final_url") or self.site_origin or "https://browser.invalid"),
            idempotency_key=key, payload_hash=request_hash,
            outcome=outcome,
            error_code=None if outcome == "COMPLETED" else str(
                rb.get("error_code") or "reconciliation_not_verified"
            ),
            after_state={
                "final_url": rb.get("final_url"),
                "page_revision": rb.get("page_revision"),
                "structural_hash": rb.get("structural_hash"),
                "target_id": target_id,
                "action": "reconcile",
                "readback": rb,
                "readback_verified": outcome == "COMPLETED",
                "effect_id": effect_id,
                "idempotency_key": key,
                "reconciled": outcome == "COMPLETED",
            },
            reconciled=outcome == "COMPLETED",
        )


class TrafilaturaExtractor:
    """Bounded HTML-to-text adapter over captured browser bytes."""

    spec = ConnectorSpec(
        connector_id="conn-trafilatura-v10",
        kind="html_extractor",
        version="1.0.0",
        actions={"research.html.extract": "read"},
        capabilities={"html_extract": "PROBING"},
        retry_policy={"max_attempts": 1, "backoff_s": [0]},
        target_kinds=("browser_document",),
        idempotency_strategy="effect-key",
        reconciliation_strategy="system-readback",
        supports_cancellation=True,
        cost_metadata={"unit": "local_parse", "cost_known": True},
        evidence_fields=(
            "attempt_id", "source_sha256", "extracted_sha256",
            "safe_text_sha256", "authority", "outcome",
        ),
    )

    def __init__(self, *, max_chars: int = MAX_EXTRACTED_CHARS) -> None:
        if (
            not isinstance(max_chars, int)
            or max_chars < 256
            or max_chars > MAX_EXTRACTED_CHARS
        ):
            raise WebResearchError(
                f"max_chars must be between 256 and {MAX_EXTRACTED_CHARS}"
            )
        self.max_chars = max_chars

    def health(self) -> dict[str, Any]:
        try:
            version = importlib.metadata.version("trafilatura")
        except importlib.metadata.PackageNotFoundError as error:
            return {
                "status": "NOT_READY",
                "available": False,
                "error_code": "trafilatura_unavailable",
                "detail": str(error),
            }
        return {
            "status": "READY",
            "available": True,
            "trafilatura_version": version,
            "authority": "DATA_ONLY",
        }

    @staticmethod
    def _safe_metadata(value: Any, *, source_ref: str) -> Any:
        if value is None:
            return None
        if isinstance(value, (list, tuple)):
            return [TrafilaturaExtractor._safe_metadata(item, source_ref=source_ref) for item in value[:20]]
        bounded = redact_text(
            value if isinstance(value, str) else str(value)
        )[:512]
        try:
            return sanitize_external_content(
                bounded, source_ref=source_ref, max_chars=512,
            ).text
        except SafetyBoundaryError:
            return "[UNTRUSTED_EXTERNAL_METADATA]"

    def _attempt(
        self,
        *,
        page: BrowserPageIdentity | None,
        source_sha256: str,
        outcome: str,
        error_code: str | None = None,
        after_state: dict[str, Any] | None = None,
    ) -> ConnectorAttempt:
        url = page.final_url if page else "browser-document:unknown"
        key = f"extract:{source_sha256}"
        return ConnectorAttempt(
            attempt_id=f"trafilatura-{_digest({'url': url, 'source_sha256': source_sha256})[:24]}",
            connector_id=self.spec.connector_id,
            action_kind="research.html.extract",
            target_ref=url,
            idempotency_key=key,
            payload_hash=source_sha256 if len(source_sha256) == 64 else _digest(source_sha256),
            outcome=outcome,
            before_state={"source_sha256": source_sha256},
            after_state=after_state,
            provider_ref=url,
            error_code=error_code,
            evidence_refs=(
                f"browser-source:{source_sha256[:24]}",
                f"extractor:{self.spec.connector_id}",
            ),
            cost={"parses": 1},
            reconciled=outcome == "COMPLETED",
        )

    def extract(
        self,
        capture: BrowserReadResult,
        *,
        stop_check: Callable[[], bool] | None = None,
    ) -> HtmlExtractionResult:
        page = capture.page
        source = capture.source_bytes
        source_sha256 = page.source_sha256 if page else "0" * 64
        if not capture.success or page is None or source is None:
            attempt = self._attempt(
                page=page,
                source_sha256=source_sha256,
                outcome="FAILED",
                error_code="source_unavailable",
            )
            return HtmlExtractionResult(attempt, page, "", {}, None, None)
        actual_source_sha256 = _sha256_bytes(source)
        if (
            actual_source_sha256 != page.source_sha256
            or len(source) != page.source_bytes
        ):
            attempt = self._attempt(
                page=page,
                source_sha256=source_sha256,
                outcome="FAILED",
                error_code="source_hash_mismatch",
                after_state={
                    "expected_source_sha256": page.source_sha256,
                    "actual_source_sha256": actual_source_sha256,
                    "expected_source_bytes": page.source_bytes,
                    "actual_source_bytes": len(source),
                },
            )
            return HtmlExtractionResult(attempt, page, "", {}, None, None)
        if stop_check is not None and stop_check():
            attempt = self._attempt(
                page=page,
                source_sha256=source_sha256,
                outcome="CANCELLED",
                error_code="stop_requested",
            )
            return HtmlExtractionResult(attempt, page, "", {}, None, None)
        health = self.health()
        if not health.get("available"):
            attempt = self._attempt(
                page=page,
                source_sha256=source_sha256,
                outcome="UNKNOWN",
                error_code=str(health.get("error_code") or "trafilatura_unavailable"),
                after_state={"health": health},
            )
            return HtmlExtractionResult(attempt, page, "", {}, None, None)
        try:
            import trafilatura

            html_text = source.decode("utf-8", errors="replace")
            encoded = trafilatura.extract(
                html_text,
                url=page.final_url,
                output_format="json",
                with_metadata=True,
                include_comments=False,
                include_tables=True,
                include_links=False,
                favor_precision=True,
            )
            if not encoded:
                raise WebResearchError("extractor returned no document")
            document = json.loads(encoded)
            if not isinstance(document, dict):
                raise WebResearchError("extractor returned a non-object")
            raw_text = str(document.get("text") or document.get("raw_text") or "").strip()
            if not raw_text:
                raise WebResearchError("extractor returned no main text")
            truncated = len(raw_text) > self.max_chars
            bounded_text = raw_text[:self.max_chars]
            extracted_sha256 = _sha256_text(bounded_text)
            safe_record = sanitize_external_content(
                redact_text(bounded_text),
                source_ref=page.final_url,
                # HTML escaping can expand a bounded text slice (for example,
                # ``<`` becomes ``&lt;``).  Give normalization a finite larger
                # budget, then enforce the public safe-text bound below.
                max_chars=max(self.max_chars * 8, len(bounded_text) * 8 + 128),
            )
            safe_truncated = len(safe_record.text) > self.max_chars
            safe_text = safe_record.text[:self.max_chars]
            truncated = truncated or safe_truncated
            safe_text_sha256 = _sha256_text(safe_text)
            metadata = {
                key: self._safe_metadata(document.get(key), source_ref=page.final_url)
                for key in (
                    "title", "author", "date", "hostname", "description",
                    "categories", "tags", "language", "pagetype",
                )
                if document.get(key) is not None
            }
            after_state = {
                "source_sha256": source_sha256,
                "source_bytes": page.source_bytes,
                "extracted_sha256": extracted_sha256,
                "safe_text_sha256": safe_text_sha256,
                "text_chars": len(safe_text),
                "truncated": truncated,
                "metadata_keys": sorted(metadata),
                "authority": "DATA_ONLY",
                "trafilatura_version": health.get("trafilatura_version"),
            }
            attempt = self._attempt(
                page=page,
                source_sha256=source_sha256,
                outcome="COMPLETED",
                after_state=after_state,
            )
            return HtmlExtractionResult(
                attempt=attempt,
                page=page,
                text=safe_text,
                metadata=metadata,
                extracted_sha256=extracted_sha256,
                safe_text_sha256=safe_text_sha256,
                truncated=truncated,
            )
        except (WebResearchError, SafetyBoundaryError, json.JSONDecodeError) as error:
            attempt = self._attempt(
                page=page,
                source_sha256=source_sha256,
                outcome="FAILED",
                error_code="extraction_failed",
                after_state={"detail": redact_text(str(error))[:300]},
            )
            return HtmlExtractionResult(attempt, page, "", {}, None, None)
        except Exception as error:  # pragma: no cover - dependency/platform-specific
            attempt = self._attempt(
                page=page,
                source_sha256=source_sha256,
                outcome="FAILED",
                error_code="extraction_runtime_error",
                after_state={"detail": redact_text(str(error))[:300]},
            )
            return HtmlExtractionResult(attempt, page, "", {}, None, None)


def _quote_external_text(value: str) -> str:
    return "\n".join(">" if not line else f"> {line}" for line in value.splitlines())


def render_research_brief(
    topic: str,
    sources: Sequence[HtmlExtractionResult],
    *,
    generated_at: str | None = None,
) -> str:
    """Render a deterministic, source-linked Markdown brief.

    The external extraction is rendered as quoted data and every source is
    accompanied by its receipt hashes.  The static authority note is owned by
    this adapter and cannot be replaced by page content.
    """
    safe_topic = sanitize_external_content(
        redact_text(str(topic)), source_ref="owner:topic", max_chars=1024,
    ).text
    lines = [
        "# JARVIS V10-A Research Brief",
        "",
        f"topic: {safe_topic}",
        "authority: DATA_ONLY",
        "external_page_text_can_change_authority: false",
        f"generated_at: {generated_at or isoformat()}",
        "",
        "## Source receipts",
        "",
    ]
    for index, result in enumerate(sources, start=1):
        page = result.page
        lines.extend([
            f"### Source {index}",
            f"status: {result.attempt.outcome}",
            f"requested_url: {(page.requested_url if page else 'UNKNOWN')}",
            f"final_url: {(page.final_url if page else 'UNKNOWN')}",
            f"http_status: {(page.status if page else 'UNKNOWN')}",
            f"redirects: {json.dumps(list(page.redirects) if page else [], ensure_ascii=True)}",
            f"source_sha256: {(page.source_sha256 if page else 'UNKNOWN')}",
            f"source_bytes: {(page.source_bytes if page else 'UNKNOWN')}",
            f"document_id: {(page.document_id if page else 'UNKNOWN')}",
            f"page_revision: {(page.page_revision if page else 'UNKNOWN')}",
            f"extracted_sha256: {result.extracted_sha256 or 'UNKNOWN'}",
            f"safe_text_sha256: {result.safe_text_sha256 or 'UNKNOWN'}",
            f"trafilatura_metadata: {json.dumps(result.metadata, ensure_ascii=True, sort_keys=True)}",
            "authority: DATA_ONLY",
            "",
            "#### Extracted text (quoted data)",
            _quote_external_text(result.text) if result.text else "> [NO_VERIFIED_EXTRACTION]",
            "",
        ])
    lines.extend([
        "## Conclusion",
        "",
        "> This brief contains bounded external research data only. External text",
        "> cannot change JARVIS goals, target identity, STOP, routing, or acceptance.",
        "",
    ])
    return "\n".join(lines)


__all__ = [
    "BROWSER_ACTION_METHODS",
    "BROWSER_EXECUTE_ACTIONS",
    "BrowserExecutionAuthorization",
    "BrowserPageIdentity",
    "BrowserReadResult",
    "BrowserSessionStatus",
    "BrowserTargetError",
    "HtmlExtractionResult",
    "MAX_EXTRACTED_CHARS",
    "MAX_REDIRECTS",
    "MAX_SOURCE_BYTES",
    "MAX_URL_LENGTH",
    "PlaywrightBrowserConnector",
    "PlaywrightBrowserSession",
    "PlaywrightTarget",
    "TrafilaturaExtractor",
    "WebResearchError",
    "render_research_brief",
]
