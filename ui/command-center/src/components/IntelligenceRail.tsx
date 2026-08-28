import { Link } from "react-router-dom";
import type { Mission, OwnerAttentionRecord } from "../lib/contracts";
import type { FoundryExecutive } from "../lib/snapshotTypes";

type IntelligenceRailProps = {
  missions: Mission[];
  executive: FoundryExecutive | null;
  ownerAttention: OwnerAttentionRecord[];
};

function deriveIntent(missions: Mission[], executive: FoundryExecutive | null): string {
  const active = missions.find((m) => m.status === "ACTIVE" || m.status === "IN_PROGRESS");
  if (active?.objective) return active.objective;
  if (active?.title) return active.title;
  const goals = executive?.latest_decision?.active_goals;
  if (goals?.length) return goals[0];
  const reason = executive?.latest_decision?.reason;
  if (reason) return reason;
  return "UNKNOWN";
}

function derivePhase(missions: Mission[], executive: FoundryExecutive | null): string {
  const active = missions.find((m) => m.status === "ACTIVE" || m.status === "IN_PROGRESS");
  if (active?.stage) return active.stage.toUpperCase();
  const decision = executive?.latest_decision?.decision;
  if (decision) return decision.toUpperCase();
  return "UNKNOWN";
}

function deriveTarget(missions: Mission[]): string {
  const active = missions.find((m) => m.status === "ACTIVE" || m.status === "IN_PROGRESS");
  if (active?.code) return active.code;
  if (active?.title) return active.title;
  return "UNKNOWN";
}

function deriveNextAction(missions: Mission[], executive: FoundryExecutive | null): string {
  const active = missions.find((m) => m.status === "ACTIVE" || m.status === "IN_PROGRESS");
  if (active?.nextAction) return active.nextAction;
  const wait = executive?.latest_decision?.wait?.reason;
  if (wait) return wait;
  return "UNKNOWN";
}

function deriveConfidence(executive: FoundryExecutive | null): string {
  const conf = executive?.latest_decision?.prediction?.prediction?.["confidence"];
  if (typeof conf === "number" && Number.isFinite(conf)) {
    return `${Math.round(conf * 100)}%`;
  }
  return "UNKNOWN";
}

export function IntelligenceRail({ missions, executive, ownerAttention }: IntelligenceRailProps) {
  const openAttention = ownerAttention.filter((a) => a.status === "OPEN");
  const intent = deriveIntent(missions, executive);
  const phase = derivePhase(missions, executive);
  const target = deriveTarget(missions);
  const nextAction = deriveNextAction(missions, executive);
  const confidence = deriveConfidence(executive);

  return (
    <aside className="intelligence-rail" aria-label="Current operational intelligence">
      <section className="intel-block">
        <h2 className="intel-heading">Current intent</h2>
        <p className="intel-value">{intent}</p>
      </section>

      <div className="intel-divider" aria-hidden="true" />

      <section className="intel-block">
        <h2 className="intel-heading">Current phase</h2>
        <p className="intel-value intel-value--phase">{phase}</p>
      </section>

      <div className="intel-divider" aria-hidden="true" />

      <section className="intel-block">
        <h2 className="intel-heading">Target</h2>
        <p className="intel-value">{target}</p>
      </section>

      <div className="intel-divider" aria-hidden="true" />

      <section className="intel-block">
        <h2 className="intel-heading">Next action</h2>
        <p className="intel-value">{nextAction}</p>
      </section>

      <div className="intel-divider" aria-hidden="true" />

      <section className="intel-block">
        <h2 className="intel-heading">Confidence</h2>
        <p className="intel-value intel-value--confidence">{confidence}</p>
      </section>

      <div className="intel-divider" aria-hidden="true" />

      <section className="intel-block intel-block--attention">
        <h2 className="intel-heading">Owner attention</h2>
        {openAttention.length === 0 ? (
          <p className="intel-value intel-value--nominal">None required</p>
        ) : (
          <ul className="intel-attention-list">
            {openAttention.slice(0, 3).map((item) => (
              <li key={item.queue_id}>
                <p className="intel-attention-summary">{item.summary ?? item.classification}</p>
                {item.entity_id ? (
                  <Link
                    to={
                      item.mission_id
                        ? `/missions/${item.mission_id}`
                        : item.entity_kind === "mission"
                          ? `/missions/${item.entity_id}`
                          : "/owner"
                    }
                    className="intel-attention-cta"
                  >
                    Inspect
                  </Link>
                ) : (
                  <Link to="/owner" className="intel-attention-cta">
                    Inspect
                  </Link>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>
    </aside>
  );
}
