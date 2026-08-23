import { describe, expect, it } from "vitest";
import { metaLabelToRef, refsFromTimelineEvent } from "../entityRefs";
import type { TimelineEvent } from "../contracts";

describe("refsFromTimelineEvent", () => {
  it("collects every populated cross-link field", () => {
    const event: TimelineEvent = {
      id: "TL-0905",
      at: "2026-08-16T06:42:00Z",
      group: "NOW",
      category: "INCIDENT",
      title: "worker.crash",
      detail: "Crash",
      meta: [],
      actions: [{ label: "View record", kind: "view" }],
      missionId: "M-CRS-008",
      agentId: "WRK-OPENHANDS",
      systemId: "SYS-OPENHANDS-WORKER",
      evidenceId: "EV-1142-002",
      verdictId: "VF-2042",
      memoryId: "n-reconcile",
    };
    const refs = refsFromTimelineEvent(event);
    expect(refs.map((r) => `${r.kind}:${r.id}`)).toEqual([
      "mission:M-CRS-008",
      "agent:WRK-OPENHANDS",
      "system:SYS-OPENHANDS-WORKER",
      "evidence:EV-1142-002",
      "verdict:VF-2042",
      "memory:n-reconcile",
    ]);
  });
});

describe("metaLabelToRef", () => {
  it("maps repair runs to their worker when known", () => {
    expect(metaLabelToRef("REPAIR", "A-91")).toEqual({
      kind: "agent",
      id: "WRK-OPENHANDS",
      label: "A-91 · WRK-OPENHANDS",
    });
  });

  it("returns null for unknown repair runs", () => {
    expect(metaLabelToRef("REPAIR", "A-999")).toBeNull();
  });

  it("maps mission and verdict labels", () => {
    expect(metaLabelToRef("MISSION", "M-HYB-014")?.kind).toBe("mission");
    expect(metaLabelToRef("VERDICT", "VF-2041")?.kind).toBe("verdict");
  });
});
