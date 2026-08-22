import type { ReactNode } from "react"
import { useCommandCenter } from "../context/CommandCenterContext"
import type { RouteQueryResult } from "../hooks/useRouteQuery"
import { isDetailNotFound, routeFetchErrorDetail, shouldShowRouteFetchError } from "../lib/routeResilience"
import { ErrorState, LoadingState } from "./states"

type DetailRouteQueryGateProps<T> = {
  query: RouteQueryResult<T | null>
  loadingLabel: string
  notFoundLabel: string
  notFoundDetail: string
  errorLabel: string
  surface: string
  shellClass?: string
  children: (data: T) => ReactNode
}

export const DetailRouteQueryGate = <T,>({
  query,
  loadingLabel,
  notFoundLabel,
  notFoundDetail,
  errorLabel,
  surface,
  shellClass = "shell-content",
  children,
}: DetailRouteQueryGateProps<T>) => {
  const { sourceMode, connection } = useCommandCenter()

  if (query.status === "loading") {
    return (
      <div className={shellClass}>
        <LoadingState label={loadingLabel} />
      </div>
    )
  }

  if (isDetailNotFound(query)) {
    return (
      <div className={shellClass}>
        <ErrorState label={notFoundLabel} detail={notFoundDetail} />
      </div>
    )
  }

  if (
    query.status === "error" ||
    shouldShowRouteFetchError(sourceMode, query.error, query.data !== null)
  ) {
    return (
      <div className={shellClass}>
        <ErrorState
          label={errorLabel}
          detail={routeFetchErrorDetail(sourceMode, connection, query.error, surface)}
        />
      </div>
    )
  }

  if (query.data === null) {
    return (
      <div className={shellClass}>
        <ErrorState label={notFoundLabel} detail={notFoundDetail} />
      </div>
    )
  }

  return <>{children(query.data)}</>
}
