/** Sentinel for numeric fields that must not be rendered as fabricated values. */
export const NOT_REPORTED_NUMERIC = -1

export type MeasuredCostSource = "MEASURED" | "UNKNOWN" | "NOT_REPORTED"

export type MeasuredCostView = {
  display: string
  source: MeasuredCostSource
  unknownMeasurements: number | null
}

const isUnknownToken = (value: unknown): boolean => {
  if (value === null || value === undefined) return true
  if (typeof value === "string") {
    const normalized = value.trim().toUpperCase()
    return normalized === "" || normalized === "UNKNOWN" || normalized === "NOT_REPORTED" || normalized === "NOT_AVAILABLE"
  }
  return false
}

export { isUnknownToken }

export const formatUsd = (amount: number): string => `$${amount.toFixed(2)}`

export const formatMeasuredCost = (costs: Record<string, unknown> | undefined): MeasuredCostView => {
  if (!costs || Object.keys(costs).length === 0) {
    return { display: "UNKNOWN", source: "NOT_REPORTED", unknownMeasurements: null }
  }

  const unknownMeasurements =
    typeof costs.unknown_measurements === "number" ? costs.unknown_measurements : null
  const total = costs.measured_actual_total_usd

  if (isUnknownToken(total)) {
    return { display: "UNKNOWN", source: "UNKNOWN", unknownMeasurements }
  }

  if (typeof total === "number" && Number.isFinite(total)) {
    return {
      display: formatUsd(total),
      source: "MEASURED",
      unknownMeasurements,
    }
  }

  return { display: "UNKNOWN", source: "NOT_REPORTED", unknownMeasurements }
}

export const formatResourceMetric = (value: number, unit: string): string => {
  if (value === NOT_REPORTED_NUMERIC) return "NOT REPORTED"
  if (!Number.isFinite(value)) return "UNKNOWN"
  return `${value}${unit}`
}

export const formatCoveragePercent = (coverage: number): string => {
  if (coverage === NOT_REPORTED_NUMERIC) return "NOT REPORTED"
  if (!Number.isFinite(coverage)) return "UNKNOWN"
  return `${coverage}%`
}

export const MEASURED_COST_LABEL = "Measured cost"

export const findMeasuredCostResource = (
  resourceUsage: { label: string; value: number; unit: string }[],
): MeasuredCostView | null => {
  const row = resourceUsage.find((entry) => entry.label === MEASURED_COST_LABEL)
  if (!row) return null
  if (row.value === NOT_REPORTED_NUMERIC) {
    return { display: "UNKNOWN", source: "UNKNOWN", unknownMeasurements: null }
  }
  if (row.value === 0 && row.unit.trim() === "USD") {
    return { display: formatUsd(0), source: "MEASURED", unknownMeasurements: null }
  }
  if (row.value > 0 && row.unit.trim() === "USD") {
    return { display: formatUsd(row.value), source: "MEASURED", unknownMeasurements: null }
  }
  return { display: "UNKNOWN", source: "NOT_REPORTED", unknownMeasurements: null }
}

export const isStaleTimestamp = (iso: string | undefined, generatedAt: string, staleAfterMs = 5 * 60 * 1000): boolean => {
  if (!iso) return true
  const observed = new Date(iso).getTime()
  const generated = new Date(generatedAt).getTime()
  if (!Number.isFinite(observed) || !Number.isFinite(generated)) return true
  return generated - observed > staleAfterMs
}

export const formatMeasuredCostDisplay = (
  view: MeasuredCostView,
  sourceMode: "LIVE" | "SIMULATED" | "DISCONNECTED" | "DEGRADED" | "UNKNOWN",
): string => {
  if (sourceMode === "SIMULATED") {
    if (view.source === "MEASURED") return `${view.display} · SIMULATED`
    return "UNKNOWN · SIMULATED"
  }
  return view.display
}

export const trustFactorNumericDetail = (detail: string): number | null => {
  const match = detail.match(/^(\d+)\b/)
  if (!match) return null
  const parsed = Number(match[1])
  return Number.isFinite(parsed) ? parsed : null
}

export const formatFreshnessLabel = (iso: string | undefined, generatedAt: string): "CURRENT" | "STALE" | "UNKNOWN" => {
  if (!iso) return "UNKNOWN"
  return isStaleTimestamp(iso, generatedAt) ? "STALE" : "CURRENT"
}
