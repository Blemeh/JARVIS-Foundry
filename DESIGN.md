---
name: JARVIS Command Center
description: An evidence-bearing telemetry observatory for a persistent autonomous institution.
colors:
  instrument-void: "#03070a"
  downlink-panel: "#071016"
  raised-instrument: "#0a151c"
  structural-line: "#23414b"
  active-line: "#3a6875"
  telemetry-white: "#edfafa"
  telemetry-muted: "#9cb6bc"
  telemetry-dim: "#77939b"
  verified-signal: "#64f4d2"
  evidence-link: "#75ddff"
  caution-amber: "#ffc66b"
  critical-red: "#ff7777"
typography:
  display:
    fontFamily: "Tektur, Segoe UI, sans-serif"
    fontSize: "31px"
    fontWeight: 700
    lineHeight: 1.1
    letterSpacing: "-0.025em"
  title:
    fontFamily: "Tektur, Segoe UI, sans-serif"
    fontSize: "17px"
    fontWeight: 700
    lineHeight: 1.2
    letterSpacing: "0.06em"
  body:
    fontFamily: "Segoe UI Variable Text, Segoe UI, system-ui, sans-serif"
    fontSize: "14px"
    fontWeight: 500
    lineHeight: 1.5
  measurement:
    fontFamily: "Cascadia Mono, ui-monospace, monospace"
    fontSize: "24px"
    fontWeight: 650
    lineHeight: 1.1
  label:
    fontFamily: "Cascadia Mono, ui-monospace, monospace"
    fontSize: "10px"
    fontWeight: 700
    lineHeight: 1.2
    letterSpacing: "0.12em"
rounded:
  focus: "4px"
  control: "7px"
  navigation: "8px"
  field: "10px"
spacing:
  micro: "4px"
  tight: "8px"
  control: "12px"
  panel: "20px"
  section: "28px"
  shell: "30px"
components:
  command-button-locked:
    backgroundColor: "{colors.raised-instrument}"
    textColor: "{colors.telemetry-dim}"
    typography: "{typography.label}"
    rounded: "{rounded.control}"
    padding: "8px 12px"
  directive-field:
    backgroundColor: "{colors.instrument-void}"
    textColor: "{colors.telemetry-muted}"
    rounded: "{rounded.field}"
    padding: "11px 13px"
  navigation-active:
    backgroundColor: "{colors.downlink-panel}"
    textColor: "{colors.telemetry-white}"
    rounded: "{rounded.navigation}"
    padding: "10px 11px"
  status-nominal:
    textColor: "{colors.verified-signal}"
    typography: "{typography.label}"
  status-caution:
    textColor: "{colors.caution-amber}"
    typography: "{typography.label}"
  status-critical:
    textColor: "{colors.critical-red}"
    typography: "{typography.label}"
---

# Design System: JARVIS Command Center

## Overview

**Creative North Star: "The Evidence-Bearing Telemetry Observatory"**

JARVIS inhabits a deep instrument-black world: a satellite downlink dossier built to expose operational truth, not decorate it. Dense telemetry feels deliberate and calm because hierarchy comes from a continuous frame, precise type roles, measured dividers, and provenance links rather than a field of interchangeable cards.

The visual story always begins with safety posture and owner intent, passes through missions and cross-check measurements, and terminates in an evidence-bearing event spine. Science-fiction character comes from clipped telemetry geometry, calibration marks, and restrained signal light; it never obscures uncertainty, exceptions, or ledger lineage.

**Key Characteristics:**

- One continuous chamfered downlink frame rather than a collection of floating cards.
- Aqua means verified or live signal; cyan means inspectable linkage; amber and red are reserved for exceptions.
- Display type names systems and intent, Segoe carries explanation, and monospace carries measurements and provenance.
- Operational density remains readable through explicit grouping, hairline structure, and honest unknown states.
- Motion is rare, finite, and reducible: only the newest event receives restrained illumination.

## Colors

Near-black blue-green surfaces form the instrument body; cold whites carry truth, while narrow spectral accents encode meaning rather than atmosphere alone.

### Primary

- **Verified Signal:** The rare aqua voice for nominal posture, live nodes, evidence counts, and the active calibration edge.

### Secondary

- **Evidence Link:** The cooler cyan reserved for links, inspectable records, and focus communication.

### Tertiary

- **Caution Amber:** Unknown, gated, degraded, or attention-needed conditions that are not yet critical.
- **Critical Red:** STOP and materially unsafe or failed states only.

### Neutral

- **Instrument Void:** The page-level darkness and visual silence outside the apparatus.
- **Downlink Panel:** The canonical operational surface inside the frame.
- **Raised Instrument:** The slight tonal lift for controls nested within a panel.
- **Structural Line / Active Line:** Hairline boundaries and calibrated hot edges; structure replaces ornamental containers.
- **Telemetry White / Muted / Dim:** A three-step text hierarchy for truth, explanation, and metadata.

**The Semantic Signal Rule.** Aqua, cyan, amber, and red must retain their operational meanings; never use them as arbitrary decoration.

**The Unknown Is Visible Rule.** Unknown or unavailable values use explicit text and caution treatment, never a false zero or a success color.

## Typography

**Display Font:** Tektur (self-hosted, with Segoe UI fallback)  
**Body Font:** Segoe UI Variable Text (with Segoe UI and system fallbacks)  
**Label/Mono Font:** Cascadia Mono (with `ui-monospace` and monospace fallbacks)

**Character:** Tektur gives the command surface a technical, cut-metal identity without becoming theatrical. Segoe keeps operational explanation familiar and highly legible; monospace makes counts, states, timestamps, and record identifiers feel measurable and auditable.

### Hierarchy

- **Display** (700, 31px, 1.1): Owner intent and the single dominant command statement; mobile reduces it to 25px.
- **Title** (700, 14–17px, compact): Product, panel, and system headings with controlled tracking.
- **Body** (500, 14px, 1.5): Operational explanation, constrained to roughly 64 characters where sustained reading occurs.
- **Measurement** (650, 24px, 1.1): Cross-check metrics and durable counts; always tabular-feeling and allowed to wrap safely.
- **Label** (700, 9–10px, 0.07–0.14em tracking, uppercase): State, provenance, table headings, and calibration language.

**The Three-Instrument Rule.** Tektur names, Segoe explains, monospace measures; do not blur these roles.

## Layout

The desktop shell uses a fixed 232px command rail and a fluid main plane capped at 1680px. Within the continuous downlink frame, owner communication and current intent form the first band, five operational cross-checks form the second, and mission operations plus the newest-first evidence spine form the third. One-pixel dividers create adjacency and lineage rather than independent card islands.

At 1120px, the rail compresses to 78px, major two-column regions stack, and telemetry shifts to three columns. At 700px, the rail becomes a compact horizontal header, secondary navigation and signals recede, telemetry becomes two columns, the mission table becomes labeled stacked records, and frame chamfers tighten from 18px to 12px. Content gutters step from 30px to 10–14px without removing evidence access.

**The First-Viewport Rule.** Safety posture, owner intent, the command channel, cross-check telemetry, and the beginning of mission operations must read as one downlink surface before secondary detail.

## Elevation & Depth

Depth is primarily tonal and structural. The downlink frame alone receives a broad, low-opacity ambient shadow (`0 28px 80px rgba(0,0,0,.30)`); nested panels remain flat and connected by lines. Signal glows identify live or focused state, not physical elevation. The sticky top bar may use translucent black and a 14px backdrop blur to preserve context over scrolling evidence.

**The One Apparatus Rule.** Elevate the continuous downlink frame, not every internal region.

## Shapes

The signature silhouette is an eight-point chamfered frame with 18px desktop cuts and 12px mobile cuts. Controls use restrained 7–10px radii; status lamps are circular; navigation and timeline nodes use small squares rotated 45 degrees. Borders are one-pixel blue-green hairlines, with brighter edges only where the apparatus is active or inspectable.

**The Clipped Geometry Rule.** Use chamfers and diamond nodes to signal infrastructure; reserve ordinary rounded rectangles for interactive controls.

## Components

### Buttons

- **Shape:** Compact instrument control with a restrained 7px radius.
- **Locked:** Raised-panel fill, dim text, structural border, and monospace uppercase label; disabled state remains plainly visible rather than disappearing.
- **Hover / Focus:** Enabled variants may shift toward evidence cyan, but keyboard focus always uses the dedicated cyan halo.

### Status Signals

- **Style:** Uppercase monospace label paired with a 6px lamp.
- **State:** Nominal aqua may glow softly; caution is amber; critical is red; neutral uses dim telemetry text.
- **Behavior:** Status text always accompanies color so state is never color-only.

### Cards / Containers

- **Corner Style:** Internal regions are square and contiguous; the outer dossier alone carries the chamfer.
- **Background:** Downlink panel with occasional raised-instrument control surfaces.
- **Shadow Strategy:** No internal card shadows; use the single apparatus shadow described above.
- **Border:** One-pixel structural lines establish rows, columns, and provenance flow.
- **Internal Padding:** Typically 17–28px on desktop, contracting to 15–22px on mobile.

### Inputs / Fields

- **Style:** A three-part directive field on instrument void with a structural border and 10px radius.
- **Focus:** A 3px translucent evidence-cyan halo; the text field itself remains visually quiet.
- **Disabled:** The channel lock is labeled explicitly and retains legible dim contrast.

### Navigation

The command rail uses small diamond markers, muted labels, and a downlink-panel active state. Desktop width is fixed; the middle breakpoint collapses labels; mobile keeps the active destination visible in a horizontal rail and hides inactive destinations rather than squeezing them into unreadable tabs.

### Downlink Dossier

The defining container combines the chamfered frame, calibration ruler, active aqua edge, safety/intent band, telemetry strip, mission ledger, and newest-first decision spine. Every material record offers an evidence-colored path toward its durable source.

### Decision Signal

Events sit on a one-pixel vertical spine with diamond nodes. Only the newest node breathes, for two finite cycles at 2.8 seconds; reduced-motion replaces animation with a static halo. Amber and red nodes inherit exception semantics.

## Do's and Don'ts

### Do:

- **Do** preserve a single continuous apparatus with hairline internal divisions and ledger-linked records.
- **Do** place state text beside every colored lamp and keep unknown, unavailable, blocked, and untested values explicit.
- **Do** use Tektur for system identity, Segoe for explanation, and monospace for measurements and provenance.
- **Do** maintain the 1120px and 700px structural transitions and the reduced-motion equivalent for newest-event illumination.
- **Do** use cyan links to make the evidence spine inspectable from every material operational claim.

### Don't:

- **Don't** turn the observatory into a generic grid of floating cards, pills, gradients, or decorative metrics.
- **Don't** use aqua, amber, or red without their verified, caution, and critical meanings.
- **Don't** imply success with missing data, substitute zero for unknown, or remove provenance to simplify a view.
- **Don't** animate the whole timeline, loop signal motion indefinitely, or depend on motion to convey state.
- **Don't** let science-fiction styling reduce contrast, keyboard focus, mobile access, or operational candor.
