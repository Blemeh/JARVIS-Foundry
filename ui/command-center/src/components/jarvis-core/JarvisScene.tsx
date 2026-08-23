import { useEffect, useRef } from "react";
import {
  JARVIS_CORE_STATE_LABEL,
  jarvisCoreStateClass,
  type JarvisCoreVisualState,
} from "../../lib/jarvisCoreState";
import { createJarvisWebGL } from "./webglRenderer";
import "./jarvis-core.css";

type JarvisSceneProps = {
  state: JarvisCoreVisualState;
  paused?: boolean;
  compact?: boolean;
};

export function JarvisScene({ state, paused = false, compact = false }: JarvisSceneProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const rendererRef = useRef<ReturnType<typeof createJarvisWebGL> | null>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    try {
      const renderer = createJarvisWebGL(canvas);
      rendererRef.current = renderer;
      return () => {
        renderer.destroy();
        rendererRef.current = null;
      };
    } catch {
      return undefined;
    }
  }, []);

  useEffect(() => {
    rendererRef.current?.setState(state);
  }, [state]);

  useEffect(() => {
    rendererRef.current?.setPaused(paused);
  }, [paused]);

  useEffect(() => {
    const onResize = () => rendererRef.current?.resize();
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  const stateClass = jarvisCoreStateClass(state);

  return (
    <div
      className={`jarvis-core-scene ${stateClass} ${compact ? "jarvis-core-scene--compact" : ""}`}
      data-jarvis-core-mode="webgl"
      data-jarvis-core-state={state}
    >
      <canvas ref={canvasRef} className="jarvis-core-canvas" aria-hidden="true" />
      <div className="jarvis-core-overlay">
        <span className="jarvis-core-label">JARVIS</span>
        <span className="jarvis-core-state-label">{JARVIS_CORE_STATE_LABEL[state]}</span>
      </div>
      <span className="sr-only">JARVIS core visual state {JARVIS_CORE_STATE_LABEL[state]}</span>
    </div>
  );
}
