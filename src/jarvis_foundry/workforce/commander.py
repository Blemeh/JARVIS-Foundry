"""Composable V13 workforce facade under the existing JARVIS authorities."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from ..database import FoundryDatabase
from ..supervisor.worker import WorkerController
from .contracts import ConvergenceDecision, WorkPackage
from .convergence import (
    PackageConvergenceEvidence,
    analyze_convergence,
    record_convergence_decision,
)
from .gateway import GatewayLaunch, WorkforceGateway
from .observation import WorkforceObservationReport, WorkforceObserver
from .recovery import (
    WorkforceReconciliationReport,
    WorkforceRecovery,
    WorkforceRecoveryReport,
)
from .state import (
    SUPERVISOR_CONTROLLER,
    WorkPackageState,
    WorkforceState,
    create_work_package,
    rebuild_workforce_state,
)
from .steering import InterventionExecution, WorkforceSteering
from .suitability import (
    SuitabilityCandidate,
    SuitabilityRecommendation,
    SuitabilityRecommendationPolicy,
    recommend_suitable_candidates,
)
from .worktrees import WorktreeManager, WorktreePreparation


@dataclass(frozen=True, slots=True)
class PackageAdmission:
    package_id: str
    package_state: WorkPackageState
    preparation: WorktreePreparation | None = None
    blocked_reason: str | None = None


@dataclass(frozen=True, slots=True)
class WorkforceLaunchReport:
    launches: tuple[GatewayLaunch, ...]
    live_run_ids: tuple[str, ...]
    peak_concurrency: int


class WorkforceCommander:
    """One convenient surface over existing workforce control components.

    The commander accepts decomposition output from the existing Executive.
    It does not invent missions, schedule wakeups, select providers, certify
    results, or mutate an integration target.
    """

    def __init__(
        self,
        database: FoundryDatabase,
        *,
        workers: WorkerController | None = None,
        stop_path: str | Path | None = None,
        controller: str = SUPERVISOR_CONTROLLER,
        source_id: str = "workforce-commander",
        worker_config_path: str | Path | None = None,
    ) -> None:
        self.database = database
        self.stop_path = (
            Path(stop_path).expanduser().resolve()
            if stop_path is not None
            else None
        )
        self.controller = controller
        self.source_id = source_id
        self.workers = workers or WorkerController(database)
        if Path(self.workers.database.path).resolve() != Path(database.path).resolve():
            raise ValueError("workers must use the commander database")
        self.worktrees = WorktreeManager(
            database,
            stop_path=self.stop_path,
            controller=controller,
            source_id=source_id,
        )
        self.gateway = WorkforceGateway(
            database,
            stop_path=self.stop_path,
            controller=controller,
            source_id=source_id,
            worker_config_path=worker_config_path,
        )
        self.observer = WorkforceObserver(
            database,
            source_id=f"{source_id}-observer",
        )
        self.steering = WorkforceSteering(
            database,
            gateway=self.gateway,
            stop_path=self.stop_path,
            controller=controller,
            source_id=f"{source_id}-steering",
        )
        self.recovery = WorkforceRecovery(
            database,
            stop_path=self.stop_path,
            gateway=self.gateway,
            workers=self.workers,
            worktrees=self.worktrees,
            observer=self.observer,
        )

    def admit(
        self,
        packages: Iterable[WorkPackage],
        *,
        source_repositories: str | Path | Mapping[str, str | Path],
        max_parallel: int = 4,
    ) -> tuple[PackageAdmission, ...]:
        """Record packages and prepare every dependency-ready worktree."""

        requested = tuple(packages)
        package_ids = [package.package_id for package in requested]
        if len(package_ids) != len(set(package_ids)):
            raise ValueError("workforce admission package ids must be unique")
        if not requested:
            return ()
        worker_count = int(max_parallel)
        if worker_count < 1:
            raise ValueError("max_parallel must be positive")

        existing = rebuild_workforce_state(self.database)
        requested_by_id = {
            package.package_id: package
            for package in requested
        }
        available_ids = set(existing.packages) | set(requested_by_id)
        unknown_dependencies = {
            package.package_id: tuple(
                dependency
                for dependency in package.dependencies
                if dependency not in available_ids
            )
            for package in requested
        }
        unknown_dependencies = {
            package_id: dependencies
            for package_id, dependencies in unknown_dependencies.items()
            if dependencies
        }
        if unknown_dependencies:
            detail = "; ".join(
                f"{package_id}: {', '.join(dependencies)}"
                for package_id, dependencies in sorted(
                    unknown_dependencies.items()
                )
            )
            raise ValueError(f"unknown workforce dependencies: {detail}")

        known_ids = set(existing.packages)
        remaining = dict(requested_by_id)
        creation_order: list[WorkPackage] = []
        while remaining:
            ready = sorted(
                (
                    package
                    for package in remaining.values()
                    if set(package.dependencies) <= known_ids
                ),
                key=lambda package: package.package_id,
            )
            if not ready:
                cycle = "; ".join(
                    (
                        f"{package.package_id}: "
                        f"{', '.join(package.dependencies)}"
                    )
                    for package in sorted(
                        remaining.values(),
                        key=lambda item: item.package_id,
                    )
                )
                raise ValueError(
                    f"cyclic workforce dependencies: {cycle}"
                )
            for package in ready:
                creation_order.append(package)
                known_ids.add(package.package_id)
                remaining.pop(package.package_id)

        for package in creation_order:
            create_work_package(
                self.database,
                package,
                controller=self.controller,
                stop_path=self.stop_path,
                source_id=self.source_id,
            )
        state = rebuild_workforce_state(self.database)
        pending_dependencies = {
            package.package_id: tuple(
                dependency
                for dependency in package.dependencies
                if state.packages[dependency].status != "ACCEPTED"
            )
            for package in requested
        }
        eligible = [
            package
            for package in requested
            if not pending_dependencies[package.package_id]
        ]
        prepared: dict[str, WorktreePreparation] = {}
        blocked: dict[str, str] = {
            package.package_id: (
                "dependencies pending: "
                + ", ".join(pending_dependencies[package.package_id])
            )
            for package in requested
            if pending_dependencies[package.package_id]
        }

        def prepare(package: WorkPackage) -> WorktreePreparation:
            return self.worktrees.prepare(
                package.package_id,
                source_repository=self._source_repository(
                    package,
                    source_repositories,
                ),
            )

        worker_count = min(worker_count, len(eligible) or 1)
        with ThreadPoolExecutor(max_workers=worker_count) as pool:
            futures = {
                pool.submit(prepare, package): package.package_id
                for package in eligible
            }
            for future in as_completed(futures):
                package_id = futures[future]
                try:
                    prepared[package_id] = future.result()
                except Exception as error:
                    blocked[package_id] = (
                        f"{type(error).__name__}: {str(error)}"
                    )
        state = rebuild_workforce_state(self.database)
        return tuple(
            PackageAdmission(
                package_id=package.package_id,
                package_state=state.package(package.package_id),
                preparation=prepared.get(package.package_id),
                blocked_reason=blocked.get(package.package_id),
            )
            for package in requested
        )

    def launch_processes(
        self,
        package_ids: Iterable[str],
    ) -> WorkforceLaunchReport:
        """Launch independent bounded subprocesses and report actual overlap."""

        requested = tuple(str(package_id) for package_id in package_ids)
        if len(requested) != len(set(requested)):
            raise ValueError("workforce launch package ids must be unique")
        started: list[GatewayLaunch] = []
        peak_concurrency = 0
        for package_id in requested:
            started.append(
                self.gateway.spawn(package_id, self.workers)
            )
            peak_concurrency = max(
                peak_concurrency,
                sum(
                    self.workers.status(
                        launch.binding.session.run_id
                    ).alive
                    for launch in started
                ),
            )
        live = tuple(
            launch.binding.session.run_id
            for launch in started
            if self.workers.status(launch.binding.session.run_id).alive
        )
        return WorkforceLaunchReport(
            launches=tuple(started),
            live_run_ids=live,
            peak_concurrency=peak_concurrency,
        )

    def heartbeat_active(self) -> tuple[str, ...]:
        """Renew exact ownership for every active workforce session."""

        state = rebuild_workforce_state(self.database)
        renewed: list[str] = []
        for package_id in sorted(state.packages):
            sessions = [
                session
                for session in state.sessions_for(package_id)
                if session.status
                not in {"BLOCKED", "FAILED", "COMPLETED", "TERMINATED", "STOPPED"}
            ]
            if len(sessions) > 1:
                raise RuntimeError(
                    f"package has multiple active sessions: {package_id}"
                )
            if not sessions:
                continue
            self.gateway.heartbeat(
                package_id,
                workers=self.workers,
            )
            renewed.append(sessions[0].session_id)
        return tuple(renewed)

    def observe(
        self,
        *,
        package_id: str | None = None,
        now=None,
    ) -> WorkforceObservationReport:
        return self.observer.observe(package_id=package_id, now=now)

    def intervene(self, package_id: str, action: str, **kwargs) -> InterventionExecution:
        kwargs.setdefault("workers", self.workers)
        return self.steering.intervene(package_id, action, **kwargs)

    def audit_restart(
        self,
        *,
        package_id: str | None = None,
        now=None,
    ) -> WorkforceRecoveryReport:
        return self.recovery.audit(package_id=package_id, now=now)

    def reconcile_restart(
        self,
        *,
        package_id: str | None = None,
    ) -> WorkforceReconciliationReport:
        return self.recovery.reconcile_spawned(package_id=package_id)

    def recommend_backend(
        self,
        candidates: Sequence[SuitabilityCandidate],
        *,
        policy: SuitabilityRecommendationPolicy,
    ) -> SuitabilityRecommendation:
        return recommend_suitable_candidates(
            self.database,
            candidates,
            policy=policy,
        )

    def converge(
        self,
        repository: str | Path,
        *,
        decision_id: str,
        integration_target: str,
        target_sha: str,
        packages: Iterable[PackageConvergenceEvidence],
        record: bool = True,
    ) -> ConvergenceDecision:
        decision = analyze_convergence(
            repository,
            decision_id=decision_id,
            integration_target=integration_target,
            target_sha=target_sha,
            packages=packages,
        )
        if not record:
            return decision
        return record_convergence_decision(
            self.database,
            decision,
            source_id=self.source_id,
            controller=self.controller,
            stop_path=self.stop_path,
        )

    def snapshot(self) -> WorkforceState:
        return rebuild_workforce_state(self.database)

    @staticmethod
    def _source_repository(
        package: WorkPackage,
        sources: str | Path | Mapping[str, str | Path],
    ) -> str | Path:
        if isinstance(sources, Mapping):
            try:
                return sources[package.repository_id]
            except KeyError as error:
                raise ValueError(
                    "no source repository was supplied for "
                    f"{package.repository_id}"
                ) from error
        return sources


__all__ = [
    "PackageAdmission",
    "WorkforceCommander",
    "WorkforceLaunchReport",
]
