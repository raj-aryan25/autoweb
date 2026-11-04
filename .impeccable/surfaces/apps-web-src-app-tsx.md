---
version: 1
slug: "apps-web-src-app-tsx"
primary_target: "apps/web/src/App.tsx"
related_targets: ["apps/web/src/styles.css"]
---

# Split workspace

Mode: Operate. Local developer watches a login run and diagnoses individual steps.
Scope: React UI with deterministic simulated runs; no browser/email/AI calls. The
selected static preview remains untouched. Backend integration is a later milestone.

## Direction contract

THESIS: Keep the complete step sequence visible beside a selected-step inspector.
OWN-WORLD: Match split.html: graphite #181B20, darker navigation, teal #5ED3C4
selection, amber waiting; DM Sans interface and IBM Plex Mono measurements.
STORY: Select a profile, follow a simulated run, inspect expected versus observed
conditions, and switch between details, evidence and events without losing place.
FIRST VIEWPORT: At 1440px, 196px left navigation, 72px breadcrumb bar, run title
and controls above a metadata row, 302px step pane beside the flexible inspector.
Primary action is top right. The selected step is the only filled teal row. Small
screens stack navigation, steps and inspector with natural scrolling. Include
idle, waiting, success, failure, cancellation and empty evidence states.
FORM: User selected Split workspace, top-ranked grounded candidate #1, from
seed:cee36d77b0391083. Code-led. The critique reference is
.impeccable/mocks/decision/split.html and its existing split-1440.png capture;
the returned split.png does not exist and must not be invented as an approved comp.
FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance
