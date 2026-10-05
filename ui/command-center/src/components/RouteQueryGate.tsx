import type { ReactNode } from "react"
import { useCommandCenter } from "../context/CommandCenterContext"
import { routeFetchErrorDetail, shouldShowRouteFetchError } from "../lib/routeResilience"
import type { RouteQueryResult } from "../hooks/useRouteQuery"
import { ErrorState, LoadingState } from "./states"

type RouteQueryGateProps<T> = {
  query: RouteQueryResult<T>
  loadingLabel: string
  errorLabel: string
  surface: string
  shellClass?: string
  children: (data: T) => ReactNode
}

export const RouteQueryGate = <T,>({
  query,
  loadingLabel,
  errorLabel,
  surface,
  shellClass = "shell-content",
  children,
}: RouteQueryGateProps<T>) => {
  const { sourceMode, connection } = useCommandCenter()

  if (query.status === "loading") {
    return (
      <div className={shellClass}>
        <LoadingState label={loadingLabel} />
      </div>
    )
  }

  const hasData = query.data !== null
  if (
    !hasData && (query.status === "error" ||
    shouldShowRouteFetchError(sourceMode, query.error, hasData))
  ) {
    return (
      <div className={shellClass}>
        <ErrorState
          label={errorLabel}
          detail={routeFetchErrorDetail(sourceMode, connection, query.error, surface)}
          onRetry={query.reload}
        />
      </div>
    )
  }

  if (!hasData) {
    return (
      <div className={shellClass}>
        <ErrorState
          label={errorLabel}
          detail={routeFetchErrorDetail(sourceMode, connection, query.error, surface)}
          onRetry={query.reload}
        />
      </div>
    )
  }

  return <>
    {(query.error || connection.stale) && <div className={shellClass} role="status">
      <p>Showing last known state. {query.error ?? connection.error ?? "Live updates are reconnecting."}</p>
      <button type="button" onClick={query.reload}>Retry</button>
    </div>}
    {children(query.data as T)}
  </>
}
