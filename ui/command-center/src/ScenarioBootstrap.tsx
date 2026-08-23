import { useEffect } from "react";
import { useSearchParams } from "react-router-dom";
import type { Scenario } from "./lib/contracts";
import { useCommandCenter } from "./context/CommandCenterContext";

const SCENARIOS = new Set<Scenario>([
  "NORMAL_OPERATION",
  "VERIFICATION_FAILURE",
  "WORKER_CRASH",
  "PROVIDER_DEGRADED",
  "MISSION_BLOCKED",
  "MEMORY_CONFLICT",
  "STOP_ACTIVE",
  "RECOVERY",
  "ROLLBACK",
  "MISSION_COMPLETE",
  "EMPTY_STATE",
  "STREAM_DISCONNECTED",
]);

/** Development-only fixture bootstrap: ?scenario=WORKER_CRASH etc. */
export function ScenarioBootstrap() {
  const [params] = useSearchParams();
  const { runScenario } = useCommandCenter();
  useEffect(() => {
    const s = params.get("scenario");
    if (s && SCENARIOS.has(s as Scenario)) runScenario(s as Scenario);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  return null;
}
