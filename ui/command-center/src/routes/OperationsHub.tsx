import { Link } from "react-router-dom";
import { JarvisCoreBadge } from "../components/jarvis-core/JarvisCoreStage";
import { SectionHeader } from "../components/SectionHeader";
import { StatusBadge } from "../components/StatusBadge";
import { RouteQueryGate } from "../components/RouteQueryGate";
import { useRouteQuery } from "../hooks/useRouteQuery";
import { useCommandCenter } from "../context/CommandCenterContext";
import { useLocale } from "../i18n/LocaleContext";
import type { Mission } from "../lib/contracts";

function MissionOperationCard({ mission }: { mission: Mission }) {
  const stages = ["Research", "Plan", "Build", "Verify", "Integrate"];
  const currentIdx = Math.max(0, stages.findIndex((s) => s.toLowerCase() === (mission.stage ?? "").toLowerCase()));

  return (
    <Link to={`/missions/${mission.id}`} className="ops-card">
      <div className="ops-card-head">
        <span className="ops-card-code">{mission.code ?? mission.id}</span>
        <StatusBadge status={mission.status} />
      </div>
      <h3>{mission.title}</h3>
      <div className="ops-pipeline" aria-label="Mission pipeline progress">
        <div className="ops-pipeline-labels">
          {stages.map((s) => (
            <span key={s}>{s}</span>
          ))}
        </div>
        <div className="ops-pipeline-dots">
          {stages.map((s, i) => (
            <span
              key={s}
              className={`ops-pipeline-dot ${i < currentIdx ? "ops-pipeline-dot--done" : i === currentIdx ? "ops-pipeline-dot--active" : ""}`}
              aria-hidden="true"
            />
          ))}
        </div>
      </div>
      <div className="ops-card-foot">
        <span>{mission.nextAction ?? mission.stage ?? "UNKNOWN"}</span>
      </div>
    </Link>
  );
}

export function OperationsHub() {
  const { client, version, trustSummary } = useCommandCenter();
  const { t } = useLocale();
  const query = useRouteQuery(() => client.listMissions(), [client, version]);

  return (
    <div className="shell-content ops-hub">
      <div className="ops-hub-head">
        <SectionHeader
          title={t("page.operations")}
          description="Mission and project operations — narrative progress over tables."
        />
        <JarvisCoreBadge
          activeMissionCount={
            (query.data ?? []).filter((m) => m.status === "ACTIVE" || m.status === "IN_PROGRESS").length
          }
          trustHealth={trustSummary?.health}
        />
      </div>

      <RouteQueryGate query={query} loadingLabel="Loading operations" errorLabel="Operations unavailable" surface="Operations">
        {(missions) => {
          const active = missions.filter((m) => m.status === "ACTIVE" || m.status === "IN_PROGRESS");
          const waiting = missions.filter((m) => m.status === "STANDBY" || m.status === "INTERVENTION");
          const blocked = missions.filter((m) => m.status === "BLOCKED" || m.status === "HALTED");

          return (
            <>
              <div className="ops-summary" aria-label="Operations summary">
                <span>
                  <strong>{String(active.length).padStart(2, "0")}</strong> Active
                </span>
                <span>
                  <strong>{String(waiting.length).padStart(2, "0")}</strong> Waiting
                </span>
                <span>
                  <strong>{String(blocked.length).padStart(2, "0")}</strong> Blocked
                </span>
              </div>

              <div className="ops-card-grid">
                {[...active, ...waiting, ...blocked].slice(0, 12).map((m) => (
                  <MissionOperationCard key={m.id} mission={m} />
                ))}
              </div>

              {missions.length === 0 ? (
                <p className="ops-empty">No missions in durable records.</p>
              ) : null}
            </>
          );
        }}
      </RouteQueryGate>
    </div>
  );
}
