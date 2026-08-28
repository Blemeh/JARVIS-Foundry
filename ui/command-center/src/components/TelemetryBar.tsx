import { Link } from "react-router-dom";
import { useCommandCenter } from "../context/CommandCenterContext";
import { useRouteQuery } from "../hooks/useRouteQuery";
import {
  findMeasuredCostResource,
  formatMeasuredCostDisplay,
  formatResourceMetric,
} from "../lib/truthFormat";

function formatMemGb(value: number, unit: string): string {
  if (!Number.isFinite(value) || value < 0) return "UNKNOWN";
  if (unit.toLowerCase().includes("gb")) return `${value.toFixed(1)}GB`;
  if (unit.toLowerCase().includes("mb")) return `${(value / 1024).toFixed(1)}GB`;
  return formatResourceMetric(value, unit);
}

export function TelemetryBar() {
  const { client, version, sourceMode } = useCommandCenter();
  const query = useRouteQuery(
    () =>
      Promise.all([
        client.getSystemOverview(),
        client.listWorkers(),
        client.listMissions(),
        client.listEvidence(),
      ]).then(([overview, workers, missions, evidence]) => ({
        overview,
        workers,
        missions,
        evidence,
      })),
    [client, version],
  );

  const data = query.data;
  const resources = data?.overview.resourceUsage ?? [];
  const cpu = resources.find((r) => /cpu/i.test(r.label));
  const mem = resources.find((r) => /mem/i.test(r.label));
  const activeWorkers = (data?.workers ?? []).filter((w) => w.status === "ACTIVE").length;
  const activeMissions = (data?.missions ?? []).filter(
    (m) => m.status === "ACTIVE" || m.status === "IN_PROGRESS",
  ).length;
  const verifying = (data?.evidence ?? []).filter((e) => e.verdict === "EVALUATING").length;
  const costBase = findMeasuredCostResource(resources) ?? {
    display: "UNKNOWN",
    source: "NOT_REPORTED" as const,
    unknownMeasurements: null,
  };
  const cost = formatMeasuredCostDisplay(costBase, sourceMode);

  const items = [
    {
      label: "CPU",
      value: cpu ? formatResourceMetric(cpu.value, cpu.unit === "%" ? "%" : `% ${cpu.unit}`) : "UNKNOWN",
      to: "/systems",
    },
    {
      label: "MEM",
      value: mem ? formatMemGb(mem.value, mem.unit) : "UNKNOWN",
      to: "/systems",
    },
    {
      label: "WORKERS",
      value: data ? String(activeWorkers).padStart(2, "0") : "UNKNOWN",
      to: "/agents?status=active",
    },
    {
      label: "MISSIONS",
      value: data ? String(activeMissions).padStart(2, "0") : "UNKNOWN",
      to: "/missions?status=active",
    },
    {
      label: "VERIFYING",
      value: data ? String(verifying).padStart(2, "0") : "UNKNOWN",
      to: "/evidence?verdict=EVALUATING",
    },
    {
      label: "COST",
      value: cost,
      to: "/systems",
    },
  ];

  return (
    <footer className="telemetry-bar" role="contentinfo" aria-label="System telemetry">
      <div className="telemetry-bar-inner">
        {items.map((item) => (
          <Link key={item.label} to={item.to} className="telemetry-bar-item">
            <span className="telemetry-bar-label">{item.label}</span>
            <span className="telemetry-bar-value">{item.value}</span>
          </Link>
        ))}
      </div>
    </footer>
  );
}
