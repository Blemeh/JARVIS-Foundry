# Donor Attribution

Visual language for the cinematic JARVIS core (particle sphere, holographic
core, arc reactor rings, orbital accents) is **adapted from** the open-source
[my-jarvis](https://github.com/harsh-raj00/my-jarvis) project by harsh-raj00.

## Donor reference

| Field | Value |
| --- | --- |
| Repository | https://github.com/harsh-raj00/my-jarvis |
| Branch inspected | `main` |
| Commit inspected | `fab0f28296c977ced38b249067803bd652c0a84c` |
| Live demo (visual reference only) | https://jarvis-frontend-uj30.onrender.com/ |
| License (per donor README) | MIT |

## Components adapted (presentation only)

The Foundry Command Center reimplemented the following **visual concepts** in
TypeScript/React with a native WebGL shader path and CSS fallback. No donor
backend, voice stack, Gemini integration, or API contracts were imported.

| Donor path | Foundry adaptation |
| --- | --- |
| `frontend/src/components/3D/JarvisScene.jsx` | `src/components/jarvis-core/JarvisScene.tsx` + `webglRenderer.ts` |
| `frontend/src/components/3D/HolographicCore.jsx` | `src/components/jarvis-core/JarvisCoreFallback.tsx` (holographic sphere) |
| `frontend/src/components/3D/ArcReactor.jsx` | `src/components/jarvis-core/JarvisCoreFallback.tsx` (concentric rings) |
| `frontend/src/components/3D/RotatingRings.jsx` | Orbital ring CSS + WebGL ring shader |
| Donor dashboard / navigation chrome | Evolved existing `Sidebar`, `TopStatusBar`, `v4.css` shell (b52381e baseline) |

## Explicitly rejected from donor

- FastAPI / donor REST backend
- Gemini or other LLM provider wiring
- Voice / STT / TTS (`VoiceIndicator`, speech APIs)
- WebSocket chat protocols
- Fabricated telemetry or demo metrics

## Foundry-specific behavior

The cinematic core reacts only to **real Command Center state**:

- `HEALTHY / IDLE`, `ACTIVE MISSION`, `VERIFYING`, `DEGRADED`, `STOPPED`, `DISCONNECTED`
- Derived in `src/lib/jarvisCoreState.ts` from `stopActive`, `sourceMode`, `streamConnected`, trust health, and mission/evidence counts.
- WebGL is lazy-loaded; CSS ArcReactor fallback is used when WebGL is unavailable or `prefers-reduced-motion` is set.
- Animation pauses when the document tab is hidden.

## Notice

This file satisfies attribution requirements for MIT-licensed visual reference
material. JARVIS Foundry runtime, ledger, STOP semantics, and owner controls
remain independent of the donor project.
