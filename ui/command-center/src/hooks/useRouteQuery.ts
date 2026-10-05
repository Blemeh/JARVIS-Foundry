import { useEffect, useRef, useState } from "react"

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
  options?: { keepPreviousWhileLoading?: boolean; timeoutMs?: number },
): RouteQueryResult<T> => {
  const keepPrevious = options?.keepPreviousWhileLoading ?? false
  const previousDeps = useRef(deps)
  const timeoutMs = options?.timeoutMs ?? 20_000
  const [status, setStatus] = useState<RouteQueryStatus>("loading")
  const [data, setData] = useState<T | null>(null)
  const dataRef = useRef<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [reloadToken, setReloadToken] = useState(0)

  const reload = () => setReloadToken((value) => value + 1)

  useEffect(() => {
    let cancelled = false
    const changed = deps.some((value, index) => !Object.is(value, previousDeps.current[index]))
    previousDeps.current = deps
    if (changed && !keepPrevious) dataRef.current = null
    const shouldShowLoading = dataRef.current === null
    if (shouldShowLoading) {
      queueMicrotask(() => {
        if (cancelled) return
        if (changed && !keepPrevious) setData(null)
        setStatus("loading")
        setError(null)
      })
    }

    const timer = setTimeout(() => {
      if (cancelled) return
      cancelled = true
      setError("This page took too long to load. Retry to reconnect.")
      setStatus(dataRef.current === null ? "error" : "ready")
    }, timeoutMs)

    Promise.resolve().then(fetcher)
      .then((result) => {
        if (cancelled) return
        clearTimeout(timer)
        dataRef.current = result
        setData(result)
        setError(null)
        setStatus("ready")
      })
      .catch((err: unknown) => {
        if (cancelled) return
        clearTimeout(timer)
        setError(err instanceof Error ? err.message : String(err))
        setStatus(dataRef.current === null ? "error" : "ready")
      })

    return () => {
      cancelled = true
      clearTimeout(timer)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, reloadToken])

  return { status, data, error, reload }
}
