import { NavLink } from "react-router-dom";
import {
  Activity,
  BrainCircuit,
  Braces,
  Database,
  FileCheck2,
  History,
  LayoutDashboard,
  MemoryStick,
  Bell,
  Radar,
  Settings,
  UserRound,
} from "lucide-react";
import { ShieldQuestion } from "lucide-react";
import { StopControl } from "./EmergencyStop";
import { useCommandCenter } from "../context/CommandCenterContext";

const nav = [
  { to: "/", label: "Overview", icon: LayoutDashboard, end: true },
  { to: "/executive", label: "Executive", icon: BrainCircuit },
  { to: "/discovery", label: "Discovery", icon: Radar },
  { to: "/missions", label: "Missions", icon: Braces },
  { to: "/agents", label: "Agents", icon: Activity },
  { to: "/evidence", label: "Evidence", icon: FileCheck2 },
  { to: "/memory", label: "Memory", icon: MemoryStick },
  { to: "/systems", label: "Systems", icon: Settings },
  { to: "/timeline", label: "Timeline", icon: History },
  { to: "/notifications", label: "Notifications", icon: Bell },
];

export function SidebarContent() {
  const { openTrustDialog } = useCommandCenter();

  return (
    <>
      <div className="sidebar-brand">
        <div className="sidebar-logo" aria-hidden="true">
          <Database size={20} />
        </div>
        <div>
          <p className="sidebar-title">JARVIS FOUNDRY</p>
          <p className="t-label sidebar-version">Command Center Candidate</p>
        </div>
      </div>
      <nav className="sidebar-nav" aria-label="Primary">
        {nav.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.end}
            className={({ isActive }) => `nav-item ${isActive ? "nav-item-active" : ""}`}
          >
            <item.icon size={16} aria-hidden="true" />
            <span className="t-label">{item.label}</span>
          </NavLink>
        ))}
      </nav>
      <div className="sidebar-bottom">
        <button type="button" className="nav-item" onClick={openTrustDialog}>
          <ShieldQuestion size={16} aria-hidden="true" />
          <span className="t-label">Trust State</span>
        </button>
        <StopControl />
      </div>
      <div className="sidebar-footer">
        <span className="sidebar-avatar" aria-hidden="true">
          <UserRound size={16} />
        </span>
        <div>
          <p className="t-label">Owner Operator</p>
          <p className="t-mono sidebar-owner-id">ID: OWNER-01</p>
        </div>
      </div>
    </>
  );
}

export function Sidebar({ id }: { id?: string }) {
  return (
    <aside className="sidebar" id={id} aria-label="Command center sections">
      <SidebarContent />
    </aside>
  );
}
