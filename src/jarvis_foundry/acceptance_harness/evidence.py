"""Append-only evidence writing and integrity checks for harness runs."""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

from .contracts import RunCheckpoint, ScenarioSpec


_SECRET_KEY = re.compile(r"(?:secret|token|password|credential|api[_-]?key|private[_-]?key)", re.I)
_SECRET_VALUE = re.compile(r"(?:sk-[A-Za-z0-9_-]{12,}|Bearer\s+[A-Za-z0-9._-]{12,})", re.I)


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def redact(value: Any, *, key: str | None = None) -> Any:
    if key and _SECRET_KEY.search(key):
        return "<REDACTED>"
    if isinstance(value, Mapping):
        return {str(name): redact(item, key=str(name)) for name, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [redact(item) for item in value]
    if isinstance(value, str) and _SECRET_VALUE.search(value):
        return _SECRET_VALUE.sub("<REDACTED>", value)
    return value


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(redact(value), indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")


class EvidenceWriter:
    """Persist one run under an owned directory with resumable writes."""

    required_artifacts = (
        "RUN_MANIFEST.json",
        "SCENARIO.json",
        "TIMELINE.jsonl",
        "INVARIANTS.jsonl",
        "FAULTS.jsonl",
        "RESOURCE_SAMPLES.jsonl",
        "AUTONOMY_METRICS.json",
        "INCIDENT_SUMMARY.json",
        "EFFECT_SUMMARY.json",
        "BRIEFING_SUMMARY.json",
        "FINAL_VERDICT.json",
        "FINAL_REPORT.md",
    )

    def __init__(self, root: str | Path, *, run_id: str, scenario: ScenarioSpec) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.run_id = run_id
        self.scenario = scenario
        self._seen_keys: dict[tuple[str, str], set[Any]] = {}
        self._loaded_key_files: set[tuple[str, str]] = set()

    def path(self, name: str) -> Path:
        if Path(name).name != name or name in {".", ".."}:
            raise ValueError("evidence artifact names must be plain filenames")
        return self.root / name

    def write_bytes(self, name: str, data: bytes) -> Path:
        target = self.path(name)
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_bytes(data)
        os.replace(temporary, target)
        return target

    def write_json(self, name: str, value: Any) -> Path:
        return self.write_bytes(name, _json_bytes(value))

    def write_text(self, name: str, value: str) -> Path:
        return self.write_bytes(name, value.encode("utf-8"))

    def append_jsonl(self, name: str, value: Mapping[str, Any], *, key_field: str | None = None) -> Path:
        target = self.path(name)
        row = redact(dict(value))
        if key_field is not None:
            key = row.get(key_field)
            if key is None:
                raise ValueError(f"{key_field} is required for idempotent JSONL append")
            cache_key = (name, key_field)
            seen = self._seen_keys.setdefault(cache_key, set())
            if cache_key not in self._loaded_key_files and target.exists():
                for line in target.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    try:
                        existing_key = json.loads(line).get(key_field)
                        if existing_key is not None:
                            seen.add(existing_key)
                    except (TypeError, json.JSONDecodeError):
                        raise ValueError(f"malformed existing evidence in {target}")
                self._loaded_key_files.add(cache_key)
            if key in seen:
                return target
            seen.add(key)
            self._loaded_key_files.add(cache_key)
        with target.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n")
        return target

    def write_checkpoint(self, checkpoint: RunCheckpoint) -> Path:
        return self.write_json("CHECKPOINT.json", checkpoint.to_mapping())

    def read_checkpoint(self) -> RunCheckpoint | None:
        path = self.path("CHECKPOINT.json")
        if not path.exists():
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
        return RunCheckpoint.from_mapping(value)

    def write_scenario(self) -> Path:
        return self.write_json("SCENARIO.json", self.scenario.to_mapping())

    def missing_artifacts(self) -> list[str]:
        return [
            name for name in self.required_artifacts
            if name != "RUN_MANIFEST.json" and not self.path(name).exists()
        ]

    def artifact_hashes(self) -> dict[str, str]:
        return {
            name: hashlib.sha256(self.path(name).read_bytes()).hexdigest()
            for name in self.required_artifacts
            if name != "RUN_MANIFEST.json" and self.path(name).exists()
        }

    def write_manifest(self, *, status: str, phase: str, extra: Mapping[str, Any] | None = None) -> Path:
        payload = {
            "harness": "jarvis-unattended-acceptance",
            "run_id": self.run_id,
            "scenario_id": self.scenario.scenario_id,
            "generated_at": utc_now(),
            "status": status,
            "phase": phase,
            "scope": "disposable fixtures only",
            "artifact_hashes": self.artifact_hashes(),
            "missing_artifacts": self.missing_artifacts(),
        }
        if extra:
            payload["extra"] = dict(extra)
        return self.write_json("RUN_MANIFEST.json", payload)

    def verify_hashes(self, manifest: Mapping[str, Any]) -> list[str]:
        mismatches: list[str] = []
        hashes = manifest.get("artifact_hashes", {})
        if not isinstance(hashes, Mapping):
            return ["artifact_hashes is not an object"]
        for name, expected in hashes.items():
            path = self.path(str(name))
            if not path.exists():
                mismatches.append(f"missing artifact: {name}")
                continue
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
            if actual != expected:
                mismatches.append(f"hash mismatch: {name}")
        return mismatches
