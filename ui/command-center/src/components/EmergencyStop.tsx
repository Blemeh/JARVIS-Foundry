import { AlertOctagon, OctagonX, RotateCcw, ShieldCheck } from "lucide-react";
import { Dialog } from "./Dialog";
import { useCommandCenter } from "../context/CommandCenterContext";

export function EmergencyStopDialog() {
  const { stopDialogOpen, closeStopDialog, stopActive, stopActivatedAt, activateStop, resetStop, mode, mutationsEnabled } =
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
              {live ? "STOP active" : "Simulated"} at {stopActivatedAt ? new Date(stopActivatedAt).toLocaleTimeString() : ""}
            </span>
            <button type="button" className="btn" onClick={resetStop} disabled={!mutationsEnabled && live}>
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
                ? live
                  ? "LIVE STOP - CANONICAL DIRECTIVE"
                  : "SIMULATED STOP - UI CANDIDATE"
                : "STOP IS THE HIGHEST AUTHORITY OWNER CONTROL"}
            </p>
            <p className="prose">
              {live
                ? "Activating STOP records STOP_EVERYTHING on the durable owner directive ledger and engages the emergency-stop sentinel when configured. UI disconnect cannot clear STOP."
                : "This candidate is not connected to the JARVIS Supervisor. Activating STOP runs a local UI simulation only."}
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
            {live ? "Uses the same OwnerControlService path as authenticated controls probes" : "The future runtime adapter will replace this simulation with the real directive path"}
          </li>
        </ul>
        {stopActive ? (
          <p className="stop-banner" role="status">
            {live ? "UI STATE: LIVE STOP ACTIVE" : "UI STATE: SIMULATED STOP - NO PRODUCTION SIDE EFFECT"}
          </p>
        ) : null}
      </div>
    </Dialog>
  );
}

export function StopControl({ compact = false, label = "Emergency STOP" }: { compact?: boolean; label?: string }) {
  const { openStopDialog, stopActive } = useCommandCenter();
  return (
    <button
      type="button"
      className={`stop-control ${compact ? "stop-control-compact" : ""} ${stopActive ? "stop-control-armed" : ""}`}
      onClick={openStopDialog}
      aria-label={stopActive ? "STOP active. Open Emergency STOP control." : label}
    >
      <AlertOctagon size={compact ? 15 : 16} aria-hidden="true" />
      <span>{stopActive ? "STOP ACTIVE" : label}</span>
    </button>
  );
}
