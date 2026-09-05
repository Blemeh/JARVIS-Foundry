import { lazy, Suspense, type ReactNode } from "react";
import { Route, Routes } from "react-router-dom";
import { AppShell } from "./components/AppShell";
import { LoadingState } from "./components/states";
import { ErrorState } from "./components/states";
import { ScenarioBootstrap } from "./ScenarioBootstrap";

const Overview = lazy(() => import("./routes/Overview").then((m) => ({ default: m.Overview })));
const Missions = lazy(() => import("./routes/Missions").then((m) => ({ default: m.Missions })));
const MissionDetail = lazy(() => import("./routes/MissionDetail").then((m) => ({ default: m.MissionDetail })));
const Agents = lazy(() => import("./routes/Agents").then((m) => ({ default: m.Agents })));
const AgentDetail = lazy(() => import("./routes/AgentDetail").then((m) => ({ default: m.AgentDetail })));
const Memory = lazy(() => import("./routes/Memory").then((m) => ({ default: m.Memory })));
const Evolution = lazy(() => import("./routes/Evolution").then((m) => ({ default: m.Evolution })));
const Autonomy = lazy(() => import("./routes/Autonomy").then((m) => ({ default: m.Autonomy })));
const Discovery = lazy(() => import("./routes/Discovery").then((m) => ({ default: m.Discovery })));
const Evidence = lazy(() => import("./routes/Evidence").then((m) => ({ default: m.Evidence })));
const EvidenceDetail = lazy(() => import("./routes/EvidenceDetail").then((m) => ({ default: m.EvidenceDetail })));
const Timeline = lazy(() => import("./routes/Timeline").then((m) => ({ default: m.Timeline })));
const Systems = lazy(() => import("./routes/Systems").then((m) => ({ default: m.Systems })));
const SystemDetail = lazy(() => import("./routes/SystemDetail").then((m) => ({ default: m.SystemDetail })));
const Notifications = lazy(() => import("./routes/Notifications").then((m) => ({ default: m.Notifications })));
const OwnerManagement = lazy(() => import("./routes/OwnerManagement").then((m) => ({ default: m.OwnerManagement })));
const OperationsHub = lazy(() => import("./routes/OperationsHub").then((m) => ({ default: m.OperationsHub })));
const Work = lazy(() => import("./routes/Work").then((m) => ({ default: m.Work })));
const World = lazy(() => import("./routes/World").then((m) => ({ default: m.World })));
const Capabilities = lazy(() => import("./routes/Capabilities").then((m) => ({ default: m.Capabilities })));
const You = lazy(() => import("./routes/You").then((m) => ({ default: m.You })));
const Settings = lazy(() => import("./routes/Settings").then((m) => ({ default: m.Settings })));
const ForensicRecordDetail = lazy(() => import("./routes/ForensicRecordDetail").then((m) => ({ default: m.ForensicRecordDetail })));

function RouteSuspense({ children }: { children: ReactNode }) {
  return (
    <Suspense
      fallback={
        <div className="shell-content">
          <LoadingState label="Loading section" />
        </div>
      }
    >
      {children}
    </Suspense>
  );
}

export function App() {
  return (
    <>
      <ScenarioBootstrap />
      <Routes>
        <Route element={<AppShell />}>
          <Route path="/" element={<RouteSuspense><Overview /></RouteSuspense>} />
          <Route path="/work" element={<RouteSuspense><Work /></RouteSuspense>} />
          <Route path="/world" element={<RouteSuspense><World /></RouteSuspense>} />
          <Route path="/capabilities" element={<RouteSuspense><Capabilities /></RouteSuspense>} />
          <Route path="/you" element={<RouteSuspense><You /></RouteSuspense>} />
          <Route path="/activity" element={<RouteSuspense><Timeline /></RouteSuspense>} />
          <Route path="/settings" element={<RouteSuspense><Settings /></RouteSuspense>} />
          <Route path="/operations" element={<RouteSuspense><OperationsHub /></RouteSuspense>} />
          <Route path="/missions" element={<RouteSuspense><Missions /></RouteSuspense>} />
          <Route path="/missions/:missionId" element={<RouteSuspense><MissionDetail /></RouteSuspense>} />
          <Route path="/agents" element={<RouteSuspense><Agents /></RouteSuspense>} />
          <Route path="/agents/:agentId" element={<RouteSuspense><AgentDetail /></RouteSuspense>} />
          <Route path="/memory" element={<RouteSuspense><Memory /></RouteSuspense>} />
          <Route path="/memory/:memoryId" element={<RouteSuspense><Memory /></RouteSuspense>} />
          <Route path="/evolution" element={<RouteSuspense><Evolution /></RouteSuspense>} />
          <Route path="/executive" element={<RouteSuspense><Autonomy /></RouteSuspense>} />
          <Route path="/discovery" element={<RouteSuspense><Discovery /></RouteSuspense>} />
          <Route path="/evidence" element={<RouteSuspense><Evidence /></RouteSuspense>} />
          <Route path="/evidence/:evidenceId" element={<RouteSuspense><EvidenceDetail /></RouteSuspense>} />
          <Route path="/timeline" element={<RouteSuspense><Timeline /></RouteSuspense>} />
          <Route path="/systems" element={<RouteSuspense><Systems /></RouteSuspense>} />
          <Route path="/systems/:systemId" element={<RouteSuspense><SystemDetail /></RouteSuspense>} />
          <Route path="/notifications" element={<RouteSuspense><Notifications /></RouteSuspense>} />
          <Route path="/owner" element={<RouteSuspense><OwnerManagement /></RouteSuspense>} />
          <Route path="/records/:recordKind/:recordId" element={<RouteSuspense><ForensicRecordDetail /></RouteSuspense>} />
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
