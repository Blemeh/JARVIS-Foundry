import { OctagonX } from "lucide-react";
import { useCommandCenter } from "../context/CommandCenterContext";

export function StopBanner() {
  const { stopActive, stopActivatedAt, mode } = useCommandCenter();
  if (!stopActive) return null;
  return (
    <div className="stop-banner-global" role="alert">
      <OctagonX size={15} aria-hidden="true" />
      <span className="t-label">Suspended / STOP active</span>
      <span className="t-mono stop-banner-global-note">
        {mode === "live" ? "LIVE STOP - CANONICAL DIRECTIVE" : "SIMULATED STOP - UI CANDIDATE"}
        {stopActivatedAt ? ` · ${new Date(stopActivatedAt).toLocaleTimeString()}` : ""}
      </span>
    </div>
  );
}
