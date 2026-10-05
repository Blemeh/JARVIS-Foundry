import { useMemo, useState } from "react";
import {
  Bot,
  Braces,
  AppWindow,
  ChevronRight,
  Database,
  FileText,
  FolderOpen,
  Github,
  MailX,
  MonitorCog,
  Network,
  PlugZap,
  ServerCog,
  Sparkles,
  type LucideIcon,
} from "lucide-react";
import { Drawer } from "../components/Drawer";
import { RouteQueryGate } from "../components/RouteQueryGate";
import { useCommandCenter } from "../context/CommandCenterContext";
import { useRouteQuery } from "../hooks/useRouteQuery";
import { loadOverviewData, type OverviewData } from "../lib/overviewLoader";
import { sourceModeLabel } from "../lib/sourceMode";
import { useLocale } from "../i18n/LocaleContext";

type CapabilityRow = { spec: Record<string, unknown>; metadata: Record<string, unknown> | null; readiness: Record<string, unknown> };
type GroupName = "Web" | "Browser" | "Code" | "GitHub" | "Files" | "Documents" | "Data" | "System" | "Desktop" | "MCP" | "Providers" | "Self-acquired";
type Group = { name: GroupName; icon: LucideIcon; rows: CapabilityRow[] };

const groupDefs: Array<Omit<Group, "rows">> = [
  { name: "Web", icon: Network }, { name: "Browser", icon: AppWindow }, { name: "Code", icon: Braces },
  { name: "GitHub", icon: Github }, { name: "Files", icon: FolderOpen }, { name: "Documents", icon: FileText },
  { name: "Data", icon: Database }, { name: "System", icon: ServerCog }, { name: "Desktop", icon: MonitorCog },
  { name: "MCP", icon: PlugZap }, { name: "Providers", icon: Bot }, { name: "Self-acquired", icon: Sparkles },
];

function string(value: unknown, fallback = "UNKNOWN"): string {
  return typeof value === "string" && value.trim() ? value.trim() : fallback;
}

function classify(row: CapabilityRow): GroupName {
  const haystack = `${string(row.spec.capability_id)} ${string(row.spec.action_kind)} ${string(row.spec.kind)} ${string(row.metadata?.source, "")} ${JSON.stringify(row.metadata?.provenance ?? {})}`.toLowerCase();
  if (/self|acquir|generated|synth/.test(haystack)) return "Self-acquired";
  if (/github|git\./.test(haystack)) return "GitHub";
  if (/browser|playwright|chrome/.test(haystack)) return "Browser";
  if (/web|http|search|fetch|url/.test(haystack)) return "Web";
  if (/document|pdf|word|docx|slide|sheet/.test(haystack)) return "Documents";
  if (/file|path|directory|folder|hash/.test(haystack)) return "Files";
  if (/database|sql|data|query|ledger/.test(haystack)) return "Data";
  if (/desktop|computer|window|keyboard|mouse/.test(haystack)) return "Desktop";
  if (/mcp|tool server/.test(haystack)) return "MCP";
  if (/provider|model|llm|api/.test(haystack)) return "Providers";
  if (/code|repo|script|test|build/.test(haystack)) return "Code";
  return "System";
}

function trustState(row: CapabilityRow): string {
  return string(row.readiness.trust_state ?? row.metadata?.trust_state);
}

function displayedState(row: CapabilityRow): string {
  const trust = trustState(row);
  if (trust === "PROBATION" || trust === "DISABLED") return trust;
  return string(row.readiness.state);
}

function provenance(row: CapabilityRow): string {
  const nested = row.metadata?.provenance;
  if (nested && typeof nested === "object") {
    const source = (nested as Record<string, unknown>).source;
    const stage = (nested as Record<string, unknown>).stage;
    const authority = (nested as Record<string, unknown>).authority;
    return [source, stage, authority].filter((value): value is string => typeof value === "string" && Boolean(value.trim())).join(" · ") || "UNKNOWN";
  }
  return string(row.metadata?.source);
}

function CapabilitiesContent(data: OverviewData) {
  const { sourceMode } = useCommandCenter();
  const { t } = useLocale();
  const [selected, setSelected] = useState<CapabilityRow | null>(null);
  const rawRows = data.capabilityFabric?.capabilities;
  const groups = useMemo<Group[]>(() => {
    const rows = Array.isArray(rawRows) ? rawRows.filter((row): row is CapabilityRow => Boolean(row && typeof row === "object" && row.spec && row.readiness)) : [];
    return groupDefs.map((definition) => ({ ...definition, rows: rows.filter((row) => classify(row) === definition.name) }));
  }, [rawRows]);
  const surfaceStatus = data.capabilityFabric?.status ?? "UNAVAILABLE";
  const surfaceAuthority = data.capabilityFabric?.authority ?? "CAPABILITY_FABRIC_CATALOG";
  const allRows = useMemo(() => groups.flatMap((g) => g.rows), [groups]);
  const countBy = (pred: (s: string) => boolean) => allRows.filter((r) => pred(displayedState(r).toUpperCase())).length;
  const available = countBy((s) => s === "READY" || s === "AVAILABLE");
  const inUse = countBy((s) => s === "ACTIVE" || s === "IN_USE" || s === "PROBATION");
  const limited = countBy((s) => s === "DEGRADED" || s === "NOT_READY" || s === "LIMITED" || s === "PROBATION");
  const unavailable = countBy((s) => s === "DISABLED" || s === "UNAVAILABLE" || s === "UNKNOWN");

  return (
    <div className="shell-content rm-page rm-capabilities">
      <header className="rm-page-head">
        <div><h1>{t("page.capabilities")}</h1><p>{t("page.capabilitiesSubtitle")}</p></div>
        <div className="rm-page-source"><span>{sourceModeLabel(sourceMode)} · {surfaceStatus}</span><small>{surfaceAuthority}</small></div>
      </header>

      <aside className="rm-gmail-policy">
        <MailX size={18} aria-hidden="true" /><div><strong>{t("settings.gmail")}</strong><p>{t("settings.gmailBody")}</p></div><span>DEFERRED_BY_OWNER</span>
      </aside>

      <div className="rm-capability-summary">
        <div><strong>{data.capabilityFabric?.count ?? "—"}</strong><span>catalog entries</span></div>
        <p>{surfaceStatus === "UNAVAILABLE" ? "The connected source does not publish the additive Capability Fabric projection. No fixture capabilities are substituted." : surfaceStatus === "EMPTY" ? "The canonical catalog is available and currently empty." : "Registration is not readiness. Every state below comes from observed Fabric evidence."}</p>
      </div>

      {surfaceStatus !== "UNAVAILABLE" ? (
        <div className="work-summary-grid" aria-label="Capability status summary">
          <div className="work-summary-card"><h3>Available</h3><p>{available} ready to use</p></div>
          <div className="work-summary-card"><h3>In use</h3><p>{inUse} currently active</p></div>
          <div className="work-summary-card"><h3>Limited or unavailable</h3><p>{limited + unavailable} need attention or are off</p></div>
        </div>
      ) : null}

      <div className="rm-capability-groups">
        {groups.map(({ name, icon: Icon, rows: groupRows }) => (
          <section key={name} className={`rm-capability-group ${groupRows.length ? "has-rows" : ""}`} aria-labelledby={`capability-${name.toLowerCase().replace(/[^a-z]+/g, "-")}`}>
            <header><Icon size={17} aria-hidden="true" /><h2 id={`capability-${name.toLowerCase().replace(/[^a-z]+/g, "-")}`}>{name}</h2><span>{groupRows.length || "—"}</span></header>
            {groupRows.length ? <div>{groupRows.map((row) => {
              const id = string(row.spec.capability_id);
              const readiness = displayedState(row);
              return <button key={id} type="button" className="rm-capability-row" onClick={() => setSelected(row)}>
                <span className={`rm-readiness rm-readiness--${readiness.toLowerCase()}`} />
                <span><strong>{id}</strong><small>{string(row.spec.action_kind)} · {string(row.spec.kind)}</small></span>
                <em>{readiness}</em><ChevronRight size={15} aria-hidden="true" />
              </button>;
            })}</div> : <p>{surfaceStatus === "UNAVAILABLE" ? "No entries in this group" : "No canonical entries"}</p>}
          </section>
        ))}
      </div>

      <Drawer open={selected !== null} title="Capability record" onClose={() => setSelected(null)}>
        {selected ? <div className="rm-capability-detail">
          <Network size={22} /><h2>{string(selected.spec.capability_id)}</h2><p>{string(selected.spec.action_kind)} through {string(selected.spec.executor)}.</p>
          <dl>
            <div><dt>Observed readiness</dt><dd>{string(selected.readiness.state)}</dd></div>
            <div><dt>Catalog trust</dt><dd>{trustState(selected)}</dd></div>
            <div><dt>Last verified</dt><dd>{string(selected.readiness.last_verified_at)}</dd></div>
            <div><dt>Last outcome</dt><dd>{string(selected.readiness.last_outcome)}</dd></div>
            <div><dt>Claim vs verified</dt><dd>{string(selected.readiness.claimed_vs_verified)}</dd></div>
            <div><dt>Semantic</dt><dd>{string(selected.spec.semantic)}</dd></div>
            <div><dt>Unattended</dt><dd>{string(selected.spec.unattended)}</dd></div>
            <div><dt>Effect class</dt><dd>{string(selected.metadata?.effect_class)}</dd></div>
            <div><dt>Provenance</dt><dd>{provenance(selected)}</dd></div>
          </dl>
          <h3>Limitations</h3><p>{Array.isArray(selected.spec.limitations) && selected.spec.limitations.length ? selected.spec.limitations.join(" · ") : "No limitation metadata was published."}</p>
          <h3>Trust note</h3><p>{string(selected.readiness.note, "No readiness note was published.")}</p>
        </div> : null}
      </Drawer>
    </div>
  );
}

export function Capabilities() {
  const { client, version } = useCommandCenter();
  const query = useRouteQuery(() => loadOverviewData(client), [client, version], { keepPreviousWhileLoading: true });
  return <RouteQueryGate query={query} loadingLabel="Loading capabilities" errorLabel="Capabilities unavailable" surface="Capability Fabric projection">{(data) => <CapabilitiesContent {...data} />}</RouteQueryGate>;
}
