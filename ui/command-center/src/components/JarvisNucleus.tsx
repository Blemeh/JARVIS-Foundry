import { useId } from "react";
import type { JarvisNucleusState } from "../lib/nucleusState";

export type { JarvisNucleusState } from "../lib/nucleusState";

const stateCopy: Record<JarvisNucleusState, string> = {
  WAITING: "Standing by for the next durable condition",
  OBSERVING: "Reading the current operational field",
  PLANNING: "Resolving a bounded course of action",
  RESEARCHING: "Gathering evidence before commitment",
  EXECUTING: "Work is moving through the authority spine",
  VERIFYING: "Independent evidence is being reconciled",
  RECOVERING: "Restoring continuity from durable state",
  BLOCKED: "Progress is held by an unresolved dependency",
  NEEDS_OWNER: "A consequential owner decision is required",
  STOPPED: "STOP is active; autonomous effects are suspended",
  UNKNOWN: "Canonical executive state is unavailable",
};

export function JarvisNucleus({
  state,
  phase,
  compact = false,
}: {
  state: JarvisNucleusState;
  phase?: string | null;
  compact?: boolean;
}) {
  const id = useId().replace(/:/g, "");
  const titleId = `${id}-title`;
  const detailId = `${id}-detail`;
  const resolvedPhase = phase?.trim() || stateCopy[state];

  return (
    <figure
      className={`jarvis-nucleus jarvis-nucleus--${state.toLowerCase()} ${compact ? "jarvis-nucleus--compact" : ""}`}
      data-jarvis-core-state={state.toLowerCase()}
      aria-labelledby={`${titleId} ${detailId}`}
    >
      <svg className="jarvis-nucleus__instrument" viewBox="0 0 320 320" role="img" aria-hidden="true">
        <defs>
          <radialGradient id={`${id}-field`} cx="50%" cy="50%" r="50%">
            <stop offset="0%" stopColor="currentColor" stopOpacity=".22" />
            <stop offset="42%" stopColor="currentColor" stopOpacity=".05" />
            <stop offset="100%" stopColor="currentColor" stopOpacity="0" />
          </radialGradient>
          <filter id={`${id}-glow`} x="-80%" y="-80%" width="260%" height="260%">
            <feGaussianBlur stdDeviation="4" result="blur" />
            <feMerge><feMergeNode in="blur" /><feMergeNode in="SourceGraphic" /></feMerge>
          </filter>
        </defs>
        <circle className="jarvis-nucleus__field" cx="160" cy="160" r="148" fill={`url(#${id}-field)`} />
        <g className="jarvis-nucleus__datum">
          <path d="M160 7v18M160 295v18M7 160h18M295 160h18" />
          <path d="M57 57l12 12M251 251l12 12M263 57l-12 12M69 251l-12 12" />
        </g>
        <circle className="jarvis-nucleus__orbit jarvis-nucleus__orbit--outer" cx="160" cy="160" r="126" pathLength="100" />
        <circle className="jarvis-nucleus__orbit jarvis-nucleus__orbit--middle" cx="160" cy="160" r="101" pathLength="100" />
        <circle className="jarvis-nucleus__orbit jarvis-nucleus__orbit--inner" cx="160" cy="160" r="75" pathLength="100" />
        <g className="jarvis-nucleus__phase-marks">
          {Array.from({ length: 12 }, (_, index) => (
            <path key={index} d="M160 35v8" transform={`rotate(${index * 30} 160 160)`} />
          ))}
        </g>
        <g className="jarvis-nucleus__vectors">
          <path d="M160 56l9 14h-18z" />
          <path d="M264 160l-14 9v-18z" />
          <path d="M160 264l-9-14h18z" />
          <path d="M56 160l14-9v18z" />
        </g>
        <g className="jarvis-nucleus__planning-frame">
          <path d="M160 78l71 41v82l-71 41-71-41v-82z" />
          <path d="M160 92v22M228 131l-19 11M228 189l-19-11M160 228v-22M92 189l19-11M92 131l19 11" />
        </g>
        <g className="jarvis-nucleus__verification-frame">
          <path d="M111 111h24M111 111v24M209 111h-24M209 111v24M111 209h24M111 209v-24M209 209h-24M209 209v-24" />
          <circle cx="160" cy="160" r="57" />
        </g>
        <g className="jarvis-nucleus__recovery-frame">
          <path d="M86 137a78 78 0 0 1 128-39" />
          <path d="M234 183a78 78 0 0 1-128 39" />
          <path d="M207 92l10 8-13 5M113 228l-10-8 13-5" />
        </g>
        <path className="jarvis-nucleus__unresolved" d="M76 91a108 108 0 0 1 37-28" pathLength="100" />
        <g className="jarvis-nucleus__core" filter={`url(#${id}-glow)`}>
          <path d="M160 103l43 25v50l-43 25-43-25v-50z" />
          <path d="M160 122l26 15v30l-26 15-26-15v-30z" />
          <circle cx="160" cy="152" r="8" />
          <path d="M160 160v13" />
        </g>
        <g className="jarvis-nucleus__halt-frame">
          <path d="M139 139l42 42M181 139l-42 42" />
          <path d="M125 125h70v70h-70z" />
        </g>
        <g className="jarvis-nucleus__particles">
          <circle cx="160" cy="34" r="2.5" />
          <circle cx="276" cy="160" r="2" />
          <circle cx="82" cy="242" r="2" />
        </g>
      </svg>
      <figcaption className="jarvis-nucleus__caption">
        <span id={titleId} className="jarvis-nucleus__state">{state.replace("_", " ")}</span>
        <span id={detailId} className="jarvis-nucleus__phase">{resolvedPhase}</span>
      </figcaption>
    </figure>
  );
}
