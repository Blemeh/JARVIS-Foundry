import type { MockUniverse } from "./cohesion";

/**
 * Cross-reference integrity check for the mock JARVIS universe.
 *
 * A visually correct UI backed by contradictory fixture data is not a PASS.
 * This checker walks every entity reference and reports broken links so tests
 * and future developers can keep the simulated universe coherent.
 */
export function validateUniverseIntegrity(u: MockUniverse): string[] {
  const issues: string[] = [];
  const missionIds = new Set(u.missions.map((m) => m.id));
  const workerIds = new Set(Object.keys(u.workers));
  const systemIds = new Set(Object.keys(u.systems));
  const evidenceIds = new Set(u.evidence.map((e) => e.id));
  const timelineIds = new Set(u.timeline.map((e) => e.id));
  const memoryIds = new Set(u.memoryGraph.nodes.map((n) => n.id));
  const verdictIds = new Set<string>();
  for (const run of Object.values(u.runs)) for (const v of run.verdicts) verdictIds.add(v.id);
  const notificationIds = new Set<string>();
  const agentRunIds = new Set<string>();

  for (const m of u.missions) {
    if (m.id !== m.code) issues.push(`mission ${m.id} code mismatch: ${m.code}`);
    const run = u.runs[m.id];
    if (!run) issues.push(`mission ${m.id} has no mission run`);
    if (u.steps[m.id]?.length === 0) issues.push(`mission ${m.id} has no steps`);
  }

  for (const e of u.evidence) {
    if (!missionIds.has(e.missionRef)) issues.push(`evidence ${e.id} references unknown mission ${e.missionRef}`);
    if (!/^[0-9a-f]{64}$/i.test(e.hash)) issues.push(`evidence ${e.id} hash is not 64 hex chars`);
    if (e.workerId && !workerIds.has(e.workerId)) issues.push(`evidence ${e.id} references unknown worker ${e.workerId}`);
    if (e.verdictId && !verdictIds.has(e.verdictId)) issues.push(`evidence ${e.id} references unknown verdict ${e.verdictId}`);
    if (e.stepId && !u.steps[e.missionRef]?.some((s) => s.id === e.stepId)) {
      issues.push(`evidence ${e.id} references unknown step ${e.stepId} on mission ${e.missionRef}`);
    }
    if (e.relatedEventId && !timelineIds.has(e.relatedEventId)) {
      issues.push(`evidence ${e.id} references unknown timeline event ${e.relatedEventId}`);
    }
  }

  for (const event of u.timeline) {
    if (event.missionId && !missionIds.has(event.missionId)) issues.push(`timeline ${event.id} references unknown mission ${event.missionId}`);
    if (event.agentId && !workerIds.has(event.agentId)) issues.push(`timeline ${event.id} references unknown agent ${event.agentId}`);
    if (event.systemId && !systemIds.has(event.systemId)) issues.push(`timeline ${event.id} references unknown system ${event.systemId}`);
    if (event.evidenceId && !evidenceIds.has(event.evidenceId)) issues.push(`timeline ${event.id} references unknown evidence ${event.evidenceId}`);
    if (event.verdictId && !verdictIds.has(event.verdictId)) issues.push(`timeline ${event.id} references unknown verdict ${event.verdictId}`);
    if (event.memoryId && !memoryIds.has(event.memoryId)) issues.push(`timeline ${event.id} references unknown memory node ${event.memoryId}`);
  }

  for (const n of u.notifications) {
    if (notificationIds.has(n.id)) issues.push(`duplicate notification id ${n.id}`);
    notificationIds.add(n.id);
    for (const r of n.refs) {
      if (!refTargetExists(r.kind, r.id, { missionIds, workerIds, systemIds, evidenceIds, timelineIds, memoryIds, verdictIds })) {
        issues.push(`notification ${n.id} references unknown ${r.kind} ${r.id}`);
      }
    }
  }

  for (const run of u.agentRuns) {
    if (agentRunIds.has(run.id)) issues.push(`duplicate agent run id ${run.id}`);
    agentRunIds.add(run.id);
    if (!missionIds.has(run.missionId)) issues.push(`agent run ${run.id} references unknown mission ${run.missionId}`);
    if (!workerIds.has(run.workerId)) issues.push(`agent run ${run.id} references unknown worker ${run.workerId}`);
    for (const eid of run.evidenceIds) if (!evidenceIds.has(eid)) issues.push(`agent run ${run.id} references unknown evidence ${eid}`);
    if (run.verdictId && !verdictIds.has(run.verdictId)) issues.push(`agent run ${run.id} references unknown verdict ${run.verdictId}`);
  }

  for (const [systemId, sys] of Object.entries(u.systems)) {
    for (const dep of sys.dependencies) if (!systemIds.has(dep)) issues.push(`system ${systemId} references unknown dependency ${dep}`);
    for (const mid of sys.missionIds ?? []) if (!missionIds.has(mid)) issues.push(`system ${systemId} references unknown mission ${mid}`);
    for (const tid of sys.incidentEventIds ?? []) if (!timelineIds.has(tid)) issues.push(`system ${systemId} references unknown timeline event ${tid}`);
  }

  for (const w of Object.values(u.workers)) {
    for (const mid of w.missionIds ?? []) if (!missionIds.has(mid)) issues.push(`worker ${w.id} references unknown mission ${mid}`);
    if (w.systemId && !systemIds.has(w.systemId)) issues.push(`worker ${w.id} references unknown system ${w.systemId}`);
    for (const eid of w.evidenceIds ?? []) if (!evidenceIds.has(eid)) issues.push(`worker ${w.id} references unknown evidence ${eid}`);
  }

  for (const node of u.memoryGraph.nodes) {
    if (node.ref && !refTargetExists(node.ref.kind, node.ref.id, { missionIds, workerIds, systemIds, evidenceIds, timelineIds, memoryIds, verdictIds })) {
      issues.push(`memory node ${node.id} references unknown ${node.ref.kind} ${node.ref.id}`);
    }
  }

  for (const edge of u.memoryGraph.edges) {
    if (!memoryIds.has(edge.from)) issues.push(`memory edge ${edge.from}->${edge.to} references unknown node ${edge.from}`);
    if (!memoryIds.has(edge.to)) issues.push(`memory edge ${edge.from}->${edge.to} references unknown node ${edge.to}`);
  }

  return issues;
}

interface IdSets {
  missionIds: Set<string>;
  workerIds: Set<string>;
  systemIds: Set<string>;
  evidenceIds: Set<string>;
  timelineIds: Set<string>;
  memoryIds: Set<string>;
  verdictIds: Set<string>;
}

function refTargetExists(kind: string, id: string, sets: IdSets): boolean {
  switch (kind) {
    case "mission":
      return sets.missionIds.has(id);
    case "agent":
      return sets.workerIds.has(id);
    case "evidence":
      return sets.evidenceIds.has(id);
    case "system":
      return sets.systemIds.has(id);
    case "memory":
      return sets.memoryIds.has(id);
    case "timeline":
      return sets.timelineIds.has(id);
    case "verdict":
      return sets.verdictIds.has(id);
    case "procedure":
      return id === "GATE-V2" || sets.memoryIds.has(id);
    default:
      return false;
  }
}
