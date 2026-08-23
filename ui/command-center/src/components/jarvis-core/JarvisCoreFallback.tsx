import {
  JARVIS_CORE_STATE_LABEL,
  jarvisCoreStateClass,
  type JarvisCoreVisualState,
} from "../../lib/jarvisCoreState";
import "./jarvis-core.css";

type JarvisCoreFallbackProps = {
  state: JarvisCoreVisualState;
  compact?: boolean;
};

export function JarvisCoreFallback({ state, compact = false }: JarvisCoreFallbackProps) {
  const stateClass = jarvisCoreStateClass(state);
  return (
    <div
      className={`jarvis-core-fallback ${stateClass} ${compact ? "jarvis-core-fallback--compact" : ""}`}
      data-jarvis-core-mode="fallback"
      data-jarvis-core-state={state}
      role="img"
      aria-label={`JARVIS core visual state ${JARVIS_CORE_STATE_LABEL[state]}`}
    >
      <div className="jarvis-arc-reactor" aria-hidden="true">
        <span className="jarvis-arc-ring jarvis-arc-ring--one" />
        <span className="jarvis-arc-ring jarvis-arc-ring--two" />
        <span className="jarvis-arc-ring jarvis-arc-ring--three" />
        <span className="jarvis-holo-core">
          <span className="jarvis-holo-wire" />
          <span className="jarvis-holo-glow" />
        </span>
      </div>
      <span className="jarvis-core-label">JARVIS</span>
      <span className="jarvis-core-state-label">{JARVIS_CORE_STATE_LABEL[state]}</span>
    </div>
  );
}
