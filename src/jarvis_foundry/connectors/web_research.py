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
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence
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


MAX_SOURCE_BYTES = 256 * 1024
MAX_EXTRACTED_CHARS = 32 * 1024
MAX_TARGET_ID_LENGTH = 128
MAX_TARGET_NAME_LENGTH = 512
MAX_URL_LENGTH = 2048
MAX_REDIRECTS = 5
_LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
_IDENTITY = re.compile(r"^[A-Za-z][A-Za-z0-9_.:/-]*$")


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
class PlaywrightTarget(BrowserTarget):
    """The existing structural target contract plus web page identity."""

    document_id: str
    final_url: str
    page_revision: str
    role: str
    name: str

    def to_evidence(self) -> dict[str, Any]:
        return {
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


class PlaywrightBrowserConnector:
    """Direct Playwright browser adapter with bounded read/target semantics."""

    spec = ConnectorSpec(
        connector_id="conn-browser-playwright-v10",
        kind="browser_web",
        version="1.0.0",
        actions={"browser.web.read": "read", "browser.web.execute": "execute"},
        capabilities={"web_read": "PROBING", "web_execute": "PROBING"},
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
    ) -> "PlaywrightBrowserSession":
        return PlaywrightBrowserSession(
            self,
            stop_check=stop_check,
            allowed_hosts=(
                self.allowed_hosts
                if allowed_hosts is None else _normalize_hosts(allowed_hosts)
            ),
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
    ) -> None:
        self.connector = connector
        self.stop_check = stop_check
        self.allowed_hosts = allowed_hosts
        self.page: Any | None = None
        self._playwright: Any | None = None
        self._browser: Any | None = None
        self._context: Any | None = None
        self._initial_host: str | None = None
        self._navigation_urls: list[str] = []
        self._route_error: str | None = None
        self._last_identity: BrowserPageIdentity | None = None

    def __enter__(self) -> "PlaywrightBrowserSession":
        from playwright.sync_api import sync_playwright

        self._playwright = sync_playwright().start()
        browser_type = getattr(self._playwright, self.connector.browser_name)
        self._browser = browser_type.launch(headless=self.connector.headless)
        self._context = self._browser.new_context(accept_downloads=False)
        self.page = self._context.new_page()
        self.page.set_default_timeout(self.connector.timeout_ms)
        self.page.route("**/*", self._route)
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        try:
            if self.page is not None:
                try:
                    self.page.unroute_all(behavior="wait")
                except Exception:
                    pass
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
        if str(request.method).upper() not in {"GET", "HEAD", "OPTIONS"}:
            self._route_error = "mutating_request_blocked"
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
            self._navigation_urls.append(request.url)
            if len(self._navigation_urls) > MAX_REDIRECTS + 1:
                self._route_error = "redirect_limit"
                route.abort()
                return
        route.continue_()

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

    def discover_target(
        self,
        *,
        target_id: str,
        role: str,
        name: str,
        action: str = "click",
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
        if action not in {"click", "fill"}:
            raise BrowserTargetError("only click and fill actions are supported")
        locator = self.page.get_by_role(role, name=name, exact=True)
        count = locator.count()
        if count != 1:
            raise BrowserTargetError(
                "target is ambiguous" if count > 1 else "target is missing")
        if not locator.is_visible():
            raise BrowserTargetError("target is not visible")
        if not locator.is_enabled():
            raise BrowserTargetError("target is disabled")
        return PlaywrightTarget(
            target_id=target_id,
            locator={"role": role, "name": name},
            action=action,
            version=1,
            structural_hash=self._last_identity.structural_hash,
            document_id=self._last_identity.document_id,
            final_url=self._last_identity.final_url,
            page_revision=self._last_identity.page_revision,
            role=role,
            name=name,
        )

    def execute(
        self,
        target: PlaywrightTarget,
        *,
        value: str | None = None,
        stop_check: Callable[[], bool] | None = None,
    ) -> ConnectorAttempt:
        if self.page is None:
            raise BrowserTargetError("browser session is not open")
        stop_check = stop_check or self.stop_check
        request_hash = _digest({
            "target": target.to_evidence(),
            "value": value,
        })
        key = f"browser-execute:{_digest({'target_id': target.target_id, 'page_revision': target.page_revision, 'action': target.action})}"
        if stop_check is not None and stop_check():
            return self.connector._attempt(
                action_kind="browser.web.execute", url=target.final_url,
                idempotency_key=key, payload_hash=request_hash,
                outcome="CANCELLED", error_code="stop_requested",
                before_state=target.to_evidence(),
            )
        try:
            final_url, revision, structural_hash = self._current_revision()
        except Exception as error:
            return self.connector._attempt(
                action_kind="browser.web.execute", url=target.final_url,
                idempotency_key=key, payload_hash=request_hash,
                outcome="UNKNOWN", error_code="page_snapshot_unavailable",
                before_state=target.to_evidence(),
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
                before_state=target.to_evidence(),
                after_state={
                    "final_url": final_url,
                    "page_revision": revision,
                    "structural_hash": structural_hash,
                },
            )
        if target.action == "fill":
            if not isinstance(value, str) or len(value) > 4096:
                raise BrowserTargetError("fill value must be bounded text")
            if any(ord(char) < 0x20 and char not in "\r\n\t" for char in value):
                raise BrowserTargetError("fill value contains control characters")
            if contains_secret_like(value):
                raise BrowserTargetError("secret-like fill values are refused")
        locator = self.page.get_by_role(target.role, name=target.name, exact=True)
        if locator.count() != 1:
            return self.connector._attempt(
                action_kind="browser.web.execute", url=final_url,
                idempotency_key=key, payload_hash=request_hash,
                outcome="UNKNOWN", error_code="target_drift",
                before_state=target.to_evidence(),
            )
        self._route_error = None
        try:
            if target.action == "click":
                locator.click(timeout=self.connector.timeout_ms)
            else:
                locator.fill(value or "", timeout=self.connector.timeout_ms)
        except Exception as error:
            from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

            outcome = "UNKNOWN" if isinstance(error, PlaywrightTimeoutError) else "FAILED"
            return self.connector._attempt(
                action_kind="browser.web.execute", url=final_url,
                idempotency_key=key, payload_hash=request_hash,
                outcome=outcome,
                error_code="timeout" if outcome == "UNKNOWN" else "browser_action_failed",
                before_state=target.to_evidence(),
                after_state={"detail": redact_text(str(error))[:300]},
            )
        after_url, after_revision, after_structure_hash = self._current_revision()
        after = {
            "final_url": after_url,
            "page_revision": after_revision,
            "structural_hash": after_structure_hash,
            "target_id": target.target_id,
            "action": target.action,
        }
        if self._route_error in {
            "mutating_request_blocked", "redirect_policy_rejected", "redirect_limit",
            "stop_requested",
        }:
            return self.connector._attempt(
                action_kind="browser.web.execute", url=after_url,
                idempotency_key=key, payload_hash=request_hash,
                outcome="CANCELLED" if self._route_error == "stop_requested" else "UNKNOWN",
                error_code=self._route_error,
                before_state=target.to_evidence(), after_state=after,
            )
        if stop_check is not None and stop_check():
            return self.connector._attempt(
                action_kind="browser.web.execute", url=after_url,
                idempotency_key=key, payload_hash=request_hash,
                outcome="UNKNOWN", error_code="stop_after_effect",
                before_state=target.to_evidence(), after_state=after,
            )
        return self.connector._attempt(
            action_kind="browser.web.execute", url=after_url,
            idempotency_key=key, payload_hash=request_hash,
            outcome="COMPLETED", before_state=target.to_evidence(),
            after_state=after, provider_ref=after_url, reconciled=True,
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
    "BrowserPageIdentity",
    "BrowserReadResult",
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
