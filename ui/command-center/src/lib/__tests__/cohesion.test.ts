import { describe, expect, it } from "vitest";
import { MockJarvisClient } from "../mockClient";
import { buildUniverse, relatedFor, searchUniverse } from "../cohesion";
import { validateUniverseIntegrity } from "../integrity";

function client() {
  return new MockJarvisClient();
}

describe("mock universe integrity", () => {
  it("baseline universe has no broken cross-references", () => {
    expect(validateUniverseIntegrity(buildUniverse())).toEqual([]);
  });

  it("stays coherent after the verification failure scenario", async () => {
    const c = client();
    await c.runScenario("VERIFICATION_FAILURE");
    expect(c.integrityIssues()).toEqual([]);
  });

  it("stays coherent after the worker crash and recovery scenarios", async () => {
    const c = client();
    await c.runScenario("WORKER_CRASH");
    expect(c.integrityIssues()).toEqual([]);
    await c.runScenario("RECOVERY");
    expect(c.integrityIssues()).toEqual([]);
  });

  it("stays coherent across repeated verification fail->repair->recover->pass cycles", async () => {
    const c = client();
    for (let i = 0; i < 2; i++) {
      await c.simulateEvent("verification.failed");
      await c.simulateEvent("repair.created");
      await c.simulateEvent("worker.recovered");
      await c.simulateEvent("verification.passed");
    }
    expect(c.integrityIssues()).toEqual([]);

    const runs = await c.getAgentRuns("WRK-OPENHANDS");
    expect(runs.filter((r) => r.id === "A-104" && r.missionId === "M-VER-031")).toHaveLength(1);

    const notifications = await c.listNotifications();
    expect(notifications.filter((n) => n.category === "VERIFICATION_FAILED").every((n) => n.state === "RESOLVED")).toBe(true);

    const evidence = await c.listEvidence();
    expect(evidence.filter((e) => e.verdict === "FAILED")).toHaveLength(0);

    const trust = await c.getTrustSummary();
    expect(trust.unresolvedFindings).toBe(2);
    expect(trust.gateStatus).toBe("PASS");
  });
});

describe("event consistency", () => {
  it("verification.failed changes mission, verdict, evidence, timeline, and notifications together", async () => {
    const c = client();
    await c.simulateEvent("verification.failed");

    const mission = await c.getMission("M-VER-031");
    expect(mission?.status).toBe("INTERVENTION");
    expect(mission?.nextAction).toContain("VF-2047");

    const run = await c.getMissionRun("M-VER-031");
    expect(run.verdicts.find((v) => v.id === "VF-2047")?.state).toBe("FAILED");

    const evidence = await c.listEvidence();
    const failed = evidence.find((e) => e.verdict === "FAILED" && e.missionRef === "M-VER-031");
    expect(failed).toBeTruthy();
    expect(failed?.verdictId).toBe("VF-2047");

    const timeline = await c.listTimeline();
    expect(timeline.some((e) => e.title === "verification.failed" && e.missionId === "M-VER-031")).toBe(true);

    const notifications = await c.listNotifications();
    expect(notifications.some((n) => n.category === "VERIFICATION_FAILED" && n.state === "UNREAD")).toBe(true);
  });

  it("verification.passed reconciles the failed surfaces", async () => {
    const c = client();
    await c.simulateEvent("verification.failed");
    await c.simulateEvent("repair.created");
    await c.simulateEvent("worker.recovered");
    await c.simulateEvent("verification.passed");

    const mission = await c.getMission("M-VER-031");
    expect(mission?.completion).toBe(100);
    const run = await c.getMissionRun("M-VER-031");
    expect(run.verdicts.find((v) => v.id === "VF-2047")?.state).toBe("PASSED");
    const notifications = await c.listNotifications();
    const failed = notifications.find((n) => n.category === "VERIFICATION_FAILED");
    expect(failed?.state).toBe("RESOLVED");
    const trust = await c.getTrustSummary();
    expect(trust.gateStatus).toBe("PASS");
    const timeline = await c.listTimeline();
    expect(timeline.some((e) => e.title === "verification.passed")).toBe(true);
  });
});

describe("scenarios", () => {
  it("worker crash propagates to worker, system, mission, timeline, and notifications", async () => {
    const c = client();
    await c.runScenario("WORKER_CRASH");

    const worker = await c.getAgent("WRK-OPENHANDS");
    expect(worker?.status).toBe("FAILED");
    const system = await c.getSystem("SYS-OPENHANDS-WORKER");
    expect(system?.health).toBe("DEGRADED");
    const timeline = await c.listTimeline();
    expect(timeline.some((e) => e.title === "worker.crash" && e.systemId === "SYS-OPENHANDS-WORKER")).toBe(true);
    const notifications = await c.listNotifications();
    expect(notifications.some((n) => n.category === "WORKER_CRASH")).toBe(true);
  });

  it("recovery reconciles the crashed surfaces", async () => {
    const c = client();
    await c.runScenario("RECOVERY");
    const worker = await c.getAgent("WRK-OPENHANDS");
    expect(worker?.status).toBe("ACTIVE");
    const system = await c.getSystem("SYS-OPENHANDS-WORKER");
    expect(system?.health).toBe("HEALTHY");
    const timeline = await c.listTimeline();
    expect(timeline.some((e) => e.title === "worker.recovered")).toBe(true);
  });

  it("STOP propagates and resume restores the healthy mock state", async () => {
    const c = client();
    await c.runScenario("STOP_ACTIVE");
    const stop = await c.getSystem("SYS-STOP");
    expect(stop?.state).toBe("ACTIVE");
    expect(stop?.recovery).toContain("SIMULATED STOP");
    const timeline = await c.listTimeline();
    expect(timeline.some((e) => e.title === "emergency_stop.requested")).toBe(true);
    expect(timeline.some((e) => e.title === "emergency_stop.frozen")).toBe(true);
    const notifications = await c.listNotifications();
    expect(notifications.some((n) => n.category === "STOP_ACTIVATED")).toBe(true);

    await c.resumeSimulation();
    const after = await c.getSystem("SYS-STOP");
    expect(after?.state).toBe("ARMED");
    const timelineAfter = await c.listTimeline();
    expect(timelineAfter.some((e) => e.title === "resume.reorientation")).toBe(true);
    const notificationsAfter = await c.listNotifications();
    expect(notificationsAfter.find((n) => n.category === "STOP_ACTIVATED")?.state).toBe("RESOLVED");
  });
});

describe("search", () => {
  it("finds missions, agents, evidence, and systems by id and intent terms", async () => {
    const c = client();
    const byMission = await c.search("M-HYB-014");
    expect(byMission.missions.some((m) => m.id === "M-HYB-014")).toBe(true);
    const byAgent = await c.search("WRK-VERIFIER");
    expect(byAgent.agents.some((a) => a.id === "WRK-VERIFIER")).toBe(true);
    const byEvidence = await c.search("EV-1131-007");
    expect(byEvidence.evidence.some((e) => e.id === "EV-1131-007")).toBe(true);
    const bySystem = await c.search("supervisor");
    expect(bySystem.systems.some((s) => s.id === "SYS-SUPERVISOR")).toBe(true);
    const byVerdict = await c.search("VF-2041");
    expect(byVerdict.missions.some((m) => m.id === "VF-2041")).toBe(true);
  });

  it("returns empty results for a nonsense query", async () => {
    const c = client();
    const results = await c.search("zzz-no-such-object");
    expect(results.missions).toHaveLength(0);
    expect(results.agents).toHaveLength(0);
    expect(results.evidence).toHaveLength(0);
    expect(results.systems).toHaveLength(0);
  });
});

describe("related entities", () => {
  it("resolves the full relationship set for a mission", () => {
    const u = buildUniverse();
    const related = relatedFor(u, "mission", "M-HYB-014");
    expect(related.agents.some((a) => a.id === "WRK-HERMES")).toBe(true);
    expect(related.evidence.some((e) => e.id === "EV-1142-003")).toBe(true);
    expect(related.systems.some((s) => s.id === "SYS-WORKERS")).toBe(true);
    expect(related.timeline.some((t) => t.id === "TL-0903")).toBe(true);
    expect(related.memory.some((m) => m.id === "n-mission")).toBe(true);
  });
});

describe("lineage", () => {
  it("shows the fail -> finding -> repair -> pass path for the ledger gate mission", async () => {
    const c = client();
    const lineage = await c.getLineage("M-GAT-022");
    expect(lineage.some((n) => n.kind === "finding" && n.status === "FAIL")).toBe(true);
    expect(lineage.some((n) => n.kind === "repair")).toBe(true);
    expect(lineage.some((n) => n.kind === "pass")).toBe(true);
  });

  it("shows a closed repair lineage for the crash recovery mission", async () => {
    const c = client();
    const lineage = await c.getLineage("M-CRS-008");
    expect(lineage.some((n) => n.id === "repair" && n.label.includes("A-91"))).toBe(true);
    expect(lineage.some((n) => n.kind === "pass" && n.status === "PASS")).toBe(true);
  });
});

describe("search universe helper", () => {
  it("matches intent terms like degraded and blocked", () => {
    const u = buildUniverse();
    const degraded = searchUniverse(u, "degraded");
    expect(degraded.systems.some((s) => s.id === "SYS-ROUTING" || s.id === "SYS-WORKERS")).toBe(true);
    const blocked = searchUniverse(u, "blocked");
    expect(blocked.missions.some((m) => m.id === "M-GAT-022")).toBe(true);
  });
});
