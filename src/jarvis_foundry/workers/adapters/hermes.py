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
import yaml
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable

from ...clock import isoformat, utc_now
from ...paths import LiveHermesPath
from ..contracts import WorkerStepContract
from ..envelope import (
    COMPLETED,
    FAILED,
    NOT_AVAILABLE,
    VERDICT_FAIL,
    VERDICT_NOT_TESTED,
    ResultEnvelope,
)
from ...runtime.provider_config import (
    ProviderCredentialError,
    ProviderSettings,
    apply_hermes_provider_env,
)
from ...runtime.provider_spend import (
    AUTHORIZED_API_MODE,
    AUTHORIZED_MODEL,
    AUTHORIZED_PROVIDER,
    OpenCodeSpendGateway,
    ProviderSpendBlocked,
)
from ...database import FoundryDatabase
from ...roles.adapter import ProviderFailure, classify_provider_failure
from .base import (
    AdapterConfigurationError,
    ProviderUnavailable,
    configured_api_key,
    redact_secrets,
    run_subprocess_with_heartbeat,
)

from ..result_contract import MISSING_RESULT_NOTE, ingest_result_claim, result_env


class HermesWorkerAdapter:
    LIVE_HERMES = LiveHermesPath(checkout=True)
    LIVE_HERMES_HOME = LiveHermesPath(checkout=False)
    FAILURE_DIAGNOSTIC_LIMIT = 500
    FAILURE_STREAM_TAIL_LIMIT = 240

    #: Router provider names -> Hermes CLI canonical provider ids (verified
    #: against the .h checkout: "opencode_go" is not a CLI alias).
    CLI_PROVIDER_IDS = {
        "opencode_go": "opencode-go",
        "opencode_zen": "opencode-zen",
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
        r"^\s*VERDICT\s*:\s*(PASS|FAIL|BLOCKED|NOT_TESTED)\b(?!\s*\|)",
        re.IGNORECASE,
    )
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
        provider_settings: ProviderSettings | None = None,
        provider_settings_by_id: dict[str, ProviderSettings] | None = None,
        database: FoundryDatabase | None = None,
    ) -> None:
        from ...runtime.provider_config import default_provider_settings

        self.hermes_root = hermes_root.resolve()
        self.hermes_home = hermes_home.resolve()
        self.enabled = enabled
        self.timeout_seconds = timeout_seconds
        self.python_exe = python_exe
        self.framework = "hermes"
        self.provider_settings = provider_settings or default_provider_settings()
        self.database = database
        self.strict_provider_identity = provider_settings_by_id is not None
        self.provider_settings_by_id = dict(provider_settings_by_id or {})
        self.provider_settings_by_id.setdefault(
            self.provider_settings.provider, self.provider_settings)
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
        settings = (
            self.provider_settings_by_id.get(contract.provider)
            if self.strict_provider_identity else self.provider_settings
        )
        if settings is None or (
            self.strict_provider_identity and settings.model != contract.model
        ):
            raise ProviderFailure(
                f"provider/model identity is not configured: "
                f"{contract.provider}/{contract.model}",
                failure_class="INCOMPATIBLE",
            )
        if (
            settings.provider != AUTHORIZED_PROVIDER
            or settings.model != AUTHORIZED_MODEL
            or contract.provider != AUTHORIZED_PROVIDER
            or contract.model != AUTHORIZED_MODEL
        ):
            raise ProviderFailure(
                "worker dispatch is outside the exact owner-authorized OpenCode Go model",
                failure_class="INCOMPATIBLE",
            )
        if not settings.api_key:
            raise ProviderUnavailable(
                "OpenCode API key is not configured; model dispatch blocked",
                retryable=False,
            )
        if self.database is None:
            raise ProviderUnavailable(
                "canonical provider-spend ledger is unavailable; model dispatch blocked",
                retryable=False,
            )
        gateway: OpenCodeSpendGateway | None = None
        try:
            gateway = OpenCodeSpendGateway(
                self.database,
                settings,
                stop_path=self.stop_path,
                mission_id=contract.mission_id,
                timeout_seconds=self.timeout_seconds,
            )
            gateway_url = gateway.start()
            child_settings = replace(
                settings,
                api_key=gateway.local_api_key,
                base_url=gateway_url,
            )
            provider_env = apply_hermes_provider_env(
                child_settings, os.environ)
            _pin_hermes_gateway_profile(
                self.hermes_home,
                gateway_url=gateway_url,
                provider_settings=settings,
                headers=_provider_headers_from_home(self.hermes_home),
            )
        except ProviderCredentialError as error:
            raise ProviderUnavailable(str(error), retryable=False) from error
        except (ProviderSpendBlocked, OSError, ValueError) as error:
            if gateway is not None:
                gateway.close()
            raise ProviderUnavailable(
                f"bounded OpenCode Go spend route is unavailable: {error}",
                retryable=False,
            ) from error
        started = isoformat(utc_now())
        usage_path: Path | None = None
        if self.timeout_seconds > 0:
            usage_path = Path(tempfile.gettempdir()) / f"jf-hermes-usage-{uuid.uuid4().hex}.json"
        env = {
            **provider_env,
            "HERMES_HOME": str(self.hermes_home),
            **result_env(contract.working_root),
            # Hermes resolves its terminal environment while the CLI module
            # is starting, before ``--in`` performs its later ``chdir``.
            # Pin the terminal cwd explicitly so the first tool call cannot
            # fall back to the launcher/home directory (and escape the
            # registered worktree).
            "TERMINAL_CWD": str(contract.working_root),
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
            self._scrub_disposable_home(extra_secrets=(gateway.local_api_key,))
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
            gateway.close()
        cost = self._read_usage_cost(usage_path)
        usage_availability = "available" if cost not in (0.0, None) else NOT_AVAILABLE
        stdout = redact_secrets(
            process.stdout or "", configured_key=configured_api_key(settings))
        stdout = redact_secrets(
            stdout, configured_key=gateway.local_api_key)
        stderr = redact_secrets(
            process.stderr or "", configured_key=configured_api_key(settings))
        stderr = redact_secrets(
            stderr, configured_key=gateway.local_api_key)
        self._scrub_disposable_home(extra_secrets=(gateway.local_api_key,))
        if process.returncode != 0:
            return ResultEnvelope.from_contract(
                contract, framework=self.framework,
                worker_status=FAILED,
                verdict=VERDICT_FAIL if contract.role == "verifier" else VERDICT_NOT_TESTED,
                claimed_outcome="Hermes CLI exited non-zero",
                summary=self._failure_diagnostic(
                    stdout, stderr, process.returncode,
                ),
                started_at=started,
                exit_state=f"EXIT_{process.returncode}",
                usage_cost=cost if usage_availability == "available" else NOT_AVAILABLE,
                usage_availability=usage_availability,
            )
        first_line = (stdout.strip().splitlines() or [""])[0]
        if (first_line.startswith(self.FAILURE_REPORT_PREFIXES)
                or self._FAILURE_REPORT_HTTP.match(first_line)):
            failure_class = classify_provider_failure(first_line)
            failure_type = (
                ProviderUnavailable
                if failure_class == "OUTAGE" else ProviderFailure)
            raise failure_type(
                f"oneshot reported provider failure (exit 0): {first_line[:200]}",
                failure_class=failure_class,
                retryable=failure_class in {"OUTAGE", "DEGRADED"},
            )
        verdict = self._structured_verdict(stdout)
        verdict_output = stdout
        # A completed Hermes session may leave the final response on stderr
        # when its launcher or host wrapper redirects the one-shot stream.
        # Keep stdout authoritative when it contains a structured verdict;
        # otherwise accept only the same exact verdict grammar from stderr.
        if verdict is None:
            stderr_verdict = self._structured_verdict(stderr)
            if stderr_verdict is not None:
                verdict = stderr_verdict
                verdict_output = stderr
        note = ""
        if verdict is None:
            verdict = VERDICT_NOT_TESTED
            note = ("[adapter] no structured verdict found in the session "
                    "output — recorded NOT_TESTED, never PASS")
        evidence = verdict_output.strip()[-2000:]
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
        """Read the last explicit verdict without accepting prompt echoes.

        Real Hermes sessions may place the final verdict after a long
        inspection transcript. The line grammar is deliberately exact, so a
        quoted instruction such as ``VERDICT: PASS | FAIL`` is not a verdict;
        the last exact line wins when a session reports more than one.
        """
        verdict: str | None = None
        for line in (stdout or "").splitlines():
            match = cls._VERDICT_LINE.match(line)
            if match:
                verdict = match.group(1).upper()
                continue
            stripped = line.strip()
            if stripped.startswith("{") and stripped.endswith("}"):
                try:
                    value = str(json.loads(stripped).get("verdict", "")).upper()
                except ValueError:
                    continue
                if value in cls._VERDICT_VALUES:
                    verdict = value
        return verdict

    @classmethod
    def _failure_diagnostic(
        cls, stdout: str, stderr: str, returncode: int,
    ) -> str:
        """Retain bounded context from both redacted CLI output streams."""
        parts = []
        for name, value in (("stderr", stderr), ("stdout", stdout)):
            tail = str(value or "").strip()[-cls.FAILURE_STREAM_TAIL_LIMIT:]
            if tail:
                parts.append(f"{name}: {tail}")
        if not parts:
            return f"Hermes CLI exited with code {returncode}"
        return "\n".join(parts)[:cls.FAILURE_DIAGNOSTIC_LIMIT]

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

    def _scrub_disposable_home(
        self,
        limit: int = 5000,
        *,
        extra_secrets: tuple[str, ...] = (),
    ) -> None:
        """Redact credential-shaped tokens from the disposable HERMES_HOME
        (state.db, auth.json, sessions) so no retained run-state file ever
        holds the secret value."""
        keys = tuple(
            key for key in (
                configured_api_key(settings)
                for settings in self.provider_settings_by_id.values()
            ) if key
        ) + tuple(value for value in extra_secrets if value)
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
            redacted = text
            for key in keys or (None,):
                redacted = redact_secrets(redacted, configured_key=key)
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


def _provider_headers_from_home(home: Path) -> dict[str, str]:
    path = home / "config.yaml"
    if not path.is_file():
        return {}
    parsed = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(parsed, dict):
        raise ValueError("isolated Hermes config.yaml must be a mapping")
    providers = parsed.get("providers") or {}
    if not isinstance(providers, dict):
        raise ValueError("isolated Hermes providers must be a mapping")
    selected = providers.get("opencode-go") or {}
    if not isinstance(selected, dict):
        raise ValueError("isolated Hermes OpenCode GO provider config is invalid")
    headers = selected.get("extra_headers") or {}
    if not isinstance(headers, dict):
        raise ValueError("isolated Hermes OpenCode GO headers are invalid")
    return {str(key): str(value) for key, value in headers.items() if value is not None}


def _pin_hermes_gateway_profile(
    home: Path,
    *,
    gateway_url: str,
    provider_settings: ProviderSettings,
    headers: dict[str, str],
) -> None:
    """Force main and auxiliary Hermes requests through the exact loopback route."""
    path = home / "config.yaml"
    document = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
    if not isinstance(document, dict):
        raise ValueError("isolated Hermes config.yaml must be a mapping")
    if provider_settings.provider != AUTHORIZED_PROVIDER or provider_settings.model != AUTHORIZED_MODEL:
        raise ValueError("Hermes profile provider identity is outside the exact authorized route")
    headers = dict(headers)
    if not any(
        key.casefold() == "x-opencode-session" and value
        for key, value in headers.items()
    ):
        headers["x-opencode-session"] = str(uuid.uuid4())
    document["model"] = {
        "provider": "opencode-go",
        "model": AUTHORIZED_MODEL,
    }
    document["providers"] = {
        "opencode-go": {
            "api": gateway_url,
            "extra_headers": dict(headers),
        },
    }
    document["fallback_providers"] = []
    document["fallback_model"] = []
    document["credential_pool_strategies"] = {}
    auxiliary = document.get("auxiliary")
    if not isinstance(auxiliary, dict):
        auxiliary = {}
    auxiliary["transient_retries"] = 0
    web_extract = auxiliary.get("web_extract")
    if not isinstance(web_extract, dict):
        auxiliary["web_extract"] = {}
    for key, value in auxiliary.items():
        if not isinstance(value, dict):
            continue
        value["provider"] = "opencode-go"
        value["model"] = AUTHORIZED_MODEL
        value["base_url"] = gateway_url
        value["api_key"] = ""
        value["api_mode"] = AUTHORIZED_API_MODE
        value["fallback_chain"] = []
    document["auxiliary"] = auxiliary
    delegation = document.get("delegation")
    if not isinstance(delegation, dict):
        delegation = {}
    delegation["provider"] = "opencode-go"
    delegation["model"] = AUTHORIZED_MODEL
    delegation["base_url"] = gateway_url
    delegation["api_key"] = ""
    delegation["api_mode"] = AUTHORIZED_API_MODE
    delegation["fallback_providers"] = []
    delegation["request_overrides"] = _delegation_request_overrides(
        delegation.get("request_overrides"), headers)
    document["delegation"] = delegation
    agent = document.get("agent")
    if not isinstance(agent, dict):
        agent = {}
    agent["api_max_retries"] = 1
    document["agent"] = agent
    temporary = path.with_suffix(".yaml.tmp")
    temporary.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    temporary.replace(path)


def _delegation_request_overrides(
    value: Any,
    headers: dict[str, str],
) -> dict[str, Any]:
    """Keep child requests in the same OpenCode session as the parent."""
    session_id = next(
        (str(v) for k, v in headers.items()
         if k.casefold() == "x-opencode-session" and v),
        None,
    )
    if not session_id:
        raise ValueError("OpenCode session header is required for delegated requests")
    overrides = dict(value) if isinstance(value, dict) else {}
    extra_headers = overrides.get("extra_headers")
    extra_headers = dict(extra_headers) if isinstance(extra_headers, dict) else {}
    for key in list(extra_headers):
        if str(key).casefold() == "x-opencode-session":
            del extra_headers[key]
    extra_headers["x-opencode-session"] = session_id
    overrides["extra_headers"] = extra_headers
    return overrides
