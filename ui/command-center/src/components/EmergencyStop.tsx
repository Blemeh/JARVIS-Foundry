import { AlertOctagon, OctagonX, RotateCcw, ShieldCheck } from "lucide-react";
import { Dialog } from "./Dialog";
import { useCommandCenter } from "../context/CommandCenterContext";
import { isCanonicalSource, sourceModeLabel, type ResolvedSourceMode } from "../lib/sourceMode";

// eslint-disable-next-line react-refresh/only-export-components
export function canResume(
  live: boolean,
  sourceMode: ResolvedSourceMode,
  targetDirectiveId: string | null = null,
  sourceCurrent = true,
): boolean {
  return !live || sourceCurrent && isCanonicalSource(sourceMode) && Boolean(targetDirectiveId);
}

// eslint-disable-next-line react-refresh/only-export-components
export function canActivateStop(live: boolean, sourceMode: ResolvedSourceMode): boolean {
  return !live || isCanonicalSource(sourceMode);
}

export function EmergencyStopDialog() {
  const { stopDialogOpen, closeStopDialog, stopActive, stopActivatedAt, resumeTargetId, activateStop, resetStop, mode, sourceMode, streamConnected } =
    useCommandCenter();

  const live = mode === "live";
  const canonical = isCanonicalSource(sourceMode);

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
              {canonical ? "STOP active" : sourceMode === "SIMULATED" ? "Simulated" : `STOP ${sourceModeLabel(sourceMode)}`} at {stopActivatedAt ? new Date(stopActivatedAt).toLocaleTimeString() : ""}
            </span>
            <button
              type="button"
              className="btn"
              onClick={resetStop}
              disabled={!canResume(live, sourceMode, resumeTargetId, streamConnected)}
              title={live && resumeTargetId ? `Revoke ${resumeTargetId}` : undefined}
            >
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
              disabled={!canActivateStop(live, sourceMode)}
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
                ? canonical
                  ? "LIVE STOP - CANONICAL DIRECTIVE"
                  : sourceMode === "SIMULATED"
                    ? "SIMULATED STOP - UI CANDIDATE"
                    : `STOP STATE ${sourceModeLabel(sourceMode)} - CANONICAL STATUS UNAVAILABLE`
                : "STOP IS THE HIGHEST AUTHORITY OWNER CONTROL"}
            </p>
            <p className="prose">
              {canonical
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
            {canonical ? "Uses the same OwnerControlService path as authenticated controls probes" : sourceMode === "SIMULATED" ? "The future runtime adapter will replace this simulation with the real directive path" : "Canonical owner-control path unavailable"}
          </li>
        </ul>
        {stopActive ? (
          <p className="stop-banner" role="status">
            {canonical ? "UI STATE: LIVE STOP ACTIVE" : sourceMode === "SIMULATED" ? "UI STATE: SIMULATED STOP - NO PRODUCTION SIDE EFFECT" : `UI STATE: STOP ${sourceModeLabel(sourceMode)}`}
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
      <span>{compact ? (stopActive ? "STOP ACTIVE" : label) : stopActive ? (sourceMode === "SIMULATED" ? "STOP simulation active" : "STOP ACTIVE") : label}</span>
    </button>
  );
}
