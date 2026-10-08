import { useEffect, useState } from "react";
import { Info, ShieldQuestion } from "lucide-react";
import { Dialog } from "./Dialog";
import type { TrustSummary } from "../lib/contracts";
import { StatusBadge } from "./StatusBadge";
import { useCommandCenter } from "../context/CommandCenterContext";
import { sourceCopy, sourceModeLabel } from "../lib/sourceMode";
import { formatCoveragePercent } from "../lib/truthFormat";

export function TrustDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { client, version, sourceMode, stopActive } = useCommandCenter();
  const [trust, setTrust] = useState<TrustSummary | null>(null);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    client.getTrustSummary().then((t) => {
      if (!cancelled) setTrust(t);
    });
    return () => {
      cancelled = true;
    };
  }, [open, client, version]);

  return (
    <Dialog
      open={open}
      title="Trust State - Explainable"
      onClose={onClose}
      labelledBy="trust-dialog-title"
      footer={
        <span className="t-mono text-muted trust-sim-note">
          {sourceCopy(sourceMode, {
            live: "LIVE SOURCE - canonical Foundry/Supervisor projection.",
            simulated: "SIMULATED FIXTURE - not canonical JARVIS truth. The runtime adapter supplies measured values in live mode.",
            unavailable: `SOURCE ${sourceModeLabel(sourceMode)} - canonical trust state is unavailable; no fixture fallback is shown.`,
          })}
        </span>
      }
    >
      {trust ? (
        <div className="trust-dialog">
          <div className="trust-hero">
            <div className="trust-hero-label">
              <ShieldQuestion size={18} aria-hidden="true" />
              <span className="t-label">TRUST STATE</span>
            </div>
            <div className="trust-hero-values">
              <StatusBadge status={trust.state} />
              <span className="t-mono text-muted" aria-label={trust.score === null ? "Trust score not available" : `Trust score ${trust.score} percent`}>
                {trust.score === null ? "SCORE: NOT AVAILABLE" : `SCORE: ${trust.score}%`}
              </span>
            </div>
          </div>
          <div className="trust-grid">
            {(stopActive || trust.state === "SUSPENDED") ? (
              <p className="trust-suspended-note">
                Suspended — missing live-observation evidence. Permitted work can still run within constraints.
              </p>
            ) : null}
            <div className="trust-cell">
              <span className="t-label text-muted">Gate</span>
              <span className="t-mono">{trust.gateStatus}</span>
            </div>
            <div className="trust-cell">
              <span className="t-label text-muted">Evidence coverage</span>
              <span className="t-mono">{formatCoveragePercent(trust.evidenceCoverage)}</span>
            </div>
            <div className="trust-cell">
              <span className="t-label text-muted">Open findings</span>
              <span className="t-mono">{trust.unresolvedFindings}</span>
            </div>
            <div className="trust-cell">
              <span className="t-label text-muted">Health</span>
              <span className="t-mono">{trust.health}</span>
            </div>
            <div className="trust-cell trust-cell-wide">
              <span className="t-label text-muted">Last verified</span>
              <span className="t-mono">{new Date(trust.lastVerifiedAt).toISOString()}</span>
            </div>
          </div>
          <h3 className="t-label text-muted trust-factors-title">What produces this state</h3>
          <ul className="trust-factors">
            {trust.factors.map((f) => (
              <li key={f.id} className="trust-factor">
                <div className="trust-factor-head">
                  <span className="t-mono">{f.label}</span>
                  <StatusBadge status={f.state} />
                </div>
                <p className="prose">{f.detail}</p>
              </li>
            ))}
          </ul>
          <p className="t-mono text-muted trust-explain-note">
            <Info size={13} aria-hidden="true" /> {sourceCopy(sourceMode, {
              live: "This trust projection is derived from canonical runtime records; unavailable factors remain explicit.",
              simulated: "A numeric score is withheld in simulation because it would imply canonical measurement. Every factor above can be traced to a fixture record.",
              unavailable: `Trust explanation is ${sourceMode.toLowerCase()}; no canonical measurement or fixture fallback is asserted.`,
            })}
          </p>
        </div>
      ) : (
        <div className="state-block">
          <span className="t-label text-muted">Loading trust summary...</span>
        </div>
      )}
    </Dialog>
  );
}
