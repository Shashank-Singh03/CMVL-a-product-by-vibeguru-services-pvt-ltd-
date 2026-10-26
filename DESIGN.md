# Codemap study workspace

An editorial developer workspace for following a change from intent to evidence.
The interface should feel calm enough to read for an hour, and structured enough
to find a task in seconds. The dashboard is a working application, not a landing page.

## Foundations

- Manrope, locally embedded under the SIL Open Font License. Use system monospace
  for source and identifiers. Body 14–15px, reading text 15px/1.8, secondary text
  12–13px, headings 24–32px. Avoid tiny explanatory copy.
- Deep green and pale green foundations with selective translucent glass surfaces.
  Mint identifies change/actions; sky identifies source/structure; violet identifies
  relationships/learning; amber and rose communicate findings. Labels and icons
  accompany color. Reading surfaces stay opaque enough for sustained reading.
- Shared tokens live in `skills/codemap/assets/studio.css`. Both themes have the
  same hierarchy. No remote fonts, runtime CDN, or fake data. Subtle radial lighting
  gives translucent surfaces depth without competing with the content.
- 4px spacing scale. Layout gaps 24–32px; panel padding 24px; controls at least
  40px tall; 8px controls, 12px panels. Soft borders, restrained shadows.

## Structure and behavior

- Desktop: 80px rail, expanded labels in a 272px overlay. The visible menu/brand
  trigger opens navigation; outside pointer interaction, focus moving outside,
  navigation selection, and Escape dismiss it. No dedicated collapse button.
- Mobile: compact header and explicit Menu trigger. The drawer opens over a scrim,
  focuses the first navigation item, and dismisses when focus moves outside it.
  Escape restores focus to the opening trigger. Keep Shashank's credit at foot.
- Fluid workspace width, with gutters `clamp(20px, 2.8vw, 80px)`. Header, content
  and footer align. Limit prose width; do not cap the entire dashboard. Verify the
  actual 3221px-wide composition alongside desktop, tablet, and phone layouts.
- Page heading precedes one focused metric spotlight. Rotate every seven seconds
  with manual topic selection and Pause/Play. Hover suspends; keyboard focus pauses;
  reduced-motion preferences disable automatic rotation. Never auto-announce slides.
  Label unavailable and partial metrics. Keep attention findings outside rotation.
- Maps and walkthroughs stay in the workspace shell. Keep selected map view/item
  in the parent route and preserve it when returning from learning or analysis.
- Learning reads like a chapter: one focused example, contextual definitions,
  next/back controls, with deeper evidence disclosed on demand.
- Keep real URLs and browser history. Selected, hovered, focused and disabled
  states are distinct. Collapsed icons retain accessible names and tooltips.
- Respect reduced motion. Persist sidebar and theme preferences when storage
  is available. Failure to access storage must not break navigation.

## References

Informed by the design-system approach in
[Awesome DESIGN.md](https://github.com/VoltAgent/awesome-design-md) and the
audit, color/shape consistency and theme guidance in
[Taste documentation](https://www.tasteskill.dev/docs). No third-party skill or
installer was executed. Product data and existing navigation remain authoritative.

The 26 October refinement was independently audited using official
[Fluent layout](https://fluent2.microsoft.design/layout),
[Fluent drawer](https://fluent2.microsoft.design/components/web/react/core/drawer/usage),
[Carbon color](https://www.carbondesignsystem.com/building-blocks/foundations/color/overview),
and [W3C carousel guidance](https://www.w3.org/WAI/ARIA/apg/patterns/carousel/).
See [the audit and implementation rationale](docs/brand-ui-audit.md).
