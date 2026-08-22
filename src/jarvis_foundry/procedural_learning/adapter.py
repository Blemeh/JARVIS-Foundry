"""OpenAdapt-Flow substrate adapter.

This module launches only a frozen bundle after the caller has performed the
JARVIS ActionEnvelope checks.  It has no mission, verification, STOP authority,
or promotion logic of its own.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ..control.redaction import redact_text
from .bundle import verify_bundle
from .contracts import (
    OpenAdaptUnavailable,
    ProcedureBundle,
    ReplayOutcome,
    SUPPORTED_OPENADAPT_FLOW_VERSION,
    validate_parameters,
)
from .process import OwnedProcessHandle, launch_owned


@dataclass(frozen=True, slots=True)
class OpenAdaptConfig:
    flow_executable: Path
    deployment_config: Path | None = None
    browser_url: str | None = None
    allowed_flow_version: str = SUPPORTED_OPENADAPT_FLOW_VERSION
    timeout_s: float = 180.0
    allow_model_grounding: bool = False


def _clean_env() -> dict[str, str]:
    """Remove credentials from the child environment."""
    env = dict(os.environ)
    for key in list(env):
        upper = key.upper()
        if upper.endswith(("_API_KEY", "_TOKEN", "_SECRET", "_PASSWORD")):
            env.pop(key, None)
    env.pop("OPENAI_API_KEY", None)
    return env


def _version_from_output(text: str) -> str | None:
    match = re.search(r"(?:openadapt-flow|version)\s*[=:]?\s*(\d+\.\d+\.\d+)", text, re.I)
    return match.group(1) if match else None


def check_openadapt(config: OpenAdaptConfig) -> dict[str, Any]:
    executable = config.flow_executable.expanduser().resolve()
    if not executable.is_file():
        raise OpenAdaptUnavailable(f"OpenAdapt-Flow executable is missing: {executable}")
    try:
        from ..platform.windows_subprocess import headless_run_kwargs

        result = subprocess.run(
            [str(executable), "--version"], capture_output=True, text=True,
            timeout=15, check=False, env=_clean_env(),
            **headless_run_kwargs(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise OpenAdaptUnavailable(f"cannot invoke OpenAdapt-Flow: {exc}") from exc
    output = f"{result.stdout}\n{result.stderr}"
    version = _version_from_output(output)
    if result.returncode != 0 or version != config.allowed_flow_version:
        raise OpenAdaptUnavailable(
            f"OpenAdapt-Flow version check failed: exit={result.returncode}, version={version!r}")
    return {"executable": str(executable), "version": version, "model_grounding": False}


def _read_report(run_dir: Path) -> tuple[Path | None, int | None]:
    report = run_dir / "report.json"
    if not report.is_file():
        return None, None
    try:
        payload = json.loads(report.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return report, None
    model_calls = payload.get("model_calls")
    if model_calls is None:
        model_calls = payload.get("metrics", {}).get("model_calls")
    try:
        return report, int(model_calls) if model_calls is not None else None
    except (TypeError, ValueError):
        return report, None


def _params_file(run_dir: Path, params: dict[str, str]) -> Path:
    safe = {str(k): str(params[k]) for k in sorted(params)}
    path = run_dir / ".params.json"
    path.write_text(json.dumps(safe, sort_keys=True), encoding="utf-8")
    return path


class OpenAdaptAdapter:
    """Reference-only OpenAdapt adapter used behind JARVIS's executor."""

    def __init__(self, config: OpenAdaptConfig) -> None:
        self.config = config

    def compile_recording(
        self,
        recording_dir: str | Path,
        output_dir: str | Path,
        *,
        name: str,
        params_from: str | Path | None = None,
    ) -> dict[str, Any]:
        """Invoke the qualified OpenAdapt-Flow compiler once."""
        version = check_openadapt(self.config)
        recording = Path(recording_dir).expanduser().resolve()
        output = Path(output_dir).expanduser().resolve()
        if not recording.is_dir():
            raise ValueError(f"recording directory is missing: {recording}")
        output.mkdir(parents=True, exist_ok=True)
        command = [str(self.config.flow_executable), "compile", str(recording),
                   "--out", str(output), "--name", name, "--no-confirm-params"]
        if params_from is not None:
            command.extend(["--params-from", str(Path(params_from).resolve())])
        run_id = f"compile-{uuid.uuid4().hex[:12]}"
        handle = launch_owned(command, run_id=run_id, cwd=output, env=_clean_env())
        code = handle.wait(timeout=self.config.timeout_s)
        output_text = ""
        if handle.process.stdout is not None:
            output_text = handle.process.stdout.read() or ""
        leftovers = handle.terminate_tree(reason="compile cleanup") if code != 0 else ()
        if leftovers:
            raise RuntimeError(f"compiler owned descendants remain: {leftovers}")
        handle.assert_clean()
        if code != 0:
            raise RuntimeError(f"OpenAdapt-Flow compile failed ({code}): {redact_text(output_text)}")
        manifest = output / "manifest.json"
        if not manifest.is_file():
            raise RuntimeError("OpenAdapt-Flow compile returned no manifest.json")
        return {"run_id": run_id, "version": version["version"],
                "bundle_dir": str(output), "output": redact_text(output_text)}

    def replay(
        self,
        bundle: ProcedureBundle,
        *,
        params: dict[str, str],
        run_dir: str | Path,
        stop_path: str | Path | None = None,
        on_started: Callable[[str], None] | None = None,
    ) -> ReplayOutcome:
        """Run OpenAdapt as a substrate and return only its claim.

        JARVIS effect verification must be called by the governing executor;
        this function never labels a provider effect as completed.
        """
        verify_bundle(bundle)
        check_openadapt(self.config)
        normalized_params = validate_parameters(bundle.input_schema, params)
        run_path = Path(run_dir).expanduser().resolve()
        run_path.mkdir(parents=True, exist_ok=True)
        run_id = f"replay-{uuid.uuid4().hex[:12]}"
        if stop_path is not None and Path(stop_path).exists():
            return ReplayOutcome("FROZEN", run_id, None, bundle.bundle_hash,
                                 None, None, reason="STOP active before launch")
        command = [str(self.config.flow_executable), "replay",
                   str(bundle.artifact_dir), "--run-dir", str(run_path)]
        if self.config.deployment_config is not None:
            command.extend(["--config", str(self.config.deployment_config.resolve())])
        if self.config.browser_url:
            command.extend(["--url", self.config.browser_url])
        params_path = _params_file(run_path, normalized_params)
        command.extend(["--params-file", str(params_path), "--backend", "web"])
        if self.config.allow_model_grounding:
            raise OpenAdaptUnavailable("model grounding is forbidden for Phase-4 healthy replay")
        handle = launch_owned(command, run_id=run_id, cwd=run_path, env=_clean_env())
        if on_started is not None:
            on_started(run_id)
        stopped = False
        deadline = time.monotonic() + self.config.timeout_s
        while handle.process.poll() is None:
            handle.refresh()
            if stop_path is not None and Path(stop_path).exists():
                stopped = True
                handle.terminate_tree(reason="STOP during replay")
                break
            if time.monotonic() >= deadline:
                handle.terminate_tree(reason="replay timeout")
                stopped = True
                break
            time.sleep(0.1)
        code = handle.process.poll()
        if code is None:
            code = handle.wait(timeout=10)
        output_text = ""
        if handle.process.stdout is not None:
            output_text = handle.process.stdout.read() or ""
        leftovers = handle.terminate_tree(reason="post-replay cleanup") if stopped else ()
        # A normal process can still have browser children; cleanup is always
        # attempted before returning, even when OpenAdapt reports success.
        handle.refresh()
        if handle.descendants_seen:
            leftovers = handle.terminate_tree(reason="normal completion cleanup")
        try:
            params_path.unlink(missing_ok=True)
        except OSError:
            pass
        try:
            handle.assert_clean()
        except RuntimeError as exc:
            report_path, model_calls = _read_report(run_path)
            return ReplayOutcome(
                "FROZEN", run_id, None, bundle.bundle_hash, model_calls,
                str(report_path) if report_path else None,
                reason=redact_text(str(exc)),
                recovery_classification="AMBIGUOUS",
            )
        leftovers = ()
        report_path, model_calls = _read_report(run_path)
        if leftovers:
            return ReplayOutcome("FROZEN", run_id, None, bundle.bundle_hash,
                                 model_calls, str(report_path) if report_path else None,
                                 reason=f"owned process cleanup incomplete: {leftovers}")
        if stopped or (stop_path is not None and Path(stop_path).exists()):
            return ReplayOutcome("FROZEN", run_id, None, bundle.bundle_hash,
                                 model_calls, str(report_path) if report_path else None,
                                 reason="STOP during replay")
        status = "COMPLETED_UNVERIFIED" if code == 0 else "FAILED"
        return ReplayOutcome(
            status, run_id, None, bundle.bundle_hash, model_calls,
            str(report_path) if report_path else None,
            reason=redact_text(output_text[-2000:]),
        )
