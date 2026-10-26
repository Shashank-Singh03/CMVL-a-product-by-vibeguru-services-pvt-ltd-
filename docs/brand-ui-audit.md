# Dashboard refinement audit · 26 October 2026

The goal is a coherent product workspace for exploring and studying code changes.
An independent agent reviewed the existing implementation and established product
design systems before this implementation pass.

## What was wrong

1. The example centered the entire workspace inside a 1540px cap. On the supplied
   3221px-wide screen, that left half the canvas unused. React had competing
   1440px and 1594px caps. Flexbox already existed; the container policy was wrong.
2. Map and video actions left the dashboard for standalone files. The shell,
   navigation, and current context disappeared.
3. Sidebar collapse depended on a separate control. The requested interaction is
   light dismissal: outside pointer interaction or Escape closes the expanded nav.
4. Several cards repeated the same intent, and four metrics competed for attention.
5. Mostly green surfaces did not distinguish structure, relationships, learning,
   and review. Many equal borders made hierarchy depend on reading every label.

## Chosen structure

- One persistent workspace shell. Overview, guide, map, and walkthrough are views
  within it. Source downloads remain explicit downloads.
- A slim desktop icon rail; activate the brand/menu to expand labels. Expanded
  navigation overlays the content without resizing it and dismisses on outside
  interaction. Phones use a dismissible drawer with a backdrop.
- Fluid workspace width and balanced, clamped gutters. Restrict the width of
  prose, not the whole dashboard. Test 360, 390, 768, 1440, 1920, and 3221px.
- One prominent summary topic at a time. Every six seconds it moves to the next
  metric, with a clear explanation and an appropriate action. Pause, manual topic
  selection, hover/focus suspension, hidden-page suspension, and reduced-motion
  behavior keep the rotation under the reader's control.
- A short change-intent panel followed by an area directory. Deeper learning and
  review details live in their own views, avoiding duplicate explanation cards.

## Visual rules

Use a deep green foundation with translucent, blurred surfaces, soft highlights,
and reliable opaque fallbacks. Never put text directly over a busy image.

Mint identifies changes and the primary action. Blue identifies files and symbols.
Violet identifies relationships and learning. Amber means attention; red remains
reserved for high-risk or removed content. Text labels carry meaning alongside
color. The same role has the same color in the example and React dashboard.

Keep Manrope offline, strong headings, comfortable reading text, and consistent
control sizes. Preserve Shashank's credit at the foot. Motion should help people
notice a state change, not continuously compete with the material.

## Integration boundaries

The map renderer remains a separate generated document, displayed inside the shell
in a restricted iframe. Only generated map responses may be framed by the same
origin. Dashboard/API responses keep their existing frame restrictions. Parent and
map communicate through a small, source-validated message protocol; source code is
never executed. File and HTTP viewing must both work.

## Review references and acceptance

The audit used official [Fluent layout guidance](https://fluent2.microsoft.design/layout),
[Fluent drawer guidance](https://fluent2.microsoft.design/components/web/react/core/drawer/usage),
[Fluent materials](https://fluent2.microsoft.design/material),
[Carbon role-based color](https://www.carbondesignsystem.com/building-blocks/foundations/color/overview),
and the [W3C carousel pattern](https://www.w3.org/WAI/ARIA/apg/patterns/carousel/).
The glass treatment is Codemap's interpretation of those principles.

The implementation was independently reviewed again after screenshots at 1440px,
3221px and 390px. That review led to semantic area colors, a bounded metric-number
column, focus-out dismissal for navigation, and retained map selections. A late-map
state race was reproduced and covered by an explicit navigation regression.

Browser acceptance covers local files, static HTTP, and the scoped local server;
phone/tablet/desktop widths from 360px through 3221px; light and dark themes;
outside/inside pointer behavior; Escape and focus dismissal; real video playback;
embedded map search and return links; map selection restoration; invalid message
sources; and automatic/manual/paused rotation. Phone topic changes retain a stable
panel height. Runtime checks and screenshots are kept under the ignored local
`benchmarks/brand/2026-10-26/` evidence directory.
