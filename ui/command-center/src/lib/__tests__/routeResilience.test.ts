import { describe, expect, it } from "vitest"
import type { ConnectionState } from "../contracts"
import {
  formatArchivedMissionLabel,
  isDetailNotFound,
  routeFetchErrorDetail,
  shouldShowRouteFetchError,
} from "../routeResilience"
import type { RouteQueryResult } from "../../hooks/useRouteQuery"

const disconnected: ConnectionState = {
  connected: false,
  source: "DISCONNECTED",
  stale: true,
  lastFetchedAt: null,
  error: "Runtime offline",
}

const live: ConnectionState = {
  connected: true,
  source: "LIVE",
  stale: false,
  lastFetchedAt: "2026-08-22T12:00:00Z",
  error: null,
}

const disconnectedQuiet: ConnectionState = {
  connected: false,
  source: "DISCONNECTED",
  stale: true,
  lastFetchedAt: null,
  error: null,
}

const readyQuery = <T,>(data: T): RouteQueryResult<T> => ({
  status: "ready",
  data,
  error: null,
  reload: () => undefined,
})

describe("routeResilience helpers", () => {
  it("prefers connection error, then load error, then disconnected surface copy", () => {
    expect(
      routeFetchErrorDetail(
        "DISCONNECTED",
        disconnected,
        "fetch blew up",
        "Mission vectors",
      ),
    ).toBe("Runtime offline")

    expect(routeFetchErrorDetail("LIVE", live, "timeout", "Mission vectors")).toBe(
      "timeout",
    )

    expect(routeFetchErrorDetail("DISCONNECTED", disconnectedQuiet, null, "Mission vectors")).toBe(
      "Mission vectors unavailable: runtime source is DISCONNECTED; no fixture fallback is shown.",
    )
  })

  it("shows fetch errors when loadError is present or disconnected without data", () => {
    expect(shouldShowRouteFetchError("LIVE", "boom", true)).toBe(true)
    expect(shouldShowRouteFetchError("DISCONNECTED", null, false)).toBe(true)
    expect(shouldShowRouteFetchError("SIMULATED", null, true)).toBe(false)
  })

  it("formats archived mission labels without inventing counts", () => {
    expect(formatArchivedMissionLabel(4)).toBe("Archived (4)")
    expect(formatArchivedMissionLabel(null)).toBe("Archived (NOT REPORTED)")
  })

  it("detects detail not-found when a nullable query resolves ready with null", () => {
    expect(isDetailNotFound(readyQuery(null))).toBe(true)
    expect(isDetailNotFound(readyQuery({ id: "m-1" }))).toBe(false)
    expect(
      isDetailNotFound({
        status: "loading",
        data: null,
        error: null,
        reload: () => undefined,
      }),
    ).toBe(false)
  })
})
