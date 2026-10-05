import { describe, expect, it } from "vitest";
import { summarizeListText } from "../format";

describe("summarizeListText", () => {
  it("replaces raw JSON envelopes with a human fallback", () => {
    const raw = `{"control_request_id": "command-center-repair-live-acceptance-restart-20261005", "creation_identity": {"exe": "C:\\\\Python313\\\\python.exe", "pid": 1848}}`;
    expect(summarizeListText(raw, "Observed signal — open for detail")).toBe("Observed signal — open for detail");
  });

  it("collapses long raw prompts to one short line", () => {
    const long = `Fix failing CI on blemesh/jarvis-foundry: workflow 'github/workflows/windows-ci.yml' concluded failure at 2026-08-12; changes currently land without CI verification. `.repeat(4);
    const out = summarizeListText(long, "fallback");
    expect(out.length).toBeLessThanOrEqual(160);
    expect(out).toMatch(/…$/);
    expect(out).not.toContain("\n");
  });

  it("passes short human text through untouched", () => {
    expect(summarizeListText("Hybrid Worker Activation", "fallback")).toBe("Hybrid Worker Activation");
  });
});
