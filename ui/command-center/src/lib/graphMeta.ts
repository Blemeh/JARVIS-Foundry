export const categoryColors: Record<string, string> = {
  MISSION: "#22d3ee",
  EVIDENCE: "#2dd4bf",
  BELIEF: "#94a3b8",
  DECISION: "#60a5fa",
  PROCEDURE: "#d97706",
  OBSERVATION: "#f472b6",
  RESOURCE: "#a78bfa",
};

export const categoryLegend: { key: string; label: string; note: string }[] = [
  { key: "MISSION", label: "Mission", note: "Active operational objective" },
  { key: "EVIDENCE", label: "Evidence", note: "Fingerprinted, provable record" },
  { key: "BELIEF", label: "Belief", note: "Durable institutional stance" },
  { key: "DECISION", label: "Decision", note: "Committed course of action" },
  { key: "PROCEDURE", label: "Procedure", note: "Repeatable policy or gate" },
  { key: "OBSERVATION", label: "Observation", note: "Measured system fact" },
  { key: "RESOURCE", label: "Resource", note: "Service or worker capability" },
];
