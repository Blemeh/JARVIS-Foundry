import { useState } from "react";
import { NavLink } from "react-router-dom";
import {
  Activity,
  BrainCircuit,
  ChevronLeft,
  ChevronRight,
  CircleUserRound,
  FolderKanban,
  Gauge,
  Globe2,
  LibraryBig,
  Network,
  Settings2,
  ShieldCheck,
  type LucideIcon,
} from "lucide-react";
import { useLocale, type MessageKey } from "../i18n/LocaleContext";

type NavItem = {
  labelKey: MessageKey;
  to: string;
  icon: LucideIcon;
  end?: boolean;
};

const primary: NavItem[] = [
  { labelKey: "nav.jarvis", to: "/", icon: BrainCircuit, end: true },
  { labelKey: "nav.work", to: "/work", icon: FolderKanban },
  { labelKey: "nav.world", to: "/world", icon: Globe2 },
  { labelKey: "nav.capabilities", to: "/capabilities", icon: Network },
  { labelKey: "nav.you", to: "/you", icon: CircleUserRound },
];

const secondary: NavItem[] = [
  { labelKey: "nav.activity", to: "/activity", icon: Activity },
  { labelKey: "nav.evidence", to: "/evidence", icon: ShieldCheck },
  { labelKey: "nav.settings", to: "/settings", icon: Settings2 },
];

const legacy: NavItem[] = [
  { labelKey: "nav.missions", to: "/missions", icon: FolderKanban },
  { labelKey: "nav.agents", to: "/agents", icon: CircleUserRound },
  { labelKey: "nav.autonomy", to: "/executive", icon: Gauge },
  { labelKey: "nav.discovery", to: "/discovery", icon: Globe2 },
  { labelKey: "nav.memory", to: "/memory", icon: LibraryBig },
  { labelKey: "nav.systems", to: "/systems", icon: Network },
  { labelKey: "nav.timeline", to: "/timeline", icon: Activity },
  { labelKey: "nav.owner", to: "/owner", icon: CircleUserRound },
];

function NavList({ items, onNavigate, className }: { items: NavItem[]; onNavigate?: () => void; className: string }) {
  const { t } = useLocale();
  return (
    <ul className={className}>
      {items.map(({ labelKey, to, icon: Icon, end }) => {
        const label = t(labelKey);
        return (
          <li key={to} className="nav-rail-item">
            <NavLink
              to={to}
              end={end}
              className={({ isActive }) => `nav-rail-link ${isActive ? "nav-rail-link--active" : ""}`}
              onClick={onNavigate}
              title={label}
            >
              <Icon size={17} aria-hidden="true" />
              <span className="nav-rail-link-text">{label}</span>
            </NavLink>
          </li>
        );
      })}
    </ul>
  );
}

export function NavRailContent({ variant = "rail", onNavigate }: { variant?: "rail" | "drawer"; onNavigate?: () => void }) {
  const { t } = useLocale();
  if (variant === "drawer") {
    return (
      <nav className="nav-rail nav-rail--drawer" aria-label={t("nav.primary")}>
        <div className="nav-drawer-brand">
          <span className="nav-rail-mark" aria-hidden="true">J</span>
          <div><strong>JARVIS</strong><span>{t("nav.drawerBrand")}</span></div>
        </div>
        <span className="nav-section-label">{t("nav.operate")}</span>
        <NavList items={primary} onNavigate={onNavigate} className="nav-rail-list" />
        <span className="nav-section-label">{t("nav.inspect")}</span>
        <NavList items={secondary} onNavigate={onNavigate} className="nav-rail-list nav-rail-list--secondary" />
        <details className="nav-legacy">
          <summary>{t("nav.systemViews")}</summary>
          <NavList items={legacy} onNavigate={onNavigate} className="nav-rail-list nav-rail-list--legacy" />
        </details>
      </nav>
    );
  }

  return (
    <nav className="nav-rail" aria-label={t("nav.primary")}>
      <NavLink to="/" className="nav-rail-brand" aria-label={t("nav.home")}>
        <span className="nav-rail-mark" aria-hidden="true">J</span>
        <span className="nav-rail-brand-copy"><strong>JARVIS</strong><small>{t("nav.commandCenter")}</small></span>
      </NavLink>
      <span className="nav-section-label">{t("nav.operate")}</span>
      <NavList items={primary} className="nav-rail-list" />
      <span className="nav-section-label">{t("nav.inspect")}</span>
      <NavList items={secondary} className="nav-rail-list nav-rail-list--secondary" />
      <div className="nav-rail-foot">
        <span className="nav-rail-foot-line" />
        <span className="nav-rail-foot-copy">{t("nav.foot").split("\n")[0]}<br />{t("nav.foot").split("\n")[1]}</span>
      </div>
      <ul className="nav-rail-a11y-list" aria-label={t("nav.legacy")}>
        {legacy.map((item) => <li key={item.to}><NavLink to={item.to}>{t(item.labelKey)}</NavLink></li>)}
      </ul>
    </nav>
  );
}

export function NavRail() {
  const [collapsed, setCollapsed] = useState(false);
  const { t } = useLocale();
  return (
    <aside className={`nav-rail-shell ${collapsed ? "nav-rail-shell--collapsed" : ""}`} aria-label={t("nav.navigation")}>
      <NavRailContent />
      <button
        type="button"
        className="nav-rail-collapse"
        onClick={() => setCollapsed((value) => !value)}
        aria-label={collapsed ? t("nav.expand") : t("nav.collapse")}
        aria-expanded={!collapsed}
      >
        {collapsed ? <ChevronRight size={15} aria-hidden="true" /> : <ChevronLeft size={15} aria-hidden="true" />}
      </button>
    </aside>
  );
}
