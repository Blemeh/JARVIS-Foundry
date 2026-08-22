import type { RouteQueryResult } from "../hooks/useRouteQuery"
import type { ConnectionState } from "./contracts"
import { sourceModeLabel, type ResolvedSourceMode } from "./sourceMode"

export function routeFetchErrorDetail(
  sourceMode: ResolvedSourceMode,
  connection: ConnectionState,
  loadError: string | null | undefined,
  surface: string,
): string {
  if (connection.error) return connection.error
  if (loadError) return loadError
  if (sourceMode === "DISCONNECTED" || sourceMode === "UNKNOWN") {
    return `${surface} unavailable: runtime source is ${sourceModeLabel(sourceMode)}; no fixture fallback is shown.`
  }
  return `${surface} request failed without detail.`
}

export function shouldShowRouteFetchError(
  sourceMode: ResolvedSourceMode,
  loadError: string | null | undefined,
  hasData: boolean,
): boolean {
  if (loadError) return true
  if (!hasData && (sourceMode === "DISCONNECTED" || sourceMode === "UNKNOWN")) return true
  return false
}

export function formatArchivedMissionLabel(count: number | null): string {
  if (count === null) return "Archived (NOT REPORTED)"
  return `Archived (${count})`
}

export function isDetailNotFound<T>(query: RouteQueryResult<T | null>): boolean {
  return query.status === "ready" && query.data === null
}
