import { useMemo, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { HelpCircle, RefreshCcw, ShieldQuestion } from "lucide-react";
import { useCommandCenter } from "../context/CommandCenterContext";
import type { MemoryGraph, MemoryNode } from "../lib/contracts";
import { KnowledgeGraph } from "../components/KnowledgeGraph";
import { StatusBadge } from "../components/StatusBadge";
import { Dialog } from "../components/Dialog";
import { EmptyState, ErrorState } from "../components/states";
import { RouteQueryGate } from "../components/RouteQueryGate";
import { ContextChip, EntityRefLink } from "../components/EntityRefLink";
import { useRouteQuery } from "../hooks/useRouteQuery";
import { sourceModeLabel } from "../lib/sourceMode";
import { NOT_REPORTED_NUMERIC } from "../lib/truthFormat";

const formatConfidenceLabel = (value: number): string => {
  if (value === NOT_REPORTED_NUMERIC || !Number.isFinite(value) || value < 0) return "NOT REPORTED";
  return `${value}%`;
};

function MemoryContent({ graph }: { graph: MemoryGraph }) {
  const { sourceMode } = useCommandCenter();
  const { memoryId } = useParams();
  const [searchParams, setSearchParams] = useSearchParams();
  const navigate = useNavigate();
  const missionParam = searchParams.get("mission");
  const [manualSelected, setManualSelected] = useState<MemoryNode | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [requested, setRequested] = useState(false);

  const missionNoNode = Boolean(
    missionParam &&
      !memoryId &&
      !graph.nodes.some((n) => n.ref?.kind === "mission" && n.ref.id === missionParam),
  );

  const derivedSelected = useMemo(() => {
    if (memoryId) {
      return graph.nodes.find((n) => n.id === memoryId) ?? null;
    }
    if (missionParam) {
      return graph.nodes.find((n) => n.ref?.kind === "mission" && n.ref.id === missionParam) ?? null;
    }
    return graph.nodes.find((n) => n.id === "n-mission") ?? graph.nodes[0] ?? null;
  }, [graph, memoryId, missionParam]);

  const selected = manualSelected ?? derivedSelected;

  const connectedCount = useMemo(() => {
    const ids = new Set(graph.edges.flatMap((e) => [e.from, e.to]));
    return ids.size;
  }, [graph]);

  if (memoryId && !graph.nodes.some((n) => n.id === memoryId)) {
    return (
      <div className="shell-content">
        <ErrorState
          label="Memory node not found"
          detail="No knowledge node exists at this identifier."
        />
        <div className="context-row">
          <Link to="/memory" className="text-link">
            Back to the knowledge graph
          </Link>
        </div>
      </div>
    );
  }

  if (graph.nodes.length === 0) {
    return (
      <div className="shell-content">
        <EmptyState
          label="Knowledge graph empty"
          detail={
            sourceMode === "SIMULATED"
              ? "No memory nodes are present in this simulated projection."
              : sourceMode === "LIVE"
                ? "No canonical memory nodes are currently projected."
                : `Knowledge graph unavailable: runtime source is ${sourceModeLabel(sourceMode)}.`
          }
        />
      </div>
    );
  }

  if (!selected && !missionNoNode) {
    return (
      <div className="shell-content">
        <EmptyState
          label="No node selected"
          detail="Select a node in the knowledge graph to inspect it."
        />
      </div>
    );
  }

  return (
    <div className="memory-layout">
      <div className="memory-main">
        <div className="memory-head">
          <div>
            <p className="t-label text-muted page-kicker">Knowledge Graph</p>
            <h1 className="t-display">Knowledge Graph</h1>
            <p className="prose page-desc">
              Epistemic state, not a flat store: observations, evidence, claims, beliefs, decisions, procedures,
              missions, and resources are distinct categories with distinct provenance.
            </p>
          </div>
          <div className="memory-head-meta">
            <span className="t-mono text-muted">{graph.nodes.length} nodes</span>
            <span className="t-mono text-muted">{connectedCount} connected</span>
            <span className="t-mono text-muted">{graph.edges.length} edges</span>
          </div>
        </div>
        {missionParam ? (
          <div className="context-row memory-context-row">
            <ContextChip
              label={`Memory related to mission ${missionParam}`}
              onClear={() => setSearchParams({}, { replace: true })}
            />
          </div>
        ) : null}
        <KnowledgeGraph
          graph={graph}
          selectedId={selected?.id ?? null}
          onSelect={(node) => {
            setManualSelected(node);
            navigate(`/memory/${node.id}`, { replace: memoryId === node.id });
          }}
        />
      </div>

      <aside className="memory-inspector panel panel-corner" aria-label="Node inspector">
        <div className="panel-header">
          <h2 className="t-label panel-title text-muted">Node Inspector</h2>
          <span className="t-mono text-muted">{sourceModeLabel(sourceMode)}</span>
        </div>
        <div className="inspector-body">
          {missionNoNode && !selected ? (
            <div className="inspector-selection">
              <span className="t-mono inspector-active-label">No memory node</span>
              <h3 className="inspector-title">No node for {missionParam}</h3>
              <p className="prose inspector-summary">
                {sourceMode === "SIMULATED" ? "This simulated mission has no memory node in the knowledge graph. Select a node to inspect it." : sourceMode === "LIVE" ? "This canonical mission has no memory node in the knowledge graph. Select a node to inspect it." : `Memory relationship unavailable: runtime source is ${sourceModeLabel(sourceMode)}.`}
              </p>
            </div>
          ) : null}
          {selected ? (
            <>
              <div className="inspector-selection">
                <span className="t-mono inspector-active-label">Active selection</span>
                <h3 className="inspector-title">{selected.label}</h3>
                <p className="prose inspector-summary">{selected.summary}</p>
                <div className="inspector-category">
                  <StatusBadge status={selected.category} />
                </div>
              </div>

              {selected.ref ? (
                <div className="inspector-section">
                  <h4 className="t-label text-muted inspector-section-title">Operational record</h4>
                  <EntityRefLink entity={selected.ref} />
                </div>
              ) : null}

              <div className="inspector-section">
                <h4 className="t-label text-muted inspector-section-title">Epistemic status</h4>
                <div className="inspector-bars">
                  <div>
                    <div className="inspector-bar-head">
                      <span className="t-mono">Confidence</span>
                      <span className="t-mono inspector-bar-value">{formatConfidenceLabel(selected.confidence)}</span>
                    </div>
                    {selected.confidence >= 0 && Number.isFinite(selected.confidence) ? (
                      <div className="meter">
                        <div className="meter-fill" style={{ width: `${selected.confidence}%` }} />
                      </div>
                    ) : (
                      <p className="t-mono text-muted inspector-unknown-none">Confidence not reported in canonical projection.</p>
                    )}
                  </div>
                  <div>
                    <div className="inspector-bar-head">
                      <span className="t-mono">Freshness</span>
                      <span className="t-mono inspector-bar-value">{selected.freshness || "NOT REPORTED"}</span>
                    </div>
                    <p className="t-mono text-muted inspector-unknown-none">Freshness is shown as the recorded timestamp, not a fabricated percentage.</p>
                  </div>
                </div>
              </div>

              <div className="inspector-section">
                <h4 className="t-label text-muted inspector-section-title">Known unknowns</h4>
                {selected.unknowns.length === 0 ? (
                  <p className="t-mono text-muted inspector-unknown-none">None recorded for this node.</p>
                ) : (
                  <ul className="inspector-unknowns">
                    {selected.unknowns.map((u) => (
                      <li key={u}>
                        <HelpCircle size={13} className="text-muted" aria-hidden="true" />
                        <span className="t-mono">{u}</span>
                      </li>
                    ))}
                  </ul>
                )}
              </div>

              <div className="inspector-actions">
                <button type="button" className="btn btn-primary inspector-action" onClick={() => setDialogOpen(true)} disabled={requested || sourceMode !== "SIMULATED"} title={sourceMode === "SIMULATED" ? undefined : `Memory re-evaluation is unavailable in ${sourceModeLabel(sourceMode)} source mode`}>
                  <RefreshCcw size={14} />
                  {requested ? "Re-evaluation requested" : "Request Re-evaluation"}
                </button>
                <p className="t-mono text-muted inspector-note">
                  <ShieldQuestion size={12} aria-hidden="true" /> Requests never fabricate epistemic state; the runtime
                  decides from evidence.
                </p>
              </div>
            </>
          ) : null}
        </div>
      </aside>

      <Dialog
        open={dialogOpen && selected !== null}
        title={sourceMode === "SIMULATED" ? "Re-evaluation Request - Simulated" : `Re-evaluation Request - ${sourceModeLabel(sourceMode)}`}
        onClose={() => {
          setDialogOpen(false);
          if (requested) setRequested(false);
        }}
        footer={
          requested ? (
            <span className="t-mono text-muted">RE-EVALUATION QUEUED LOCALLY - OUTCOME DEPENDS ON RUNTIME EVIDENCE</span>
          ) : (
            <>
              <button type="button" className="btn" onClick={() => setDialogOpen(false)}>
                Cancel
              </button>
              <button
                type="button"
                className="btn btn-primary"
                onClick={() => {
                  setRequested(true);
                  setDialogOpen(false);
                }}
              >
                Request re-evaluation
              </button>
            </>
          )
        }
      >
        <div className="state-block">
          <RefreshCcw size={22} aria-hidden="true" />
          <span className="t-label">{sourceMode === "SIMULATED" ? "Simulated re-evaluation request" : `Re-evaluation request (${sourceModeLabel(sourceMode)})`}</span>
          <p className="prose">
            {sourceMode === "SIMULATED" ? `The inspector requests a fresh evaluation of ${selected?.label}. This candidate records the request locally; a future runtime adapter will submit it to the Hybrid Memory service.` : `A fresh evaluation of ${selected?.label} is not available from the ${sourceModeLabel(sourceMode)} source.`}
          </p>
        </div>
      </Dialog>
    </div>
  );
}

export function Memory() {
  const { client, version } = useCommandCenter();
  const graphQuery = useRouteQuery(
    () => client.getMemoryGraph(),
    [client, version],
  );

  return (
    <RouteQueryGate
      query={graphQuery}
      loadingLabel="Loading knowledge graph"
      errorLabel="Knowledge graph unavailable"
      surface="Knowledge graph"
      shellClass="shell-content"
    >
      {(graph) => <MemoryContent graph={graph} />}
    </RouteQueryGate>
  );
}
