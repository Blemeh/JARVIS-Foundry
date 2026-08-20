import { OctagonX } from "lucide-react";
import { useCommandCenter } from "../context/CommandCenterContext";
import { sourceModeLabel } from "../lib/sourceMode";

export function StopBanner() {
  const { stopActive, stopActivatedAt, sourceMode } = useCommandCenter();
  if (!stopActive) return null;
  return (
    <div className="stop-banner-global" role="alert">
      <OctagonX size={15} aria-hidden="true" />
      <span className="t-label">Suspended / STOP active</span>
      <span className="t-mono stop-banner-global-note">
        {sourceMode === "LIVE" ? "LIVE STOP - CANONICAL DIRECTIVE" : sourceMode === "SIMULATED" ? "SIMULATED STOP - UI CANDIDATE" : `STOP STATE ${sourceModeLabel(sourceMode)} - CANONICAL STATUS UNAVAILABLE`}
        {stopActivatedAt ? ` · ${new Date(stopActivatedAt).toLocaleTimeString()}` : ""}
      </span>
    </div>
  );
}
