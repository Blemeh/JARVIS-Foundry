"""Real Hermes worker dispatch, safely bounded (DISABLED by default).

Mirrors the canonical ``roles.adapter.HermesOneshotAdapter`` safety
invariants for the worker layer:

- ``hermes_root`` must be the isolated development checkout (``.h``); the
  live Hermes installation is rejected by path check.
- ``hermes_home`` must be an explicit, isolated home (never the live
  profile); a missing/None home raises AdapterConfigurationError.
- ``enabled`` defaults to False; dispatch raises ProviderUnavailable until
  an operator explicitly enables it with an isolated home.
- ``python_exe`` must be an EXPLICIT interpreter whose site-packages
  resolve the checkout's dependencies.
- Sessions are bounded by ``timeout_seconds``; exit-0 provider-failure
  reports (network/billing/model unsupported) raise ProviderUnavailable so
  the router records an honest BLOCKED state, never a fabricated PASS.
- The adapter never installs Foundry tools into Hermes sessions and never
  touches the live Hermes profile.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Any, Callable

from ...clock import isoformat, utc_now
from ..contracts import WorkerStepContract
from ..envelope import (
    COMPLETED,
    FAILED,
    NOT_AVAILABLE,
    VERDICT_FAIL,
    VERDICT_NOT_TESTED,
    ResultEnvelope,
)
from .base import (
    AdapterConfigurationError,
    ProviderUnavailable,
    configured_api_key,
    redact_secrets,
    run_subprocess_with_heartbeat,
)
from ..result_contract import MISSING_RESULT_NOTE, ingest_result_claim, result_env


class HermesWorkerAdapter:
    LIVE_HERMES = Path(r"C:\Users\b\AppData\Local\hermes\hermes-agent")
    LIVE_HERMES_HOME = Path(r"C:\Users\b\AppData\Local\hermes")

    #: Router provider names -> Hermes CLI canonical provider ids (verified
    #: against the .h checkout: "opencode_go" is not a CLI alias).
    CLI_PROVIDER_IDS = {
        "opencode_go": "opencode-go",
        "vertex_ai": "vertex",
    }

    #: Exit-0 provider-failure report prefixes (verified 2026-08-12 against
    #: the .h checkout). These mean the session NEVER produced a result.
    FAILURE_REPORT_PREFIXES = (
        "API call failed after ",
        "Billing or credits exhausted:",
        "Invalid API response after ",
        "Request payload too large",
        "Context length exceeded",
        "Model generated invalid tool call:",
        "Response truncated due to output length limit",
    )
    _FAILURE_REPORT_HTTP = re.compile(r"^HTTP \d{3}[:\s]")
    _VERDICT_LINE = re.compile(
        r"^\s*VERDICT\s*:\s*(PASS|FAIL|BLOCKED|NOT_TESTED)\b", re.IGNORECASE)
    _VERDICT_VALUES = {"PASS", "FAIL", "BLOCKED", "NOT_TESTED"}

    def __init__(
        self,
        *,
        hermes_root: Path,
        hermes_home: Path,
        enabled: bool = False,
        timeout_seconds: int = 900,
        python_exe: str = "python",
        stop_path: Path | str | None = None,
        pid_table: Path | str | None = None,
        workers: Any = None,
    ) -> None:
        self.hermes_root = hermes_root.resolve()
        self.hermes_home = hermes_home.resolve()
        self.enabled = enabled
        self.timeout_seconds = timeout_seconds
        self.python_exe = python_exe
        self.framework = "hermes"
        self.stop_path = Path(stop_path).resolve() if stop_path is not None else None
        self.pid_table = Path(pid_table).resolve() if pid_table is not None else None
        self.workers = workers
        self._validate()

    def _validate(self) -> None:
        if not self.hermes_root.exists():
            raise AdapterConfigurationError(
                f"hermes_root does not exist: {self.hermes_root}")
        if (self.hermes_root == self.LIVE_HERMES
                or self.LIVE_HERMES in self.hermes_root.parents):
            raise AdapterConfigurationError(
                "refusing to dispatch through the live Hermes installation")
        if (self.hermes_home == self.LIVE_HERMES_HOME
                or self.LIVE_HERMES_HOME in self.hermes_home.parents):
            raise AdapterConfigurationError(
                "refusing an isolated home inside the live Hermes profile "
                f"({self.LIVE_HERMES_HOME})")
        if not self.hermes_home.exists() and self.enabled:
            raise AdapterConfigurationError(
                f"hermes_home does not exist: {self.hermes_home} "
                "(isolated home required)")

    def dispatch(
        self,
        contract: WorkerStepContract,
        *,
        heartbeat: Callable[[], None],
    ) -> ResultEnvelope:
        if not self.enabled:
            raise ProviderUnavailable(
                "real Hermes dispatch is disabled; enable explicitly with "
                "an isolated HERMES_HOME")
        started = isoformat(utc_now())
        usage_path: Path | None = None
        if self.timeout_seconds > 0:
            usage_path = Path(tempfile.gettempdir()) / f"jf-hermes-usage-{uuid.uuid4().hex}.json"
        env = {
            **os.environ,
            "HERMES_HOME": str(self.hermes_home),
            **result_env(contract.working_root),
            # The session must operate INSIDE the contract working root so
            # every write lands in the worker's allowed scope. The checkout
            # code is resolved via PYTHONPATH (prepended before site-packages),
            # never by the process cwd (live finding 2026-08-16: cwd=.h let a
            # probe session write hello.txt into the checkout).
            "PYTHONPATH": str(self.hermes_root) + os.pathsep
                         + os.environ.get("PYTHONPATH", ""),
        }
        command = [
            self.python_exe, "-m", "hermes_cli.main", "-z",
            contract.instructions or contract.objective or contract.run_id,
            "--provider", self.CLI_PROVIDER_IDS.get(contract.provider, contract.provider),
            "--model", contract.model,
            "--in", str(contract.working_root),
            "--yolo", "--cli",
        ]
        if usage_path is not None:
            command += ["--usage-file", str(usage_path)]
        try:
            process = run_subprocess_with_heartbeat(
                command,
                cwd=str(contract.working_root),
                env=env,
                timeout_seconds=self.timeout_seconds,
                heartbeat=heartbeat,
                run_id=contract.run_id,
                workers=self.workers,
                stop_path=self.stop_path,
                pid_table=self.pid_table,
                mission_id=contract.mission_id,
                step_id=contract.step_id,
                worker_type=self.framework,
            )
        except subprocess.TimeoutExpired:
            heartbeat()
            self._scrub_disposable_home()
            files, valid = ingest_result_claim(contract.working_root)
            summary = f"session exceeded {self.timeout_seconds}s bound"
            if not valid:
                summary = f"{summary}\n{MISSING_RESULT_NOTE}"
            return ResultEnvelope.from_contract(
                contract, framework=self.framework,
                worker_status=FAILED, verdict=VERDICT_NOT_TESTED,
                claimed_outcome="session timed out",
                summary=summary,
                files_changed=files,
                started_at=started,
                exit_state="TIMEOUT",
                blockers=(f"timeout after {self.timeout_seconds}s",),
                usage_availability=NOT_AVAILABLE,
            )
        except OSError as error:
            heartbeat()
            raise ProviderUnavailable(f"could not launch Hermes: {error}") from error
        finally:
            heartbeat()
        cost = self._read_usage_cost(usage_path)
        usage_availability = "available" if cost not in (0.0, None) else NOT_AVAILABLE
        stdout = redact_secrets(
            process.stdout or "", configured_key=configured_api_key())
        stderr = redact_secrets(
            process.stderr or "", configured_key=configured_api_key())
        self._scrub_disposable_home()
        if process.returncode != 0:
            return ResultEnvelope.from_contract(
                contract, framework=self.framework,
                worker_status=FAILED,
                verdict=VERDICT_FAIL if contract.role == "verifier" else VERDICT_NOT_TESTED,
                claimed_outcome="Hermes CLI exited non-zero",
                summary=(stderr or stdout or "").strip()[-500:],
                started_at=started,
                exit_state=f"EXIT_{process.returncode}",
                usage_cost=cost if usage_availability == "available" else NOT_AVAILABLE,
                usage_availability=usage_availability,
            )
        first_line = (stdout.strip().splitlines() or [""])[0]
        if (first_line.startswith(self.FAILURE_REPORT_PREFIXES)
                or self._FAILURE_REPORT_HTTP.match(first_line)):
            raise ProviderUnavailable(
                f"oneshot reported provider failure (exit 0): {first_line[:200]}")
        verdict = self._structured_verdict(stdout)
        note = ""
        if verdict is None:
            verdict = VERDICT_NOT_TESTED
            note = ("[adapter] no structured verdict found in the session "
                    "output — recorded NOT_TESTED, never PASS")
        evidence = stdout.strip()[-2000:]
        if note:
            evidence = f"{evidence}\n{note}" if evidence else note
        result_files, valid_claim = ingest_result_claim(contract.working_root)
        if not valid_claim:
            if MISSING_RESULT_NOTE not in (evidence or ""):
                evidence = (f"{evidence}\n{MISSING_RESULT_NOTE}"
                            if evidence else MISSING_RESULT_NOTE)
            verdict = VERDICT_NOT_TESTED
        return ResultEnvelope.from_contract(
            contract, framework=self.framework,
            worker_status=COMPLETED, verdict=verdict,
            claimed_outcome="Hermes session completed",
            summary=evidence,
            worker_results=(evidence,),
            files_changed=result_files,
            started_at=started,
            usage_cost=cost if usage_availability == "available" else NOT_AVAILABLE,
            usage_availability=usage_availability,
        )

    @classmethod
    def _structured_verdict(cls, stdout: str) -> str | None:
        for line in (stdout or "").splitlines()[:20]:
            match = cls._VERDICT_LINE.match(line)
            if match:
                return match.group(1).upper()
            stripped = line.strip()
            if stripped.startswith("{") and stripped.endswith("}"):
                try:
                    value = str(json.loads(stripped).get("verdict", "")).upper()
                except ValueError:
                    continue
                if value in cls._VERDICT_VALUES:
                    return value
        return None

    @staticmethod
    def _read_usage_cost(usage_path: Path | None) -> float:
        if usage_path is None or not usage_path.exists():
            return 0.0
        try:
            report = json.loads(usage_path.read_text(encoding="utf-8"))
            return float(report.get("estimated_cost_usd") or 0.0)
        except (OSError, ValueError, TypeError):
            return 0.0
        finally:
            try:
                usage_path.unlink(missing_ok=True)
            except OSError:
                pass

    def _scrub_disposable_home(self, limit: int = 5000) -> None:
        """Redact credential-shaped tokens from the disposable HERMES_HOME
        (state.db, auth.json, sessions) so no retained run-state file ever
        holds the secret value."""
        key = configured_api_key()
        if not self.hermes_home.exists():
            return
        scanned = 0
        for path in self.hermes_home.rglob("*"):
            if not path.is_file():
                continue
            scanned += 1
            if scanned > limit:
                break
            try:
                data = path.read_bytes()
            except OSError:
                continue
            if b"sk-" not in data:
                continue
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                continue
            redacted = redact_secrets(text, configured_key=key)
            if redacted != text:
                try:
                    path.write_bytes(redacted.encode("utf-8"))
                except OSError:
                    pass


def make_hermes_worker_adapter(
    hermes_root: Path,
    *,
    enabled: bool = False,
    **kwargs: Any,
) -> HermesWorkerAdapter:
    """Convenience factory: isolated temp HERMES_HOME unless provided."""
    home = kwargs.pop("hermes_home", None)
    if home is None:
        home = Path(tempfile.mkdtemp(prefix="jf-worker-hermes-home-"))
    return HermesWorkerAdapter(
        hermes_root=hermes_root, hermes_home=Path(home),
        enabled=enabled, **kwargs,
    )
