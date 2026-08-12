"""Role session adapters (WP4).

A ``RoleAdapter`` dispatches ONE isolated role session and returns a
structured ``RoleResult``. Two implementations:

- ``FakeHermesAdapter`` — deterministic parity adapter used by the gate: it
  records the exact context manifest it received (including provider
  identity) and returns scripted outcomes, including simulated provider
  failure for fallback testing. It performs no external mutation.
- ``HermesOneshotAdapter`` — real Hermes dispatch through the isolated
  development checkout (``.h``) via ``hermes --oneshot`` with an isolated
  ``HERMES_HOME``. It is DISABLED by default: any dispatch raises
  ``ProviderUnavailable`` unless the adapter is explicitly enabled with an
  isolated home, so it can never touch live Hermes by accident. It never
  installs Foundry tools into normal Hermes sessions.

Invariant: the adapter layer is the ONLY boundary through which a role
session is launched; workers never invoke Hermes directly.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Any, Protocol

from ..clock import isoformat, utc_now
from .manifest import ContextManifest
from .results import (
    BLOCKED,
    COMPLETED,
    FAILED,
    RoleResult,
    VERDICT_FAIL,
    VERDICT_NOT_TESTED,
    VERDICT_PASS,
)


class ProviderUnavailable(RuntimeError):
    """The requested provider cannot serve a session right now."""


class AdapterConfigurationError(RuntimeError):
    """The adapter was configured in a way that violates a safety invariant."""


class RoleAdapter(Protocol):
    def dispatch(self, manifest: ContextManifest, *, heartbeat) -> RoleResult:
        """Run one isolated role session; call ``heartbeat`` periodically."""
        ...


class FakeHermesAdapter:
    """Deterministic parity adapter for gate tests and staging.

    ``fail_provider`` simulates a provider outage for that provider name so
    the router's fallback path is exercised. ``outcome`` controls the
    returned status/verdict. The adapter records every received manifest in
    ``received`` for exact-context assertions.
    """

    def __init__(
        self,
        *,
        fail_provider: str | None = None,
        outcome: str = COMPLETED,
        verdict: str = VERDICT_PASS,
        evidence: tuple[str, ...] = ("fake adapter ran the session",),
        error_class: str | None = None,
    ) -> None:
        self.fail_provider = fail_provider
        self.outcome = outcome
        self.verdict = verdict
        self.evidence = evidence
        self.error_class = error_class
        self.received: list[ContextManifest] = []

    def dispatch(self, manifest: ContextManifest, *, heartbeat) -> RoleResult:
        self.received.append(manifest)
        if self.fail_provider and manifest.provider == self.fail_provider:
            raise ProviderUnavailable(f"provider {self.fail_provider} unavailable (scripted)")
        heartbeat()
        if self.outcome == COMPLETED:
            return RoleResult(
                run_id=manifest.run_id,
                role=manifest.role,
                status=COMPLETED,
                provider=manifest.provider,
                model=manifest.model,
                verdict=self.verdict,
                evidence=self.evidence,
                cost_usd=0.0,
                started_at=isoformat(utc_now()),
                finished_at=isoformat(utc_now()),
            )
        return RoleResult(
            run_id=manifest.run_id,
            role=manifest.role,
            status=FAILED,
            provider=manifest.provider,
            model=manifest.model,
            verdict=VERDICT_FAIL if self.verdict == VERDICT_FAIL else VERDICT_NOT_TESTED,
            evidence=(),
            error_class=self.error_class or "scripted_failure",
            error_detail="scripted failure outcome",
            started_at=isoformat(utc_now()),
            finished_at=isoformat(utc_now()),
        )


class HermesOneshotAdapter:
    """Real Hermes role dispatch, safely bounded (DISABLED by default).

    Safety invariants:
    - ``hermes_root`` must be the isolated development checkout (``.h``);
      live Hermes installations are rejected by path check.
    - ``hermes_home`` must be an explicit, isolated home (never the live
      profile); a missing/None home raises AdapterConfigurationError.
    - ``enabled`` defaults to False; dispatch raises ProviderUnavailable
      until the operator explicitly enables it in staging.
    - ``python_exe`` must be an EXPLICIT interpreter whose site-packages
      resolve the checkout's dependencies. The ``"python"`` default is
      PATH-resolved and therefore host-fragile (FIND-2026-08-12-03: on the
      proving host, Windows CreateProcess resolves it to the Foundry base
      3.13, which cannot import the checkout's cp311 deps; the working
      interpreter is the Hermes agent venv python). Deployments must pass
      the agent-venv python explicitly.
    - Sessions are bounded by ``timeout_seconds``; the subprocess is killed
      when the bound expires. An externally killed session returns FAILED
      with ``error_class="hermes_oneshot_failed"`` and leaves no orphaned
      processes (F2-live probe, 2026-08-12).
    - The adapter never adds Foundry tools/plugins to any Hermes session.
    """

    #: live Hermes installation root (never a valid hermes_root)
    LIVE_HERMES = Path(r"C:\Users\b\AppData\Local\hermes\hermes-agent")
    #: live Hermes profile/home (never a valid hermes_home for a role session)
    LIVE_HERMES_HOME = Path(r"C:\Users\b\AppData\Local\hermes")

    #: Router provider names -> Hermes CLI canonical provider ids. Verified
    #: 2026-08-12 against the .h checkout's hermes_cli/models.py
    #: normalize_provider() and hermes_cli/auth.py ProviderConfig registry:
    #: "opencode_go" and "vertex_ai" are NOT CLI aliases — normalize_provider
    #: passes them through unchanged and the CLI cannot resolve them. The
    #: canonical ids are "opencode-go" and "vertex". The router keeps its own
    #: names (they are the ledger/evidence vocabulary); the adapter translates
    #: at the ONLY boundary that talks to the real CLI.
    CLI_PROVIDER_IDS = {
        "opencode_go": "opencode-go",
        "vertex_ai": "vertex",
    }

    def __init__(
        self,
        *,
        hermes_root: Path,
        hermes_home: Path,
        enabled: bool = False,
        timeout_seconds: int = 300,
        python_exe: str = "python",
    ) -> None:
        self.hermes_root = hermes_root.resolve()
        self.hermes_home = hermes_home.resolve()
        self.enabled = enabled
        self.timeout_seconds = timeout_seconds
        self.python_exe = python_exe
        self._validate()

    def _validate(self) -> None:
        if not self.hermes_root.exists():
            raise AdapterConfigurationError(f"hermes_root does not exist: {self.hermes_root}")
        if self.hermes_root == self.LIVE_HERMES or self.LIVE_HERMES in self.hermes_root.parents:
            raise AdapterConfigurationError("refusing to dispatch through the live Hermes installation")
        if self.hermes_home == self.LIVE_HERMES_HOME or self.LIVE_HERMES_HOME in self.hermes_home.parents:
            raise AdapterConfigurationError(
                "refusing an isolated home inside the live Hermes profile "
                f"({self.LIVE_HERMES_HOME})")
        if not self.hermes_home.exists() and self.enabled:
            raise AdapterConfigurationError(
                f"hermes_home does not exist: {self.hermes_home} (isolated home required)")

    def dispatch(self, manifest: ContextManifest, *, heartbeat) -> RoleResult:
        if not self.enabled:
            raise ProviderUnavailable(
                "real Hermes dispatch is disabled; enable explicitly with an isolated HERMES_HOME")
        # Bounded oneshot invocation through the isolated checkout only.
        # Real CLI surface (verified against .h hermes_cli/_parser.py): the
        # entry module is hermes_cli.main (there is NO importable 'hermes'
        # module), the prompt is the -z VALUE, --provider pairs with --model,
        # there is no --role flag (role identity rides in the
        # manifest/instructions), and --usage-file returns a JSON cost report
        # even on failure.
        usage_path: Path | None = None
        if self.timeout_seconds > 0:
            usage_path = Path(tempfile.gettempdir()) / f"jf-hermes-usage-{uuid.uuid4().hex}.json"
        env = {
            **os.environ,
            "HERMES_HOME": str(self.hermes_home),
        }
        command = [
            self.python_exe, "-m", "hermes_cli.main", "-z",
            manifest.instructions or manifest.run_id,
            "--provider", self.CLI_PROVIDER_IDS.get(manifest.provider, manifest.provider),
            "--model", manifest.model,
        ]
        if usage_path is not None:
            command += ["--usage-file", str(usage_path)]
        try:
            process = subprocess.run(
                command,
                cwd=str(self.hermes_root),
                env=env,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            return RoleResult(
                run_id=manifest.run_id, role=manifest.role, status=FAILED,
                provider=manifest.provider, model=manifest.model,
                verdict=VERDICT_NOT_TESTED, error_class="timeout",
                error_detail=f"session exceeded {self.timeout_seconds}s bound",
                started_at=isoformat(utc_now()), finished_at=isoformat(utc_now()),
            )
        except OSError as error:
            raise ProviderUnavailable(f"could not launch Hermes: {error}") from error
        finally:
            heartbeat()
        cost_usd = self._read_usage_cost(usage_path)
        if process.returncode != 0:
            return RoleResult(
                run_id=manifest.run_id, role=manifest.role, status=FAILED,
                provider=manifest.provider, model=manifest.model,
                verdict=VERDICT_FAIL if manifest.role == "verifier" else VERDICT_NOT_TESTED,
                error_class="hermes_oneshot_failed",
                error_detail=(process.stderr or process.stdout or "").strip()[-500:],
                cost_usd=cost_usd,
                started_at=isoformat(utc_now()), finished_at=isoformat(utc_now()),
            )
        return RoleResult(
            run_id=manifest.run_id, role=manifest.role, status=COMPLETED,
            provider=manifest.provider, model=manifest.model,
            verdict=VERDICT_PASS,
            evidence=(process.stdout or "").strip()[-2000:],
            cost_usd=cost_usd,
            started_at=isoformat(utc_now()), finished_at=isoformat(utc_now()),
        )

    @staticmethod
    def _read_usage_cost(usage_path: Path | None) -> float:
        """Parse the estimated cost from the oneshot usage report (best-effort)."""
        if usage_path is None or not usage_path.exists():
            return 0.0
        try:
            import json
            report = json.loads(usage_path.read_text(encoding="utf-8"))
            return float(report.get("estimated_cost_usd") or 0.0)
        except (OSError, ValueError, TypeError):
            return 0.0
        finally:
            try:
                usage_path.unlink(missing_ok=True)
            except OSError:
                pass


def make_hermes_adapter(hermes_root: Path, *, enabled: bool = False, **kwargs: Any) -> HermesOneshotAdapter:
    """Convenience factory: isolated temp HERMES_HOME unless provided."""
    home = kwargs.pop("hermes_home", None)
    if home is None:
        home = Path(tempfile.mkdtemp(prefix="jf-hermes-home-"))
    return HermesOneshotAdapter(
        hermes_root=hermes_root, hermes_home=Path(home),
        enabled=enabled, **kwargs,
    )
