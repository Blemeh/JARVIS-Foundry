import { NavLink } from "react-router-dom";
import { BrainCircuit, FolderKanban, Globe2, Menu } from "lucide-react";
import { StopControl } from "./EmergencyStop";

export function MobileNav({ onOpenDrawer }: { onOpenDrawer: () => void }) {
  return (
    <nav className="mobile-nav" aria-label="Mobile">
      <NavLink to="/" end className={({ isActive }) => `mobile-nav-item ${isActive ? "mobile-nav-item-active" : ""}`}>
        <BrainCircuit size={20} aria-hidden="true" />
        <span className="t-label">JARVIS</span>
      </NavLink>
      <NavLink to="/work" className={({ isActive }) => `mobile-nav-item ${isActive ? "mobile-nav-item-active" : ""}`}>
        <FolderKanban size={20} aria-hidden="true" />
        <span className="t-label">Work</span>
      </NavLink>
      <div className="mobile-nav-item mobile-nav-stop">
        <StopControl compact label="STOP" />
      </div>
      <NavLink to="/world" className={({ isActive }) => `mobile-nav-item ${isActive ? "mobile-nav-item-active" : ""}`}>
        <Globe2 size={20} aria-hidden="true" />
        <span className="t-label">World</span>
      </NavLink>
      <button type="button" className="mobile-nav-item" onClick={onOpenDrawer} aria-label="Open all sections">
        <Menu size={20} aria-hidden="true" />
        <span className="t-label">More</span>
      </button>
    </nav>
  );
}
