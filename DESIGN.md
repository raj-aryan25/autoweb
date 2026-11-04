---
name: AutoWeb Studio
description: A graphite and teal workspace for inspecting login runs.
colors:
  bg: "#181b20"
  sidebar: "#14181c"
  surface: "#22262d"
  steps: "#1b1f25"
  border: "#343d46"
  text: "#e7ebef"
  muted: "#a5b3be"
  accent: "#5ed3c4"
  accent-text: "#b8f0e8"
  accent-surface: "#233a3a"
  amber: "#f0cb8d"
  amber-surface: "#332e25"
  danger: "#f0b4a8"
  danger-surface: "#312727"
  primary-ink: "#12332d"
  primary-hover: "#87e3d6"
  field: "#181e23"
  field-border: "#50606d"
  nav-active: "#213a39"
  nav-active-text: "#90e7dc"
typography:
  headline:
    fontFamily: "'DM Sans Variable', 'DM Sans', sans-serif"
    fontSize: "29px"
    fontWeight: 600
    lineHeight: 1.25
    letterSpacing: "-0.8px"
  title:
    fontFamily: "'DM Sans Variable', 'DM Sans', sans-serif"
    fontSize: "21px"
    fontWeight: 550
    lineHeight: 1.35
    letterSpacing: "-0.4px"
  body:
    fontFamily: "'DM Sans Variable', 'DM Sans', sans-serif"
    fontSize: "14px"
    fontWeight: 450
    lineHeight: 1.65
  label:
    fontFamily: "'DM Sans Variable', 'DM Sans', sans-serif"
    fontSize: "12px"
  data:
    fontFamily: "'IBM Plex Mono', monospace"
    fontSize: "12px"
rounded:
  badge: "5px"
  field: "6px"
  control: "7px"
  panel: "8px"
spacing:
  compact: "8px"
  control: "12px"
  group: "16px"
  section: "24px"
  page: "32px"
components:
  button-primary:
    backgroundColor: "{colors.accent}"
    textColor: "{colors.primary-ink}"
    rounded: "{rounded.control}"
    padding: "10px 14px"
  button-primary-hover:
    backgroundColor: "{colors.primary-hover}"
  button-secondary:
    backgroundColor: "transparent"
    textColor: "{colors.text}"
    rounded: "{rounded.control}"
    padding: "10px 14px"
  button-stop:
    backgroundColor: "{colors.danger-surface}"
    textColor: "#f0c5bd"
    rounded: "{rounded.control}"
    padding: "10px 14px"
  input:
    backgroundColor: "{colors.field}"
    textColor: "{colors.text}"
    rounded: "{rounded.field}"
    padding: "10px 12px"
  select:
    backgroundColor: "{colors.field}"
    textColor: "{colors.text}"
    rounded: "{rounded.field}"
    padding: "10px 24px 10px 12px"
  nav-active:
    backgroundColor: "{colors.nav-active}"
    textColor: "{colors.nav-active-text}"
    rounded: "{rounded.control}"
    padding: "12px 13px"
  chip-waiting:
    backgroundColor: "{colors.amber-surface}"
    textColor: "{colors.amber}"
    rounded: "{rounded.badge}"
    padding: "5px 9px"
  configuration-card:
    backgroundColor: "#20262c"
    rounded: "{rounded.panel}"
    padding: "23px 32px"
  step-selected:
    backgroundColor: "{colors.accent-surface}"
    textColor: "{colors.accent-text}"
    rounded: "{rounded.panel}"
    padding: "13px 11px"
  inspector-tab:
    backgroundColor: "transparent"
    textColor: "{colors.accent}"
    padding: "0 0 13px"
---

# Design System: AutoWeb Studio

## Overview

**Creative North Star: "Split workspace"**

AutoWeb Studio uses the user-selected graphite and teal palette: a quiet dark workspace with clearly marked actions and inspectable execution state. Dense information stays legible through restrained headings, generous section padding, and a separate monospace voice for measurements.

Surfaces are flat and divided by fine borders. Teal connects the primary action, current navigation, selected step, and keyboard focus; semantic status colors explain waiting and failure alongside text and icons.

**Key Characteristics:**

- Graphite surfaces with teal interaction cues.
- Compact controls and readable operational detail.
- Flat tonal layering and thin structural dividers.
- DM Sans interface paired with IBM Plex Mono data.

## Colors

The palette combines cool graphite neutrals with a clear teal accent and warm semantic feedback. Frontmatter values are normative; the CSS source is `apps/web/src/styles.css`.

### Primary

- **Clear teal** (`accent`): primary action, focus ring, active tabs, progress, and successful-step icons.
- **Pale teal** (`accent-text`): selected-step title on **Deep teal surface** (`accent-surface`).
- **Navigation teal** (`nav-active`, `nav-active-text`): current navigation item.
- **Primary ink / hover** (`primary-ink`, `primary-hover`): dark button text and brighter hover fill.

### Neutral

- **Graphite** (`bg`): main canvas; **Rail graphite** (`sidebar`): navigation; **Step graphite** (`steps`): execution list.
- **Raised graphite** (`surface`): reserved CSS surface token; most implemented grouping uses borders rather than raised cards.
- **Cool divider** (`border`): structural rules; **Near-white** (`text`): primary copy; **Mist** (`muted`): labels and supporting copy.
- **Field graphite / field border** (`field`, `field-border`): native inputs and selects.

Waiting uses **Soft amber** (`amber`, `amber-surface`); failure uses **Soft red** (`danger`, `danger-surface`). Success and running badges use their implemented green/teal variants. Idle and cancellation remain neutral.

**The State Pairing Rule.** Status color accompanies a readable state label or icon.

## Typography

DM Sans Variable, falling back to DM Sans and sans-serif, carries the interface. IBM Plex Mono, falling back to monospace, carries identifiers and measurements with tabular numerals. The interface has no separate decorative display face.

- **Headline:** page and run title; mobile reduces the heading to (26px).
- **Title:** inspector heading; secondary pages use smaller section headings (18px).
- **Body:** primary content; paragraphs use the frontmatter line height. Most supporting copy is (13px), with help and section notes constrained to (75ch).
- **Label:** metadata, forms, badges, and supporting details. Labels use sentence case.
- **Data:** run IDs, durations, timestamps, and counts. Small count pills use (10px).

## Layout

The desktop app uses a navigation rail (196px), a main area, and a breadcrumb bar at least (72px) high. The run workspace divides into an execution pane (302px) and a flexible inspector. Main horizontal padding is (32px), with compact spacing inside controls and larger spacing between sections. Configuration uses a five-column form grid; metadata wraps.

At (1250px) and below, the execution pane reduces to (280px) and forms use three columns. At (1050px), the rail becomes (176px), steps become (252px), header actions wrap beneath the title, and condition pairs stack. At (740px), navigation becomes a horizontal scrolling row, the workspace stacks in document order, page padding becomes (18px), and forms use two columns with the first field full width. Wide history tables scroll within their container. Above (1600px), inspector right padding increases to control reading width.

Selecting a step on mobile scrolls the inspector into view. Keep content in natural document scrolling rather than placing the inspector in a fixed overlay.

## Elevation & Depth

There are no box shadows in the implemented system. Depth comes from darker navigation, a separate step background, selected-state tint, and one-pixel dividers. Cards remain flat. The focus treatment is an offset teal outline (2px, 4px offset), not a shadow.

## Shapes

Controls are gently rounded: fields (6px), buttons and navigation items (7px), panels and steps (8px), and compact badges (5px). Borders are thin and explicit. Circular step markers and small status dots distinguish state from rectangular controls. Icons are simple strokes, usually (18px), reduced inside controls.

## Components

### Buttons

Primary buttons use clear teal with dark ink, medium-heavy text (600), and a transparent border. Secondary buttons are transparent with a visible gray border; hover lifts their fill and border tone. Stop buttons use a muted failure surface and warm text. Shared buttons have a minimum height (40px), increasing to (42px) on mobile. Primary hover brightens the teal. Disabled controls mute fill and text and use the not-allowed cursor. Text actions are teal and underline on hover; icon actions use a compact transparent square.

### Chips

Status badges are compact, softly rounded, and carry a colored dot plus state text. Waiting uses amber; failure uses soft red; success and running use distinct green/teal pairings. Neutral states use gray. Preview badges use an outline to identify demo context without competing with the primary action.

### Cards / Containers

Grouping is usually an open section bounded by dividers. The settings configuration container uses a flat dark fill, thin border, panel corners, and section padding. Inline failure notices use the semantic failure surface; empty evidence uses centered copy and a restrained line icon.

### Inputs / Fields

Native inputs and selects use the dark field fill, visible stroke, near-white text, and a minimum height (41px). Labels sit above controls with a gap (9px). Input caret and keyboard focus use teal. Invalid nonempty inputs shift their border to soft red. Selects retain native dropdown behavior; checkboxes use teal accent color.

### Navigation

Rail links pair line icons and sentence-case labels. Hover adds a graphite fill; the current route uses teal fill and lighter teal text. Counts sit at the trailing edge in monospace. Mobile navigation preserves the links in a horizontal scrolling row and hides auxiliary counts and account details.

### Execution steps and inspector tabs

Step rows carry a circular state marker, title, supporting outcome, and optional monospace duration. Selection adds a deep teal fill and visible border; hover adds a graphite tint. Text and marker maintain distinct pending, running, waiting, succeeded, failed, and cancelled states. The current selection is exposed with `aria-current="step"`.

Details, Evidence, and Events tabs use a thin teal underline and teal text for selection. Other tabs remain muted and brighten on hover. Keyboard focus retains the shared offset outline. Step background and border transitions last (180ms, ease-out); reduced-motion preference disables transitions and animation.

## Do's and Don'ts

### Do:

- **Do** use teal for primary actions, selection, and keyboard focus.
- **Do** pair semantic color with readable status text or an icon.
- **Do** preserve the monospace treatment for run IDs, times, and counts.
- **Do** let navigation, steps, and inspector stack and scroll naturally on small screens.

### Don't:

- **Don't** replace the graphite and teal palette with unrelated accents.
- **Don't** add decorative shadows to the flat workspace.
- **Don't** hide keyboard focus or rely on color alone to explain a run state.
- **Don't** use the selected-step fill for every step in the sequence.
