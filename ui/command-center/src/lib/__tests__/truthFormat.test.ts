import { describe, expect, it } from "vitest"
import {
  findMeasuredCostResource,
  formatCoveragePercent,
  formatFreshnessLabel,
  formatMeasuredCost,
  formatMeasuredCostDisplay,
  formatResourceMetric,
  MEASURED_COST_LABEL,
  NOT_REPORTED_NUMERIC,
} from "../truthFormat"

describe("truthFormat", () => {
  it("does not render currency when cost is absent or UNKNOWN", () => {
    expect(formatMeasuredCost({})).toEqual({
      display: "UNKNOWN",
      source: "NOT_REPORTED",
      unknownMeasurements: null,
    })
    expect(formatMeasuredCost({ measured_actual_total_usd: "UNKNOWN", unknown_measurements: 1 })).toEqual({
      display: "UNKNOWN",
      source: "UNKNOWN",
      unknownMeasurements: 1,
    })
    expect(formatMeasuredCost({ measured_actual_total_usd: "UNKNOWN" }).display).not.toMatch(/\$/)
  })

  it("renders $0.00 only for authoritative zero totals", () => {
    expect(formatMeasuredCost({ measured_actual_total_usd: 0, unknown_measurements: 0 })).toEqual({
      display: "$0.00",
      source: "MEASURED",
      unknownMeasurements: 0,
    })
    expect(formatMeasuredCost({ measured_actual_total_usd: 0.38, unknown_measurements: 0 })).toEqual({
      display: "$0.38",
      source: "MEASURED",
      unknownMeasurements: 0,
    })
  })

  it("labels absent resource metrics as NOT REPORTED", () => {
    expect(formatResourceMetric(NOT_REPORTED_NUMERIC, "%")).toBe("NOT REPORTED")
    expect(formatCoveragePercent(NOT_REPORTED_NUMERIC)).toBe("NOT REPORTED")
  })

  it("marks stale timestamps as STALE", () => {
    const generatedAt = "2026-08-20T12:00:00Z"
    const staleAt = "2026-08-20T11:00:00Z"
    expect(formatFreshnessLabel(staleAt, generatedAt)).toBe("STALE")
    expect(formatFreshnessLabel(generatedAt, generatedAt)).toBe("CURRENT")
    expect(formatFreshnessLabel(undefined, generatedAt)).toBe("UNKNOWN")
  })

  it("labels simulated measured cost explicitly", () => {
    expect(formatMeasuredCostDisplay({ display: "UNKNOWN", source: "UNKNOWN", unknownMeasurements: 1 }, "SIMULATED")).toBe("UNKNOWN · SIMULATED")
    expect(formatMeasuredCostDisplay({ display: "$0.00", source: "MEASURED", unknownMeasurements: 0 }, "SIMULATED")).toBe("$0.00 · SIMULATED")
  })

  it("renders negative measured cost rows as currency", () => {
    const view = findMeasuredCostResource([
      { label: MEASURED_COST_LABEL, value: -0.42, unit: "USD" },
    ])
    expect(view).toEqual({ display: "$-0.42", source: "MEASURED", unknownMeasurements: null })
  })

  it("treats NOT_REPORTED unit as unknown cost", () => {
    const view = findMeasuredCostResource([
      { label: MEASURED_COST_LABEL, value: NOT_REPORTED_NUMERIC, unit: "NOT_REPORTED" },
    ])
    expect(view?.source).toBe("UNKNOWN")
    expect(view?.display).toBe("UNKNOWN")
  })
})
