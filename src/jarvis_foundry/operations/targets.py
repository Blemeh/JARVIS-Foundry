"""Exact application, window, document, and structural browser targets.

The broad-operation layer may observe titles and geometry, but those values do
not establish authority.  A target is resolvable only when an exact stable
identity (process/executable, accessibility id, browser document/url, or
document reference) selects one candidate.  The browser helper below uses
DOM-like control identity and revision checks; it intentionally has no
coordinate fallback.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field, replace
from typing import Any, Iterable, Mapping

from ..events import canonical_json
from .procedure import OperationError


class TargetResolutionError(OperationError):
    """A target or structural control cannot be resolved safely."""


class TargetNotFound(TargetResolutionError):
    pass


class AmbiguousTarget(TargetResolutionError):
    pass


class ControlNotFound(TargetResolutionError):
    pass


class AmbiguousControl(TargetResolutionError):
    pass


class ControlUnavailable(TargetResolutionError):
    pass


class StalePage(TargetResolutionError):
    pass


class UnexpectedRedirect(TargetResolutionError):
    pass


class ModalContextRequired(TargetResolutionError):
    pass


_WILDCARD = re.compile(r"(?:\$\(|\$\{|%[^%]+%|\*|<[^>]+>)")


def _exact(value: object, *, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TargetResolutionError(f"{field_name} must be a non-empty string")
    result = value.strip()
    if any(ord(char) < 32 for char in result):
        raise TargetResolutionError(f"{field_name} contains a control character")
    if _WILDCARD.search(result):
        raise TargetResolutionError(f"{field_name} must be exact, not a wildcard")
    return result


def _optional_exact(value: object, *, field_name: str) -> str | None:
    if value is None:
        return None
    return _exact(value, field_name=field_name)


@dataclass(frozen=True, slots=True)
class TargetSpec:
    """A deterministic target identity used by an operation step."""

    target_id: str
    process_name: str | None = None
    executable_path: str | None = None
    process_id: int | None = None
    window_accessibility_id: str | None = None
    browser_url: str | None = None
    browser_document_id: str | None = None
    document_ref: str | None = None

    def __post_init__(self) -> None:
        target_id = _exact(self.target_id, field_name="target_id")
        names = {
            "process_name": _optional_exact(self.process_name, field_name="process_name"),
            "executable_path": _optional_exact(self.executable_path, field_name="executable_path"),
            "window_accessibility_id": _optional_exact(
                self.window_accessibility_id, field_name="window_accessibility_id"),
            "browser_url": _optional_exact(self.browser_url, field_name="browser_url"),
            "browser_document_id": _optional_exact(
                self.browser_document_id, field_name="browser_document_id"),
            "document_ref": _optional_exact(self.document_ref, field_name="document_ref"),
        }
        if self.process_id is not None and (
            not isinstance(self.process_id, int) or self.process_id <= 0
        ):
            raise TargetResolutionError("process_id must be a positive integer")
        if not any(value is not None for value in (*names.values(), self.process_id)):
            raise TargetResolutionError(
                "target requires a stable process, window, browser, or document identity"
            )
        object.__setattr__(self, "target_id", target_id)
        for key, value in names.items():
            object.__setattr__(self, key, value)

    @property
    def stable_keys(self) -> tuple[str, ...]:
        return tuple(
            key for key in (
                "process_name", "executable_path", "process_id",
                "window_accessibility_id", "browser_url",
                "browser_document_id", "document_ref",
            ) if getattr(self, key) is not None
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "target_id": self.target_id,
            "process_name": self.process_name,
            "executable_path": self.executable_path,
            "process_id": self.process_id,
            "window_accessibility_id": self.window_accessibility_id,
            "browser_url": self.browser_url,
            "browser_document_id": self.browser_document_id,
            "document_ref": self.document_ref,
        }


@dataclass(frozen=True, slots=True)
class TargetObservation:
    """One observed process/window/document candidate."""

    observation_id: str
    process_name: str | None = None
    executable_path: str | None = None
    process_id: int | None = None
    window_accessibility_id: str | None = None
    window_title: str | None = None
    browser_url: str | None = None
    browser_document_id: str | None = None
    document_ref: str | None = None
    document_revision: str | None = None
    visible: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "observation_id", _exact(
            self.observation_id, field_name="observation_id"))
        for key in (
            "process_name", "executable_path", "window_accessibility_id",
            "window_title", "browser_url", "browser_document_id",
            "document_ref", "document_revision",
        ):
            object.__setattr__(self, key, _optional_exact(
                getattr(self, key), field_name=key))
        if not isinstance(self.visible, bool):
            raise TargetResolutionError("visible must be boolean")

    @property
    def fingerprint(self) -> str:
        payload = {
            "observation_id": self.observation_id,
            "process_name": self.process_name,
            "executable_path": self.executable_path,
            "process_id": self.process_id,
            "window_accessibility_id": self.window_accessibility_id,
            "browser_url": self.browser_url,
            "browser_document_id": self.browser_document_id,
            "document_ref": self.document_ref,
            "document_revision": self.document_revision,
        }
        return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class TargetMatch:
    spec: TargetSpec
    observation: TargetObservation
    confidence: str = "EXACT"

    @property
    def identity(self) -> str:
        return self.spec.target_id


class TargetResolver:
    """Resolve a target against a bounded observation set."""

    @staticmethod
    def resolve(
        spec: TargetSpec,
        observations: Iterable[TargetObservation],
    ) -> TargetMatch:
        candidates = []
        for observation in observations:
            if not observation.visible:
                continue
            if all(getattr(observation, key) == getattr(spec, key)
                   for key in spec.stable_keys):
                candidates.append(observation)
        if not candidates:
            raise TargetNotFound(f"no exact observation matches {spec.target_id}")
        if len(candidates) > 1:
            ids = ", ".join(item.observation_id for item in candidates)
            raise AmbiguousTarget(
                f"target {spec.target_id} matched multiple observations: {ids}")
        return TargetMatch(spec=spec, observation=candidates[0])


@dataclass(frozen=True, slots=True)
class ControlNode:
    control_id: str
    role: str
    name: str
    text: str = ""
    value: str = ""
    visible: bool = True
    enabled: bool = True
    document_id: str = ""
    modal_id: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for key in ("control_id", "role", "name"):
            object.__setattr__(self, key, _exact(
                getattr(self, key), field_name=key))
        if self.document_id:
            object.__setattr__(self, "document_id", _exact(
                self.document_id, field_name="document_id"))
        if self.modal_id is not None:
            object.__setattr__(self, "modal_id", _exact(
                self.modal_id, field_name="modal_id"))
        for key in ("text", "value"):
            value = getattr(self, key)
            if not isinstance(value, str) or any(ord(char) < 32 for char in value):
                raise TargetResolutionError(f"{key} must be printable text")
        if not isinstance(self.visible, bool) or not isinstance(self.enabled, bool):
            raise TargetResolutionError("control visibility and enabled state must be boolean")
        if not isinstance(self.attributes, Mapping):
            raise TargetResolutionError("control attributes must be an object")
        object.__setattr__(self, "attributes", dict(self.attributes))


@dataclass(frozen=True, slots=True)
class BrowserPage:
    document_id: str
    url: str
    revision: int
    controls: tuple[ControlNode, ...]
    title: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "document_id", _exact(
            self.document_id, field_name="document_id"))
        object.__setattr__(self, "url", _exact(self.url, field_name="url"))
        if not isinstance(self.revision, int) or self.revision < 0:
            raise TargetResolutionError("page revision must be a non-negative integer")
        controls = tuple(self.controls)
        if len({control.control_id for control in controls}) != len(controls):
            raise TargetResolutionError("browser control ids must be unique")
        for control in controls:
            if control.document_id and control.document_id != self.document_id:
                raise TargetResolutionError("control belongs to a different document")
        object.__setattr__(self, "controls", tuple(
            replace(control, document_id=self.document_id)
            if not control.document_id else control for control in controls
        ))


@dataclass(frozen=True, slots=True)
class BrowserActionResult:
    action: str
    target_id: str
    before_revision: int
    after_revision: int
    result_fingerprint: str


@dataclass(frozen=True, slots=True)
class ControlSelector:
    control_id: str | None = None
    role: str | None = None
    name: str | None = None
    text: str | None = None

    def __post_init__(self) -> None:
        if self.control_id is None and self.name is None and self.text is None:
            raise TargetResolutionError(
                "control selector requires an accessibility id, exact name, or exact text")
        for key in ("control_id", "role", "name", "text"):
            value = getattr(self, key)
            if value is not None:
                object.__setattr__(self, key, _exact(value, field_name=key))


class StructuralBrowser:
    """Small deterministic browser surface for synthetic/local applications."""

    def __init__(self, page: BrowserPage) -> None:
        self._page = page

    @property
    def page(self) -> BrowserPage:
        return self._page

    def navigate(self, page: BrowserPage, *, expected_url: str | None = None) -> BrowserActionResult:
        if expected_url is not None and page.url != _exact(expected_url, field_name="expected_url"):
            raise UnexpectedRedirect(
                f"expected {expected_url!r}, observed {page.url!r}")
        previous_revision = self._page.revision
        self._page = page
        return self._result("navigate", page.document_id, previous_revision)

    def _check_page(
        self,
        *,
        expected_document_id: str | None = None,
        expected_revision: int | None = None,
        expected_url: str | None = None,
    ) -> None:
        if expected_document_id is not None and self._page.document_id != expected_document_id:
            raise StalePage("browser document identity changed")
        if expected_revision is not None and self._page.revision != expected_revision:
            raise StalePage(
                f"browser revision changed: expected {expected_revision}, observed {self._page.revision}")
        if expected_url is not None and self._page.url != expected_url:
            raise StalePage(f"browser URL changed: expected {expected_url}, observed {self._page.url}")

    def select(
        self,
        selector: ControlSelector,
        *,
        expected_document_id: str | None = None,
        expected_revision: int | None = None,
        expected_modal_id: str | None = None,
    ) -> ControlNode:
        self._check_page(
            expected_document_id=expected_document_id,
            expected_revision=expected_revision,
        )
        if expected_modal_id is not None:
            expected_modal_id = _exact(expected_modal_id, field_name="expected_modal_id")
        candidates = []
        for control in self._page.controls:
            if selector.control_id is not None and control.control_id != selector.control_id:
                continue
            if selector.role is not None and control.role != selector.role:
                continue
            if selector.name is not None and control.name != selector.name:
                continue
            if selector.text is not None and control.text != selector.text:
                continue
            candidates.append(control)
        if not candidates:
            raise ControlNotFound(f"no control matches {selector}")
        hidden = [control for control in candidates if not control.visible]
        if hidden:
            raise ControlUnavailable(f"control {hidden[0].control_id} is hidden")
        if len(candidates) > 1:
            ids = ", ".join(control.control_id for control in candidates)
            raise AmbiguousControl(f"selector matched multiple controls: {ids}")
        control = candidates[0]
        if control.modal_id != expected_modal_id:
            if control.modal_id is not None and expected_modal_id is None:
                raise ModalContextRequired(
                    f"control {control.control_id} requires modal {control.modal_id}")
            if expected_modal_id is not None:
                raise ControlNotFound(
                    f"control {control.control_id} is not in modal {expected_modal_id}")
        if not control.enabled:
            raise ControlUnavailable(f"control {control.control_id} is disabled")
        return control

    def enter_text(
        self,
        selector: ControlSelector,
        text: str,
        *,
        expected_revision: int | None = None,
        expected_modal_id: str | None = None,
    ) -> BrowserActionResult:
        control = self.select(
            selector, expected_revision=expected_revision,
            expected_modal_id=expected_modal_id,
        )
        if control.role not in {"textbox", "input", "combobox"}:
            raise ControlUnavailable(f"control {control.control_id} is not a text input")
        if not isinstance(text, str) or any(ord(char) < 32 for char in text):
            raise TargetResolutionError("entered text must not contain control characters")
        before = self._page.revision
        controls = tuple(
            replace(item, value=text) if item.control_id == control.control_id else item
            for item in self._page.controls
        )
        self._page = replace(self._page, revision=before + 1, controls=controls)
        return self._result("enter_text", control.control_id, before)

    def trigger(
        self,
        selector: ControlSelector,
        *,
        expected_revision: int | None = None,
        expected_modal_id: str | None = None,
        result_updates: Mapping[str, Mapping[str, Any]] | None = None,
        navigate_to: BrowserPage | None = None,
    ) -> BrowserActionResult:
        control = self.select(
            selector, expected_revision=expected_revision,
            expected_modal_id=expected_modal_id,
        )
        if control.role not in {"button", "link", "menuitem", "submit"}:
            raise ControlUnavailable(f"control {control.control_id} is not actionable")
        before = self._page.revision
        if navigate_to is not None:
            self._page = navigate_to
        else:
            controls = list(self._page.controls)
            for control_id, changes in (result_updates or {}).items():
                for index, item in enumerate(controls):
                    if item.control_id == control_id:
                        controls[index] = replace(item, **dict(changes))
                        break
                else:
                    raise ControlNotFound(f"result control {control_id} does not exist")
            self._page = replace(self._page, revision=before + 1, controls=tuple(controls))
        return self._result("trigger", control.control_id, before)

    def verify(
        self,
        selector: ControlSelector,
        *,
        expected_text: str | None = None,
        expected_value: str | None = None,
        expected_revision: int | None = None,
        expected_modal_id: str | None = None,
    ) -> ControlNode:
        control = self.select(
            selector, expected_revision=expected_revision,
            expected_modal_id=expected_modal_id,
        )
        if expected_text is not None and control.text != expected_text:
            raise TargetResolutionError(
                f"control {control.control_id} text does not match structural oracle")
        if expected_value is not None and control.value != expected_value:
            raise TargetResolutionError(
                f"control {control.control_id} value does not match structural oracle")
        return control

    def snapshot_fingerprint(self) -> str:
        payload = {
            "document_id": self._page.document_id,
            "url": self._page.url,
            "revision": self._page.revision,
            "controls": [
                {
                    "control_id": item.control_id,
                    "role": item.role,
                    "name": item.name,
                    "text": item.text,
                    "value": item.value,
                    "visible": item.visible,
                    "enabled": item.enabled,
                    "modal_id": item.modal_id,
                }
                for item in self._page.controls
            ],
        }
        return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()

    def _result(self, action: str, target_id: str, before_revision: int) -> BrowserActionResult:
        return BrowserActionResult(
            action=action,
            target_id=target_id,
            before_revision=before_revision,
            after_revision=self._page.revision,
            result_fingerprint=self.snapshot_fingerprint(),
        )


@dataclass(frozen=True, slots=True)
class DesktopWindow:
    """Synthetic accessibility window used for local desktop proofs."""

    observation: TargetObservation
    controls: tuple[ControlNode, ...]
    revision: int = 1

    def __post_init__(self) -> None:
        if not isinstance(self.revision, int) or self.revision < 0:
            raise TargetResolutionError("desktop revision must be non-negative")
        object.__setattr__(self, "controls", tuple(self.controls))


class StructuralDesktop:
    """Accessibility-identity desktop actions with no coordinate fallback."""

    def __init__(self, windows: Iterable[DesktopWindow]) -> None:
        values = tuple(windows)
        ids = [window.observation.observation_id for window in values]
        if len(set(ids)) != len(ids):
            raise TargetResolutionError("desktop observation ids must be unique")
        self._windows = {
            window.observation.observation_id: StructuralBrowser(BrowserPage(
                document_id=window.observation.observation_id,
                url=f"desktop://{window.observation.observation_id}",
                revision=window.revision,
                controls=window.controls,
            )) for window in values
        }
        self._observations = tuple(window.observation for window in values)

    def resolve(self, spec: TargetSpec) -> TargetMatch:
        return TargetResolver.resolve(spec, self._observations)

    def browser(self, spec: TargetSpec) -> StructuralBrowser:
        match = self.resolve(spec)
        return self._windows[match.observation.observation_id]

    def select(self, spec: TargetSpec, selector: ControlSelector, *, expected_revision: int | None = None) -> ControlNode:
        return self.browser(spec).select(selector, expected_revision=expected_revision)

    def enter_text(
        self, spec: TargetSpec, selector: ControlSelector, text: str,
        *, expected_revision: int | None = None,
    ) -> BrowserActionResult:
        return self.browser(spec).enter_text(selector, text, expected_revision=expected_revision)

    def trigger(
        self, spec: TargetSpec, selector: ControlSelector,
        *, expected_revision: int | None = None,
        result_updates: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> BrowserActionResult:
        return self.browser(spec).trigger(
            selector, expected_revision=expected_revision,
            result_updates=result_updates,
        )

    def verify(
        self, spec: TargetSpec, selector: ControlSelector,
        *, expected_text: str | None = None, expected_value: str | None = None,
        expected_revision: int | None = None,
    ) -> ControlNode:
        return self.browser(spec).verify(
            selector, expected_text=expected_text, expected_value=expected_value,
            expected_revision=expected_revision,
        )


__all__ = [
    "AmbiguousControl", "AmbiguousTarget", "BrowserActionResult", "BrowserPage",
    "ControlNode", "ControlNotFound", "ControlSelector", "ControlUnavailable",
    "DesktopWindow", "StructuralDesktop",
    "ModalContextRequired", "StalePage", "StructuralBrowser", "TargetMatch",
    "TargetNotFound", "TargetObservation", "TargetResolutionError", "TargetResolver",
    "TargetSpec", "UnexpectedRedirect",
]
