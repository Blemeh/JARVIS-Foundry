import { OctagonX } from "lucide-react";
import { useCommandCenter } from "../context/CommandCenterContext";
import { isCanonicalSource, sourceModeLabel } from "../lib/sourceMode";
import { useLocale } from "../i18n/LocaleContext";

export function StopBanner() {
  const { stopActive, stopActivatedAt, sourceMode } = useCommandCenter();
  const { t } = useLocale();
  if (!stopActive) return null;
  return (
    <div className="stop-banner-global" role="alert">
      <OctagonX size={15} aria-hidden="true" />
      <span className="t-label">{t("stop.banner")}</span>
      <span className="t-mono stop-banner-global-note">
        {isCanonicalSource(sourceMode) ? "LIVE STOP - CANONICAL DIRECTIVE" : sourceMode === "SIMULATED" ? "SIMULATED STOP - UI CANDIDATE" : `STOP STATE ${sourceModeLabel(sourceMode)} - CANONICAL STATUS UNAVAILABLE`}
        {stopActivatedAt ? ` · ${new Date(stopActivatedAt).toLocaleTimeString()}` : ""}
      </span>
    </div>
  );
}
