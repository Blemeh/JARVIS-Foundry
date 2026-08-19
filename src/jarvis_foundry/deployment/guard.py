"""Refuse live Foundry home starts from a mutable git checkout."""
from __future__ import annotations

from pathlib import Path

from ..runtime.authority import runtime_source_identity
from .layout import current_pointer, default_live_foundry_home
from .release import ReleaseError


def refuse_developer_checkout_on_live_home(home: Path) -> None:
    if home.resolve() != default_live_foundry_home():
        return
    if not current_pointer().is_file():
        return
    identity = runtime_source_identity()
    if identity.get("source_kind") == "git_checkout":
        raise ReleaseError(
            "refusing to open the live Foundry home from a developer git checkout "
            "after an immutable CURRENT release exists"
        )
