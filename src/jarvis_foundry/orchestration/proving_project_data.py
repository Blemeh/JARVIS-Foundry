"""Real proving-project sources shared by stage setup and R8 tests."""

from __future__ import annotations

ALPHA_STORE_SOURCE = '''\
"""Append-only JSONL event store with per-line payload hashes."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


class IntegrityError(RuntimeError):
    """Raised when the event file fails payload-hash verification."""


class ConflictError(RuntimeError):
    """Raised when an idempotency key is reused with different content."""


def _payload_hash(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class EventStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._records: list[tuple[dict[str, Any], str]] = []
        self._keys: dict[str, int] = {}
        self._sequence = 0
        if self.path.is_file():
            self._load()

    def _load(self) -> None:
        for line_no, raw in enumerate(
            self.path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if not raw.strip():
                continue
            marker = raw.rfind("|")
            if marker < 0:
                raise IntegrityError(f"line {line_no} has no hash marker")
            body, digest = raw[:marker], raw[marker + 1 :]
            try:
                record = json.loads(body)
            except json.JSONDecodeError as exc:
                raise IntegrityError(f"line {line_no} is not valid JSON: {exc}") from exc
            if _payload_hash(record) != digest:
                raise IntegrityError(f"line {line_no} payload hash mismatch")
            self._apply_record(record)

    def _apply_record(self, record: dict[str, Any]) -> None:
        sequence = int(record["sequence"])
        self._records.append((record, _payload_hash(record)))
        self._keys[str(record["idempotency_key"])] = sequence
        self._sequence = max(self._sequence, sequence)

    def append(self, payload: Mapping[str, Any], *, idempotency_key: str) -> int:
        if idempotency_key in self._keys:
            stored = next(
                record
                for record, _ in self._records
                if record["idempotency_key"] == idempotency_key
            )
            if stored["payload"] != dict(payload):
                raise ConflictError(f"idempotency key {idempotency_key!r} reused differently")
            return int(stored["sequence"])
        sequence = self._sequence + 1
        record = {
            "sequence": sequence,
            "idempotency_key": idempotency_key,
            "payload": dict(payload),
        }
        digest = _payload_hash(record)
        line = (
            json.dumps(record, sort_keys=True, separators=(",", ":"))
            + "|"
            + digest
            + "\\n"
        )
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(line)
        self._records.append((record, digest))
        self._keys[idempotency_key] = sequence
        self._sequence = sequence
        return sequence

    def read(self) -> list[dict[str, Any]]:
        return [record["payload"] for record, _ in self._records]

    def verify(self) -> None:
        self._records = []
        self._keys = {}
        self._sequence = 0
        if self.path.is_file():
            self._load()

    def state_hash(self) -> str:
        encoded = json.dumps(
            [record for record, _ in self._records],
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def event_count(self) -> int:
        return len(self._records)
'''

ALPHA_TEST_SOURCE = '''\
"""Initial smoke tests for the alpha_ledger event store."""

from __future__ import annotations

from pathlib import Path

from alpha_ledger.store import EventStore


def test_append_and_read(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "events.jsonl")
    store.append({"kind": "a"}, idempotency_key="k1")
    store.append({"kind": "b"}, idempotency_key="k2")
    assert [record["kind"] for record in store.read()] == ["a", "b"]


def test_verify_reloads(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    EventStore(path).append({"kind": "a"}, idempotency_key="k1")
    reloaded = EventStore(path)
    reloaded.verify()
    assert reloaded.event_count() == 1
'''

ALPHA_README_SOURCE = """\
# alpha_ledger

Small append-only JSONL event store with per-line payload hashes, used as a
real bounded proving project for the R8 autonomous production machinery.

## Commands

```text
python -m pytest tests -q
```
"""

ALPHA_PYPROJECT_SOURCE = """\
[project]
name = "alpha-ledger"
version = "0.1.0"
requires-python = ">=3.13"
dependencies = []

[tool.pytest.ini_options]
pythonpath = ["src"]
testpaths = ["tests"]
"""

REPRO_TEST_SOURCE = '''\
"""Real adversarial regression test: a verifier must not trust worker claims."""

from __future__ import annotations

import subprocess
from pathlib import Path

from jarvis_foundry.orchestration.paths import sha256_file
from jarvis_foundry.orchestration.schemas import FileChange, WorkerResult
from jarvis_foundry.orchestration.verify import Verifier


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.txt").write_text("a", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=str(repo), check=True)
    subprocess.run(["git", "config", "user.email", "r8@test.local"], cwd=str(repo), check=True)
    subprocess.run(["git", "config", "user.name", "R8 Test"], cwd=str(repo), check=True)
    subprocess.run(["git", "add", "a.txt"], cwd=str(repo), check=True)
    subprocess.run(["git", "commit", "-q", "-m", "baseline"], cwd=str(repo), check=True)
    (repo / "a.txt").write_text("a2", encoding="utf-8")
    (repo / "b.txt").write_text("b", encoding="utf-8")
    return repo


def test_verifier_rejects_unclaimed_changes(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    claimed = FileChange(
        path="a.txt",
        action="modified",
        sha256=sha256_file(repo / "a.txt"),
        size=(repo / "a.txt").stat().st_size,
    )
    result = WorkerResult(
        status="PASS",
        claims={},
        files_changed=(claimed,),
        commands_run=(),
        tests=(),
        evidence=(),
        artifacts=(),
        external_effects={},
        blockers=(),
        uncertainty="low",
        recommended_next_action="none",
    )
    verifier = Verifier(
        python=Path("python"),
        verifier_id="repro-verifier",
        verified_at="2026-08-15T20:30:00Z",
        proving_root=tmp_path,
    )
    verdict = verifier.verify(repo, result, [])
    assert any(issue.field == "completeness" for issue in verdict.issues), str(verdict.to_dict())
'''
