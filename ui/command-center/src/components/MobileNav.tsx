import { NavLink } from "react-router-dom";
import { Braces, LayoutDashboard, Menu, Terminal } from "lucide-react";
import { StopControl } from "./EmergencyStop";

export function MobileNav({ onOpenDrawer }: { onOpenDrawer: () => void }) {
  return (
    <nav className="mobile-nav" aria-label="Mobile">
      <NavLink to="/" end className={({ isActive }) => `mobile-nav-item ${isActive ? "mobile-nav-item-active" : ""}`}>
        <LayoutDashboard size={20} aria-hidden="true" />
        <span className="t-label">Overview</span>
      </NavLink>
      <NavLink to="/missions" className={({ isActive }) => `mobile-nav-item ${isActive ? "mobile-nav-item-active" : ""}`}>
        <Braces size={20} aria-hidden="true" />
        <span className="t-label">Missions</span>
      </NavLink>
      <div className="mobile-nav-item mobile-nav-stop">
        <StopControl compact label="STOP" />
      </div>
      <button type="button" className="mobile-nav-item" onClick={onOpenDrawer} aria-label="Open all sections">
        <Menu size={20} aria-hidden="true" />
        <span className="t-label">More</span>
      </button>
      <span className="mobile-nav-command" aria-hidden="true">
        <Terminal size={20} />
      </span>
    </nav>
  );
}
