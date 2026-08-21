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

import json
import os
import re
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Any, Protocol

from ..clock import isoformat, utc_now
from ..paths import LiveHermesPath
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
        working_root: Path | None = None,
    ) -> None:
        self.fail_provider = fail_provider
        self.outcome = outcome
        self.verdict = verdict
        self.evidence = evidence
        self.error_class = error_class
        self.working_root = Path(working_root) if working_root is not None else None
        self.received: list[ContextManifest] = []

    def _write_owned_artifact(self, manifest: ContextManifest) -> None:
        if self.outcome != COMPLETED or manifest.role == "verifier":
            return
        dests: list[Path] = []
        if self.working_root is not None:
            names = list(manifest.expected_artifacts) or ["result.txt"]
            dests.extend(self.working_root / name for name in names)
        text = manifest.instructions or ""
        marker = " named report.md"
        if marker in text:
            token = text.split(marker, 1)[0].split()[-1]
            dests.append(Path(token) / "report.md")
        for path in dests:
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists() or manifest.role == "repair":
                path.write_text(
                    "jarvis-owned-evidence\nVERIFIED_BY fake-adapter\n",
                    encoding="utf-8")

    def dispatch(self, manifest: ContextManifest, *, heartbeat) -> RoleResult:
        self.received.append(manifest)
        if self.fail_provider and manifest.provider == self.fail_provider:
            raise ProviderUnavailable(f"provider {self.fail_provider} unavailable (scripted)")
        heartbeat()
        if self.outcome == COMPLETED:
            self._write_owned_artifact(manifest)
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
    LIVE_HERMES = LiveHermesPath(checkout=True)
    #: live Hermes profile/home (never a valid hermes_home for a role session)
    LIVE_HERMES_HOME = LiveHermesPath(checkout=False)

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
        "opencode_zen": "opencode-zen",
        "vertex_ai": "vertex",
    }

    #: First-line prefixes the real Hermes CLI prints to STDOUT when a
    #: oneshot exhausts its provider retries WITHOUT exiting non-zero
    #: (verified 2026-08-12 against .h agent/conversation_loop.py —
    #: `_final_response = "API call failed after {N} retries: {summary}"`,
    #: the billing variant, "Invalid API response after {N} retries: ...",
    #: "Request payload too large ...", "Context length exceeded ...",
    #: "Model generated invalid tool call: ...", "Response truncated due
    #: to output length limit", and observed "HTTP 401: Model <id> is not
    #: supported" — FIND-2026-08-12-04/-05 — all emitted as the session's
    #: final response with exit code 0). exit 0 + these prefixes means the
    #: session NEVER produced a result (transport failure, billing block,
    #: model not supported): classify as provider unavailability so the
    #: router fallback chain engages and exhaustion becomes an honest
    #: BLOCKED state — never a fabricated COMPLETED with the error text as
    #: "evidence" (WP8 F5/F6: outages must not advance a mission).
    FAILURE_REPORT_PREFIXES = (
        "API call failed after ",
        "Billing or credits exhausted:",
        "Invalid API response after ",
        "Request payload too large",
        "Context length exceeded",
        "Model generated invalid tool call:",
        "Response truncated due to output length limit",
    )

    #: Matches the observed "HTTP 401: Model X is not supported" /
    #: "HTTP 429: ..." exit-0 error lines (status code + separator).
    _FAILURE_REPORT_HTTP = re.compile(r"^HTTP \d{3}[:\s]")

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
        first_line = ((process.stdout or "").strip().splitlines() or [""])[0]
        if (first_line.startswith(self.FAILURE_REPORT_PREFIXES)
                or self._FAILURE_REPORT_HTTP.match(first_line)):
            # The oneshot ran but the provider was unreachable (network
            # outage / billing block / model not supported): the CLI
            # reports it on STDOUT with exit 0. Raise so the dispatcher
            # engages the provider fallback chain; with no fallback
            # configured the step lands in an honest
            # BLOCKED/provider_unavailable state (WP8 F5/F6).
            raise ProviderUnavailable(
                f"oneshot reported provider failure (exit 0): {first_line[:200]}")
        # DIR-2026-08-12 verifier truth: the verdict must be an EXPLICIT
        # STRUCTURED VERDICT in the session output, never the exit code.
        # Missing/ambiguous output is NOT_TESTED — never PASS.
        verdict = self._structured_verdict(process.stdout)
        if verdict is None:
            verdict = VERDICT_NOT_TESTED
            note = ("[adapter] no structured verdict found in the session "
                    "output (expected a line 'VERDICT: PASS | FAIL | "
                    "BLOCKED | NOT_TESTED' within the first 20 lines) — "
                    "recorded NOT_TESTED, never PASS")
        else:
            note = ""
        evidence = (process.stdout or "").strip()[-2000:]
        if note:
            evidence = f"{evidence}\n{note}" if evidence else note
        return RoleResult(
            run_id=manifest.run_id, role=manifest.role, status=COMPLETED,
            provider=manifest.provider, model=manifest.model,
            verdict=verdict,
            evidence=(evidence,),
            cost_usd=cost_usd,
            started_at=isoformat(utc_now()), finished_at=isoformat(utc_now()),
        )

    #: Structured-verdict line: ``VERDICT: PASS|FAIL|BLOCKED|NOT_TESTED``
    #: (word-boundary: "PASSED" never matches "PASS").
    _VERDICT_LINE = re.compile(
        r"^\s*VERDICT\s*:\s*(PASS|FAIL|BLOCKED|NOT_TESTED)\b", re.IGNORECASE)
    _VERDICT_VALUES = {"PASS", "FAIL", "BLOCKED", "NOT_TESTED"}

    @classmethod
    def _structured_verdict(cls, stdout: str) -> str | None:
        """Extract the explicit verdict from the session output.

        Deterministic and bounded to the first 20 lines: (1) a line
        matching ``VERDICT: <value>``; (2) a JSON object line with a
        valid ``verdict`` field. Returns None when no explicit verdict
        exists — the caller must record NOT_TESTED, never PASS.
        """
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
