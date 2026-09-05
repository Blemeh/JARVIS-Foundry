import { Link } from "react-router-dom";
import { ArrowRight, Database, Eye, MailX, Radio, ShieldCheck } from "lucide-react";
import { RouteQueryGate } from "../components/RouteQueryGate";
import { useCommandCenter } from "../context/CommandCenterContext";
import { useRouteQuery } from "../hooks/useRouteQuery";
import { loadOverviewData, type OverviewData } from "../lib/overviewLoader";
import { sourceModeLabel } from "../lib/sourceMode";

function SettingsContent(data: OverviewData) {
  const { sourceMode, connection, streamConnected } = useCommandCenter();
  const rows = [
    { icon: Database, label: "Data source", value: sourceModeLabel(sourceMode), detail: connection.error ?? (connection.lastFetchedAt ? `Last fetched ${connection.lastFetchedAt}` : "No live fetch timestamp") },
    { icon: Radio, label: "Update channel", value: streamConnected ? "CONNECTED" : "PAUSED", detail: streamConnected ? "Projection refresh events are connected." : "Updates are paused; displayed state may be stale." },
    { icon: ShieldCheck, label: "Trust", value: data.trust.state, detail: `Gate ${data.trust.gateStatus} · health ${data.trust.health}` },
    { icon: Eye, label: "Display policy", value: "TRUTH FIRST", detail: "Unknown, unavailable, deferred, and simulated states remain explicit." },
  ];
  return (
    <div className="shell-content rm-page rm-settings">
      <header className="rm-page-head">
        <div><h1>Settings</h1><p>Connection truth, display policy, and integration boundaries for this Command Center candidate.</p></div>
        <div className="rm-page-source"><span>READ ONLY</span><small>Candidate configuration</small></div>
      </header>
      <div className="rm-settings-list">
        {rows.map(({ icon: Icon, label, value, detail }) => <section key={label}><Icon size={18} /><div><h2>{label}</h2><p>{detail}</p></div><strong>{value}</strong></section>)}
      </div>
      <section className="rm-settings-policy">
        <MailX size={19} /><div><h2>Gmail integration</h2><p>Deferred by owner. It will not be probed, activated, or treated as a blocker by this candidate.</p></div><span>DEFERRED_BY_OWNER</span>
      </section>
      <section className="rm-settings-seams">
        <div><h2>Theme</h2><p>Graphite operational field</p><small>Fixed for this remaster; user theming is not integrated.</small></div>
        <div><h2>Notification routing</h2><p>NOT YET INTEGRATED</p><small>Inspect current in-product notifications without claiming external delivery.</small></div>
        <div><h2>Command permissions</h2><p>EXISTING AUTHORITY</p><small>This UI does not create a parallel permission system.</small></div>
      </section>
      <div className="rm-settings-links"><Link to="/systems">Runtime systems <ArrowRight size={14} /></Link><Link to="/evidence">Evidence forensics <ArrowRight size={14} /></Link></div>
    </div>
  );
}

export function Settings() {
  const { client, version } = useCommandCenter();
  const query = useRouteQuery(() => loadOverviewData(client), [client, version], { keepPreviousWhileLoading: true });
  return <RouteQueryGate query={query} loadingLabel="Loading settings" errorLabel="Settings unavailable" surface="Command Center status">{(data) => <SettingsContent {...data} />}</RouteQueryGate>;
}
