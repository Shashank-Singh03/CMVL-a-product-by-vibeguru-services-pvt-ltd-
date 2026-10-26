# Codemap dashboard

Navigation uses bounded hash routes for views, captured tasks, lesson steps and selected examples. Back/Home,
browser Back/Forward and breadcrumbs connect the library, learning and scoped analysis. Learning shows one
change at a time, with direct links to recorded map nodes and expandable definitions. See [navigation](../docs/navigation.md).

This React dashboard is compiled into the existing local task-history HTML. Published history pages include React, styles, and public task data in one file. They work offline and on a local static server, without Node, a CDN, or a separate API.

The study workspace uses a compact navigation rail with outside-click dismissal,
an explicit mobile drawer, and shared light/dark glass surfaces. Appearance persists locally. Manrope is
embedded for offline reading under the SIL Open Font License; its license is kept
with the shared assets and embedded in the generated stylesheet. The style guide
is [DESIGN.md](../DESIGN.md); shared typography and color tokens live in
`skills/codemap/assets/studio.css`, with layout refinements in `src/studio.css` and
`src/glass.css`. The independent review is recorded in `docs/brand-ui-audit.md`.

The overview uses a single rotating snapshot with manual selection, pause, focus and
hover suspension, and reduced-motion support. Unknown counts remain unavailable.
The map is a workspace route with a sandboxed frame; a small source-validated message
bridge synchronizes theme, dismissal, navigation, and selected view/item. Only public
generated map responses allow same-origin framing on the scoped local server.

The curated Notes app overview has a source template in `dashboard/example/`.
Run `python scripts/refresh_demo.py` from the repository root to rebuild its overview
and map shell while retaining the original captured map data and 13 audio clips.
The overview's counts and area links come from that captured payload; its reading
guide is specific to this example, separate from the generated task lessons.

For frontend development:

```powershell
cd dashboard
npm ci
npm run dev
```

The development page uses clearly separated example metadata from `index.html`; it does not read project snapshots. To package UI changes into the Python app:

```powershell
npm run build
```

The build writes `history.bundle.js` and `history.css` into `skills/codemap/scripts/codemap_lib/assets`. Regenerate a project's history using `codemap.py task history <project>` to include the new UI in its HTML. The bundle name marks generated JavaScript so source capture does not treat the compiled React/ECharts code as an application source change.

The page reads `window.__CODEMAP_HISTORY__`. Static-server refresh reads only the inert JSON element `codemap-history-data` from the current history HTML. Active tasks poll every five seconds; refreshing preserves an open video player. A `file://` page uses normal reload for its Refresh action. Automatic HTML refresh is restricted to the no-JavaScript fallback so active captures do not reset a running React view.

Learning provides a reading path through the goal, concepts, highlighted changes, connections and review questions assembled from the saved analysis. Definitions expand in place. Added and removed lines are styled distinctly and are rendered as text, never executable HTML. `Mark understood` is a personal reading checklist, not a test of understanding. Checklist records contain only task IDs, learning revisions and step IDs; together with viewed-task markers they are bounded to the most recent 200 task records in localStorage. The theme preference is also stored locally. New lesson revisions do not inherit completion from an earlier version.

Analysis uses bundled Apache ECharts bar and pie charts, with text data tables for the same figures. Language/domain counts are summed across saved captures and can count a file more than once. Generation progress counts recorded stages; it does not estimate remaining execution time or whole-project completion. Source-review coverage is not an accuracy score. The build preserves runtime dependency license notices, including ECharts' Apache license and NOTICE.

A development server's own live-reload feature can still reload the whole page when files change. Disable that feature for uninterrupted playback while other tasks generate outputs; the dashboard's polling will continue to update task metadata.

Compare puts two saved task summaries, metrics and selected change excerpts side by side. It reports differences between recorded task figures, rather than manufacturing a repository diff between the two end snapshots. Its downloaded Markdown summary includes public task metadata only.

Search, filters, comparisons, learning, charts and video previews work in the browser without a server API. For project settings and task controls, start the scoped local server from the codemap checkout:

```powershell
python skills/codemap/scripts/codemap.py serve path/to/project --open
```

The server adds a runtime marker before the bundled script. Only this explicit marker enables `/api/session` and `/api/history`; ordinary static pages and Live Server do not probe for an API. The session token stays in memory, never in localStorage or a URL. Connected dashboards poll even while idle so new captures appear without resetting search, lessons or an open video.

Settings configure future tasks; existing captures keep their pinned settings. Automatic mode still requires installed Claude hooks. Task detail controls retry a failed stage using its saved inputs, or cancel generation after an explicit confirmation. A source review or explanation that needs correction still requires the coding agent's input. Disconnection leaves saved history available and offers Reconnect. Static/offline dashboards show startup instructions instead of pretending those controls are active.
