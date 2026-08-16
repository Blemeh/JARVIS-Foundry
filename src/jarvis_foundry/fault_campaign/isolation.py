"""Isolated proving workspace helpers for the R12 fault campaign."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from jarvis_foundry.capabilities_live.ledger_events import r9_ledger
from jarvis_foundry.capabilities_live.registry import ActivationProof, CapabilityRegistry
from jarvis_foundry.command_center.proving import ProvingLayout
from jarvis_foundry.evolution_live.store import EvolutionStore, copy_tree, tree_hash
from jarvis_foundry.kernel.clock import FixedClock
from jarvis_foundry.kernel.ledger import Ledger
from jarvis_foundry.kernel.portfolio.records import (
    CandidateAction,
    PortfolioProject,
    ProjectRisk,
    SourceRef,
)
from jarvis_foundry.kernel.portfolio.store import PortfolioStore
from jarvis_foundry.orchestration.ledger_events import r8_ledger

DEFAULT_TEMP_BASE = Path("C:/Users/b/AppData/Local/Temp/jarvis-r12-builder")

PROTECTED_ROOTS: tuple[Path, ...] = (
    Path("C:/Users/b/Documents/ChatGPT/Autonomous AI powered by Hoowen/JARVIS-Foundry"),
    Path("C:/Users/b/AppData/Local/hermes/foundry"),
    Path("C:/Users/b/Blendi"),
)


def temp_base() -> Path:
    return Path(os.environ.get("JARVIS_R12_TEMP", str(DEFAULT_TEMP_BASE)))


def utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def json_hash(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha256_bytes(encoded)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def safe_clear(path: Path) -> None:
    """Clear a path only when it is inside the R12 proving root."""
    resolved = path.resolve()
    base = temp_base().resolve()
    if not resolved.is_relative_to(base):
        raise RuntimeError(f"refusing to clear outside R12 temp: {resolved}")
    if resolved == base:
        raise RuntimeError("refusing to clear the R12 temp root itself")
    if resolved.is_dir():
        for child in resolved.rglob("*"):
            if child.is_file():
                child.chmod(stat.S_IWRITE | stat.S_IREAD)
        shutil.rmtree(resolved)
    elif resolved.is_file():
        resolved.unlink(missing_ok=True)


class ProvingWorkspace:
    """One isolated proving area for a fault scenario."""

    def __init__(self, root: Path, *, fixed_utc: str) -> None:
        resolved = root.resolve()
        if not resolved.is_relative_to(temp_base().resolve()):
            raise RuntimeError(f"proving workspace outside R12 temp: {resolved}")
        for protected in PROTECTED_ROOTS:
            if resolved.is_relative_to(protected.resolve()) or protected.resolve().is_relative_to(
                resolved
            ):
                raise RuntimeError(f"proving workspace overlaps protected root {protected}")
        self.root = resolved
        self.fixed_utc = fixed_utc
        for sub in ("stores", "live", "releases", "logs", "queues", "fixtures"):
            (self.root / sub).mkdir(parents=True, exist_ok=True)
        self._clock = FixedClock(fixed_utc)

    def fresh(self, name: str) -> ProvingWorkspace:
        target = self.root / name
        safe_clear(target)
        return ProvingWorkspace(target, fixed_utc=self.fixed_utc)

    def ledger(self, registry: str = "capability") -> Ledger:
        if registry == "capability":
            return r9_ledger(
                self.root / "stores" / "capability-ledger.db", fixed_utc=self.fixed_utc
            )
        if registry == "evolution":
            from jarvis_foundry.evolution_live.events import r11_ledger

            return r11_ledger(
                self.root / "stores" / "evolution-ledger.db", fixed_utc=self.fixed_utc
            )
        if registry == "orchestration":
            return r8_ledger(
                self.root / "stores" / "orchestration-ledger.db", fixed_utc=self.fixed_utc
            )
        raise ValueError(f"unknown registry {registry}")

    def portfolio(self, name: str = "portfolio") -> PortfolioStore:
        return PortfolioStore(self.root / "stores" / name)

    def capability_registry(self, name: str = "capability") -> CapabilityRegistry:
        return CapabilityRegistry(
            r9_ledger(self.root / "stores" / f"{name}-ledger.db", fixed_utc=self.fixed_utc),
            clock=self._clock,
        )

    def evolution(self, name: str = "evolution") -> EvolutionStore:
        return EvolutionStore(self.root / "stores" / name, fixed_utc=self.fixed_utc)

    def layout(self) -> ProvingLayout:
        return ProvingLayout(self.root)

    def fixture_dir(self, name: str = "target") -> Path:
        target = self.root / "fixtures" / name
        target.mkdir(parents=True, exist_ok=True)
        return target

    def manifest(self) -> list[dict[str, str]]:
        entries: list[dict[str, str]] = []
        for path in sorted(self.root.rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts and ".git" not in path.parts:
                entries.append(
                    {
                        "path": path.relative_to(self.root).as_posix(),
                        "sha256": sha256_file(path),
                        "size": str(path.stat().st_size),
                    }
                )
        return entries

    def tree_hash(self) -> str:
        return json_hash({"files": self.manifest()})

    def snapshot_bytes(self) -> bytes:
        return json.dumps(self.manifest(), sort_keys=True).encode("utf-8")


def seed_micro_fixture(workspace: ProvingWorkspace, name: str = "target") -> Path:
    """Write a small deterministic micro target used by evolution scenarios."""
    target = workspace.fixture_dir(name)
    values_path = target / "values.py"
    values_path.write_text("SCORING_ENABLED = True\nDEFAULT_BUDGET_USD = 0.0\n", encoding="utf-8")
    probe_path = target / "probe.py"
    probe_path.write_text(
        "import json\n"
        "from values import SCORING_ENABLED\n"
        "print(json.dumps({'checks': {'scoring_enabled': SCORING_ENABLED}, "
        "'passed': 1 if SCORING_ENABLED else 0, 'total': 1}))\n",
        encoding="utf-8",
    )
    tests_dir = target / "tests"
    tests_dir.mkdir(parents=True, exist_ok=True)
    (tests_dir / "test_values.py").write_text(
        "from values import SCORING_ENABLED\n\n"
        "def test_scoring_enabled():\n    assert SCORING_ENABLED is True\n",
        encoding="utf-8",
    )
    return target


def seed_portfolio(store: PortfolioStore, *, fixed_utc: str) -> None:
    """Seed two real projects and candidates for portfolio decisions."""
    store.append_project(
        PortfolioProject(
            project_id="p-alpha",
            title="Alpha goal",
            canonical_target="fixtures/alpha",
            goal_id="g-alpha",
            goal_title="Alpha objective",
            status="ACTIVE",
            deadline=None,
            resources=("fixtures/alpha",),
            cost_usd=0.0,
            expected_value=0.9,
            urgency=0.8,
            information_gain=0.7,
            reversibility=0.6,
            opportunity_cost=0.2,
            risk=0.1,
            dependencies=(),
            risks=(
                ProjectRisk(
                    risk_id="r-alpha-1",
                    title="low risk",
                    severity="low",
                    likelihood="unlikely",
                    source_ref=SourceRef(
                        source_type="observation",
                        source_id="obs-alpha",
                        captured_at=fixed_utc,
                        content_hash="a" * 64,
                    ),
                ),
            ),
            source_ref=SourceRef(
                source_type="observation",
                source_id="obs-alpha",
                captured_at=fixed_utc,
                content_hash="a" * 64,
            ),
            observed_at=fixed_utc,
        )
    )
    store.append_candidate(
        CandidateAction(
            candidate_id="c-alpha-1",
            project_id="p-alpha",
            objective="advance alpha",
            action_class="read_only",
            canonical_target="fixtures/alpha",
            evidence_refs=(
                SourceRef(
                    source_type="observation",
                    source_id="obs-alpha",
                    captured_at=fixed_utc,
                    content_hash="a" * 64,
                ),
            ),
            expected_value=0.8,
            uncertainty=0.1,
            urgency=0.7,
            information_gain=0.6,
            reversibility=0.9,
            opportunity_cost=0.1,
            risk=0.05,
            cost_usd=0.0,
            budget_effects=0,
            status="READY",
        )
    )
    store.append_project(
        PortfolioProject(
            project_id="p-beta",
            title="Beta goal",
            canonical_target="fixtures/beta",
            goal_id="g-beta",
            goal_title="Beta objective",
            status="ACTIVE",
            deadline=None,
            resources=("fixtures/beta",),
            cost_usd=0.0,
            expected_value=0.4,
            urgency=0.3,
            information_gain=0.2,
            reversibility=0.9,
            opportunity_cost=0.4,
            risk=0.3,
            dependencies=(),
            risks=(),
            source_ref=SourceRef(
                source_type="observation",
                source_id="obs-beta",
                captured_at=fixed_utc,
                content_hash="b" * 64,
            ),
            observed_at=fixed_utc,
        )
    )
    store.append_candidate(
        CandidateAction(
            candidate_id="c-beta-1",
            project_id="p-beta",
            objective="advance beta",
            action_class="read_only",
            canonical_target="fixtures/beta",
            evidence_refs=(
                SourceRef(
                    source_type="observation",
                    source_id="obs-beta",
                    captured_at=fixed_utc,
                    content_hash="b" * 64,
                ),
            ),
            expected_value=0.3,
            uncertainty=0.2,
            urgency=0.2,
            information_gain=0.2,
            reversibility=0.8,
            opportunity_cost=0.3,
            risk=0.2,
            cost_usd=0.0,
            budget_effects=0,
            status="READY",
        )
    )


def seed_capability(registry: CapabilityRegistry, capability_id: str) -> None:
    """Drive a capability to ACTIVE with a real before/after proof."""
    registry.discover(
        capability_id=capability_id,
        domain="files",
        name="Isolated file capability",
        system_id="r12-proving",
    )
    registry.probe(capability_id, probe_note="r12 probe")
    registry.mark_ready(capability_id, reason="probe succeeded")
    registry.activate(
        capability_id,
        ActivationProof(
            before_state_hash="before" + "0" * 58,
            after_state_hash="after" + "0" * 57,
            receipt_hash="receipt" + "0" * 55,
            reconciliation={"reconciled": True, "probe": "read-back"},
        ),
    )


def copy_tree_into(source: Path, destination: Path) -> None:
    copy_tree(source, destination)


def tree_hash_of(root: Path) -> str:
    return tree_hash(root)
