# Browser acceptance tests

The published dashboard already contains its runtime assets. Node and Playwright
are only needed to develop the UI or run these optional browser checks.

From the repository root, install the dashboard's locked development dependencies:

```sh
npm --prefix dashboard ci
node tests/dashboard-smoke.cjs
node tests/navigation-smoke.cjs
node tests/learning-dashboard-smoke.cjs
node tests/compare-dashboard-smoke.cjs
node tests/controls-dashboard-smoke.cjs
node tests/brand-dashboard-smoke.cjs
```

The shared helper first looks for Chrome, Edge or Chromium in common installation
locations and on `PATH`. To choose a particular executable, set `CODEMAP_BROWSER`
to its full path before running a test. It must point to an existing file.

PowerShell:

```powershell
$env:CODEMAP_BROWSER = 'C:/Program Files/Microsoft/Edge/Application/msedge.exe'
node tests/dashboard-smoke.cjs
```

POSIX shell:

```sh
CODEMAP_BROWSER=/usr/bin/chromium node tests/dashboard-smoke.cjs
```

When no system browser is found, the helper uses Playwright's downloaded Chromium.
Install it explicitly if needed; browser downloads are never part of normal app use:

```sh
node dashboard/node_modules/playwright-core/cli.js install chromium
```

On Linux, the browser may also require operating-system libraries. Playwright's
installer reports missing dependencies; use the instructions appropriate to your
environment. Run UI tests in an environment that permits launching a headless browser
and listening on temporary loopback ports. Python and Git must also be available.

Tests create disposable source fixtures and parse them using Codemap's own tooling.
They do not execute the fixture applications or their deployment/CI configuration.
The controls suite launches only its own temporary local dashboard server. The
brand suite also plays the curated Notes app recording.

All suites use the same helper at `tests/browser-test-support.cjs`. No developer's
personal `NODE_PATH` or fixed workstation path is required. The old
`studio-dashboard-smoke.cjs` entry point forwards to the current brand suite.
