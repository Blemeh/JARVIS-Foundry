import { useEffect } from "react";
import { Route, Routes, useSearchParams } from "react-router-dom";
import type { Scenario } from "./lib/contracts";
import { AppShell } from "./components/AppShell";
import { Overview } from "./routes/Overview";
import { Missions } from "./routes/Missions";
import { MissionDetail } from "./routes/MissionDetail";
import { Agents } from "./routes/Agents";
import { AgentDetail } from "./routes/AgentDetail";
import { Memory } from "./routes/Memory";
import { Evidence } from "./routes/Evidence";
import { EvidenceDetail } from "./routes/EvidenceDetail";
import { Timeline } from "./routes/Timeline";
import { Systems } from "./routes/Systems";
import { SystemDetail } from "./routes/SystemDetail";
import { Notifications } from "./routes/Notifications";
import { ErrorState } from "./components/states";
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
function ScenarioBootstrap() {
  const [params] = useSearchParams();
  const { runScenario } = useCommandCenter();
  useEffect(() => {
    const s = params.get("scenario");
    if (s && SCENARIOS.has(s as Scenario)) runScenario(s as Scenario);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  return null;
}

export function App() {
  return (
    <>
      <ScenarioBootstrap />
      <Routes>
        <Route element={<AppShell />}>
          <Route path="/" element={<Overview />} />
          <Route path="/missions" element={<Missions />} />
          <Route path="/missions/:missionId" element={<MissionDetail />} />
          <Route path="/agents" element={<Agents />} />
          <Route path="/agents/:agentId" element={<AgentDetail />} />
          <Route path="/memory" element={<Memory />} />
          <Route path="/memory/:memoryId" element={<Memory />} />
          <Route path="/evidence" element={<Evidence />} />
          <Route path="/evidence/:evidenceId" element={<EvidenceDetail />} />
          <Route path="/timeline" element={<Timeline />} />
          <Route path="/systems" element={<Systems />} />
          <Route path="/systems/:systemId" element={<SystemDetail />} />
          <Route path="/notifications" element={<Notifications />} />
          <Route
            path="*"
            element={
              <div className="shell-content">
                <ErrorState label="Route not found" detail="This candidate has no section at this address." />
              </div>
            }
          />
        </Route>
      </Routes>
    </>
  );
}
