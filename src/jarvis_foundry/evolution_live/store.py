"""Durable isolated proving store for R11 evolution cycles.

The store keeps the kernel ledger, release snapshots, the current live file
tree, a restart-safe effect journal, and preserved failed history.  All of it
lives under the R11 temp proving area; no live or protected state is touched.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jarvis_foundry.evolution_live.events import r11_ledger
from jarvis_foundry.evolution_live.projections import replay_snapshot
from jarvis_foundry.kernel.clock import Clock, FixedClock, SystemClock
from jarvis_foundry.kernel.ledger import Ledger
from jarvis_foundry.kernel.projections import ProjectionSnapshot


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def json_hash(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha256_bytes(encoded)


def file_manifest(root: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    if not root.is_dir():
        return entries
    for path in sorted(root.rglob("*")):
        if path.is_file() and not _excluded_part(path.parts):
            entries.append(
                {
                    "path": path.relative_to(root).as_posix(),
                    "sha256": sha256_file(path),
                    "size": path.stat().st_size,
                }
            )
    return entries


def tree_hash(root: Path) -> str:
    return json_hash({"files": file_manifest(root)})


def _excluded_part(parts: tuple[str, ...]) -> bool:
    return any(part in {".git", "__pycache__"} for part in parts)


def copy_tree(source: Path, destination: Path) -> None:
    if destination.is_dir():
        shutil.rmtree(destination)
    destination.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        shutil.copytree(
            source,
            destination,
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns(".git", "__pycache__"),
        )


@dataclass(frozen=True)
class ReleaseSnapshot:
    release_id: str
    cycle_id: str
    kind: str
    tree_hash: str
    manifest: tuple[dict[str, Any], ...]
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "release_id": self.release_id,
            "cycle_id": self.cycle_id,
            "kind": self.kind,
            "tree_hash": self.tree_hash,
            "manifest": list(self.manifest),
            "created_at": self.created_at,
        }


class EffectJournal:
    """Append-only filesystem effect journal used for restart safety."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.is_file():
            path.write_text("{}\n", encoding="utf-8")

    def _read(self) -> dict[str, Any]:
        payload: Any = json.loads(self.path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}

    def _write(self, entries: dict[str, Any]) -> None:
        self.path.write_text(json.dumps(entries, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def apply(self, effect_id: str, perform: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        entries = self._read()
        existing = entries.get(effect_id)
        if existing is not None:
            return {
                "status": "replayed",
                "effect_id": effect_id,
                "result_hash": str(existing["result_hash"]),
                "replayed": True,
            }
        result = perform()
        result_hash = str(result.get("result_hash", ""))
        if not result_hash:
            raise RuntimeError(f"effect {effect_id} produced no result_hash")
        entries[effect_id] = {
            "result_hash": result_hash,
            "at": result.get("at", ""),
        }
        self._write(entries)
        return {
            "status": "applied",
            "effect_id": effect_id,
            "result_hash": result_hash,
            "replayed": False,
            **result,
        }

    def entries(self) -> dict[str, Any]:
        return self._read()


class EvolutionStore:
    """Isolated proving store backed by the durable kernel ledger."""

    def __init__(
        self,
        root: Path,
        *,
        fixed_utc: str | None = None,
        ledger: Ledger | None = None,
    ) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.stores_dir = root / "stores"
        self.releases_dir = root / "releases"
        self.live_dir = root / "live"
        self.failed_history_dir = root / "failed_history"
        self.verification_dir = root / "verification"
        for directory in (
            self.stores_dir,
            self.releases_dir,
            self.live_dir,
            self.failed_history_dir,
            self.verification_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)
        self.effects_path = self.stores_dir / "effects.json"
        self.ledger_path = self.stores_dir / "ledger.db"
        self._clock: Clock = FixedClock(fixed_utc) if fixed_utc else SystemClock()
        self.ledger = (
            ledger if ledger is not None else r11_ledger(self.ledger_path, fixed_utc=fixed_utc)
        )
        self.journal = EffectJournal(self.effects_path)

    def now_utc(self) -> str:
        return self._clock.now_utc()

    def snapshot(self) -> ProjectionSnapshot:
        return replay_snapshot(self.ledger.read())

    def cycle_state(self, cycle_id: str) -> dict[str, Any] | None:
        snapshot = self.snapshot()
        cycles = snapshot.states.get("cycles", {})
        value = cycles.get(cycle_id)
        return dict(value) if isinstance(value, dict) else None

    def release_dir(self, release_id: str) -> Path:
        return self.releases_dir / release_id / "files"

    def create_release(
        self,
        *,
        release_id: str,
        cycle_id: str,
        kind: str,
        source_dir: Path,
        at: str,
    ) -> ReleaseSnapshot:
        target = self.release_dir(release_id)
        copy_tree(source_dir, target)
        snapshot = ReleaseSnapshot(
            release_id=release_id,
            cycle_id=cycle_id,
            kind=kind,
            tree_hash=tree_hash(target),
            manifest=tuple(file_manifest(target)),
            created_at=at,
        )
        meta = target.parent / "meta.json"
        meta.write_text(
            json.dumps(snapshot.to_dict(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return snapshot

    def load_release(self, release_id: str) -> ReleaseSnapshot | None:
        meta = self.releases_dir / release_id / "meta.json"
        if not meta.is_file():
            return None
        payload = json.loads(meta.read_text(encoding="utf-8"))
        return ReleaseSnapshot(
            release_id=str(payload["release_id"]),
            cycle_id=str(payload["cycle_id"]),
            kind=str(payload["kind"]),
            tree_hash=str(payload["tree_hash"]),
            manifest=tuple(payload.get("manifest", [])),
            created_at=str(payload["created_at"]),
        )

    def apply_live_files(self, source_dir: Path) -> dict[str, Any]:
        before = tree_hash(self.live_dir)
        copy_tree(source_dir, self.live_dir)
        after = tree_hash(self.live_dir)
        return {"before_hash": before, "after_hash": after}

    def preserve_failed_history(self, cycle_id: str, source_dir: Path) -> dict[str, Any]:
        ref = f"failed_history/{cycle_id}"
        target = self.failed_history_dir / cycle_id
        copy_tree(source_dir, target)
        return {
            "failed_history_ref": ref,
            "failure_hash": tree_hash(target),
        }

    def failed_history_exists(self, cycle_id: str) -> bool:
        return (self.failed_history_dir / cycle_id).is_dir()

    def live_release_id(self) -> str | None:
        snapshot = self.snapshot()
        cycles = snapshot.states.get("cycles", {})
        live_id: str | None = None
        for cycle_id in sorted(cycles):
            state = cycles[cycle_id]
            if not isinstance(state, dict):
                continue
            last = state.get("last_event")
            if last == "evolution_live.promoted":
                live_id = str(state["release_id"])
            elif last == "evolution_live.rolled_back":
                live_id = str(state["prior_release_id"])
            elif last == "evolution_live.restored":
                live_id = str(state["release_id"])
        return live_id
