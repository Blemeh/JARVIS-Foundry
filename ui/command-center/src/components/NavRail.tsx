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

type NavItem = {
  label: string;
  to: string;
  icon: LucideIcon;
  end?: boolean;
};

const primary: NavItem[] = [
  { label: "JARVIS", to: "/", icon: BrainCircuit, end: true },
  { label: "WORK", to: "/work", icon: FolderKanban },
  { label: "WORLD", to: "/world", icon: Globe2 },
  { label: "CAPABILITIES", to: "/capabilities", icon: Network },
  { label: "YOU", to: "/you", icon: CircleUserRound },
];

const secondary: NavItem[] = [
  { label: "ACTIVITY", to: "/activity", icon: Activity },
  { label: "EVIDENCE", to: "/evidence", icon: ShieldCheck },
  { label: "SETTINGS", to: "/settings", icon: Settings2 },
];

const legacy: NavItem[] = [
  { label: "Missions", to: "/missions", icon: FolderKanban },
  { label: "Agents", to: "/agents", icon: CircleUserRound },
  { label: "Autonomy", to: "/executive", icon: Gauge },
  { label: "Discovery", to: "/discovery", icon: Globe2 },
  { label: "Memory", to: "/memory", icon: LibraryBig },
  { label: "Systems", to: "/systems", icon: Network },
  { label: "Timeline", to: "/timeline", icon: Activity },
  { label: "Owner controls", to: "/owner", icon: CircleUserRound },
];

function NavList({ items, onNavigate, className }: { items: NavItem[]; onNavigate?: () => void; className: string }) {
  return (
    <ul className={className}>
      {items.map(({ label, to, icon: Icon, end }) => (
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
      ))}
    </ul>
  );
}

export function NavRailContent({ variant = "rail", onNavigate }: { variant?: "rail" | "drawer"; onNavigate?: () => void }) {
  if (variant === "drawer") {
    return (
      <nav className="nav-rail nav-rail--drawer" aria-label="Primary">
        <div className="nav-drawer-brand">
          <span className="nav-rail-mark" aria-hidden="true">J</span>
          <div><strong>JARVIS</strong><span>Executive intelligence</span></div>
        </div>
        <span className="nav-section-label">Operate</span>
        <NavList items={primary} onNavigate={onNavigate} className="nav-rail-list" />
        <span className="nav-section-label">Inspect</span>
        <NavList items={secondary} onNavigate={onNavigate} className="nav-rail-list nav-rail-list--secondary" />
        <details className="nav-legacy">
          <summary>System views</summary>
          <NavList items={legacy} onNavigate={onNavigate} className="nav-rail-list nav-rail-list--legacy" />
        </details>
      </nav>
    );
  }

  return (
    <nav className="nav-rail" aria-label="Primary">
      <NavLink to="/" className="nav-rail-brand" aria-label="JARVIS home">
        <span className="nav-rail-mark" aria-hidden="true">J</span>
        <span className="nav-rail-brand-copy"><strong>JARVIS</strong><small>Command Center</small></span>
      </NavLink>
      <span className="nav-section-label">Operate</span>
      <NavList items={primary} className="nav-rail-list" />
      <span className="nav-section-label">Inspect</span>
      <NavList items={secondary} className="nav-rail-list nav-rail-list--secondary" />
      <div className="nav-rail-foot">
        <span className="nav-rail-foot-line" />
        <span className="nav-rail-foot-copy">One authority<br />Every claim inspectable</span>
      </div>
      <ul className="nav-rail-a11y-list" aria-label="Legacy system destinations">
        {legacy.map((item) => <li key={item.to}><NavLink to={item.to}>{item.label}</NavLink></li>)}
      </ul>
    </nav>
  );
}

export function NavRail() {
  const [collapsed, setCollapsed] = useState(false);
  return (
    <aside className={`nav-rail-shell ${collapsed ? "nav-rail-shell--collapsed" : ""}`} aria-label="Command navigation">
      <NavRailContent />
      <button
        type="button"
        className="nav-rail-collapse"
        onClick={() => setCollapsed((value) => !value)}
        aria-label={collapsed ? "Expand navigation" : "Collapse navigation"}
        aria-expanded={!collapsed}
      >
        {collapsed ? <ChevronRight size={15} aria-hidden="true" /> : <ChevronLeft size={15} aria-hidden="true" />}
      </button>
    </aside>
  );
}
