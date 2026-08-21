import { AlertOctagon, OctagonX, RotateCcw, ShieldCheck } from "lucide-react";
import { Dialog } from "./Dialog";
import { useCommandCenter } from "../context/CommandCenterContext";
import { sourceModeLabel, type ResolvedSourceMode } from "../lib/sourceMode";

// eslint-disable-next-line react-refresh/only-export-components
export function canResume(live: boolean, sourceMode: ResolvedSourceMode): boolean {
  return !live || sourceMode === "LIVE";
}

export function EmergencyStopDialog() {
  const { stopDialogOpen, closeStopDialog, stopActive, stopActivatedAt, activateStop, resetStop, mode, sourceMode, mutationsEnabled } =
    useCommandCenter();

  const live = mode === "live";

  return (
    <Dialog
      open={stopDialogOpen}
      title="Emergency STOP"
      onClose={closeStopDialog}
      labelledBy="stop-dialog-title"
      footer={
        stopActive ? (
          <>
            <span className="t-mono text-muted" style={{ marginRight: "auto" }}>
              {sourceMode === "LIVE" ? "STOP active" : sourceMode === "SIMULATED" ? "Simulated" : `STOP ${sourceModeLabel(sourceMode)}`} at {stopActivatedAt ? new Date(stopActivatedAt).toLocaleTimeString() : ""}
            </span>
            <button type="button" className="btn" onClick={resetStop} disabled={!canResume(live, sourceMode)}>
              <RotateCcw size={14} />
              Resume
            </button>
          </>
        ) : (
          <>
            <button type="button" className="btn" onClick={closeStopDialog}>
              Cancel
            </button>
            <button
              type="button"
              className="btn btn-danger btn-stop-simulate"
              onClick={activateStop}
              disabled={!mutationsEnabled && live}
            >
              <OctagonX size={14} />
              {live ? "Activate STOP" : "Simulate STOP"}
            </button>
          </>
        )
      }
    >
      <div className="stop-dialog">
        <div className={`stop-state ${stopActive ? "stop-state-active" : ""}`}>
          <AlertOctagon size={22} aria-hidden="true" />
          <div>
            <p className="t-label">
              {stopActive
                ? sourceMode === "LIVE"
                  ? "LIVE STOP - CANONICAL DIRECTIVE"
                  : sourceMode === "SIMULATED"
                    ? "SIMULATED STOP - UI CANDIDATE"
                    : `STOP STATE ${sourceModeLabel(sourceMode)} - CANONICAL STATUS UNAVAILABLE`
                : "STOP IS THE HIGHEST AUTHORITY OWNER CONTROL"}
            </p>
            <p className="prose">
              {sourceMode === "LIVE"
                ? "Activating STOP records STOP_EVERYTHING on the durable owner directive ledger and engages the emergency-stop sentinel when configured. UI disconnect cannot clear STOP."
                : sourceMode === "SIMULATED"
                  ? "This candidate is not connected to the JARVIS Supervisor. Activating STOP runs a local UI simulation only."
                  : `The runtime source is ${sourceModeLabel(sourceMode)}. STOP cannot be confirmed or cleared until the canonical Supervisor is available.`}
            </p>
          </div>
        </div>
        <ul className="stop-facts">
          <li>
            <ShieldCheck size={14} aria-hidden="true" />
            Deterministic, independent of Hermes, models, network, and UI health
          </li>
          <li>
            <ShieldCheck size={14} aria-hidden="true" />
            {sourceMode === "LIVE" ? "Uses the same OwnerControlService path as authenticated controls probes" : sourceMode === "SIMULATED" ? "The future runtime adapter will replace this simulation with the real directive path" : "Canonical owner-control path unavailable"}
          </li>
        </ul>
        {stopActive ? (
          <p className="stop-banner" role="status">
            {sourceMode === "LIVE" ? "UI STATE: LIVE STOP ACTIVE" : sourceMode === "SIMULATED" ? "UI STATE: SIMULATED STOP - NO PRODUCTION SIDE EFFECT" : `UI STATE: STOP ${sourceModeLabel(sourceMode)}`}
          </p>
        ) : null}
      </div>
    </Dialog>
  );
}

export function StopControl({ compact = false, label = "Emergency STOP" }: { compact?: boolean; label?: string }) {
  const { openStopDialog, stopActive, sourceMode } = useCommandCenter();
  return (
    <button
      type="button"
      className={`stop-control ${compact ? "stop-control-compact" : ""} ${stopActive ? "stop-control-armed" : ""}`}
      onClick={openStopDialog}
      aria-label={stopActive ? (sourceMode === "SIMULATED" ? "STOP simulation active. Open Emergency STOP control." : "STOP active. Open Emergency STOP control.") : label}
    >
      <AlertOctagon size={compact ? 15 : 16} aria-hidden="true" />
      <span>{stopActive ? (sourceMode === "SIMULATED" ? "STOP simulation active" : "STOP ACTIVE") : label}</span>
    </button>
  );
}
