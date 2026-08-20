import { useEffect, useState } from "react";
import { Outlet } from "react-router-dom";
import { X } from "lucide-react";
import { Sidebar, SidebarContent } from "./Sidebar";
import { TopStatusBar } from "./TopStatusBar";
import { CommandBar } from "./CommandBar";
import { MobileNav } from "./MobileNav";
import { EmergencyStopDialog } from "./EmergencyStop";
import { StopBanner } from "./StopBanner";

export function AppShell() {
  const [mobileOpen, setMobileOpen] = useState(false);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setMobileOpen(false);
    };
    document.addEventListener("keydown", onKey, true);
    return () => document.removeEventListener("keydown", onKey, true);
  }, []);

  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">
        Skip to content
      </a>
      <Sidebar />
      <TopStatusBar onOpenMobileNav={() => setMobileOpen(true)} />
      <main className="shell-main" id="main-content">
        <StopBanner />
        <Outlet />
      </main>
      <CommandBar />
      <MobileNav onOpenDrawer={() => setMobileOpen(true)} />
      {mobileOpen ? (
        <div className="mobile-drawer-layer" role="presentation" onMouseDown={(e) => e.target === e.currentTarget && setMobileOpen(false)}>
          <div className="mobile-drawer" role="dialog" aria-modal="true" aria-label="Navigation">
            <button
              type="button"
              className="icon-btn mobile-drawer-close"
              aria-label="Close navigation"
              onClick={() => setMobileOpen(false)}
            >
              <X size={18} />
            </button>
            <SidebarContent />
          </div>
        </div>
      ) : null}
      <EmergencyStopDialog />
    </div>
  );
}
