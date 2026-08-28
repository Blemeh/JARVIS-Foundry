import { useEffect, useState } from "react"

export type RouteQueryStatus = "loading" | "ready" | "error"

export type RouteQueryResult<T> = {
  status: RouteQueryStatus
  data: T | null
  error: string | null
  reload: () => void
}

export const useRouteQuery = <T>(
  fetcher: () => Promise<T>,
  deps: readonly unknown[],
  options?: { keepPreviousWhileLoading?: boolean },
): RouteQueryResult<T> => {
  const keepPrevious = options?.keepPreviousWhileLoading ?? false
  const [status, setStatus] = useState<RouteQueryStatus>("loading")
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [reloadToken, setReloadToken] = useState(0)

  const reload = () => setReloadToken((value) => value + 1)

  useEffect(() => {
    let cancelled = false
    const shouldShowLoading = !keepPrevious || data === null
    if (shouldShowLoading) {
      queueMicrotask(() => {
        if (cancelled) return
        setStatus("loading")
        setError(null)
      })
    }

    fetcher()
      .then((result) => {
        if (cancelled) return
        setData(result)
        setStatus("ready")
      })
      .catch((err: unknown) => {
        if (cancelled) return
        setData(null)
        setError(err instanceof Error ? err.message : String(err))
        setStatus("error")
      })

    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, reloadToken])

  return { status, data, error, reload }
}
