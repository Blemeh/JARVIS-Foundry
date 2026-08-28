import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { ExternalLink, Maximize2, Minus, Plus, X } from "lucide-react"
import {
  buildMockObsidianGraph,
  fetchObsidianGraph,
  fetchObsidianNote,
  fitGraphView,
  graphSprings,
  obsidianOpenUrl,
  seedGraphSimulation,
  settleGraphLayout,
  tickGraphForce,
  type GraphSimNode,
  type GraphSpring,
  type ObsidianGraphLink,
  type ObsidianNote,
} from "../lib/obsidianVault"

const VIEW_WIDTH = 1100
const VIEW_HEIGHT = 580
const MIN_ZOOM = 0.16
const MAX_ZOOM = 5
const LABEL_FADE_START = 0.45
const LABEL_FADE_RANGE = 0.5

type ViewTransform = { x: number; y: number; k: number }

type GestureEventLike = Event & {
  scale: number
  clientX: number
  clientY: number
}

type ObsidianGraphViewProps = {
  live: boolean
}

function prefersReducedMotion(): boolean {
  return window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false
}

export function ObsidianGraphView({ live }: ObsidianGraphViewProps) {
  const stageRef = useRef<HTMLDivElement>(null)
  const svgRef = useRef<SVGSVGElement>(null)

  // The simulation owns these; React state below is a render mirror.
  const nodesRef = useRef<GraphSimNode[]>([])
  const springsRef = useRef<GraphSpring[]>([])
  const viewRef = useRef<ViewTransform>({ x: VIEW_WIDTH / 2, y: VIEW_HEIGHT / 2, k: 1.35 })
  const sizeRef = useRef({ w: VIEW_WIDTH, h: VIEW_HEIGHT })
  const alphaRef = useRef(1)
  const dragIdRef = useRef<string | null>(null)
  const panRef = useRef<{ pointerX: number; pointerY: number; viewX: number; viewY: number } | null>(
    null,
  )
  const userViewRef = useRef(false)
  const didFitRef = useRef(false)
  const gestureScaleRef = useRef(1)
  const frameRef = useRef(0)

  const [graphLoading, setGraphLoading] = useState(true)
  const [graphError, setGraphError] = useState<string | null>(null)
  const [links, setLinks] = useState<ObsidianGraphLink[]>([])
  const [nodes, setNodes] = useState<GraphSimNode[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [hoverId, setHoverId] = useState<string | null>(null)
  const [note, setNote] = useState<ObsidianNote | null>(null)
  const [noteLoading, setNoteLoading] = useState(false)
  const [view, setView] = useState<ViewTransform>({
    x: VIEW_WIDTH / 2,
    y: VIEW_HEIGHT / 2,
    k: 1.35,
  })
  const [dragId, setDragId] = useState<string | null>(null)
  const [size, setSize] = useState({ w: VIEW_WIDTH, h: VIEW_HEIGHT })

  const publishView = useCallback((next: ViewTransform) => {
    viewRef.current = next
    setView(next)
  }, [])

  useEffect(() => {
    const stage = stageRef.current
    if (!stage) return
    const apply = () => {
      const rect = stage.getBoundingClientRect()
      if (rect.width < 1 || rect.height < 1) return
      const next = { w: Math.round(rect.width), h: Math.round(rect.height) }
      sizeRef.current = next
      setSize(next)
      if (!userViewRef.current && nodesRef.current.length > 0) {
        publishView(fitGraphView(nodesRef.current, next.w, next.h))
      }
    }
    // jsdom and older embedded webviews may not expose ResizeObserver. The
    // graph has stable defaults, so render once and continue without resize
    // tracking instead of taking down the whole overview surface.
    if (typeof ResizeObserver === "undefined") {
      apply()
      return
    }
    const observer = new ResizeObserver(apply)
    observer.observe(stage)
    return () => observer.disconnect()
  }, [publishView])

  useEffect(() => {
    let cancelled = false
    const load = live ? fetchObsidianGraph() : Promise.resolve(buildMockObsidianGraph())
    load
      .then((graph) => {
        if (cancelled) return
        const reduced = prefersReducedMotion()
        const seeded = reduced
          ? settleGraphLayout(graph.nodes, graph.links)
          : seedGraphSimulation(graph.nodes, graph.links)
        nodesRef.current = seeded
        springsRef.current = graphSprings(seeded, graph.links)
        alphaRef.current = reduced ? 0 : 1
        didFitRef.current = reduced
        userViewRef.current = false
        setLinks(graph.links)
        setNodes(seeded)
        if (reduced) {
          publishView(fitGraphView(seeded, sizeRef.current.w, sizeRef.current.h))
        }
      })
      .catch((err: unknown) => {
        if (cancelled) return
        setGraphError(err instanceof Error ? err.message : String(err))
        setLinks([])
        setNodes([])
      })
      .finally(() => {
        if (!cancelled) setGraphLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [live, publishView])

  useEffect(() => {
    const tick = () => {
      const current = nodesRef.current
      if (current.length > 0 && (alphaRef.current > 0.006 || dragIdRef.current)) {
        const heat = dragIdRef.current ? Math.max(alphaRef.current, 0.2) : alphaRef.current
        tickGraphForce(current, springsRef.current, heat)
        alphaRef.current *= dragIdRef.current ? 0.99 : 0.972
        setNodes(current.slice())

        // Follow the expanding cloud until it settles so the bloom stays framed.
        if (!userViewRef.current) {
          const target = fitGraphView(current, sizeRef.current.w, sizeRef.current.h)
          const now = viewRef.current
          const ease = didFitRef.current ? 0.08 : 0.24
          didFitRef.current = true
          publishView({
            x: now.x + (target.x - now.x) * ease,
            y: now.y + (target.y - now.y) * ease,
            k: now.k + (target.k - now.k) * ease,
          })
        }
      }
      frameRef.current = requestAnimationFrame(tick)
    }
    frameRef.current = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(frameRef.current)
  }, [publishView])

  const loadNote = useCallback(
    async (relPath: string) => {
      if (!live) {
        setNote({
          relPath,
          label: relPath.replace(/\.md$/i, ""),
          content: `# ${relPath}\n\nSimulated note preview. Connect live mode to read vault files from disk.`,
        })
        return
      }
      setNoteLoading(true)
      try {
        setNote(await fetchObsidianNote(relPath))
      } catch (err: unknown) {
        setNote({
          relPath,
          label: relPath,
          content: err instanceof Error ? err.message : String(err),
        })
      } finally {
        setNoteLoading(false)
      }
    },
    [live],
  )

  useEffect(() => {
    if (!selectedId) return
    const relPath = nodesRef.current.find((node) => node.id === selectedId)?.relPath
    if (!relPath) return
    void loadNote(relPath)
  }, [selectedId, loadNote])

  const nodeMap = useMemo(() => new Map(nodes.map((node) => [node.id, node])), [nodes])

  const highlightedIds = useMemo(() => {
    if (!selectedId) return new Set<string>()
    const ids = new Set<string>([selectedId])
    for (const link of links) {
      if (link.source === selectedId) ids.add(link.target)
      if (link.target === selectedId) ids.add(link.source)
    }
    return ids
  }, [links, selectedId])

  const zoomAt = useCallback(
    (clientX: number, clientY: number, factor: number) => {
      const svg = svgRef.current
      if (!svg) return
      const rect = svg.getBoundingClientRect()
      const svgX = clientX - rect.left
      const svgY = clientY - rect.top
      const current = viewRef.current
      const worldX = (svgX - current.x) / current.k
      const worldY = (svgY - current.y) / current.k
      const nextK = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, current.k * factor))
      publishView({ k: nextK, x: svgX - worldX * nextK, y: svgY - worldY * nextK })
    },
    [publishView],
  )

  useEffect(() => {
    const stage = stageRef.current
    if (!stage) return

    const onWheel = (event: WheelEvent) => {
      event.preventDefault()
      userViewRef.current = true
      if (event.shiftKey) {
        const now = viewRef.current
        publishView({ ...now, x: now.x - event.deltaX - event.deltaY, y: now.y })
        return
      }
      // A trackpad pinch arrives as ctrl+wheel; plain two-finger scroll zooms too.
      const intensity = event.ctrlKey || event.metaKey ? 0.011 : 0.0016
      zoomAt(event.clientX, event.clientY, Math.exp(-event.deltaY * intensity))
    }

    const onGestureStart = (event: Event) => {
      event.preventDefault()
      userViewRef.current = true
      gestureScaleRef.current = 1
    }
    const onGestureChange = (event: Event) => {
      event.preventDefault()
      const gesture = event as GestureEventLike
      const factor = gesture.scale / (gestureScaleRef.current || 1)
      gestureScaleRef.current = gesture.scale
      zoomAt(gesture.clientX, gesture.clientY, factor)
    }
    const onGestureEnd = (event: Event) => {
      event.preventDefault()
      gestureScaleRef.current = 1
    }

    stage.addEventListener("wheel", onWheel, { passive: false })
    stage.addEventListener("gesturestart", onGestureStart, { passive: false })
    stage.addEventListener("gesturechange", onGestureChange, { passive: false })
    stage.addEventListener("gestureend", onGestureEnd, { passive: false })
    return () => {
      stage.removeEventListener("wheel", onWheel)
      stage.removeEventListener("gesturestart", onGestureStart)
      stage.removeEventListener("gesturechange", onGestureChange)
      stage.removeEventListener("gestureend", onGestureEnd)
    }
  }, [publishView, zoomAt])

  const clientToWorld = useCallback((clientX: number, clientY: number) => {
    const svg = svgRef.current
    if (!svg) return { x: 0, y: 0 }
    const rect = svg.getBoundingClientRect()
    const current = viewRef.current
    return {
      x: (clientX - rect.left - current.x) / current.k,
      y: (clientY - rect.top - current.y) / current.k,
    }
  }, [])

  const onPointerMove = useCallback(
    (event: React.PointerEvent<SVGSVGElement>) => {
      const dragging = dragIdRef.current
      if (dragging) {
        const point = clientToWorld(event.clientX, event.clientY)
        const node = nodesRef.current.find((item) => item.id === dragging)
        if (node) {
          node.fx = point.x
          node.fy = point.y
          node.x = point.x
          node.y = point.y
        }
        alphaRef.current = Math.max(alphaRef.current, 0.25)
        return
      }
      if (panRef.current) {
        userViewRef.current = true
        publishView({
          k: viewRef.current.k,
          x: panRef.current.viewX + (event.clientX - panRef.current.pointerX),
          y: panRef.current.viewY + (event.clientY - panRef.current.pointerY),
        })
      }
    },
    [clientToWorld, publishView],
  )

  const endPointer = useCallback(() => {
    const dragging = dragIdRef.current
    if (dragging) {
      const node = nodesRef.current.find((item) => item.id === dragging)
      if (node) {
        node.fx = null
        node.fy = null
      }
      alphaRef.current = 0.4
    }
    dragIdRef.current = null
    setDragId(null)
    panRef.current = null
  }, [])

  const onSvgPointerDown = (event: React.PointerEvent<SVGSVGElement>) => {
    if (event.button !== 0) return
    if ((event.target as SVGElement).closest(".obsidian-graph-dot")) return
    panRef.current = {
      pointerX: event.clientX,
      pointerY: event.clientY,
      viewX: viewRef.current.x,
      viewY: viewRef.current.y,
    }
    event.currentTarget.setPointerCapture(event.pointerId)
  }

  const onNodePointerDown = (event: React.PointerEvent<SVGCircleElement>, id: string) => {
    event.preventDefault()
    event.stopPropagation()
    const point = clientToWorld(event.clientX, event.clientY)
    const node = nodesRef.current.find((item) => item.id === id)
    if (node) {
      node.fx = point.x
      node.fy = point.y
    }
    dragIdRef.current = id
    setDragId(id)
    setSelectedId(id)
    alphaRef.current = Math.max(alphaRef.current, 0.35)
    event.currentTarget.setPointerCapture(event.pointerId)
  }

  const resetView = () => {
    userViewRef.current = true
    publishView(fitGraphView(nodesRef.current, sizeRef.current.w, sizeRef.current.h))
  }

  const adjustZoom = (delta: number) => {
    const stage = stageRef.current
    if (!stage) return
    userViewRef.current = true
    const rect = stage.getBoundingClientRect()
    zoomAt(rect.left + rect.width / 2, rect.top + rect.height / 2, 1 + delta)
  }

  // Obsidian fades note names out as the graph zooms away; hubs survive longest.
  const labelOpacity = (node: GraphSimNode) => {
    if (node.id === selectedId || node.id === hoverId) return 1
    if (selectedId !== null && highlightedIds.has(node.id)) return 1
    const zoom = (view.k - LABEL_FADE_START) / LABEL_FADE_RANGE
    return Math.max(0, Math.min(1, zoom + node.degree / 20 - 0.15))
  }

  const activeNote = selectedId && note?.relPath ? note : null

  return (
    <div className="obsidian-graph-shell" role="region" aria-label="Obsidian vault graph view">
      <p className="obsidian-graph-title">Graph view</p>
      <div ref={stageRef} className="obsidian-graph-stage">
        {graphLoading ? (
          <div className="obsidian-graph-loading t-label">Loading Obsidian graph…</div>
        ) : graphError ? (
          <div className="obsidian-graph-error t-label">{graphError}</div>
        ) : nodes.length === 0 ? (
          <div className="obsidian-graph-empty t-label">No indexed vault notes</div>
        ) : (
          <svg
            ref={svgRef}
            viewBox={`0 0 ${size.w} ${size.h}`}
            className={`obsidian-graph-svg${dragId ? " obsidian-graph-svg--dragging" : ""}`}
            onPointerDown={onSvgPointerDown}
            onPointerMove={onPointerMove}
            onPointerUp={endPointer}
            onPointerCancel={endPointer}
            onPointerLeave={endPointer}
          >
            <rect width={size.w} height={size.h} className="obsidian-graph-viewport-bg" />
            <g transform={`translate(${view.x} ${view.y}) scale(${view.k})`}>
              {links.map((link, index) => {
                const from = nodeMap.get(link.source)
                const to = nodeMap.get(link.target)
                if (!from || !to) return null
                const touched =
                  link.source === selectedId ||
                  link.target === selectedId ||
                  link.source === hoverId ||
                  link.target === hoverId
                const dimmed =
                  selectedId !== null &&
                  !highlightedIds.has(link.source) &&
                  !highlightedIds.has(link.target)
                return (
                  <line
                    key={`${link.source}-${link.target}-${index}`}
                    x1={from.x}
                    y1={from.y}
                    x2={to.x}
                    y2={to.y}
                    className={`obsidian-graph-edge${touched ? " obsidian-graph-edge--active" : ""}`}
                    opacity={dimmed ? 0.09 : 1}
                  />
                )
              })}
              {nodes.map((node) => {
                const selected = node.id === selectedId
                const hovered = node.id === hoverId
                const dimmed = selectedId !== null && !highlightedIds.has(node.id)
                const radius = node.radius + (selected ? 1.6 : hovered ? 0.8 : 0)
                const labelScale = 1 / view.k
                const labelAlpha = labelOpacity(node)
                return (
                  <g key={node.id} opacity={dimmed ? 0.2 : 1}>
                    <circle
                      cx={node.x}
                      cy={node.y}
                      r={radius}
                      className={`obsidian-graph-dot${selected ? " obsidian-graph-dot--selected" : ""}`}
                      onPointerDown={(event) => onNodePointerDown(event, node.id)}
                      onMouseEnter={() => setHoverId(node.id)}
                      onMouseLeave={() => setHoverId(null)}
                    />
                    {labelAlpha > 0.02 ? (
                      <text
                        className="obsidian-graph-label"
                        transform={`translate(${node.x} ${node.y + radius + 12 * labelScale}) scale(${labelScale})`}
                        textAnchor="middle"
                        opacity={labelAlpha}
                      >
                        {node.label.length > 36 ? `${node.label.slice(0, 34)}…` : node.label}
                      </text>
                    ) : null}
                  </g>
                )
              })}
            </g>
          </svg>
        )}

        <div className="obsidian-graph-zoom" role="group" aria-label="Graph zoom">
          <button type="button" className="icon-btn" aria-label="Zoom in" onClick={() => adjustZoom(0.2)}>
            <Plus size={14} />
          </button>
          <button type="button" className="icon-btn" aria-label="Zoom out" onClick={() => adjustZoom(-0.18)}>
            <Minus size={14} />
          </button>
          <button type="button" className="icon-btn" aria-label="Fit graph to view" onClick={resetView}>
            <Maximize2 size={13} />
          </button>
        </div>
      </div>

      <p className="obsidian-graph-meta t-mono">
        {nodes.length} notes · {links.length} links · two-finger scroll or pinch to zoom · drag to pan
      </p>

      {selectedId ? (
        <aside className="obsidian-note-panel" aria-label="Note preview">
          <header className="obsidian-note-panel-head">
            <div>
              <p className="obsidian-note-panel-title">{activeNote?.label ?? "Note"}</p>
              <p className="obsidian-note-panel-path t-mono">{selectedId}</p>
            </div>
            <div className="obsidian-note-panel-actions">
              {activeNote?.absolutePath ? (
                <a
                  href={obsidianOpenUrl(activeNote.absolutePath)}
                  className="obsidian-note-open"
                  title="Open in Obsidian"
                >
                  <ExternalLink size={13} aria-hidden="true" />
                  <span>Open in Obsidian</span>
                </a>
              ) : null}
              <button
                type="button"
                className="icon-btn"
                aria-label="Close note panel"
                onClick={() => setSelectedId(null)}
              >
                <X size={14} />
              </button>
            </div>
          </header>
          <div className="obsidian-note-panel-body">
            {noteLoading ? (
              <p className="t-label text-muted">Loading note…</p>
            ) : (
              <pre className="obsidian-note-content">{activeNote?.content ?? ""}</pre>
            )}
          </div>
        </aside>
      ) : null}
    </div>
  )
}
