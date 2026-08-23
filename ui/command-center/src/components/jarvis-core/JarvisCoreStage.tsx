import { lazy, Suspense, useEffect, useMemo, useState } from "react";
import { useCommandCenter } from "../../context/CommandCenterContext";
import { deriveJarvisCoreState } from "../../lib/jarvisCoreState";
import { probeWebGL } from "./webglRenderer";
import { JarvisCoreFallback } from "./JarvisCoreFallback";
import "./jarvis-core.css";

const LazyJarvisScene = lazy(() =>
  import("./JarvisScene").then((m) => ({ default: m.JarvisScene })),
);

type JarvisCoreStageProps = {
  compact?: boolean;
  activeMissionCount?: number;
  verifyingCount?: number;
  trustHealth?: string | null;
};

function useReducedMotion() {
  const [reduced, setReduced] = useState(() => {
    if (typeof window === "undefined" || typeof window.matchMedia !== "function") return false;
    return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  });
  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    const update = () => setReduced(mq.matches);
    update();
    mq.addEventListener("change", update);
    return () => mq.removeEventListener("change", update);
  }, []);
  return reduced;
}

function useTabVisible() {
  const [visible, setVisible] = useState(
    typeof document === "undefined" ? true : document.visibilityState === "visible",
  );
  useEffect(() => {
    const onChange = () => setVisible(document.visibilityState === "visible");
    document.addEventListener("visibilitychange", onChange);
    return () => document.removeEventListener("visibilitychange", onChange);
  }, []);
  return visible;
}

export function JarvisCoreStage({
  compact = false,
  activeMissionCount = 0,
  verifyingCount = 0,
  trustHealth,
}: JarvisCoreStageProps) {
  const { stopActive, sourceMode, streamConnected } = useCommandCenter();
  const reducedMotion = useReducedMotion();
  const tabVisible = useTabVisible();
  const [webglReady] = useState(() => probeWebGL());

  const state = useMemo(
    () =>
      deriveJarvisCoreState({
        stopActive,
        sourceMode,
        streamConnected,
        trustHealth,
        activeMissionCount,
        verifyingCount,
      }),
    [stopActive, sourceMode, streamConnected, trustHealth, activeMissionCount, verifyingCount],
  );

  const paused = reducedMotion || !tabVisible;
  const useFallback = reducedMotion || !webglReady;

  if (useFallback) {
    return <JarvisCoreFallback state={state} compact={compact} />;
  }

  return (
    <Suspense fallback={<JarvisCoreFallback state={state} compact={compact} />}>
      <LazyJarvisScene state={state} paused={paused} compact={compact} />
    </Suspense>
  );
}

export function JarvisCoreBadge({
  activeMissionCount = 0,
  verifyingCount = 0,
  trustHealth,
}: Omit<JarvisCoreStageProps, "compact">) {
  return (
    <JarvisCoreStage
      compact
      activeMissionCount={activeMissionCount}
      verifyingCount={verifyingCount}
      trustHealth={trustHealth}
    />
  );
}
