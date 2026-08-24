"""V7B-3/V7B-4: exact target identity and structural browser actions."""
from __future__ import annotations

import pytest

from jarvis_foundry.operations.targets import (
    AmbiguousControl,
    AmbiguousTarget,
    BrowserPage,
    ControlNode,
    ControlNotFound,
    ControlSelector,
    ControlUnavailable,
    DesktopWindow,
    ModalContextRequired,
    StalePage,
    StructuralBrowser,
    StructuralDesktop,
    TargetObservation,
    TargetResolutionError,
    TargetResolver,
    TargetSpec,
    UnexpectedRedirect,
)


def test_target_resolution_requires_stable_identity_and_rejects_duplicates() -> None:
    with pytest.raises(TargetResolutionError, match="stable"):
        TargetSpec(target_id="app:ambiguous")
    spec = TargetSpec(
        target_id="app:editor",
        process_name="editor.exe",
        executable_path=r"C:\Apps\Editor\editor.exe",
        window_accessibility_id="window:main",
    )
    exact = TargetObservation(
        observation_id="obs:1",
        process_name="editor.exe",
        executable_path=r"C:\Apps\Editor\editor.exe",
        process_id=101,
        window_accessibility_id="window:main",
        window_title="Renamed title is not identity",
    )
    assert TargetResolver.resolve(spec, [exact]).confidence == "EXACT"
    with pytest.raises(AmbiguousTarget, match="multiple"):
        TargetResolver.resolve(spec, [exact, TargetObservation(
            observation_id="obs:2",
            process_name="editor.exe",
            executable_path=r"C:\Apps\Editor\editor.exe",
            process_id=102,
            window_accessibility_id="window:main",
        )])


def _page(*, revision: int = 1) -> BrowserPage:
    return BrowserPage(
        document_id="doc:office",
        url="http://synthetic.local/office",
        revision=revision,
        controls=(
            ControlNode("search", "textbox", "Search"),
            ControlNode("save", "button", "Save", text="Save"),
            ControlNode("status", "status", "Status", text="Ready"),
            ControlNode("modal-save", "button", "Save", modal_id="modal:confirm"),
        ),
    )


def test_structural_actions_ignore_ui_movement_and_verify_dom_state() -> None:
    browser = StructuralBrowser(_page())
    entered = browser.enter_text(
        ControlSelector(control_id="search", role="textbox"), "quarterly",
        expected_revision=1,
    )
    assert entered.after_revision == 2
    triggered = browser.trigger(
        ControlSelector(control_id="save"), expected_revision=2,
        result_updates={"status": {"text": "Saved"}},
    )
    assert triggered.after_revision == 3
    assert browser.verify(
        ControlSelector(control_id="status"), expected_text="Saved",
        expected_revision=3,
    ).text == "Saved"


def test_duplicate_labels_hidden_controls_modal_and_stale_page_are_not_guessed() -> None:
    browser = StructuralBrowser(BrowserPage(
        document_id="doc:duplicate", url="http://synthetic.local/duplicate", revision=7,
        controls=(
            ControlNode("a", "button", "Run", visible=True),
            ControlNode("b", "button", "Run", visible=True),
            ControlNode("hidden", "button", "Hidden", visible=False),
        ),
    ))
    with pytest.raises(AmbiguousControl, match="multiple"):
        browser.select(ControlSelector(role="button", name="Run"))
    with pytest.raises(ControlUnavailable, match="hidden"):
        browser.select(ControlSelector(control_id="hidden"))
    with pytest.raises(StalePage, match="revision"):
        browser.select(ControlSelector(control_id="a"), expected_revision=6)
    with pytest.raises(ControlNotFound):
        browser.select(ControlSelector(control_id="a"), expected_modal_id="modal:confirm")

    modal = StructuralBrowser(_page())
    with pytest.raises(ModalContextRequired, match="modal:confirm"):
        modal.select(ControlSelector(control_id="modal-save"))
    assert modal.select(
        ControlSelector(control_id="modal-save"), expected_modal_id="modal:confirm"
    ).control_id == "modal-save"


def test_delayed_page_and_unexpected_redirect_fail_closed() -> None:
    browser = StructuralBrowser(_page())
    with pytest.raises(UnexpectedRedirect, match="expected"):
        browser.navigate(
            BrowserPage("doc:login", "http://synthetic.local/login", 1, ()),
            expected_url="http://synthetic.local/office",
        )
    browser.navigate(_page(revision=9), expected_url="http://synthetic.local/office")
    with pytest.raises(StalePage, match="document"):
        browser.select(ControlSelector(control_id="save"), expected_document_id="doc:old")


def test_structural_desktop_uses_process_and_accessibility_identity() -> None:
    observation = TargetObservation(
        observation_id="window:editor",
        process_name="synthetic-editor.exe",
        executable_path=r"C:\Synthetic\editor.exe",
        window_accessibility_id="window:editor",
    )
    target = TargetSpec(
        target_id="app:synthetic-editor",
        process_name="synthetic-editor.exe",
        executable_path=r"C:\Synthetic\editor.exe",
        window_accessibility_id="window:editor",
    )
    desktop = StructuralDesktop((DesktopWindow(
        observation=observation,
        controls=(
            ControlNode("input", "textbox", "Document"),
            ControlNode("apply", "button", "Apply", text="Apply"),
            ControlNode("result", "status", "Result", text="Idle"),
        ),
    ),))
    desktop.enter_text(target, ControlSelector(control_id="input"), "safe text", expected_revision=1)
    desktop.trigger(
        target, ControlSelector(control_id="apply"), expected_revision=2,
        result_updates={"result": {"text": "Applied"}},
    )
    assert desktop.verify(
        target, ControlSelector(control_id="result"), expected_text="Applied", expected_revision=3
    ).text == "Applied"
