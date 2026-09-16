import { useEffect, useState } from "react";
import { Outlet, useLocation } from "react-router-dom";
import { X } from "lucide-react";
import { NavRail, NavRailContent } from "./NavRail";
import { TopStatusBar } from "./TopStatusBar";
import { CommandBar } from "./CommandBar";
import { MobileNav } from "./MobileNav";
import { EmergencyStopDialog } from "./EmergencyStop";
import { StopBanner } from "./StopBanner";
import { PauseBanner } from "./PauseBanner";
import { RoutedOutletBoundary } from "./RouteErrorBoundary";
import { useDocumentTitle } from "../hooks/useDocumentTitle";
import { useLocale } from "../i18n/LocaleContext";

const routeTitles: Record<string, string> = {
  "/": "JARVIS",
  "/work": "Work",
  "/world": "World",
  "/capabilities": "Capabilities",
  "/you": "You",
  "/activity": "Activity",
  "/settings": "Settings",
  "/operations": "Operations",
  "/executive": "Autonomy",
  "/discovery": "Discovery",
  "/missions": "Missions",
  "/agents": "Agents",
  "/evidence": "Evidence",
  "/memory": "Memory",
  "/evolution": "Evolution",
  "/systems": "Systems",
  "/timeline": "Timeline",
  "/notifications": "Notifications",
  "/owner": "Owner Management",
};

export function AppShell() {
  const location = useLocation();
  const { t } = useLocale();
  const basePath = location.pathname.split("/").slice(0, 2).join("/") || "/";
  const titled: Record<string, string> = {
    "/": t("nav.jarvis"),
    "/work": t("page.work"),
    "/you": t("page.you"),
    "/settings": t("settings.title"),
    "/notifications": t("notify.title"),
    "/owner": t("owner.page"),
    "/executive": t("page.executive"),
    "/world": t("page.world"),
    "/capabilities": t("page.capabilities"),
  };
  const pageTitle = titled[location.pathname] ?? titled[basePath] ?? routeTitles[location.pathname] ?? routeTitles[basePath] ?? t("nav.commandCenter");
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
    <div className="app-shell app-shell--god" data-page={pageTitle.toLowerCase()}>
      <a className="skip-link" href="#main-content">
        {t("nav.skip")}
      </a>
      <NavRail />
      <TopStatusBar onOpenMobileNav={() => setMobileOpen(true)} />
      <main className="shell-main" id="main-content" tabIndex={-1}>
        <StopBanner />
        <PauseBanner />
        <RoutedOutletBoundary>
          <Outlet />
        </RoutedOutletBoundary>
      </main>
      <CommandBar />
      <MobileNav onOpenDrawer={() => setMobileOpen(true)} />
      {mobileOpen ? (
        <div className="mobile-drawer-layer" role="presentation" onMouseDown={(e) => e.target === e.currentTarget && setMobileOpen(false)}>
          <div className="mobile-drawer mobile-nav-rail-drawer" role="dialog" aria-modal="true" aria-label={t("nav.navigation")}>
            <button
              type="button"
              className="icon-btn mobile-drawer-close"
              aria-label={t("nav.close")}
              onClick={() => setMobileOpen(false)}
            >
              <X size={18} />
            </button>
            <NavRailContent variant="drawer" onNavigate={() => setMobileOpen(false)} />
          </div>
        </div>
      ) : null}
      <EmergencyStopDialog />
    </div>
  );
}
