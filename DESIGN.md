---
name: JARVIS Command Center
description: A cinematic command core for an autonomous institution, where owner intent resolves into ledger-linked mission vectors.
colors:
  void-black: "#02040a"
  downlink-blue: "#07101c"
  raised-blue: "#0b1928"
  structural-line: "#1a3751"
  active-line: "#2a6382"
  telemetry-white: "#effcff"
  telemetry-muted: "#9fc5d7"
  telemetry-dim: "#7093a7"
  electric-signal: "#8affef"
  evidence-cyan: "#66d9ff"
  orbit-violet: "#9b8cff"
  caution-amber: "#ffd071"
  stop-pink: "#ff7189"
typography:
  display:
    fontFamily: "Tektur, Segoe UI, sans-serif"
    fontSize: "43px"
    fontWeight: 700
    lineHeight: 1.02
    letterSpacing: "-0.035em"
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
    fontFamily: "ui-monospace, Cascadia Mono, monospace"
    fontSize: "24px"
    fontWeight: 650
    lineHeight: 1.1
  label:
    fontFamily: "ui-monospace, Cascadia Mono, monospace"
    fontSize: "10px"
    fontWeight: 700
    lineHeight: 1.2
    letterSpacing: "0.11em"
rounded:
  focus: "4px"
  control: "7px"
  navigation: "8px"
  field: "10px"
  frame: "18px"
spacing:
  micro: "4px"
  tight: "8px"
  control: "12px"
  panel: "20px"
  section: "28px"
  shell: "30px"
components:
  command-button-locked:
    backgroundColor: "{colors.raised-blue}"
    textColor: "{colors.telemetry-dim}"
    typography: "{typography.label}"
    rounded: "{rounded.control}"
    padding: "8px 12px"
  directive-field:
    backgroundColor: "{colors.void-black}"
    textColor: "{colors.telemetry-muted}"
    typography: "{typography.body}"
    rounded: "{rounded.field}"
    padding: "11px 13px"
  navigation-active:
    backgroundColor: "{colors.downlink-blue}"
    textColor: "{colors.telemetry-white}"
    typography: "{typography.body}"
    rounded: "{rounded.navigation}"
    padding: "10px 11px"
  status-nominal:
    textColor: "{colors.electric-signal}"
    typography: "{typography.label}"
  status-caution:
    textColor: "{colors.caution-amber}"
    typography: "{typography.label}"
  status-critical:
    textColor: "{colors.stop-pink}"
    typography: "{typography.label}"
  downlink-frame:
    backgroundColor: "{colors.downlink-blue}"
    textColor: "{colors.telemetry-white}"
    rounded: "{rounded.frame}"
    padding: "0"
---

# Design System: JARVIS Command Center

## Overview

**Creative North Star: "The 2100 Living Command Core"**

JARVIS is a living command core for an autonomous institution, not a dashboard waiting for clicks. The owner speaks intent; the core reveals the current system posture; mission vectors radiate outward into inspectable evidence. The visual world honors the user-pinned Iron Man JARVIS direction in a year-2100 command-deck register while keeping every state calm, candid, and operational.

The apparatus lives in deep space-black with electric cyan and violet orbit light, cinematic radial depth, spatial telemetry planes, and ledger-linked signals. Drama comes from geometry, contrast, and controlled glow rather than ornamental noise. A continuous downlink frame gives the surface one instrument-like body, while explicit state labels and evidence paths keep the science-fiction character accountable to durable truth.

**Key Characteristics:**

- One continuous chamfered downlink frame with connected telemetry planes instead of a grid of floating cards.
- An orbital command core that anchors owner intent and makes the system feel alive without inventing operational state.
- Electric cyan and violet spatial light, with amber and stop-pink reserved for uncertainty, degradation, and critical control states.
- Tektur names systems and intent, Segoe explains, and monospace measures counts, state, time, and provenance.
- Mission vectors use progress rails; the newest ledger signal receives finite illumination with a reduced-motion equivalent.

## Colors

The palette is an instrument-black field cut by cold telemetry whites and a narrow spectrum of signal light. Color is semantic: it tells the owner what is verified, what can be inspected, and what requires attention.

### Primary

- **Electric Signal:** Nominal posture, live system signals, and the active edge of the command apparatus. Its rarity makes verified state feel meaningful.

### Secondary

- **Evidence Cyan:** Inspectable links, focus communication, orbital accents, and paths from a displayed claim to its durable record.

### Tertiary

- **Orbit Violet:** Spatial depth and secondary orbit geometry; it gives the command core dimensional character without masquerading as a status.
- **Caution Amber:** Unknown, unavailable, gated, degraded, or attention-needed states that are not yet critical.
- **Stop Pink:** STOP and materially unsafe or failed states only.

### Neutral

- **Void Black:** Page-level darkness and visual silence outside the apparatus.
- **Downlink Blue:** The canonical operational surface inside the frame.
- **Raised Blue:** A slight tonal lift for controls nested inside a panel.
- **Structural Line / Active Line:** One-pixel blue lines that establish adjacency, lineage, and the current instrument edge.
- **Telemetry White / Muted / Dim:** A three-step text hierarchy for truth, explanation, and metadata.

**The Semantic Signal Rule.** Electric signal, evidence cyan, amber, and stop pink retain their operational meanings; never use them as arbitrary decoration.

**The Unknown State Rule.** Unknown, unavailable, not tested, and blocked values remain explicit and caution-colored; missing data never becomes a false zero or nominal signal.

**The Orbit Light Rule.** Violet adds spatial depth and orbit structure, not an invented health state.

## Typography

**Display Font:** Tektur (self-hosted, with Segoe UI fallback)  
**Body Font:** Segoe UI Variable Text (with Segoe UI and system fallbacks)  
**Label/Mono Font:** ui-monospace, Cascadia Mono (with monospace fallback)

**Character:** Tektur supplies the technical, cut-metal identity of a 2100 command deck. Segoe keeps operational explanation familiar and legible; monospace turns measurements and provenance into visibly auditable instruments.

### Hierarchy

- **Display** (700, 43px desktop / 32px mobile, 1.02 line-height): The single dominant owner-intent question and command statement.
- **Title** (700, 17px, 1.2 line-height): Product, panel, and subsystem names with controlled tracking.
- **Body** (500, 14px, 1.5 line-height): Calm explanation and next-action copy, kept to approximately 58–64ch where sustained reading occurs.
- **Measurement** (650, 24px, 1.1 line-height): Cross-check counts, costs, and durable values that should scan like telemetry.
- **Label** (700, 10px, 0.11em tracking, uppercase): Status, provenance, timestamps, table headings, and calibration language.

**The Three-Instrument Rule.** Tektur names, Segoe explains, and monospace measures; do not blur the roles by styling every string as display type.

## Layout

The desktop shell is a sticky 232px command rail beside a fluid main plane capped at 1680px. The main surface uses 30px shell gutters and a single downlink frame. Inside that frame, the first viewport establishes owner communication and current intent, then cross-check telemetry, then mission operations and a newest-first evidence spine. Hairline divisions keep the regions connected as one instrument.

At 1120px the rail compresses to 78px, the mission and operations regions stack, and telemetry shifts to three columns. At 700px the rail becomes a compact horizontal header, inactive destinations recede, telemetry becomes two columns, mission records stack with their labels intact, the command field tightens, and STOP remains visible. The frame chamfer contracts from 18px to 12px; content gutters step from 30px to 10–14px without removing evidence access.

The spacing rhythm is 4px micro, 8px tight, 12px control, 20px panel, 28px section, and 30px shell. Responsive changes are structural rather than merely scaled: hierarchy and audit paths survive every viewport.

**The First-Viewport Rule.** Safety posture, owner intent, the owner-link channel, cross-check telemetry, and the beginning of mission operations must read as one command surface before secondary detail.

## Elevation & Depth

Depth is tonal and spatial. The downlink frame alone receives broad ambient depth; internal planes remain flat and connected by lines. The page adds a cinematic radial background and restrained orbit glow, while the sticky top bar uses translucent black with an 18px backdrop blur. Signal glow identifies live or focused state, never a decorative layer of elevation.

### Shadow Vocabulary

- **Apparatus depth** (`0 28px 80px rgba(0,0,0,.46)`): Broad shadow under the continuous downlink frame only.
- **Orbit ambient** (`0 0 90px rgba(66,145,255,.08)`): Low-opacity atmospheric light around the apparatus; it should never become a card shadow.
- **Keyboard focus** (`0 0 0 3px rgba(102,217,255,.34)`): Visible focus halo for keyboard navigation and fields.
- **Nominal lamp** (`0 0 9px rgba(138,255,239,.60)`): Restrained glow on a verified status lamp.

**The One Apparatus Rule.** Elevate the continuous downlink frame, not every internal region.

**The Signal-Is-State Rule.** Glow is reserved for a live, focused, or newest signal; it is never a substitute for a text state label.

## Shapes

The signature silhouette is an eight-point chamfered frame with 18px desktop cuts and 12px mobile cuts. Its borders are one-pixel blue-green hairlines, with brighter active edges where the apparatus is live or inspectable. Internal regions are square and contiguous; ordinary 7–10px rounded corners belong to controls, navigation, and fields rather than to decorative containers.

Orbital geometry uses circles and crossed elliptical rings for the command core. Navigation and timeline nodes are small squares rotated 45 degrees, signaling infrastructure and ledger sequence. Status lamps are circular and always paired with text.

**The Clipped Geometry Rule.** Use chamfers and diamond nodes to signal infrastructure; reserve ordinary rounded rectangles for interactive controls.

## Components

### Buttons

- **Character:** Compact, instrument-like controls that state their authorization posture plainly.
- **Shape:** Restrained 7px radius, one-pixel structural border, monospace uppercase label.
- **Locked:** Raised blue fill with dim telemetry text; `CHANNEL LOCKED` remains legible and disabled rather than disappearing.
- **Hover / Focus:** Any enabled control may move toward evidence cyan; keyboard focus always receives the dedicated cyan halo. The locked preview does not imply that authentication is configured.

### Status Signals

- **Character:** A semantic lamp-and-label pair, never color alone.
- **Nominal:** Electric signal text and lamp for verified or live posture, with restrained glow.
- **Caution:** Amber for unknown, unavailable, gated, degraded, or attention-needed conditions.
- **Critical:** Stop pink for STOP, materially unsafe, or failed states.
- **Neutral:** Dim telemetry text when a state is not available for classification.

### Cards / Containers

- **Corner Style:** Internal panels are square and contiguous; the outer downlink frame carries the chamfer.
- **Background:** Downlink blue planes with raised blue only for nested controls.
- **Shadow Strategy:** No internal card shadows; use the single apparatus shadow and occasional signal glow from Elevation & Depth.
- **Border:** One-pixel structural lines establish rows, columns, and provenance flow.
- **Internal Padding:** 17–28px on desktop, contracting to 15–22px on mobile.

### Inputs / Fields

- **Style:** The owner-link field sits on void black with a structural border, 10px radius, an evidence-cyan microphone glyph, and a disabled channel action when authentication is unavailable.
- **Focus:** A 3px translucent evidence-cyan halo; the field itself stays visually quiet.
- **Error / Disabled:** State is stated in text. Disabled means unavailable control, not a fabricated voice capability or accepted directive.

### Navigation

The command rail uses muted labels and small diamond markers. The active destination uses downlink blue, telemetry white, and a structural border. At 1120px labels collapse while the rail remains a stable signal; at 700px the active destination stays visible in a compact horizontal header and inactive destinations recede rather than becoming unreadable tabs.

### Command Core Orbit

The current-goal panel centers a circular JARVIS core inside cyan and violet orbital rings. The geometry anchors owner intent and provides cinematic radial depth; it does not claim a health value that is not present in durable records. The core-orbit pulse runs for two 5.6-second cycles, the inner core glow for four 2.8-second cycles, and the crossed elliptical rings drift in opposite directions for two 7-second cycles. Reduced motion removes all four animations and leaves the static geometry and halo intact.

### Mission Vector

Each mission is an evidence-linked article rather than a generic table row: durable mission identifier and state lead, the objective receives Tektur emphasis, and the next action is explicit. The mission edge is a 1px semantic signal line. Progress is shown honestly as `progress UNKNOWN / state-derived signal only`; no fabricated width or percentage is presented when durable measurement is unavailable.

### Evidence Timeline

The newest-first decision signal is a scrollable vertical spine with diamond nodes, event metadata, concise summaries, and direct evidence links. Mission identifiers open `/api/record/mission/{id}` and timeline events open `/api/record/event/{event_id}`, keeping displayed claims attached to their durable records. Only the newest node receives two finite 2.8-second illumination cycles; reduced motion replaces this with a static halo. The timeline remains useful when records are absent by stating the honest empty state.

## Do's and Don'ts

### Do:

- **Do** preserve one continuous downlink apparatus with hairline internal divisions and ledger-linked records.
- **Do** keep state text beside every colored lamp and retain explicit `UNKNOWN`, `NOT_AVAILABLE`, `NOT_TESTED`, and `BLOCKED` results.
- **Do** use Tektur for system identity, Segoe for explanation, and monospace for measurements and provenance.
- **Do** maintain the 1120px and 700px structural transitions, mobile STOP visibility, and the reduced-motion equivalent for newest-event illumination.
- **Do** use evidence-cyan links to make every material operational claim inspectable from the evidence spine.

### Don't:

- **Don't** turn the command core into a generic grid of floating cards, pills, gradients, or decorative metrics.
- **Don't** use electric signal, amber, or stop pink without their verified, caution, and critical meanings.
- **Don't** imply success with missing data, substitute zero for unknown, or remove provenance to simplify the view.
- **Don't** animate the whole timeline, loop signal motion indefinitely, or depend on motion to convey state.
- **Don't** let year-2100 science-fiction styling reduce contrast, keyboard focus, mobile access, or operational candor.
