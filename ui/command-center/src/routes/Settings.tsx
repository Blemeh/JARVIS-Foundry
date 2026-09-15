import { Link } from "react-router-dom";
import { ArrowRight, Database, Eye, MailX, Radio, ShieldCheck } from "lucide-react";
import { RouteQueryGate } from "../components/RouteQueryGate";
import { useCommandCenter } from "../context/CommandCenterContext";
import { useRouteQuery } from "../hooks/useRouteQuery";
import { loadOverviewData, type OverviewData } from "../lib/overviewLoader";
import { sourceModeLabel } from "../lib/sourceMode";
import { LocaleToggle, useLocale } from "../i18n/LocaleContext";
import type { NotificationRouting } from "../i18n/catalog";

function SettingsContent(data: OverviewData) {
  const { sourceMode, connection, streamConnected } = useCommandCenter();
  const { t, routing, setRouting } = useLocale();
  const rows = [
    { icon: Database, label: t("settings.dataSource"), value: sourceModeLabel(sourceMode), detail: connection.error ?? (connection.lastFetchedAt ? `Last fetched ${connection.lastFetchedAt}` : "No live fetch timestamp") },
    { icon: Radio, label: t("settings.updateChannel"), value: streamConnected ? "CONNECTED" : "PAUSED", detail: streamConnected ? "Projection refresh events are connected." : "Updates are paused; displayed state may be stale." },
    { icon: ShieldCheck, label: t("settings.trust"), value: data.trust.state, detail: `Gate ${data.trust.gateStatus} · health ${data.trust.health}` },
    { icon: Eye, label: t("settings.displayPolicy"), value: "TRUTH FIRST", detail: "Unknown, unavailable, deferred, and simulated states remain explicit." },
  ];
  return (
    <div className="shell-content rm-page rm-settings">
      <header className="rm-page-head">
        <div><h1>{t("settings.title")}</h1><p>{t("settings.subtitle")}</p></div>
        <div className="rm-page-source"><span>{t("settings.readOnly")}</span><small>{t("settings.candidate")}</small></div>
      </header>
      <div className="rm-settings-list">
        {rows.map(({ icon: Icon, label, value, detail }) => <section key={label}><Icon size={18} /><div><h2>{label}</h2><p>{detail}</p></div><strong>{value}</strong></section>)}
      </div>
      <section className="rm-settings-policy">
        <MailX size={19} /><div><h2>{t("settings.gmail")}</h2><p>{t("settings.gmailBody")}</p></div><span>DEFERRED_BY_OWNER</span>
      </section>
      <section className="rm-settings-seams">
        <div>
          <h2>{t("settings.locale")}</h2>
          <LocaleToggle />
          <small>EN / SQ. Default English. Stored in localStorage.</small>
        </div>
        <div>
          <h2>{t("settings.notify")}</h2>
          <select
            className="settings-routing-select"
            value={routing}
            onChange={(event) => setRouting(event.target.value as NotificationRouting)}
            aria-label={t("settings.notify")}
          >
            <option value="in-app">{t("settings.notifyInApp")}</option>
            <option value="in-app-toast">{t("settings.notifyBoth")}</option>
            <option value="off">{t("settings.notifyOff")}</option>
          </select>
          <small>{t("settings.notifyHelp")}</small>
        </div>
        <div><h2>{t("settings.theme")}</h2><p>{t("settings.themeBody")}</p><small>{t("settings.themeNote")}</small></div>
        <div><h2>{t("settings.permissions")}</h2><p>{t("settings.permissionsBody")}</p><small>{t("settings.permissionsNote")}</small></div>
      </section>
      <div className="rm-settings-links"><Link to="/systems">Runtime systems <ArrowRight size={14} /></Link><Link to="/evidence">Evidence forensics <ArrowRight size={14} /></Link></div>
    </div>
  );
}

export function Settings() {
  const { client, version } = useCommandCenter();
  const { t } = useLocale();
  const query = useRouteQuery(() => loadOverviewData(client), [client, version], { keepPreviousWhileLoading: true });
  return <RouteQueryGate query={query} loadingLabel={t("settings.loading")} errorLabel={t("settings.unavailable")} surface="Command Center status">{(data) => <SettingsContent {...data} />}</RouteQueryGate>;
}
