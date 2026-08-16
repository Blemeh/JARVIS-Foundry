"""Isolated proving-store layout for the R10 Command Center."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_TEMP_BASE = Path("C:/Users/b/AppData/Local/Temp/jarvis-r10-builder")


@dataclass(frozen=True)
class ProvingLayout:
    """Every durable store the Command Center may read or mutate lives under
    one isolated proving root. Live daemon/db/vault state is never touched."""

    root: Path

    @property
    def stores(self) -> Path:
        return self.root / "stores"

    @property
    def portfolio(self) -> Path:
        """PortfolioStore root holding projects/candidates/decisions/controls."""
        return self.stores / "portfolio"

    @property
    def mission_ledger(self) -> Path:
        return self.stores / "mission-ledger.db"

    @property
    def capability_ledger(self) -> Path:
        return self.stores / "capability-ledger.db"

    @property
    def directive_ledger(self) -> Path:
        """Append-only hash-bound JSONL ledger for canonical owner directives."""
        return self.stores / "directive-ledger.jsonl"

    @property
    def capabilities(self) -> Path:
        """Capability registry snapshot extracted from real R9 evidence."""
        return self.stores / "capabilities.json"

    @property
    def missions(self) -> Path:
        """Mission records extracted from real R8 orchestration reports."""
        return self.stores / "missions.json"

    @property
    def findings(self) -> Path:
        return self.stores / "findings.json"

    @property
    def releases(self) -> Path:
        return self.stores / "releases.json"

    @property
    def voice(self) -> Path:
        return self.stores / "voice-probe.json"

    @property
    def snapshot(self) -> Path:
        return self.stores / "snapshot.json"

    @property
    def web(self) -> Path:
        return self.root / "web"

    @property
    def audio(self) -> Path:
        return self.root / "audio"

    def ensure(self) -> ProvingLayout:
        for path in (self.root, self.stores, self.web, self.audio):
            path.mkdir(parents=True, exist_ok=True)
        return self


def default_proving_layout() -> ProvingLayout:
    base = Path(os.environ.get("JARVIS_R10_TEMP", str(DEFAULT_TEMP_BASE)))
    return ProvingLayout(base)
