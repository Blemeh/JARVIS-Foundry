import { act, renderHook } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"
import { useRouteQuery } from "../useRouteQuery"

describe("bounded route loading", () => {
  afterEach(() => vi.useRealTimers())

  it("terminates a request that never settles and rejects its late result", async () => {
    vi.useFakeTimers()
    let finish!: (value: string) => void
    const pending = new Promise<string>((resolve) => { finish = resolve })
    const { result } = renderHook(() => useRouteQuery(() => pending, [], { timeoutMs: 50 }))
    await act(async () => { await vi.advanceTimersByTimeAsync(50) })
    expect(result.current.status).toBe("error")
    expect(result.current.error).toContain("too long")
    await act(async () => { finish("late") })
    expect(result.current.data).toBeNull()
  })

  it("retains last known data on error and recovers on retry", async () => {
    const fetcher = vi.fn().mockResolvedValueOnce("durable").mockRejectedValueOnce(new Error("offline")).mockResolvedValueOnce("fresh")
    const { result } = renderHook(() => useRouteQuery(fetcher, []))
    await act(async () => {})
    expect(result.current.data).toBe("durable")
    await act(async () => { result.current.reload() })
    expect(result.current.data).toBe("durable")
    expect(result.current.error).toBe("offline")
    expect(result.current.status).toBe("ready")
    await act(async () => { result.current.reload() })
    expect(result.current.data).toBe("fresh")
    expect(result.current.error).toBeNull()
  })

  it("ignores a prior navigation request completing after the next request", async () => {
    let finish!: (value: string) => void
    const previous = new Promise<string>((resolve) => { finish = resolve })
    const { result, rerender } = renderHook(({ page }) => useRouteQuery(() => page === 1 ? previous : Promise.resolve("new page"), [page]), { initialProps: { page: 1 } })
    await act(async () => { rerender({ page: 2 }) })
    expect(result.current.data).toBe("new page")
    await act(async () => { finish("old page") })
    expect(result.current.data).toBe("new page")
  })
  it("does not show a previous entity when navigation to another entity fails", async () => {
    const { result, rerender } = renderHook(({ entity }) => useRouteQuery(() => entity === "one" ? Promise.resolve("first entity") : Promise.reject(new Error("offline")), [entity]), { initialProps: { entity: "one" } })
    await act(async () => {})
    expect(result.current.data).toBe("first entity")
    await act(async () => { rerender({ entity: "two" }) })
    expect(result.current.data).toBeNull()
    expect(result.current.status).toBe("error")
  })

})
