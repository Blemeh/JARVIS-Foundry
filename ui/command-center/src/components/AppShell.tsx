import { useEffect, useState } from "react";
import { Outlet, useLocation } from "react-router-dom";
import { X } from "lucide-react";
import { NavRail, NavRailContent } from "./NavRail";
import { TopStatusBar } from "./TopStatusBar";
import { CommandBar } from "./CommandBar";
import { TelemetryBar } from "./TelemetryBar";
import { MobileNav } from "./MobileNav";
import { EmergencyStopDialog } from "./EmergencyStop";
import { StopBanner } from "./StopBanner";
import { RoutedOutletBoundary } from "./RouteErrorBoundary";
import { useDocumentTitle } from "../hooks/useDocumentTitle";

const routeTitles: Record<string, string> = {
  "/": "JARVIS",
  "/operations": "Operations",
  "/executive": "Executive",
  "/discovery": "Discovery",
  "/missions": "Missions",
  "/agents": "Agents",
  "/evidence": "Evidence",
  "/memory": "Memory",
  "/systems": "Systems",
  "/timeline": "Timeline",
  "/notifications": "Notifications",
  "/owner": "Owner Management",
};

export function AppShell() {
  const location = useLocation();
  const basePath = location.pathname.split("/").slice(0, 2).join("/") || "/";
  const pageTitle = routeTitles[location.pathname] ?? routeTitles[basePath] ?? "Command Center";
  useDocumentTitle(pageTitle);
  const [mobileOpen, setMobileOpen] = useState(false);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setMobileOpen(false);
    };
    document.addEventListener("keydown", onKey, true);
    return () => document.removeEventListener("keydown", onKey, true);
  }, []);

  return (
    <div className="app-shell app-shell--god">
      <a className="skip-link" href="#main-content">
        Skip to content
      </a>
      <NavRail />
      <TopStatusBar onOpenMobileNav={() => setMobileOpen(true)} />
      <main className="shell-main" id="main-content" tabIndex={-1}>
        <StopBanner />
        <RoutedOutletBoundary>
          <Outlet />
        </RoutedOutletBoundary>
      </main>
      <CommandBar />
      <TelemetryBar />
      <MobileNav onOpenDrawer={() => setMobileOpen(true)} />
      {mobileOpen ? (
        <div className="mobile-drawer-layer" role="presentation" onMouseDown={(e) => e.target === e.currentTarget && setMobileOpen(false)}>
          <div className="mobile-drawer mobile-nav-rail-drawer" role="dialog" aria-modal="true" aria-label="Navigation">
            <button
              type="button"
              className="icon-btn mobile-drawer-close"
              aria-label="Close navigation"
              onClick={() => setMobileOpen(false)}
            >
              <X size={18} />
            </button>
            <NavRailContent variant="drawer" />
          </div>
        </div>
      ) : null}
      <EmergencyStopDialog />
    </div>
  );
}
